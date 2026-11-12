"""A background keeps the pivoted factors when one of its solves needed them."""

from pathlib import Path

import numpy as np
import pytest

import headwater as hw
import headwater.calc.solve as solve_module
import headwater.query.background as background_module
from headwater.calc import Factorization, Step
from headwater.query._internals import internals
from tests.conftest import approx_numbers, open_database

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"


def test_a_background_keeps_the_pivoted_factors_of_a_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The unpivoted factors pass factorize's own check, then become 0.1% wrong, so the score
    # solve falls back to pivoting. Later solves must not factorize again.
    real_solve = Factorization.solve
    real_factorize = background_module.factorize
    rough = False

    def solve(self: Factorization, b: np.ndarray, *, transposed: bool = False) -> np.ndarray:
        y = real_solve(self, b, transposed=transposed)
        return y * (1 + 1e-3) if rough and self.settings.diag_pivot_thresh == 0.0 else y

    def factorize_then_roughen(*args, **kwargs) -> Factorization:
        nonlocal rough
        found = real_factorize(*args, **kwargs)
        rough = True
        return found

    monkeypatch.setattr(Factorization, "solve", solve)
    monkeypatch.setattr(background_module, "factorize", factorize_then_roughen)

    bg = hw.load(TOY / "background", method=TOY / "methods" / "toy-climate", namespace="toy")
    result = open_database(TOY / "tshirt" / "tshirt").calc(bg, "tshirt:tshirt")

    assert internals(bg).solved.factorization.settings.diag_pivot_thresh == 1.0
    assert result.score == pytest.approx(1.19205, rel=1e-9)


def roughen_forward_solves(monkeypatch: pytest.MonkeyPatch, bg: hw.Background) -> list[int]:
    """Make the forward solves of the background's unpivoted factors 0.1% wrong, so that a
    forward solve falls back to pivoting. Returns the sizes of the factorizations the solves
    then make: the study block is factorized elsewhere, so only a fallback shows up here."""
    unpivoted = internals(bg).solved.factorization
    real_solve = Factorization.solve
    real_factorize = solve_module.factorize
    made: list[int] = []

    def solve(self: Factorization, b: np.ndarray, *, transposed: bool = False) -> np.ndarray:
        y = real_solve(self, b, transposed=transposed)
        return y * (1 + 1e-3) if self is unpivoted and not transposed else y

    def factorize(*args, **kwargs) -> Factorization:
        found = real_factorize(*args, **kwargs)
        made.append(found.n)
        return found

    monkeypatch.setattr(Factorization, "solve", solve)
    monkeypatch.setattr(solve_module, "factorize", factorize)
    return made


def test_a_fallback_in_the_background_solve_of_a_study_is_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Loading passes: only the forward solve of the study's supply goes wrong. The pivoted
    # factors made for it must serve every later calc, not be made again.
    bg = hw.load(TOY / "background", method=TOY / "methods" / "toy-climate", namespace="toy")
    study = open_database(TOY / "tshirt" / "tshirt")
    made = roughen_forward_solves(monkeypatch, bg)

    first = study.calc(bg, "tshirt:tshirt")
    again = [study.calc(bg, "tshirt:tshirt"), study.calc(bg, "tshirt:tshirt")]

    assert made == [internals(bg).solved.factorization.n]  # one fallback, in the first calc
    assert internals(bg).solved.factorization.settings.diag_pivot_thresh == 1.0
    # The first calc tried the unpivoted factors and refined once before it fell back. The later
    # ones solve with the kept factors at once, with nothing to refine.
    assert Step.refined in first.provenance.path
    for later in again:
        assert Step.refined not in later.provenance.path
    for result in (first, *again):
        assert result.score == pytest.approx(1.19205, rel=1e-9)
        assert result.forward_score == pytest.approx(1.19205, rel=1e-9)
    # The condition in provenance is the one of the factors kept, not of the ones replaced.
    assert first.provenance.condition == internals(bg).solved.factorization.condition


def test_a_fallback_in_the_levers_of_a_background_product_is_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bg = hw.load(TOY / "background", method=TOY / "methods" / "toy-climate", namespace="toy")
    made = roughen_forward_solves(monkeypatch, bg)

    first = bg.sensitivity("toy:fabric-production")
    again = bg.sensitivity("toy:fabric-production")

    assert made == [internals(bg).solved.factorization.n]
    assert internals(bg).solved.factorization.settings.diag_pivot_thresh == 1.0
    assert [(x.line_id, x.elasticity) for x in first] == approx_numbers(
        [(x.line_id, x.elasticity) for x in again]
    )
