"""The public Python API: open a database, load a background, then calc, contributions and
sensitivity, or score many products at once.

The names load their module when first used, so `headwater.query.search`, which `headwater find`
needs, does not load numpy and scipy for the solver."""

from typing import TYPE_CHECKING

from headwater._lazy import lazy_exports

if TYPE_CHECKING:
    from headwater.query.background import Background, load
    from headwater.query.database import Database
    from headwater.query.monte_carlo import MonteCarlo, MonteCarloProvenance, Range, monte_carlo
    from headwater.query.result import (
        Contribution,
        Emitter,
        NotCharacterised,
        ParameterSensitivity,
        Place,
        Provenance,
        Result,
        Sensitivity,
        Where,
    )
    from headwater.query.search import Match
    from headwater.query.skus import ManyScores, score_many
    from headwater.query.study import Study, open

__getattr__, __dir__ = lazy_exports(
    globals(),
    {
        "headwater.query.background": ["Background", "load"],
        "headwater.query.monte_carlo": [
            "MonteCarlo",
            "MonteCarloProvenance",
            "Range",
            "monte_carlo",
        ],
        "headwater.query.result": [
            "Emitter",
            "Contribution",
            "Sensitivity",
            "NotCharacterised",
            "ParameterSensitivity",
            "Place",
            "Provenance",
            "Result",
            "Where",
        ],
        "headwater.query.search": ["Match"],
        "headwater.query.skus": ["ManyScores", "score_many"],
        "headwater.query.database": ["Database"],
        "headwater.query.study": ["Study", "open"],
    },
)

__all__ = [
    "Background",
    "Emitter",
    "Contribution",
    "Database",
    "Sensitivity",
    "Study",
    "Match",
    "MonteCarlo",
    "MonteCarloProvenance",
    "NotCharacterised",
    "ParameterSensitivity",
    "Place",
    "Provenance",
    "Range",
    "Result",
    "ManyScores",
    "Where",
    "load",
    "monte_carlo",
    "open",
    "score_many",
]
