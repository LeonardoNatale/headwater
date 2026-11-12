"""A normalisation and weighting set folder, from the rows a person writes and the method they are
for.

Example: a person writes `indicator,normalisation,weight` with the rows `climate-change,10,0.6`
and `ozone-formation,0.01,0.4`, from a source they name. `make_set` checks each row against the
method `toy-impacts` and pins the method by its hash, and `write_set` writes `set.yaml` and
`factors.csv` into the folder `toy-nw` beside the method's. The method's folder is not touched.
"""

import csv
import io
import shutil
from collections.abc import Sequence
from pathlib import Path

import yaml
from pydantic import ValidationError

from headwater.model import (
    InvalidDataError,
    Method,
    NwRow,
    NwSet,
    PinnedMethod,
    Problem,
    SetIndicator,
    set_indicators,
)
from headwater.release.cache import temporary_folder
from headwater.release.content import method_hash
from headwater.study._files import problems_from_validation


def make_set(
    rows: Sequence[NwRow],
    method: Method,
    *,
    id: str,
    version: str,
    unit: str | None,
    source: str,
) -> tuple[NwSet, tuple[SetIndicator, ...]]:
    """The set of `rows` for `method`, pinned by the method's hash, and the indicators it adds.

    Raises InvalidDataError naming each row whose indicator the method does not have, and each
    problem of the set itself: rows of mixed kinds, a weight with no unit, and the like.
    """
    order = {i.id: n for n, i in enumerate(method.indicators)}
    try:
        nw_set = NwSet(
            id=id,
            version=version,
            method=PinnedMethod(id=method.id, version=method.version, hash=method_hash(method)),
            unit=unit,
            source=source,
            rows=tuple(
                sorted(rows, key=lambda r: (order.get(r.indicator, len(order)), r.indicator))
            ),
        )
    except ValidationError as error:
        raise InvalidDataError(problems_from_validation(error, prefix=f"set {id}")) from None
    return nw_set, set_indicators(nw_set, method)


def write_set(nw_set: NwSet, folder: Path | str) -> Path:
    """Write `nw_set` into `folder`, which must not exist or must be empty: `set.yaml` and
    `factors.csv`, written into a hidden folder beside it, then renamed, so a failed write leaves
    no half-written set."""
    target = Path(folder)
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise InvalidDataError(
            [
                Problem(
                    "folder-not-empty",
                    f"folder {target}",
                    "already holds files. A set is never overwritten: choose another folder, or "
                    "remove this one first.",
                )
            ]
        )
    temp = temporary_folder(target.parent, target.name)
    try:
        (temp / "set.yaml").write_text(_set_yaml(nw_set), encoding="utf-8")
        (temp / "factors.csv").write_text(_factors_csv(nw_set), encoding="utf-8")
        if target.exists():
            target.rmdir()
        temp.rename(target)
    except BaseException:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    return target


def _set_yaml(nw_set: NwSet) -> str:
    data = nw_set.model_dump(mode="json", exclude={"rows"}, exclude_none=True)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


def _factors_csv(nw_set: NwSet) -> str:
    text = io.StringIO()
    writer = csv.writer(text, lineterminator="\n")
    writer.writerow(["indicator", "normalisation", "weight"])
    for row in nw_set.rows:
        writer.writerow([row.indicator, _number(row.normalisation), _number(row.weight)])
    return text.getvalue()


def _number(value: float | None) -> str:
    return "" if value is None else repr(value)
