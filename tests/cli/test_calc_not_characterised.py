"""`headwater calc` lists the flows that no indicator covers, and keeps the list short.

Example: 30 background processes that the T-shirt reaches emit nitrous oxide, which `toy-climate`
has no factor for. The list names the flow once, with its first 20 processes and a count.
"""

from collections.abc import Callable
from pathlib import Path

Invoke = Callable[..., tuple[int, str]]

SHOWN = 20


def add_emitters(toy: Path, count: int, flow_of: Callable[[int], str]) -> list[str]:
    """`count` more background processes that fabric production buys from. Process k emits one
    unit of the elementary flow `flow_of(k)`, which `toy-climate` has no factor for. Returns the
    ids of the processes."""
    background = toy / "background"
    flows = background / "flows.csv"
    fabric = background / "processes" / "fabric-production.csv"
    known: set[str] = set()
    ids: list[str] = []
    for k in range(count):
        name = f"emitter-{k:03d}"
        ids.append(f"toy:{name}")
        with flows.open("a") as handle:
            handle.write(f"toy:{name},{name},product,kg,,\n")
            if flow_of(k) not in known:
                known.add(flow_of(k))
                handle.write(f'{flow_of(k)},"Gas {flow_of(k)}",elementary,kg,air,\n')
        (background / "processes" / f"{name}.yaml").write_text(
            f"name: {name}\nlocation: GLO\nkind: ordinary_transforming_activity\n"
        )
        (background / "processes" / f"{name}.csv").write_text(
            "line,role,flow,amount,unit,supplier\n"
            f"e01,reference,toy:{name},1,kg,\n"
            f"e02,emission,{flow_of(k)},0.001,kg,\n"
        )
        with fabric.open("a") as handle:
            handle.write(f"x{k:03d},input,toy:{name},0.001,kg,toy:{name}\n")
    return ids


def calc(toy: Path, invoke: Invoke) -> str:
    code, output = invoke(
        "calc",
        toy / "tshirt" / "tshirt",
        "--release",
        toy / "background",
        "--method",
        toy / "methods" / "toy-climate",
        "--namespace",
        "toy",
    )
    assert code == 0, output
    return output


def test_a_flow_used_by_many_processes_names_the_first_20_and_counts_the_rest(
    toy: Path, invoke: Invoke
) -> None:
    ids = add_emitters(toy, 30, lambda k: "toy:n2o")

    output = calc(toy, invoke)

    assert "not characterised flows: 1" in output
    [line] = [x for x in output.splitlines() if "(toy:n2o)" in x]
    assert line.count("toy:emitter-") == SHOWN
    assert all(i in line for i in sorted(ids)[:SHOWN])
    assert sorted(ids)[SHOWN] not in line
    assert line.endswith(" and 10 more")
    assert len(line) < 1_000


def test_a_flow_used_by_few_processes_names_them_all(toy: Path, invoke: Invoke) -> None:
    add_emitters(toy, 3, lambda k: "toy:n2o")

    output = calc(toy, invoke)

    [line] = [x for x in output.splitlines() if "(toy:n2o)" in x]
    assert line.endswith("used by toy:emitter-000, toy:emitter-001, toy:emitter-002")


def test_many_flows_list_the_first_20_and_count_the_rest(toy: Path, invoke: Invoke) -> None:
    add_emitters(toy, 25, lambda k: f"toy:gas-{k:02d}")

    output = calc(toy, invoke)

    assert "not characterised flows: 25" in output
    listed = [x for x in output.splitlines() if "(toy:gas-" in x]
    assert len(listed) == SHOWN
    assert [x for x in output.splitlines() if x.strip() == "and 5 more"]


def test_the_whole_provenance_stays_short_whatever_the_background(
    toy: Path, invoke: Invoke
) -> None:
    add_emitters(toy, 400, lambda k: f"toy:gas-{k % 3}")

    output = calc(toy, invoke)

    assert "not characterised flows: 3" in output
    assert len(output) < 6_000
