"""Can Claude Code use headwater correctly, with and without its skill? (D49)

Five toy tasks with known answers, each run by `claude -p` in a fresh copy of the demo, once
without the skill and once after `headwater skill install`. headwater comes from a built wheel,
installed in a venv of its own whose `bin` is first on PATH, with a HEADWATER_HOME of its own.
Each copy of the demo has its release pulled and is committed to git.

Claude runs with the project's settings only (`--setting-sources project`) and no MCP server
(`--strict-mcp-config`), so the settings, plugins, hooks and instructions of the person running
the script do not reach it; Claude Code's built-in skills and plugins stay, the same in both
variants. The script checks what each session loaded, from its first event, and stops when a
plugin that is not built in or an MCP server is there, or when the skill is missing from a run
that installed it. Tools: Bash, Read, Edit, Write, Glob and Grep, in the copy of the demo.

A task passes when the last answer holds the expected number, with at least 4 decimals and
rounded from the exact value, and for the edit, when the file holds the new amount and the study
validates. Needs no licensed data.

    uv build
    uv run --no-project python bench/agent_tasks.py dist/headwater-0.3.0-py3-none-any.whl \\
        --model sonnet --json bench/results/agent_tasks_sonnet_results.json

Each run is a paid Claude Code session. `--repeat N` runs each task N times per variant.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEMO_RELEASE = "releases/toy-background@6864b7dbb65d"
TOOLS = "Bash,Read,Edit,Write,Glob,Grep"


@dataclass(frozen=True)
class Task:
    name: str
    prompt: str
    numbers: tuple[float, ...]
    """Every one must be in the answer."""
    words: tuple[str, ...] = ()
    """Every one must be in the answer, case ignored."""
    edited: bool = False
    """Before the run, line e02 of the T-shirt is set to 0.3 kg, uncommitted."""
    check_edit: bool = False
    """After the run, line e02 must be 0.3 kg and the study must validate."""


TASKS = (
    Task(
        "score",
        "What is the climate change score of the study project/tshirt-footprint? "
        "Give the number with its unit.",
        (1.242646,),
    ),
    Task(
        "top line",
        "Which line of which process contributes most to the score of the study "
        "project/tshirt-footprint? Name the process, the line id and its share of the score.",
        (),
        ("e02", "fabric"),
    ),
    Task(
        "edit",
        "In this folder, change the T-shirt's cotton fabric from 0.25 kg to 0.3 kg, then give the "
        "new climate change score of the study project/tshirt-footprint.",
        (1.428076,),
        check_edit=True,
    ),
    Task(
        "diff",
        "Something in this folder changed since the last git commit. How did the score of the "
        "study project/tshirt-footprint change, from what to what, and which line caused it?",
        (1.242646, 1.428076),
        ("e02",),
        edited=True,
    ),
    Task(
        "scenario",
        "What is the climate change score of the study project/tshirt-footprint in its scenario "
        "cleaner-dye?",
        (1.237646,),
    ),
)


@dataclass
class Run:
    task: str
    variant: str
    model: str
    passed: bool
    why: str
    answer: str
    commands: list[str] = field(default_factory=list[str])
    """The Bash commands of the session that ran headwater."""
    json_commands: int = 0
    changed: list[str] = field(default_factory=list[str])
    """The files that differ from the commit, as `git status --porcelain` names them: for the
    edit, the T-shirt's exchanges.csv; for the diff, the change made before the run."""
    turns: int | None = None
    seconds: float | None = None
    cost_usd: float | None = None
    models: list[str] = field(default_factory=list[str])
    skills: list[str] = field(default_factory=list[str])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wheel", type=Path, help="The built wheel of headwater.")
    parser.add_argument("--model", default="sonnet", help="The model of `claude -p`.")
    parser.add_argument("--repeat", type=int, default=1, help="Runs of each task and variant.")
    parser.add_argument("--workers", type=int, default=4, help="Sessions at once.")
    parser.add_argument("--task", action="append", help="Only these tasks, by name.")
    parser.add_argument("--json", type=Path, help="Write the results here.")
    args = parser.parse_args()

    work = Path(tempfile.mkdtemp(prefix="headwater-agent-tasks-"))
    venv = work / "venv"
    subprocess.run(["uv", "venv", "-q", str(venv), "--python", "3.13"], check=True)
    subprocess.run(
        ["uv", "pip", "install", "-q", str(args.wheel.resolve())],
        check=True,
        env={**os.environ, "VIRTUAL_ENV": str(venv)},
    )
    env = {
        **os.environ,
        "PATH": f"{venv / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "HEADWATER_HOME": str(work / "home"),
    }
    env.pop("VIRTUAL_ENV", None)
    version = _out(["headwater", "--version"], env=env, cwd=work)
    claude = _out(["claude", "--version"], env=env, cwd=work)
    print(f"{version}, Claude Code {claude}, model {args.model}, in {work}", file=sys.stderr)

    tasks = [t for t in TASKS if not args.task or t.name in args.task]
    jobs = [
        (task, variant, n)
        for n in range(args.repeat)
        for task in tasks
        for variant in ("without skill", "with skill")
    ]
    with concurrent.futures.ThreadPoolExecutor(args.workers) as pool:
        runs = list(
            pool.map(lambda job: _run(job[0], job[1], job[2], work, env, args.model), jobs)
        )

    for run in runs:
        mark = "pass" if run.passed else "FAIL"
        print(f"{mark}  {run.variant:<13}  {run.task:<9}  {run.why}", file=sys.stderr)
    data = {
        "date": datetime.date.today().isoformat(),
        "machine": _machine(),
        "headwater": version,
        "claude_code": claude,
        "model": args.model,
        "tools": TOOLS,
        "tasks": [asdict(t) for t in tasks],
        "runs": [asdict(r) for r in runs],
    }
    if args.json:
        args.json.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    shutil.rmtree(work, ignore_errors=True)


