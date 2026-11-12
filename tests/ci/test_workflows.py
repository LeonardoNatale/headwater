"""The workflow files, read as data: what they may not do, and that they fit together.

GitHub runs them, not these tests, but the rules in AGENTS.md (external actions pinned to commit
SHAs) and the choices that keep the token small are checkable without GitHub.
"""

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
ACTION = ROOT / ".github" / "actions" / "study-diff" / "action.yml"
REUSABLE = WORKFLOWS / "study-diff.yml"
DEMO = WORKFLOWS / "study-diff-demo.yml"
CI = WORKFLOWS / "ci.yml"
RELEASE = WORKFLOWS / "release.yml"
SHA = re.compile(r"[0-9a-f]{40}")


def load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict)
    if True in data:  # YAML reads the key `on` as a boolean
        data["on"] = data.pop(True)
    return data


def steps(path: Path) -> list[dict[str, Any]]:
    data = load(path)
    if path == ACTION:
        return data["runs"]["steps"]
    return [s for job in data["jobs"].values() for s in job.get("steps", [])]


def test_every_external_action_is_pinned_to_a_commit_sha() -> None:
    files = [*sorted(WORKFLOWS.glob("*.yml")), ACTION]
    uses = [(f.name, s["uses"]) for f in files for s in steps(f) if "uses" in s]
    jobs = [
        (f.name, job["uses"])
        for f in WORKFLOWS.glob("*.yml")
        for job in load(f).get("jobs", {}).values()
        if "uses" in job
    ]

    external = [(f, u) for f, u in [*uses, *jobs] if not u.startswith("./")]

    assert external, "the workflows use actions"
    for name, used in external:
        _, _, ref = used.partition("@")
        assert SHA.fullmatch(ref), f"{name}: {used} is not pinned to a commit sha"


def test_the_pins_carry_their_version_in_a_comment() -> None:
    for path in [REUSABLE, ACTION, CI, RELEASE]:
        for line in path.read_text().splitlines():
            if re.search(r"uses: [\w.-]+/[\w.-]+@[0-9a-f]{40}", line):
                assert re.search(r"# v\d", line), f"{path.name}: {line.strip()}"


def test_a_pull_request_runs_from_its_own_code_and_never_with_the_base_secrets() -> None:
    for path in WORKFLOWS.glob("*.yml"):
        assert "pull_request_target" not in load(path)["on"]


def test_the_reusable_job_may_read_the_code_and_write_comments_and_nothing_else() -> None:
    data = load(REUSABLE)

    assert data["permissions"] == {}
    [job] = data["jobs"].values()
    assert job["permissions"] == {"contents": "read", "pull-requests": "write"}
    assert "workflow_call" in data["on"]
    assert "pull_request" not in data["on"]


def test_the_demo_runs_on_pull_requests_that_touch_the_demo_studies_and_calls_the_workflow() -> (
    None
):
    data = load(DEMO)

    assert data["on"]["pull_request"]["paths"] == ["src/headwater/demo/**"]
    assert "branches" not in data["on"]["pull_request"]
    [job] = data["jobs"].values()
    assert job["uses"] == "./.github/workflows/study-diff.yml"
    assert job["permissions"] == {"contents": "read", "pull-requests": "write"}
    declared = load(REUSABLE)["on"]["workflow_call"]["inputs"]
    assert set(job["with"]) <= set(declared)
    required = {k for k, v in declared.items() if v.get("required")}
    assert required <= set(job["with"])


def test_the_workflow_passes_only_inputs_the_action_has() -> None:
    inputs = set(load(ACTION)["inputs"])
    [action_step] = [s for s in steps(REUSABLE) if s.get("uses", "").endswith("/study-diff")]

    assert set(action_step["with"]) <= inputs


def test_the_action_never_puts_a_github_expression_inside_a_script() -> None:
    """Values reach scripts through `env:`, so a branch or study name cannot become code."""
    for step in steps(ACTION):
        assert "${{" not in step["run"], step["name"]


def test_the_group_that_stops_older_runs_has_a_literal_prefix() -> None:
    [job] = load(REUSABLE)["jobs"].values()

    assert job["concurrency"]["group"].startswith("study-diff-")
    assert job["concurrency"]["cancel-in-progress"] is True


