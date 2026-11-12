"""The comment: JSON in, Markdown out. No GitHub here.

The three exit tests (a fabric change, an override, a validation error) feed the renderer what
the real command line printed, on the demo studies in a temporary git repository.
"""

import copy
import shutil
from collections.abc import Callable, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

Git = Callable[..., str]
Commit = Callable[[str], str]
Report = dict[str, Any]

FABRIC = "studies/tshirt/tshirt/processes/tshirt.csv"
RUN = "https://example.test/run/1"
BASE, HEAD = "a" * 40, "b" * 40


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"{old!r} not found in {path}"
    path.write_text(text.replace(old, new, 1))


def fixed(report: Report) -> Report:
    """The report with commit hashes that do not depend on when the test ran."""
    report["base"]["commit"], report["head"]["commit"] = BASE, HEAD
    report["base"]["label"] = "main"
    return report


def study_of(report: Report) -> Report:
    [study] = report["folders"]
    return study


# --- the three exit tests ----------------------------------------------------------------------


def test_the_fabric_change_names_both_scores_and_line_e02(
    collect: ModuleType,
    render: ModuleType,
    headwater: list[str],
    repo: Path,
    git: Git,
    commit: Commit,
) -> None:
    git("switch", "--quiet", "--create", "lighter")
    edit(repo / FABRIC, "toy:cotton-fabric,0.25", "toy:cotton-fabric,0.22")
    commit("lighter T-shirt")
    report = fixed(collect.collect_report(repo, headwater, "main", "HEAD", base_label="main"))
    version = study_of(report)["diff"]["engine_version"]

    body = render.render(report, run_url=RUN)

    assert body == (
        "<!-- headwater-study-diff -->\n"
        "## Results diff for changed databases and studies\n"
        "\n"
        "Base `main` (`aaaaaaaaaaaa`), head `HEAD` (`bbbbbbbbbbbb`). Each database and study is "
        "calculated on the release it pins.\n"
        "\n"
        "### Database `studies/tshirt/tshirt`\n"
        "\n"
        "Background `toy-background@6864b7dbb65d`, method `toy-climate 1`.\n"
        "\n"
        "| | Lines that differ |\n"
        "|---|---|\n"
        "| e02 | `tshirt:tshirt line e02` (cotton fabric): amount 0.25 → 0.22 kg |\n"
        "\n"
        "A line's column is the change that line alone makes.\n"
        "\n"
        "| Product | Indicator | Old | New | Change | % | e02 |\n"
        "|---|---|---:|---:|---:|---:|---:|\n"
        "| T-shirt | Climate change (kg CO2-eq) | 1.19205 | 1.08079 | -0.111258 | -9.3% "
        "| -0.111258 |\n"
        "\n"
        f"<sub>headwater {version} · [workflow run]({RUN})</sub>\n"
    )


def test_an_override_is_named_as_an_override_of_the_background_process(
    collect: ModuleType,
    render: ModuleType,
    headwater: list[str],
    repo: Path,
    git: Git,
    commit: Commit,
) -> None:
    git("switch", "--quiet", "--create", "wind")
    shutil.copytree(
        repo / "studies/tshirt-local-electricity/tshirt/overrides",
        repo / "studies/tshirt/tshirt/overrides",
    )
    commit("own wind power")
    report = fixed(collect.collect_report(repo, headwater, "main", "HEAD", base_label="main"))

    body = render.render(report)

    assert (
        "| T-shirt | Climate change (kg CO2-eq) | 1.19205 | 1.09205 | -0.1 | -8.4% | -0.1 |"
    ) in body
    assert (
        "| e02 | `override of toy:electricity-production line e02` (Carbon dioxide, fossil): "
        "added: emission 0.3 kg of toy:co2 |"
    ) in body