def _run(task: Task, variant: str, n: int, work: Path, env: dict[str, str], model: str) -> Run:
    folder = work / "runs" / f"{task.name.replace(' ', '-')}-{variant.replace(' ', '-')}-{n}"
    folder.parent.mkdir(parents=True, exist_ok=True)
    _out(["headwater", "demo", str(folder)], env=env, cwd=work)
    _out(["headwater", "release", "pull", DEMO_RELEASE], env=env, cwd=folder)
    _out(["git", "init", "-q"], env=env, cwd=folder)
    if variant == "with skill":
        _out(["headwater", "skill", "install"], env=env, cwd=folder)
    _out(["git", "add", "-A"], env=env, cwd=folder)
    git = ["git", "-c", "user.name=bench", "-c", "user.email=bench@example.com"]
    _out([*git, "commit", "-qm", "demo"], env=env, cwd=folder)
    if task.edited:
        _set_fabric(folder, "0.3")

    done = subprocess.run(
        [
            "claude",
            "-p",
            task.prompt,
            "--model",
            model,
            "--output-format",
            "stream-json",
            "--verbose",
            "--setting-sources",
            "project",
            "--strict-mcp-config",
            "--allowedTools",
            TOOLS,
        ],
        cwd=folder,
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
    )
    events = [json.loads(line) for line in done.stdout.splitlines() if line.startswith("{")]
    init = next((e for e in events if e.get("subtype") == "init"), {})
    _check_isolation(init, variant, folder)
    result = next((e for e in reversed(events) if e.get("type") == "result"), {})
    answer = str(result.get("result", "")) or done.stderr[-2000:]
    commands = [
        str(block["input"].get("command", ""))
        for e in events
        if e.get("type") == "assistant"
        for block in e.get("message", {}).get("content", [])
        if block.get("type") == "tool_use" and block.get("name") == "Bash"
    ]
    headwater = [c for c in commands if "headwater" in c]
    passed, why = _judge(task, answer, folder, env)
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=folder, capture_output=True, text=True, check=True
    )
    return Run(
        task=task.name,
        variant=variant,
        model=model,
        passed=passed,
        why=why,
        answer=answer[:1500],
        commands=headwater,
        json_commands=sum("--json" in c for c in headwater),
        changed=status.stdout.splitlines(),
        turns=result.get("num_turns"),
        seconds=(result.get("duration_ms") or 0) / 1000 or None,
        cost_usd=result.get("total_cost_usd"),
        models=sorted(result.get("modelUsage", {})),
        skills=[s for s in init.get("skills", []) if "headwater" in str(s)],
    )


