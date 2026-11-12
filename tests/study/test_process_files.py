"""A process is a pair of files: `processes/<stem>.csv` (exchanges) and `processes/<stem>.yaml`
(name, location, time and kind). Each mistake in the pair names the file."""

from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from headwater.model import InvalidDataError, Problem, ProcessKind
from headwater.study import read_database

Edit = Callable[[Path, str, str], None]

STUDY = "tshirt/tshirt"
YAML = "tshirt/tshirt/processes/tshirt.yaml"
CSV = "tshirt/tshirt/processes/tshirt.csv"


def problems_of(path: Path) -> tuple[Problem, ...]:
    with pytest.raises(InvalidDataError) as raised:
        read_database(path)
    return raised.value.problems


def test_csv_without_its_yaml(toy: Path) -> None:
    (toy / YAML).unlink()

    (problem,) = problems_of(toy / STUDY)

    assert problem.code == "missing-file"
    assert problem.item == "processes/tshirt.csv"
    assert "processes/tshirt.yaml" in problem.message


def test_yaml_without_its_csv(toy: Path) -> None:
    (toy / CSV).unlink()

    (problem,) = problems_of(toy / STUDY)

    assert problem.code == "missing-file"
    assert problem.item == "processes/tshirt.yaml"
    assert "processes/tshirt.csv" in problem.message


def test_each_unmatched_file_is_reported(toy: Path) -> None:
    (toy / "tshirt/tshirt/processes/dyeing.csv").write_text("line,role,flow,amount,unit,supplier\n")
    (toy / "tshirt/tshirt/processes/sewing.yaml").write_text(
        "name: sewing\nlocation: GLO\nkind: ordinary_transforming_activity\n"
    )

    problems = problems_of(toy / STUDY)

    assert sorted((p.code, p.item) for p in problems) == [
        ("missing-file", "processes/dyeing.csv"),
        ("missing-file", "processes/sewing.yaml"),
    ]


def test_name_comes_from_the_yaml_not_from_the_file_name(toy: Path, edit: Edit) -> None:
    edit(toy / YAML, "name: T-shirt", "name: Sewing of a T-shirt")

    process = read_database(toy / STUDY).processes[0]

    assert process.id == "tshirt:tshirt"
    assert process.name == "Sewing of a T-shirt"


def test_dates_are_read_as_iso_dates(toy: Path) -> None:
    with (toy / YAML).open("a") as f:
        f.write("time_start: 2024-01-01\ntime_end: 2024-12-31\n")

    process = read_database(toy / STUDY).processes[0]

    assert process.time_start == date(2024, 1, 1)
    assert process.time_end == date(2024, 12, 31)


def test_quoted_dates_are_read_too(toy: Path) -> None:
    with (toy / YAML).open("a") as f:
        f.write('time_start: "2024-01-01"\n')

    assert read_database(toy / STUDY).processes[0].time_start == date(2024, 1, 1)


@pytest.mark.parametrize("field", ["name", "location", "kind"])
def test_required_fields(toy: Path, field: str) -> None:
    text = (toy / YAML).read_text()
    (toy / YAML).write_text("".join(x for x in text.splitlines(True) if not x.startswith(field)))

    (problem,) = problems_of(toy / STUDY)

    assert problem.code == "invalid-field"
    assert problem.item == f"processes/tshirt.yaml: {field}"


def test_misspelled_field(toy: Path, edit: Edit) -> None:
    edit(toy / YAML, "location:", "locaton:")

    problems = problems_of(toy / STUDY)

    assert sorted(p.item for p in problems) == [
        "processes/tshirt.yaml: location",
        "processes/tshirt.yaml: locaton",
    ]
    assert any("Did you mean 'location'?" in p.message for p in problems)


def test_the_id_cannot_be_set_in_the_yaml(toy: Path) -> None:
    with (toy / YAML).open("a") as f:
        f.write("id: tshirt:other\n")

    (problem,) = problems_of(toy / STUDY)

    assert problem.item == "processes/tshirt.yaml: id"