def test_a_study_that_does_not_validate_lists_every_error_and_says_it_was_not_compared(
    collect: ModuleType,
    render: ModuleType,
    headwater: list[str],
    repo: Path,
    git: Git,
    commit: Commit,
) -> None:
    git("switch", "--quiet", "--create", "broken")
    edit(repo / FABRIC, "toy:fabric-production", "toy:fabric-productoin")
    edit(repo / FABRIC, "0.5,kWh", "0.5,MJ2")
    commit("two mistakes")
    report = fixed(collect.collect_report(repo, headwater, "main", "HEAD", base_label="main"))

    body = render.render(report)

    assert report["failed"] is True
    assert body == (
        "<!-- headwater-study-diff -->\n"
        "## Results diff for changed databases and studies\n"
        "\n"
        "Base `main` (`aaaaaaaaaaaa`), head `HEAD` (`bbbbbbbbbbbb`). Each database and study is "
        "calculated on the release it pins.\n"
        "\n"
        "**Failed:** `studies/tshirt/tshirt`.\n"
        "\n"
        "### Database `studies/tshirt/tshirt`\n"
        "\n"
        "**Validation failed** with 2 errors. The results were not compared.\n"
        "\n"
        "```text\n"
        "tshirt:tshirt line e02: supplier 'toy:fabric-productoin' does not exist "
        "(flow 'toy:cotton-fabric'). Did you mean 'toy:fabric-production'?\n"
        "tshirt:tshirt line e03: unknown unit 'MJ2' (flow toy:electricity). Did you mean 'MJ'?\n"
        "```\n"
    )


# --- suppliers in words, comments apart --------------------------------------------------------


def test_a_supplier_swap_names_both_suppliers_and_the_json_keeps_the_ids(
    collect: ModuleType,
    render: ModuleType,
    headwater: list[str],
    repo: Path,
    git: Git,
    commit: Commit,
) -> None:
    git("switch", "--quiet", "--create", "solar")
    processes = repo / "studies/tshirt/tshirt/processes"
    (processes / "solar.yaml").write_text(
        "name: solar electricity\nlocation: GLO\nkind: ordinary_transforming_activity\n"
    )
    (processes / "solar.csv").write_text(
        "line,role,flow,amount,unit,supplier\n"
        "e01,reference,toy:electricity,1,kWh,\n"
        "e02,emission,toy:co2,0.05,kg,\n"
    )
    edit(repo / FABRIC, "0.5,kWh,toy:electricity-production", "0.5,kWh,tshirt:solar")
    commit("solar panels")
    report = collect.collect_report(repo, headwater, "main", "HEAD")

    body = render.render(report)

    assert (
        "| e03 | `tshirt:tshirt line e03` (electricity): "
        "supplier electricity production (GLO) → solar electricity (GLO) |"
    ) in body
    assert "toy:electricity-production" not in body
    line = next(d for d in study_of(report)["diff"]["differences"] if d["kind"] == "line")
    assert line["fields"] == [
        {"name": "supplier", "old": "toy:electricity-production", "new": "tshirt:solar"}
    ]


def test_a_comment_edit_is_listed_as_a_comment_change_and_no_line_differs(
    collect: ModuleType,
    render: ModuleType,
    headwater: list[str],
    repo: Path,
    git: Git,
    commit: Commit,
) -> None:
    rows = (repo / FABRIC).read_text().splitlines()
    (repo / FABRIC).write_text("\n".join([rows[0] + ",comment", *(r + ",-" for r in rows[1:])]))
    commit("comments")
    git("switch", "--quiet", "--create", "comment")
    edit(repo / FABRIC, "toy:fabric-production,-", "toy:fabric-production,Weighed at the factory")
    commit("a better comment")
    report = collect.collect_report(repo, headwater, "main", "HEAD")

    body = render.render(report)

    assert "Lines that differ" not in body
    assert "No score changed." in body
    assert (
        "Comments changed:\n\n"
        '- `tshirt:tshirt line e02` (cotton fabric): comment "-" → "Weighed at the factory"\n'
    ) in body
    assert "Other changes" not in body


# --- other real cases --------------------------------------------------------------------------


def test_a_change_that_moves_no_score_says_so_and_lists_what_differs(
    collect: ModuleType,
    render: ModuleType,
    headwater: list[str],
    repo: Path,
    git: Git,
    commit: Commit,
) -> None:
    git("switch", "--quiet", "--create", "description")
    with (repo / "studies/tshirt/tshirt/database.yaml").open("a") as handle:
        handle.write("description: One T-shirt, external use\n")
    commit("a description")

    body = render.render(collect.collect_report(repo, headwater, "main", "HEAD"))

    assert "No score changed." in body
    assert "| T-shirt | Climate change (kg CO2-eq) | 1.19205 | 1.19205 | no change |  |" in body
    assert "Lines that differ" not in body
    assert "Other changes, not part of the score:" in body
    assert "- `database description`: none → One T-shirt, external use" in body


