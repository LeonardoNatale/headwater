from typing import TYPE_CHECKING

from headwater._lazy import lazy_exports

if TYPE_CHECKING:
    __version__: str
    from headwater.model import HeadwaterError, InvalidDataError
    from headwater.query import (
        Background,
        Contribution,
        Database,
        Emitter,
        ManyScores,
        Match,
        MonteCarlo,
        MonteCarloProvenance,
        NotCharacterised,
        ParameterSensitivity,
        Place,
        Provenance,
        Range,
        Result,
        Sensitivity,
        Study,
        Where,
        load,
        monte_carlo,
        open,
        score_many,
    )

_exported, _dir = lazy_exports(
    globals(),
    {
        "headwater.model": ["HeadwaterError", "InvalidDataError"],
        # From the module itself: once `headwater.query.monte_carlo` is imported, the attribute
        # `monte_carlo` of `headwater.query` is that module, not the function.
        "headwater.query.monte_carlo": [
            "MonteCarlo",
            "MonteCarloProvenance",
            "Range",
            "monte_carlo",
        ],
        "headwater.query": [
            "Background",
            "Emitter",
            "Contribution",
            "Database",
            "Sensitivity",
            "Match",
            "NotCharacterised",
            "ParameterSensitivity",
            "Place",
            "Provenance",
            "Result",
            "ManyScores",
            "Study",
            "Where",
            "load",
            "open",
            "score_many",
        ],
    },
)


def __getattr__(name: str) -> object:
    """The lazy exports, and `__version__`, read once from the installed metadata: pyproject.toml
    is the one place that writes the version. `import headwater` loads no `importlib.metadata`."""
    if name == "__version__":
        from importlib.metadata import version

        globals()["__version__"] = found = version("headwater")
        return found
    return _exported(name)


def __dir__() -> list[str]:
    return sorted({*_dir(), "__version__"})


__all__ = [
    "Background",
    "Emitter",
    "Contribution",
    "Database",
    "HeadwaterError",
    "InvalidDataError",
    "Sensitivity",
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
    "Study",
    "Where",
    "__version__",
    "load",
    "monte_carlo",
    "open",
    "score_many",
]
