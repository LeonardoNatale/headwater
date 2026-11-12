"""What a person decides in the mapping file: a supplier for a line, a flow of nature, and what a
database of the workbook is. And what an import does with a foreground database it is told to
pin."""

from pathlib import Path

import pytest

from headwater.io.excel import ExcelImport, Status, import_excel
from headwater.model import InvalidDataError, Role
from headwater.release import Release
from headwater.study import read_database

from .conftest import Cell, Sheet, Workbooks, block, database, line

HEADER = "type,name,reference_product,location,categories,target,who,when,comment\n"


def run(
    workbooks: Workbooks,
    release: Release,
    out: Path,
    mapping: str,
    *activities: Sheet,
    tmp: Path,
) -> ExcelImport:
    path = tmp / "mapping.csv"
    path.write_text(HEADER + mapping, encoding="utf-8")
    rows = [*database(), *(row for activity in activities for row in activity)]
    return import_excel(workbooks.write({"a": rows}), release, out, mapping=path)


def steel(location: str = "FR", database: str | None = "mini-ecoinvent") -> list[Cell]:
    return line("market for steel", 2, "kilogram", location, database=database, product="steel")


def test_a_decision_links_a_line_that_did_not_match_by_name(
    workbooks: Workbooks, mini_release: Release, out: Path, tmp_path: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        "technosphere,market for steel,steel,FR,,mini:steel-rer,leo,"
        "2026-10-12,France is Europe here\n",
        block("widget", steel()),
        tmp=tmp_path,
    )
    found = next(o for o in done.report.outcomes if o.line.startswith("market for steel"))
    assert (found.status, found.code) == (Status.mapped, "mapping")
    assert done.report.mapped_by["mapping"] == 1
    line_ = read_database(out).processes[0].exchanges[1]
    assert (line_.supplier_id, line_.flow_id, line_.unit) == ("mini:steel-rer", "mini:steel", "kg")
    assert done.report.unused_mapping == ()
    assert done.clean


def test_a_decision_wins_over_an_exact_match(
    workbooks: Workbooks, mini_release: Release, out: Path, tmp_path: Path
) -> None:
    run(
        workbooks,
        mini_release,
        out,
        "technosphere,market for steel,steel,RER,,mini:steel-row,leo,"
        "2026-10-12,use the world mix\n",
        block("widget", steel("RER")),
        tmp=tmp_path,
    )
    assert read_database(out).processes[0].exchanges[1].supplier_id == "mini:steel-row"


def test_a_decision_does_not_make_a_line_right_that_is_wrong_in_itself(
    workbooks: Workbooks, mini_release: Release, out: Path, tmp_path: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        "technosphere,market for steel,steel,FR,,mini:heat-glo,leo,2026-10-12,a mistake\n",
        block("widget", steel()),
        tmp=tmp_path,
    )
    found = next(o for o in done.report.outcomes if o.line.startswith("market for steel"))
    assert found.code == "unit-mismatch"  # kilogram of a flow counted in megajoules


def test_a_decision_that_points_at_nothing_is_reported_on_the_line(
    workbooks: Workbooks, mini_release: Release, out: Path, tmp_path: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        "technosphere,market for steel,steel,FR,,mini:gone,leo,2026-10-12,\n",
        block("widget", steel()),
        tmp=tmp_path,
    )
    found = next(o for o in done.report.outcomes if o.line.startswith("market for steel"))
    assert found.code == "mapping-target-missing"
    assert "mapping.csv:2" in found.message and "mini:gone" in found.message


def test_a_decision_no_line_needed_is_listed(
    workbooks: Workbooks, mini_release: Release, out: Path, tmp_path: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        "technosphere,market for steel,steel,FR,,mini:steel-rer,leo,2026-10-12,\n"
        "technosphere,market for gold,gold,CH,,mini:steel-rer,leo,2026-10-12,\n"
        "database,Elsewhere,,,,release,leo,2026-10-12,\n",
        block("widget", steel()),
        tmp=tmp_path,
    )
    assert done.report.unused_mapping == (
        "mapping.csv:3: technosphere market for gold",
        "mapping.csv:4: database Elsewhere",
    )


def test_a_decision_links_a_flow_of_nature(
    workbooks: Workbooks, mini_release: Release, out: Path, tmp_path: Path
) -> None:
    water = line("Water", 1, "cubic meter", None, "biosphere", "b", categories="water::fresh")
    done = run(
        workbooks,
        mini_release,
        out,
        "biosphere,Water,,,water::fresh,mini:water-ground,leo,"
        "2026-10-12,fresh is unspecified here\n",
        block("widget", water),
        tmp=tmp_path,
    )
    found = next(o for o in done.report.outcomes if o.line.startswith("Water"))
    assert (found.status, found.code) == (Status.mapped, "biosphere-mapping")
    assert read_database(out).processes[0].exchanges[1].flow_id == "mini:water-ground"