def test_a_new_database_is_one_line_and_a_removed_one_is_listed(
    collect: ModuleType,
    render: ModuleType,
    headwater: list[str],
    repo: Path,
    git: Git,
    commit: Commit,
) -> None:
    git("switch", "--quiet", "--create", "rename")
    git("mv", "studies/tshirt", "studies/classic-tshirt")
    commit("rename")

    body = render.render(collect.collect_report(repo, headwater, "main", "HEAD"))

    assert "### Database `studies/classic-tshirt/tshirt` (new)" in body
    assert (
        "New database: 1 product, not scored. `headwater calc studies/classic-tshirt/tshirt` "
        "scores them."
    ) in body
    assert "| T-shirt |" not in body
    assert "No lines differ" not in body
    assert "Lines that differ" not in body
    assert "### Database `studies/tshirt/tshirt` (removed)" in body
    assert "Deleted in this pull request." in body
    assert "Failed" not in body


# --- shapes that are cheaper to write by hand --------------------------------------------------


def difference(
    kind: str,
    item: str,
    description: str,
    *,
    scored: bool = True,
    id: str | None = None,
    flow: tuple[str, str | None] | None = None,
) -> dict[str, Any]:
    return {
        "id": id or f"{kind} {item}",
        "kind": kind,
        "change": "changed",
        "item": item,
        "description": description,
        "fields": [],
        "affects": [],
        "scored": scored,
        "flow": None if flow is None else {"id": flow[0], "name": flow[1]},
    }


def result(
    old: float | None,
    new: float | None,
    drivers: Sequence[tuple[str, float | None]] = (),
    *,
    product: str = "T-shirt",
    indicator: str = "Climate change",
    interaction: float | None = 0.0,
    changed: bool | None = None,
) -> dict[str, Any]:
    change = None if old is None or new is None else new - old
    percent = None
    if old is not None and new is not None and old != 0:
        percent = 100 * (new - old) / abs(old)
    return {
        "product": {"id": f"toy:{product}", "name": product},
        "indicator": {"id": indicator.lower(), "name": indicator, "unit": "kg CO2-eq"},
        "old": old,
        "new": new,
        "change": change,
        "percent": percent,
        "changed": bool(change) if changed is None else changed,
        "drivers": [{"difference": d, "contribution": c} for d, c in drivers],
        "interaction": interaction,
    }


def hand_built(
    results: list[dict[str, Any]], differences: list[dict[str, Any]], **study: Any
) -> Report:
    diff = {
        "format": "headwater-diff/2",
        "study": "studies/tshirt/tshirt",
        "tolerance": 1e-9,
        "engine_version": "9.9",
        "background": {"release": "toy-background@" + "6" * 64, "folder": None},
        "method": {"id": "toy-climate", "version": "1", "hash": "c" * 64},
        "results": results,
        "differences": differences,
        "new_study": False,
    }
    one = {
        "path": "studies/tshirt/tshirt",
        "status": "changed",
        "ok": True,
        "validation": {
            "exit_code": 0,
            "background": None,
            "errors": [],
            "warnings": [],
        },
        "diff": diff,
        "diff_error": None,
        **study,
    }
    return {
        "format": "headwater-ci/2",
        "base": {"label": "main", "commit": BASE},
        "head": {"label": "HEAD", "commit": HEAD},
        "failed": not one["ok"],
        "folders": [one],
    }


def test_no_study_changed(render: ModuleType) -> None:
    report = {"format": "headwater-ci/2", "base": {}, "head": {}, "failed": False, "folders": []}

    body = render.render(report)

    assert body.startswith(render.MARKER + "\n")
    assert body.endswith("No database or study changed in this pull request.\n")