def _check_isolation(init: dict[str, object], variant: str, folder: Path) -> None:
    """Stop when the session loaded a plugin or an MCP server, or lacks the skill it should
    have: the comparison would not be what it says."""
    if not init:
        raise SystemExit(f"{folder}: no init event from claude")
    every: list[dict[str, str]] = init.get("plugins") or []  # type: ignore[assignment]
    plugins = [p for p in every if p.get("path") != "builtin"]  # Claude Code's own are kept
    servers = init.get("mcp_servers") or []
    if plugins or servers:
        raise SystemExit(f"{folder}: the session loaded plugins {plugins} or MCP servers {servers}")
    skills = [str(s) for s in init.get("skills") or []]  # type: ignore[union-attr]
    has = any("headwater" in s for s in skills)
    if has != (variant == "with skill"):
        raise SystemExit(f"{folder}: {variant}, but the session's skills are {skills}")


def _judge(task: Task, answer: str, folder: Path, env: dict[str, str]) -> tuple[bool, str]:
    missing = [n for n in task.numbers if not _has_number(answer, n)]
    if missing:
        return False, f"no {', '.join(f'{n:g}' for n in missing)} in the answer"
    lower = answer.lower()
    absent = [w for w in task.words if w.lower() not in lower]
    if absent:
        return False, f"no {', '.join(absent)} in the answer"
    if task.check_edit:
        rows = (folder / "project" / "tshirt-dyed" / "exchanges.csv").read_text()
        if "tshirt,e02,input,toy:cotton-fabric,0.3,kg," not in rows:
            return False, "line e02 is not 0.3 kg in exchanges.csv"
        done = subprocess.run(
            ["headwater", "validate", "project/tshirt-footprint", "--json"],
            cwd=folder,
            env=env,
            capture_output=True,
            text=True,
        )
        if done.returncode != 0 or not json.loads(done.stdout)["valid"]:
            return False, "the study does not validate after the edit"
    return True, "expected answer" + (", file edited and valid" if task.check_edit else "")


def _has_number(text: str, expected: float) -> bool:
    """True when `text` holds `expected` to the decimals written, 4 at least: 1.24265, 1.2426 and
    1.2426460000000001 for 1.242646, not 1.243 or 1.2427."""
    for whole in re.findall(r"\d+\.\d+", text.replace(",", "")):
        decimals = len(whole.split(".")[1])
        if decimals >= 4 and abs(float(whole) - expected) <= 0.5 * 10**-decimals + 1e-12:
            return True
    return False


def _set_fabric(folder: Path, amount: str) -> None:
    path = folder / "project" / "tshirt-dyed" / "exchanges.csv"
    text = path.read_text()
    old = "tshirt,e02,input,toy:cotton-fabric,0.25,kg,"
    assert old in text
    path.write_text(text.replace(old, f"tshirt,e02,input,toy:cotton-fabric,{amount},kg,"))


def _out(command: list[str], *, env: dict[str, str], cwd: Path) -> str:
    done = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(f"{' '.join(command)} failed in {cwd}:\n{done.stdout}{done.stderr}")
    return done.stdout.strip()


def _machine() -> str:
    try:
        chip = _out(["sysctl", "-n", "machdep.cpu.brand_string"], env=dict(os.environ), cwd=Path())
    except (OSError, SystemExit):
        chip = platform.processor()
    return f"{chip}, {platform.system()} {platform.mac_ver()[0] or platform.release()}"


if __name__ == "__main__":
    main()