def test_a_decision_for_a_flow_of_nature_that_points_at_nothing_is_reported(
    workbooks: Workbooks, mini_release: Release, out: Path, tmp_path: Path
) -> None:
    water = line("Water", 1, "cubic meter", None, "biosphere", "b", categories="water::fresh")
    done = run(
        workbooks,
        mini_release,
        out,
        'biosphere,Water,,,water::fresh,mini:steel-rer,leo,2026-10-12,"a process, not a flow"\n',
        block("widget", water),
        tmp=tmp_path,
    )
    found = next(o for o in done.report.outcomes if o.line.startswith("Water"))
    assert found.code == "mapping-target-missing"


# --- what a database is ----------------------------------------------------------------------


def test_a_database_with_another_name_can_be_declared_the_background(
    workbooks: Workbooks, mini_release: Release, out: Path, tmp_path: Path
) -> None:
    sand = line(
        "market for sand", 1, "kilogram", "GLO", database="Our own LCI copy", product="sand"
    )
    block_ = block("widget", sand)
    refused = run(workbooks, mini_release, out, "", block_, tmp=tmp_path)
    assert refused.report.codes(Status.unmapped) == {"database-unresolved": 1}
    done = run(
        workbooks,
        mini_release,
        tmp_path / "again",
        "database,Our own LCI copy,,,,release,leo,2026-10-12,\n",
        block_,
        tmp=tmp_path,
    )
    assert done.report.mapped_by["release"] == 1
    assert [(d.name, d.rows) for d in done.report.databases] == [("Our own LCI copy", 1)]
    assert "the background" in done.report.databases[0].treated_as


def test_a_database_can_be_declared_this_workbook_under_another_name(
    workbooks: Workbooks, mini_release: Release, out: Path, tmp_path: Path
) -> None:
    gear = line("gear", 1, "unit", "GLO", database="old name", product="gear")
    done = run(
        workbooks,
        mini_release,
        out,
        "database,old name,,,,own,leo,2026-10-12,\n",
        block("widget", gear),
        block("gear", unit="unit"),
        tmp=tmp_path,
    )
    assert done.report.mapped_by["workbook"] == 1


def test_a_foreign_foreground_database_is_never_linked_silently(
    workbooks: Workbooks, mini_release: Release, out: Path, tmp_path: Path
) -> None:
    dye = line("reactive dye", 0.1, "kilogram", "GLO", database="Textile Chemicals", product="dye")
    done = run(workbooks, mini_release, out, "", block("widget", dye), tmp=tmp_path)
    found = next(o for o in done.report.outcomes if o.line.startswith("reactive dye"))
    assert (found.status, found.code) == (Status.unmapped, "database-unresolved")
    assert "name its folder, next to this one, in the mapping file" in found.message
    assert done.report.uses == ()
    assert read_database(out).database.uses == ()
    assert read_database(out).processes[0].exchanges[1].supplier_id is None


def test_a_database_sent_to_a_folder_that_is_not_there_waits_for_it(
    workbooks: Workbooks, mini_release: Release, out: Path, tmp_path: Path
) -> None:
    dye = line("reactive dye", 0.1, "kilogram", "GLO", database="Textile Chemicals", product="dye")
    dye2 = line("disperse dye", 0.2, "kilogram", "GLO", database="Textile Chemicals", product="dye")
    done = run(
        workbooks,
        mini_release,
        out,
        "database,Textile Chemicals,,,,textile-chemicals,leo,2026-10-12,shared study\n",
        block("widget", dye, dye2),
        tmp=tmp_path,
    )
    waiting = [o for o in done.report.outcomes if o.status is Status.waiting]
    assert len(waiting) == 2 and {o.code for o in waiting} == {"waiting-for-database"}
    assert "no folder textile-chemicals" in waiting[0].message
    # the study reads as written: it uses the folder once the folder is there
    assert done.report.uses == ()
    study = read_database(out)
    assert study.database.uses == ()
    first, second = study.processes[0].exchanges[1:]
    assert first.role is Role.input and first.supplier_id is None  # not guessed
    assert first.amount == 0.1 and second.amount == 0.2  # the amounts are kept
    assert "UNMAPPED (waiting-for-database)" in (first.comment or "")
    assert done.validation is not None
    assert [p.code for p in done.validation.errors] == ["supplier-missing"] * 2
    assert not done.clean