def test_effects_that_do_not_add_up_show_the_interaction_and_n_a(render: ModuleType) -> None:
    a = difference("line", "tshirt:tshirt line e02", "amount 0.25 → 0.5 kg")
    b = difference("line", "toy:fabric-production line e02", "amount 3 → 6 kWh")
    c = difference("line", "tshirt:tshirt line e09", "added: input 1 kg of toy:new")
    diffs = [a, b, c]
    drivers = [(a["id"], 0.89735), (b["id"], 0.39735), (c["id"], None)]
    report = hand_built([result(1.19205, 2.8841, drivers, interaction=0.4)], diffs)

    body = render.render(report)

    # two lines are e02: the columns are numbered
    assert "| 1 | `tshirt:tshirt line e02`: amount 0.25 → 0.5 kg |" in body
    assert "| 2 | `toy:fabric-production line e02`: amount 3 → 6 kWh |" in body
    assert "| 3 | `tshirt:tshirt line e09`: added: input 1 kg of toy:new |" in body
    assert "| Product | Indicator | Old | New | Change | % | 1 | 2 | 3 | interaction |" in body
    assert "| +1.69205 | +141.9% | +0.89735 | +0.39735 | n/a | +0.4 |" in body


def test_a_parameter_and_a_cut_off_are_named_by_their_kind(render: ModuleType) -> None:
    p = difference("parameter", "fabric_kg", "value 0.25 → 0.3")
    c = difference("cut_off", "tape", "recorded: share 0.0385", scored=False)
    drivers = [(p["id"], 0.18543)]
    report = hand_built([result(1.19205, 1.37748, drivers)], [p, c])

    body = render.render(report)

    assert "| fabric_kg | `parameter fabric_kg`: value 0.25 → 0.3 |" in body
    assert "| +0.18543 | +15.6% | +0.18543 |" in body
    assert "- `cut-off tape`: recorded: share 0.0385" in body


def test_only_the_largest_lines_are_listed_and_five_get_a_column(render: ModuleType) -> None:
    diffs = [difference("line", f"p line e{i:02}", f"amount {i} → {i + 1}") for i in range(15)]
    drivers = [(d["id"], 1.0 - i / 100) for i, d in enumerate(diffs)]
    report = hand_built([result(10.0, 24.0, drivers, interaction=0.0)], diffs)

    body = render.render(report)

    assert body.count("`p line e") == 10
    assert "and 5 more lines differ." in body
    assert "| % | e00 | e01 | e02 | e03 | e04 | 10 others |" in body
    # the ten others together: 0.95 + 0.94 + ... + 0.86
    assert "| +1 | +0.99 | +0.98 | +0.97 | +0.96 | +9.05 |" in body


def test_many_indicators_keep_the_methods_order_with_the_rest_folded(render: ModuleType) -> None:
    diff = difference("line", "p line e01", "amount 1 → 2")
    rows = [
        result(3.0, 1.5, [(diff["id"], -1.5)], indicator="Land use"),
        result(5.0, 5.0, indicator="Acidification"),
        result(1.0, 2.0, [(diff["id"], 1.0)], indicator="Climate change"),
    ]

    body = render.render(hand_built(rows, [diff]))

    shown, _, folded = body.partition("<summary>1 score did not change</summary>")
    assert shown.index("| Land use (kg CO2-eq) | 3 | 1.5 | -1.5 | -50.0% | -1.5 |") < shown.index(
        "| Climate change (kg CO2-eq) | 1 | 2 | +1 | +100.0% | +1 |"
    )
    assert "Acidification" not in shown
    assert "Acidification (kg CO2-eq) | 5 | 5 | no change |  |" in folded
    assert "<details open>" not in body
    assert body.count("`p line e01`") == 1


def test_each_line_is_listed_once_whatever_the_number_of_indicators(render: ModuleType) -> None:
    a = difference("line", "p line e02", "supplier market (GLO) → market (VN)")
    b = difference("line", "p line e07", "amount 9.7 → 12 tkm")
    rows = [
        result(10.0, 10.0 + n, [(a["id"], 0.9 * n), (b["id"], 0.1 * n)], indicator=f"I{n:02}")
        for n in range(1, 26)
    ]

    body = render.render(hand_built(rows, [a, b]))

    assert body.count("`p line e02`") == 1 and body.count("`p line e07`") == 1
    assert "| | Lines that differ |\n|---|---|\n| e02 |" in body
    shown, _, folded = body.partition("<summary>20 more scores</summary>")
    open_rows = [line for line in shown.splitlines() if line.startswith("| T-shirt")]
    assert [line.split(" | ")[1] for line in open_rows] == [
        f"I{n:02} (kg CO2-eq)" for n in range(1, 6)
    ]  # the method's order
    assert folded.count("| T-shirt |") == 20
    assert "| % | e02 | e07 |" in folded  # the same columns
    assert len(body.splitlines()) < 60


