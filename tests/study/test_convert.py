"""The layout before D48, `processes/` with one YAML and one CSV per process, is refused by every
command with the command that converts it, and `headwater database convert` rewrites it as the two
tables with the same records.

The fixture tests/fixtures/old-layout/tshirt-dyed is the toy case tshirt-dyed as it was before
D48: the database `dyes`, the T-shirt that uses it, and the study `tshirt-footprint`.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

import headwater as hw
from headwater.cli import app
from headwater.model import InvalidDataError
from headwater.query._hash import records_of
from headwater.study import read_database
from headwater.study._convert import convert
from headwater.study._old_layout import FileLayout
from headwater.study.reader import read_whole
from tests.conftest import open_database, open_study_folder

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
OLD = FIXTURES / "old-layout" / "tshirt-dyed"
TOY = FIXTURES / "toy"
REL = 1e-9

runner = CliRunner()


@pytest.fixture(scope="module")
def bg() -> hw.Background:
    return hw.load(TOY / "background", method=TOY / "methods" / "toy-climate", namespace="toy")


@pytest.fixture
def old(tmp_path: Path) -> Path:
    target = tmp_path / "tshirt-dyed"
    shutil.copytree(OLD, target)
    return target


def files(folder: Path) -> dict[str, bytes]:
    return {
        p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()
    }


def test_the_old_layout_is_refused_naming_the_command(old: Path, bg: hw.Background) -> None:
    with pytest.raises(InvalidDataError) as error:
        hw.open(old / "tshirt")
    [problem] = error.value.problems
    assert problem.code == "old-layout"
    assert "headwater database convert" in problem.message
    assert str(old / "tshirt") in problem.message
    with pytest.raises(InvalidDataError, match="headwater database convert"):
        hw.open(old / "tshirt-footprint")  # a study reads the databases it reaches
    with pytest.raises(InvalidDataError, match="headwater database convert"):
        hw.load(OLD / "dyes", method=TOY / "methods" / "toy-climate", namespace="toy")


def test_every_command_refuses_the_old_layout(old: Path) -> None:
    for args in (["validate", str(old / "tshirt")], ["calc", str(old / "tshirt-footprint")]):
        done = runner.invoke(app, args)
        assert done.exit_code != 0
        assert "headwater database convert" in done.output


def test_the_converted_fixture_scores_as_before(old: Path, bg: hw.Background) -> None:
    done = runner.invoke(app, ["database", "convert", str(old)])
    assert done.exit_code == 0, done.output
    assert "converted" in done.output and "dyes" in done.output and "tshirt" in done.output
    for name in ("dyes", "tshirt"):
        assert not (old / name / "processes").exists()
        assert (old / name / "processes.csv").is_file()
    study = open_study_folder(old / "tshirt-footprint")
    [base] = study.calc(bg)
    [scenario] = study.calc(bg, scenario="cleaner-dye")
    assert base.score == pytest.approx(1.242646, rel=REL)
    assert scenario.score == pytest.approx(1.237646, rel=REL)


def test_the_converted_fixture_is_the_toy_case_byte_for_byte(old: Path) -> None:
    convert(old)
    for name in ("dyes", "tshirt"):
        assert files(old / name) == files(TOY / "tshirt-dyed" / name)


def test_records_and_input_hashes_do_not_change(old: Path, bg: hw.Background) -> None:
    before = {name: read_whole(old / name, FileLayout) for name in ("dyes", "tshirt")}
    hashes = open_database(TOY / "tshirt-dyed" / "tshirt").calc(bg, "tshirt:tshirt")
    convert(old)
    for name, folder in before.items():
        after = read_whole(old / name)
        assert after.processes == folder.processes
        assert records_of(after) == records_of(folder)
    result = open_database(old / "tshirt").calc(bg, "tshirt:tshirt")
    assert result.provenance.input_hash == hashes.provenance.input_hash


def test_a_converted_folder_is_refused(old: Path) -> None:
    convert(old / "dyes")
    with pytest.raises(InvalidDataError, match="already holds processes.csv") as error:
        convert(old / "dyes")
    assert error.value.problems[0].code == "already-converted"
    convert(old)  # the T-shirt is left, one level down
    with pytest.raises(InvalidDataError, match="nothing to convert"):
        convert(old)


def test_a_comment_line_goes_into_the_comment_column(old: Path) -> None:
    yaml = old / "dyes" / "processes" / "reactive-dye.yaml"
    yaml.write_text("# Read from Dyes!A3 of dyes.xlsx.\n#  Bought in bulk.\n" + yaml.read_text())
    convert(old / "dyes")
    assert (old / "dyes" / "processes.csv").read_text().splitlines() == [
        "id,name,location,kind,comment",
        "reactive-dye,reactive dye production,GLO,ordinary_transforming_activity,"
        "Read from Dyes!A3 of dyes.xlsx. Bought in bulk.",
    ]
    [process] = read_database(old / "dyes").processes  # the comment is not a record
    assert "comment" not in process.model_dump()


def test_a_comment_after_a_value_is_refused_naming_its_line(old: Path) -> None:
    yaml = old / "dyes" / "processes" / "reactive-dye.yaml"
    yaml.write_text(yaml.read_text().replace("location: GLO", "location: GLO  # was RER"))
    before = files(old)
    with pytest.raises(InvalidDataError) as error:
        convert(old)
    [problem] = error.value.problems
    assert problem.code == "comment-after-value"
    assert problem.location == "dyes/processes/reactive-dye.yaml:2"
    assert files(old) == before  # the T-shirt, which reads, is not converted either


def test_a_hash_sign_inside_a_quoted_value_is_not_a_comment(old: Path) -> None:
    yaml = old / "dyes" / "processes" / "reactive-dye.yaml"
    yaml.write_text(
        yaml.read_text().replace("name: reactive dye production", 'name: "Reactive dye #2"')
    )
    convert(old / "dyes")
    [process] = read_database(old / "dyes").processes
    assert process.name == "Reactive dye #2"


def test_an_amount_keeps_its_text(old: Path) -> None:
    csv = old / "tshirt" / "processes" / "tshirt.csv"
    csv.write_text(csv.read_text().replace(",0.25,kg,", ",0.250,kg,"))
    convert(old / "tshirt")
    assert (
        "tshirt,e02,input,toy:cotton-fabric,0.250,kg,"
        in (old / "tshirt" / "exchanges.csv").read_text()
    )


def test_a_folder_that_does_not_read_is_refused_and_nothing_changes(old: Path) -> None:
    (old / "tshirt" / "processes" / "tshirt.yaml").unlink()
    before = files(old)
    with pytest.raises(InvalidDataError, match=r"tshirt\.csv"):
        convert(old)
    assert files(old) == before


def test_openlca_processes_are_not_a_folder_to_convert() -> None:
    with pytest.raises(InvalidDataError, match="nothing to convert"):
        convert(FIXTURES / "formats" / "olca-jsonld")


def test_the_diff_across_the_conversion_commit_finds_no_change(
    old: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repository that converts its databases in one commit: the diff across that commit reads
    the old side through the converter, and no score changes."""
    repo = old.parent
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(repo / "no-config"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    author = ["-c", "user.name=t", "-c", "user.email=t@example.com"]

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    git("init", "-q")
    git("add", ".")
    git(*author, "commit", "-q", "-m", "old layout")
    convert(old)
    git("add", "-A")
    git(*author, "commit", "-q", "-m", "convert")
    toy = ["--release", str(TOY / "background"), "--namespace", "toy"]
    toy += ["--method", str(TOY / "methods" / "toy-climate")]
    for folder in ("tshirt-footprint", "tshirt", "dyes"):
        done = runner.invoke(app, ["diff", "HEAD~1", str(old / folder), *toy])
        assert done.exit_code == 0, done.output
        assert "no change" in done.output
        assert "No lines differ." in done.output


def test_the_demo_is_in_the_two_tables(tmp_path: Path) -> None:
    done = runner.invoke(app, ["demo", str(tmp_path / "demo")])
    assert done.exit_code == 0, done.output
    studies = tmp_path / "demo" / "studies"
    assert not list(studies.rglob("processes"))
    databases = sorted(p.parent.name for p in studies.glob("*/database.yaml"))
    assert databases == ["dyes", "tshirt", "tshirt-dyed", "tshirt-local-electricity"]
    for name in databases:
        assert (studies / name / "processes.csv").is_file()
        assert (studies / name / "exchanges.csv").is_file()