def _chemicals(folder: Path, release: Release, write_study) -> None:
    """The study `textile-chemicals`, made by hand next to where the workbook is imported: two dyes
    that take 1 MJ of heat each."""
    write_study(
        folder,
        {
            "reactive-dye": "e01,reference,textile-chemicals:dye,1,kg,\n"
            "e02,input,mini:heat,1,MJ,mini:heat-glo",
            "disperse-dye": "e01,reference,textile-chemicals:disperse,1,kg,\n"
            "e02,input,mini:heat,1,MJ,mini:heat-glo",
        },
        flows=[
            "textile-chemicals:dye,dye,product,kg,,",
            "textile-chemicals:disperse,dye,product,kg,,",
        ],
        background=f"{release.name}@{release.hash[:12]}",
    )
    text = (folder / "database.yaml").read_text().replace("toy-climate", "mini-climate")
    (folder / "database.yaml").write_text(text)
    for stem, name in (("reactive-dye", "reactive dye"), ("disperse-dye", "disperse dye")):
        (folder / "processes" / f"{stem}.yaml").write_text(
            f"name: {name}\nlocation: GLO\nkind: ordinary_transforming_activity\n"
        )


def test_a_database_sent_to_a_study_folder_links_to_its_processes(
    workbooks: Workbooks, mini_release: Release, out: Path, tmp_path: Path, write_study
) -> None:
    _chemicals(out.parent / "textile-chemicals", mini_release, write_study)
    dye = line("reactive dye", 0.1, "kilogram", "GLO", database="Textile Chemicals", product="dye")
    dye2 = line("disperse dye", 0.2, "kilogram", "GLO", database="Textile Chemicals", product="dye")
    done = run(
        workbooks,
        mini_release,
        out,
        "database,Textile Chemicals,,,,textile-chemicals,leo,2026-10-12,shared study\n",
        block("widget", dye, dye2),
        tmp=tmp_path,
    )
    assert done.report.mapped_by["database"] == 2
    assert done.report.uses == ("textile-chemicals",)
    study = read_database(out)
    assert study.database.uses == ("textile-chemicals",)
    first, second = study.processes[0].exchanges[1:]
    assert (first.supplier_id, first.flow_id) == (
        "textile-chemicals:reactive-dye",
        "textile-chemicals:dye",
    )
    assert second.supplier_id == "textile-chemicals:disperse-dye"
    assert done.validation is not None and not done.validation.errors
    assert done.clean


def test_a_line_that_is_not_in_the_study_folder_is_not_guessed(
    workbooks: Workbooks, mini_release: Release, out: Path, tmp_path: Path, write_study
) -> None:
    _chemicals(out.parent / "textile-chemicals", mini_release, write_study)
    dye = line("reactive dye", 0.1, "kilogram", "RER", database="Textile Chemicals", product="dye")
    done = run(
        workbooks,
        mini_release,
        out,
        "database,Textile Chemicals,,,,textile-chemicals,leo,2026-10-12,\n",
        block("widget", dye),
        tmp=tmp_path,
    )
    [found] = [o for o in done.report.outcomes if o.line.startswith("reactive dye")]
    assert (found.status, found.code) == (Status.unmapped, "not-in-database")
    assert found.suggestions == ("textile-chemicals:reactive-dye (reactive dye | dye | GLO)",)
    assert read_database(out).processes[0].exchanges[1].supplier_id is None


def test_a_line_that_two_processes_of_the_study_fit_is_not_guessed(
    workbooks: Workbooks, mini_release: Release, out: Path, tmp_path: Path, write_study
) -> None:
    _chemicals(out.parent / "textile-chemicals", mini_release, write_study)
    (out.parent / "textile-chemicals/processes/disperse-dye.yaml").write_text(
        "name: reactive dye\nlocation: GLO\nkind: ordinary_transforming_activity\n"
    )
    dye = line("reactive dye", 0.1, "kilogram", "GLO", database="Textile Chemicals", product="dye")
    done = run(
        workbooks,
        mini_release,
        out,
        "database,Textile Chemicals,,,,textile-chemicals,leo,2026-10-12,\n",
        block("widget", dye),
        tmp=tmp_path,
    )
    [found] = [o for o in done.report.outcomes if o.line.startswith("reactive dye")]
    assert (found.status, found.code) == (Status.unmapped, "ambiguous-in-database")