def test_the_lines_with_the_largest_share_of_a_change_get_the_columns(
    render: ModuleType,
) -> None:
    diffs = [difference("line", f"p line e{i:02}", f"amount {i} → {i + 1}") for i in range(1, 8)]
    # e07 is small on the first score and the whole change of the second
    first = [(d["id"], float(i)) for i, d in enumerate(diffs[:6], 1)] + [(diffs[6]["id"], 0.001)]
    second = [(d["id"], 0.0) for d in diffs[:6]] + [(diffs[6]["id"], 1.0)]
    rows = [
        result(100.0, 121.001, first, indicator="Climate change"),
        result(1.0, 2.0, second, indicator="Ozone"),
    ]

    body = render.render(hand_built(rows, diffs))

    assert "| % | e03 | e04 | e05 | e06 | e07 | 2 others |" in body
    assert "| +21.001 | +21.0% | +3 | +4 | +5 | +6 | +0.001 | +3 |" in body
    # every line is listed, in the order of the files
    listed = [line.split(" | ")[0] for line in body.splitlines() if line.startswith("| e0")]
    assert listed == [f"| e0{i}" for i in range(1, 8)]


def test_each_product_has_its_block_and_the_first_is_open(render: ModuleType) -> None:
    diff = difference("line", "p line e01", "amount 1 → 2")
    rows = [
        result(1.0, 2.0, [(diff["id"], 1.0)], product="T-shirt"),
        result(3.0, 4.0, [(diff["id"], 1.0)], product="Fabric"),
        result(1.0, 3.0, [(diff["id"], 2.0)], product="T-shirt", indicator="Ozone"),
        result(3.0, 5.0, [(diff["id"], 2.0)], product="Fabric", indicator="Ozone"),
    ]

    body = render.render(hand_built(rows, [diff]))

    shirt, _, fabric = body.partition("<summary>Fabric: 2 scores</summary>")
    assert "<details open>\n<summary>T-shirt: 2 scores</summary>" in shirt
    assert shirt.count("| T-shirt |") == 2 and "| Fabric |" not in shirt
    assert fabric.count("| Fabric |") == 2
    assert body.count("`p line e01`") == 1


def test_a_score_on_one_side_only_is_shown_as_new_or_removed(render: ModuleType) -> None:
    diff = difference("line", "p line e01", "amount 1 → 2")
    rows = [
        result(1.0, 2.0, [(diff["id"], 1.0)]),
        result(None, 4.0, product="Hoodie"),
        result(6.0, None, product="Cap"),
    ]

    body = render.render(hand_built(rows, [diff]))

    assert "| Hoodie | Climate change (kg CO2-eq) | - | 4 | only in head |  |" in body
    assert "| Cap | Climate change (kg CO2-eq) | 6 | - | only in base |  |" in body


def test_a_percent_of_a_score_that_was_zero_is_left_empty(render: ModuleType) -> None:
    diff = difference("line", "p line e01", "amount 0 → 2")

    body = render.render(hand_built([result(0.0, 2.0, [(diff["id"], 2.0)])], [diff]))

    assert "| T-shirt | Climate change (kg CO2-eq) | 0 | 2 | +2 |  |" in body


def test_a_diff_that_cannot_be_made_shows_the_message_and_fails(render: ModuleType) -> None:
    report = hand_built(
        [],
        [],
        ok=False,
        diff=None,
        diff_error="The study studies/tshirt/tshirt cannot be read at aaaa:\n1 problem:\n- bad",
    )

    body = render.render(report)

    assert "**Failed:** `studies/tshirt/tshirt`." in body
    assert "**The database could not be compared.**" in body
    assert (
        "```text\nThe study studies/tshirt/tshirt cannot be read at aaaa:\n1 problem:\n- bad\n```"
        in body
    )