def test_the_action_scripts_use_only_the_standard_library() -> None:
    stdlib = {
        "argparse", "json", "os", "re", "shlex", "subprocess", "sys",
        "collections", "dataclasses", "pathlib", "typing",
    }  # fmt: skip
    for script in (ROOT / ".github" / "actions" / "study-diff").glob("*.py"):
        imported = re.findall(r"^(?:import|from) (\w+)", script.read_text(), flags=re.M)
        assert set(imported) <= stdlib, f"{script.name}: {sorted(set(imported) - stdlib)}"


def test_method_folders_go_from_the_workflow_through_the_action_to_collect() -> None:
    declared = load(REUSABLE)["on"]["workflow_call"]["inputs"]
    [action_step] = [s for s in steps(REUSABLE) if s.get("uses", "").endswith("/study-diff")]
    [collect_step] = [s for s in steps(ACTION) if "collect.py" in s["run"]]

    assert declared["methods"]["default"] == ""
    assert action_step["with"]["methods"] == "${{ inputs.methods }}"
    assert load(ACTION)["inputs"]["methods"]["default"] == ""
    assert collect_step["env"]["METHODS"] == "${{ inputs.methods }}"
    assert '--methods "$METHODS"' in collect_step["run"]


def test_ci_tests_the_newest_and_the_lowest_dependencies_on_python_3_13() -> None:
    job = load(CI)["jobs"]["dependencies"]
    runs = "\n".join(s.get("run", "") for s in job["steps"])
    [setup] = [s for s in job["steps"] if "setup-uv" in s.get("uses", "")]

    assert job["strategy"]["matrix"]["resolution"] == ["highest", "lowest-direct"]
    assert setup["with"]["python-version"] == "3.13"
    assert "uv pip compile pyproject.toml" in runs
    assert "--resolution ${{ matrix.resolution }}" in runs
    assert "--locked" not in runs, "these jobs resolve without the lock"
    assert "-m pytest" in runs


def test_ci_runs_the_quick_start_from_the_wheel_in_a_clean_venv() -> None:
    job = load(CI)["jobs"]["dependencies"]
    [step] = [s for s in job["steps"] if "HEADWATER_QUICK_START_BIN" in s.get("run", "")]

    assert step["if"] == "matrix.resolution == 'highest'"
    assert "uv build" in step["run"] and "dist/*.whl" in step["run"]
    assert "tests/test_readme.py" in step["run"]


def test_a_version_tag_starts_the_release_and_nothing_else_does() -> None:
    data = load(RELEASE)

    assert data["on"] == {"push": {"tags": ["v*"]}}
    assert data["permissions"] == {"contents": "read"}


def test_the_release_builds_once_then_publishes_to_testpypi_then_to_pypi() -> None:
    jobs = load(RELEASE)["jobs"]

    assert list(jobs) == ["build", "testpypi", "pypi"]
    assert "permissions" not in jobs["build"]
    assert jobs["testpypi"]["needs"] == "build"
    assert jobs["pypi"]["needs"] == "testpypi"
    for name in ("testpypi", "pypi"):
        assert jobs[name]["environment"]["name"] == name
        assert jobs[name]["permissions"] == {"id-token": "write"}
        [publish] = [s for s in jobs[name]["steps"] if "pypi-publish" in s.get("uses", "")]
        url = publish.get("with", {}).get("repository-url")
        assert url == ("https://test.pypi.org/legacy/" if name == "testpypi" else None)


def test_the_release_uses_trusted_publishing_and_no_secret() -> None:
    text = RELEASE.read_text()

    assert "secrets." not in text
    assert "password" not in text
    assert "token" not in text.replace("id-token", "")


def test_the_release_checks_the_tag_then_builds_checks_and_runs_the_quick_start() -> None:
    job = load(RELEASE)["jobs"]["build"]
    runs = [s.get("run", "") for s in job["steps"]]
    tag = next(i for i, r in enumerate(runs) if "uv version --short" in r)
    build = next(i for i, r in enumerate(runs) if r.strip().startswith("uv build"))
    check = next(i for i, r in enumerate(runs) if "twine" in r and "check --strict" in r)
    quick = next(i for i, r in enumerate(runs) if "HEADWATER_QUICK_START_BIN" in r)

    assert tag < build < check < quick
    assert job["steps"][tag]["env"]["TAG"] == "${{ github.ref_name }}"
    assert all("${{" not in r for r in runs), "values reach scripts through env:"
    assert any("upload-artifact" in s.get("uses", "") for s in job["steps"])
