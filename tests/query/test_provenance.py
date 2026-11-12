"""A result from a release carries the release's hash and the method's hash."""

from pathlib import Path

import pytest

import headwater as hw
from headwater.query._internals import internals
from headwater.release import Release, method_hash
from headwater.study import read_method

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"


def test_a_result_names_the_release_it_was_computed_from(
    release: Release, release_background: hw.Background, calc
) -> None:
    p = calc("tshirt", background=release_background).provenance

    assert [(r.name, r.hash) for r in p.releases] == [
        ("toy-background", release_background.release.hash)  # pyright: ignore[reportOptionalMemberAccess]
    ]
    assert p.method_hash == method_hash(read_method(TOY / "methods" / "toy-climate"))
    assert (p.method, p.method_version, p.indicator) == ("toy-climate", "1", "climate-change")
    # The study's own pin, as written: it names this release.
    assert p.pin == f"toy-background@{release_background.release.hash[:12]}"  # pyright: ignore[reportOptionalMemberAccess]


def test_the_input_hash_follows_the_release_not_its_records(make_release, records, calc) -> None:
    first = make_release(records)
    again = make_release(records)  # the same records: the same release, in another folder
    changed = make_release(records.with_line("toy:fabric-production", "e02", amount=4.0))

    def input_hash(release: Release) -> str:
        background = hw.load(release.path, method="toy-climate")
        return calc("tshirt", background=background).provenance.input_hash

    assert first.hash == again.hash
    assert input_hash(first) == input_hash(again)
    assert input_hash(first) != input_hash(changed)


def test_a_result_from_a_folder_has_no_release(calc) -> None:
    background = hw.load(
        TOY / "background", method=TOY / "methods" / "toy-climate", namespace="toy"
    )

    p = calc("tshirt", background=background).provenance

    assert p.releases == ()
    assert p.method_hash == method_hash(background.method)


def test_the_study_is_part_of_the_input_hash(release_background: hw.Background, calc) -> None:
    plain = calc("tshirt", background=release_background)
    waste = calc("tshirt-waste", background=release_background)

    assert plain.provenance.input_hash != waste.provenance.input_hash
    assert plain.provenance.releases == waste.provenance.releases
    assert plain.provenance.residual <= plain.provenance.solver.tolerance


@pytest.mark.parametrize("study", ["tshirt", "tshirt-waste"])
def test_the_solver_steps_and_residual_are_recorded(
    release_background: hw.Background, study: str, calc
) -> None:
    p = calc(study, background=release_background).provenance

    assert p.solver.solver == "scipy.sparse.linalg.splu"
    assert p.path
    assert 0.0 <= p.residual <= p.solver.tolerance


def test_the_condition_of_the_background_and_of_the_study_block_are_recorded(
    background: hw.Background, calc
) -> None:
    p = calc("tshirt").provenance

    # The condition of the factors kept for the background (docs/calculation.md, Factorize).
    assert p.condition == internals(background).solved.factorization.condition
    assert p.condition == pytest.approx(3.5, rel=1e-9)  # the toy's three processes alone
    # The T-shirt is a 1 x 1 block [1]: the score of an emission of 1 is 1, g = 1 + 1 = 2, and a
    # 0.1% change of the entry and the emission moves it by 0.2%.
    assert p.database_condition == pytest.approx(2.0, rel=1e-9)


def test_the_condition_is_the_same_from_a_release(
    background: hw.Background, release_background: hw.Background, calc
) -> None:
    assert calc("tshirt", background=release_background).provenance.condition == pytest.approx(
        calc("tshirt").provenance.condition, rel=1e-9
    )