def test_a_study_whose_background_could_not_be_loaded_shows_what_the_command_said(
    render: ModuleType,
) -> None:
    message = "toy-background@6864b7dbb65d is not in /home/runner/.headwater/releases."
    report = hand_built([], [], ok=False, diff=None, validation=None, diff_error=message)

    body = render.render(report)

    assert "**Failed:** `studies/tshirt/tshirt`." in body
    assert "**The database could not be compared.**" in body
    assert f"```text\n{message}\n```" in body
    assert "Validation failed" not in body


def test_warnings_are_listed_for_a_study_that_passes(render: ModuleType) -> None:
    diff = difference("line", "p line e01", "amount 1 → 2")
    report = hand_built([result(1.0, 2.0, [(diff["id"], 1.0)])], [diff])
    study_of(report)["validation"]["warnings"] = ["line e03 is not checked"]

    body = render.render(report)

    assert "Warnings (1):\n\n- line e03 is not checked\n" in body
    assert "Failed" not in body


def test_text_from_a_study_cannot_break_the_table_or_the_page(render: ModuleType) -> None:
    diff = difference("line", "p line e01", "name a|b <script> `x`")
    hostile = result(1.0, 2.0, [(diff["id"], 1.0)], product="Shirt | <b>")
    fence = ["error with ``` inside"]
    report = hand_built([hostile], [diff])
    bad = copy.deepcopy(report)
    study_of(bad).update(ok=False, diff=None)
    study_of(bad)["validation"].update(exit_code=1, errors=fence)

    body = render.render(report)
    failed = render.render(bad)

    assert "| Shirt \\| &lt;b&gt; |" in body
    assert "name a\\|b &lt;script&gt; `x`" in body
    assert "````text\nerror with ``` inside\n````" in failed


def test_a_long_comment_is_cut_to_the_limit_and_stays_well_formed(render: ModuleType) -> None:
    diffs = [difference("line", f"p line e{i}", "amount 1 → 2") for i in range(400)]
    rows = [
        result(1.0, 3.0, [(d["id"], 0.005) for d in diffs], indicator=f"Indicator {n}")
        for n in range(25)
    ]
    report = hand_built(rows, diffs)
    report["folders"] *= 3

    full = render.render(report, limit=10_000_000)
    smaller = render.render(report, limit=len(full) - 1)
    body = render.render(report, limit=3_000)

    assert len(smaller) < len(full)
    assert smaller.count("`p line e") == 3 * 3  # 3 lines listed for each study
    assert len(full) > 3_000
    assert len(body) <= 3_000
    assert body.startswith(render.MARKER)
    assert body.count("<details") == body.count("</details>")
    assert body.count("```") % 2 == 0
    assert "the run's summary" in body


def test_a_new_study_that_cannot_be_calculated_shows_why_and_fails(render: ModuleType) -> None:
    report = hand_built(
        [],
        [],
        status="new",
        ok=False,
        diff=None,
        diff_error="1 problem:\n- the study buys more than it makes",
    )

    body = render.render(report)

    assert "**Failed:** `studies/tshirt/tshirt`." in body
    assert "**The database could not be calculated.**" in body
    assert "```text\n1 problem:\n- the study buys more than it makes\n```" in body


def test_a_new_study_shows_its_scores_and_no_lines(render: ModuleType) -> None:
    diff = hand_built([result(None, 1.19205)], [], status="new")
    study_of(diff)["kind"] = "study"
    study_of(diff)["diff"]["created"] = True

    body = render.render(diff)

    assert "(new)" in body
    assert "| T-shirt | Climate change (kg CO2-eq) | - | 1.19205 | only in head |  |" in body
    assert "No lines differ" not in body


# --- the name of the flow of a line ------------------------------------------------------------


def one_line(render: ModuleType, d: dict[str, Any]) -> str:
    report = hand_built([result(1.0, 2.0, [(d["id"], 1.0)])], [d])
    [line] = [x for x in render.render(report).splitlines() if "`" in x and x.startswith("| ")]
    return line.split(" | ", 1)[1]


def test_a_line_names_its_flow_after_the_id_outside_the_code(render: ModuleType) -> None:
    d = difference(
        "line",
        "tshirt:tshirt line e02",
        "amount 0.25 → 0.22 kg",
        flow=("toy:cotton-fabric", "cotton fabric"),
    )

    assert one_line(render, d) == (
        "`tshirt:tshirt line e02` (cotton fabric): amount 0.25 → 0.22 kg |"
    )