@pytest.mark.parametrize(
    ("old", "new", "code"),
    [
        ("name: textile-chemicals", "name: chemicals", "used-database-name"),
        ("background: mini-ecoinvent@", "background: mini-ecoinvent@0", "uses-background"),
    ],
)
def test_a_study_folder_the_written_study_could_not_use_is_refused_before_writing(
    workbooks: Workbooks,
    mini_release: Release,
    out: Path,
    tmp_path: Path,
    write_study,
    old: str,
    new: str,
    code: str,
) -> None:
    _chemicals(out.parent / "textile-chemicals", mini_release, write_study)
    study_yaml = out.parent / "textile-chemicals" / "database.yaml"
    text = study_yaml.read_text()
    assert old in text
    study_yaml.write_text(text.replace(old, new, 1))
    dye = line("reactive dye", 0.1, "kilogram", "GLO", database="Textile Chemicals", product="dye")
    with pytest.raises(InvalidDataError) as error:
        run(
            workbooks,
            mini_release,
            out,
            "database,Textile Chemicals,,,,textile-chemicals,leo,2026-10-12,\n",
            block("widget", dye),
            tmp=tmp_path,
        )
    assert [p.code for p in error.value.problems] == [code]
    assert not out.exists()


def test_a_pinned_version_in_the_mapping_file_asks_for_the_folder_name(
    workbooks: Workbooks, mini_release: Release, out: Path, tmp_path: Path
) -> None:
    with pytest.raises(InvalidDataError, match="write the folder's name, textile-chemicals"):
        run(
            workbooks,
            mini_release,
            out,
            "database,Textile Chemicals,,,,textile-chemicals@v3,leo,2026-10-12,\n",
            block("widget", steel()),
            tmp=tmp_path,
        )


def test_a_database_that_names_another_version_than_the_release_is_noted(
    workbooks: Workbooks, mini_release: Release, out: Path, tmp_path: Path
) -> None:
    sand = line(
        "market for sand", 1, "kilogram", "GLO", database="ecoinvent-3.12-cutoff", product="sand"
    )
    done = run(workbooks, mini_release, out, "", block("widget", sand), tmp=tmp_path)
    assert done.report.mapped_by["release"] == 1
    (note,) = [n for n in done.report.notes if "version" in n.message]
    assert "3.12" in note.message and "3.10" in note.message and "renames" in note.message


@pytest.mark.parametrize(
    "written", ["Mini-Ecoinvent 3.10", "mini-ecoinvent", "MINI-ECOINVENT-CUTOFF"]
)
def test_the_background_is_recognised_by_the_start_of_its_name_in_any_case(
    written: str, workbooks: Workbooks, mini_release: Release, out: Path, tmp_path: Path
) -> None:
    sand = line("market for sand", 1, "kilogram", "GLO", database=written, product="sand")
    done = run(workbooks, mini_release, out, "", block("widget", sand), tmp=tmp_path)
    assert done.report.mapped_by["release"] == 1


def test_a_database_whose_name_only_begins_with_the_same_letters_is_not_the_background(
    workbooks: Workbooks, toy_release: Release, out: Path, tmp_path: Path
) -> None:
    rows = [
        *database(),
        *block(
            "widget",
            line(
                "cotton fabric production",
                1,
                "kilogram",
                "GLO",
                database="Toyota parts",
                product="cotton fabric",
            ),
        ),
    ]
    done = import_excel(workbooks.write({"a": rows}), toy_release, out)
    (found,) = [o for o in done.report.outcomes if o.code != "reference"]
    assert found.code == "database-unresolved"


def test_the_report_groups_waiting_lines_under_the_database_they_wait_for(
    workbooks: Workbooks, mini_release: Release, out: Path, tmp_path: Path
) -> None:
    from headwater.io.excel.report import format_report

    one = line("reactive dye", 0.1, "kilogram", "GLO", database="Textile Chemicals", product="dye")
    two = line("sand mix", 0.2, "kilogram", "GLO", database="Mineral Library", product="sand")
    done = run(
        workbooks,
        mini_release,
        out,
        "database,Textile Chemicals,,,,textile-chemicals,leo,2026-10-12,\n"
        "database,Mineral Library,,,,minerals,leo,2026-10-12,\n",
        block("widget", one, two),
        tmp=tmp_path,
    )
    text = format_report(done.report)
    assert "Waiting for database textile-chemicals: 1 line, 1 to decide" in text
    assert "Waiting for database minerals: 1 line, 1 to decide" in text
    assert {o.database for o in done.report.outcomes if o.status is Status.waiting} == {
        "textile-chemicals",
        "minerals",
    }


def test_a_line_that_did_not_map_keeps_the_workbooks_words_so_the_mapping_row_can_be_written(
    workbooks: Workbooks, mini_release: Release, out: Path, tmp_path: Path
) -> None:
    dye = line("reactive dye", 0.1, "kilogram", "GLO", database="Textile Chemicals", product="dye")
    done = run(workbooks, mini_release, out, "", block("widget", dye), tmp=tmp_path)
    exchange = read_database(out).processes[0].exchanges[1]
    assert exchange.flow_name == "dye"
    assert exchange.unit == "kg" and exchange.amount == 0.1
    assert "reactive dye | dye | GLO, 0.1 kilogram" in (exchange.comment or "")
    assert done.validation is not None and not done.clean