def test_kind_must_be_an_ecospold2_activity_type(toy: Path, edit: Edit) -> None:
    edit(toy / YAML, "kind: ordinary_transforming_activity", "kind: treatment")

    (problem,) = problems_of(toy / STUDY)

    assert problem.item == "processes/tshirt.yaml: kind"
    assert "'treatment'" in problem.message
    assert "market_activity" in problem.message  # the allowed values are listed


@pytest.mark.parametrize("kind", [k.value for k in ProcessKind])
def test_every_ecospold2_activity_type_is_accepted(toy: Path, edit: Edit, kind: str) -> None:
    edit(toy / YAML, "kind: ordinary_transforming_activity", f"kind: {kind}")

    assert read_database(toy / STUDY).processes[0].kind.value == kind


def test_a_year_is_not_a_date(toy: Path) -> None:
    with (toy / YAML).open("a") as f:
        f.write("time_start: 2024\n")

    (problem,) = problems_of(toy / STUDY)

    assert problem.item == "processes/tshirt.yaml: time_start"
    assert "2024-01-01" in problem.message


def test_time_end_before_time_start(toy: Path) -> None:
    with (toy / YAML).open("a") as f:
        f.write("time_start: 2024-12-31\ntime_end: 2024-01-01\n")

    (problem,) = problems_of(toy / STUDY)

    assert problem.code == "invalid-field"
    assert problem.item == "processes/tshirt.yaml"
    assert problem.location == "processes/tshirt.yaml"
    assert "time_end 2024-01-01" in problem.message
    assert "time_start 2024-12-31" in problem.message


def test_location_is_not_checked_against_a_list_yet(toy: Path, edit: Edit) -> None:
    edit(toy / YAML, "location: GLO", "location: Atlantis")

    assert read_database(toy / STUDY).processes[0].location == "Atlantis"


def test_yaml_that_does_not_parse(toy: Path) -> None:
    (toy / YAML).write_text("name: [T-shirt\n")

    (problem,) = problems_of(toy / STUDY)

    assert problem.code == "bad-yaml"
    assert problem.item == "processes/tshirt.yaml"


def test_a_problem_in_the_yaml_and_one_in_the_csv_are_both_reported(toy: Path, edit: Edit) -> None:
    edit(toy / YAML, "location: GLO", "location: ''")
    edit(toy / CSV, "0.25,kg", "abc,kg")

    problems = problems_of(toy / STUDY)

    assert sorted(p.code for p in problems) == ["bad-number", "invalid-field"]


@pytest.mark.parametrize("word", ["NO", "no", "Yes", "off"])
def test_a_yaml_yes_no_word_gets_a_hint_to_quote_it(toy: Path, edit: Edit, word: str) -> None:
    # NO is the ecoinvent geography code for Norway, and YAML reads it as false.
    edit(toy / YAML, "location: GLO", f"location: {word}")

    (problem,) = problems_of(toy / STUDY)

    assert problem.item == "processes/tshirt.yaml: location"
    assert "quotes" in problem.message
    assert "'NO'" in problem.message


def test_a_quoted_no_is_norway(toy: Path, edit: Edit) -> None:
    edit(toy / YAML, "location: GLO", 'location: "NO"')

    assert read_database(toy / STUDY).processes[0].location == "NO"


def test_a_yes_no_word_as_a_name_gets_the_same_hint(toy: Path, edit: Edit) -> None:
    edit(toy / YAML, "name: T-shirt", "name: no")

    (problem,) = problems_of(toy / STUDY)

    assert problem.item == "processes/tshirt.yaml: name"
    assert "quotes" in problem.message


def test_no_process_files_at_all(toy: Path) -> None:
    for f in (toy / "tshirt/tshirt/processes").iterdir():
        f.unlink()

    (problem,) = problems_of(toy / STUDY)

    assert problem.code == "no-processes"
    assert ".csv" in problem.message
    assert ".yaml" in problem.message