def test_a_name_that_is_the_item_is_not_repeated(render: ModuleType) -> None:
    d = difference("line", "tshirt:tshirt", "renamed", flow=("tshirt:tshirt", "tshirt:tshirt"))

    assert one_line(render, d) == "`tshirt:tshirt`: renamed |"


def test_a_name_is_text_so_pipes_and_tags_cannot_break_the_table(render: ModuleType) -> None:
    d = difference("line", "p line e01", "amount 1 → 2 kg", flow=("toy:x", "NOx | <b>NO2</b>"))

    assert one_line(render, d) == (
        "`p line e01` (NOx \\| &lt;b&gt;NO2&lt;/b&gt;): amount 1 → 2 kg |"
    )


def test_a_flow_with_no_name_or_a_report_with_no_flow_key_is_listed_by_its_item(
    render: ModuleType,
) -> None:
    unnamed = difference("line", "p line e01", "amount 1 → 2 kg", flow=("toy:x", None))
    older = difference("line", "p line e02", "amount 1 → 2 kg")
    del older["flow"]

    assert one_line(render, unnamed) == "`p line e01`: amount 1 → 2 kg |"
    assert one_line(render, older) == "`p line e02`: amount 1 → 2 kg |"


def test_only_lines_and_overrides_name_their_flow(render: ModuleType) -> None:
    d = difference("cut_off", "tape", "recorded", scored=False, flow=("toy:tape", "tape"))
    report = hand_built([result(1.0, 1.0, [])], [d])

    assert "- `cut-off tape`: recorded" in render.render(report)


def test_a_no_factor_row_is_listed_with_the_name_of_its_flow(render: ModuleType) -> None:
    d = difference(
        "no_factor",
        "tshirt:cod",
        "for method toy-climate: reason a → b",
        scored=False,
        flow=("tshirt:cod", "COD, Chemical Oxygen Demand"),
    )
    report = hand_built([result(1.0, 1.0, [])], [d])

    assert (
        "- `no_factor.csv row tshirt:cod` (COD, Chemical Oxygen Demand): "
        "for method toy-climate: reason a → b"
    ) in render.render(report)


def test_a_real_diff_of_two_products_has_one_block_each(render: ModuleType, tmp_path: Path) -> None:
    import headwater as hw
    from headwater.diff import Source, diff_databases, render_json
    from headwater.study import read_database

    from .conftest import TOY

    root = tmp_path / "tshirt"
    shutil.copytree(TOY / "tshirt" / "tshirt", root)
    (root / "processes" / "fabric.yaml").write_text(
        "name: own fabric\nlocation: GLO\nkind: ordinary_transforming_activity\n"
    )
    (root / "processes" / "fabric.csv").write_text(
        "line,role,flow,amount,unit,supplier\n"
        "e01,reference,tshirt:fabric,1,kg,\n"
        "e02,input,toy:electricity,3,kWh,toy:electricity-production\n"
    )
    with (root / "flows.csv").open("a") as handle:
        handle.write("tshirt:fabric,own fabric,product,kg,,\n")
    with (root / "processes" / "tshirt.csv").open("a") as handle:
        handle.write("e04,input,tshirt:fabric,0.1,kg,tshirt:fabric\n")
    old = read_database(root)
    edit(root / "processes" / "fabric.csv", "toy:electricity,3,", "toy:electricity,4,")
    background = hw.load(
        TOY / "background", method=TOY / "methods" / "toy-climate", namespace="toy"
    )
    diff = diff_databases(old, read_database(root), background, products="all")
    report = hand_built([], [])
    study_of(report)["diff"] = render_json(
        diff, path="studies/tshirt/tshirt", old=Source("main", BASE), new=Source(None, None)
    )

    body = render.render(report)

    assert "<details open>\n<summary>own fabric: 1 score</summary>" in body
    assert "<details>\n<summary>T-shirt: 1 score</summary>" in body
    # 1 kWh more per kg of own fabric at 0.5298, and 0.1 kg of it per T-shirt
    assert "| own fabric | Climate change (kg CO2-eq) | 1.5894 | 2.1192 | +0.5298 |" in body
    assert "| T-shirt | Climate change (kg CO2-eq) | 1.35099 | 1.40397 | +0.05298 |" in body
    assert body.count("`tshirt:fabric line e02`") == 1
