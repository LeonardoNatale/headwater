"""What the packages export, so that other code can import it from the obvious place."""

import headwater as hw
import headwater.model as model
import headwater.study as study
from headwater.model import Uncertainty


def test_the_public_api_exports_what_the_docs_name() -> None:
    for name in ("NotCharacterised", "ManyScores", "score_many", "load", "open", "Background"):
        assert name in hw.__all__
        assert hasattr(hw, name)


def test_the_model_re_exports_the_exchange_extras() -> None:
    for name in ("Uncertainty", "Pedigree", "Distribution", "ExchangeProperty", "LinkStatus"):
        assert name in model.__all__
        assert hasattr(model, name)
    assert Uncertainty.__module__ == "headwater.model.uncertainty"


def test_the_study_package_exports_study_validation() -> None:
    assert "validate_database" in study.__all__
    assert study.validate_database.__module__ == "headwater.study.validate"
