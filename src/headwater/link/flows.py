"""Find the flow of each factor of a method in a release's elementary flows.

A factor names its flow by id, or by name and compartment. Example: ecoinvent's LCIA workbook
names `Methane, fossil` to `air/urban air close to ground`, which is the one elementary flow of
the 3.12 release with that name and compartment.

In order:

1. A row of the flow mapping decides first: a person said which flow it is.
2. The same id is a flow of the release.
3. The same name and compartment is one elementary flow of the release. A key that fits two
   flows is refused, since its factor could map to either: a row of the flow mapping decides it.
   Other keys of the same flow list map as usual.
4. Anything else is unmapped, with the reason. Never guessed.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from headwater.link.mapping import FlowKey, FlowMapping, FlowRow
from headwater.model import Flow, InvalidDataError, Problem


@dataclass(frozen=True)
class Mapped:
    """The flow a key maps to, and how it was found."""

    flow_id: str
    how: str
    """`id`, `name and compartment`, or the mapping row, `flows.csv:3`."""


@dataclass(frozen=True)
class MappedFlows:
    """Where each key of a method went."""

    mapped: Mapping[FlowKey, Mapped]
    unmapped: Mapping[FlowKey, str]
    """The reason each key did not map."""
    unused_rows: tuple[FlowRow, ...]
    """Rows of the mapping no key needed, in file order."""


def map_flows(
    keys: Iterable[FlowKey], flows: Sequence[Flow], *, mapping: FlowMapping | None = None
) -> MappedFlows:
    """Map each key to one of `flows`. Raises `InvalidDataError` when a row of the mapping sends
    a key to a flow that is not one of them, or when a key by name that no row decides fits two
    flows."""
    keys = list(dict.fromkeys(keys))
    rows: Mapping[FlowKey, FlowRow] = mapping.rows if mapping is not None else {}
    ids = {f.id for f in flows}
    by_name = _by_name(flows) if any(k.id is None for k in keys) else {}
    problems = [
        Problem(
            "mapping-target-missing",
            f"mapping {row.decision.source}",
            f"maps {key} to {row.target}, which is not an elementary flow of the flow list",
        )
        for key, row in rows.items()
        if row.target not in ids
    ]
    if problems:
        raise InvalidDataError(problems)
    no_row = f", and no row of {mapping.file} maps it" if mapping is not None else ""
    mapped: dict[FlowKey, Mapped] = {}
    unmapped: dict[FlowKey, str] = {}
    ambiguous: list[Problem] = []
    for key in keys:
        row = rows.get(key)
        if row is not None:
            mapped[key] = Mapped(row.target, row.decision.source)
        elif key.id is not None:
            if key.id in ids:
                mapped[key] = Mapped(key.id, "id")
            else:
                unmapped[key] = f"no elementary flow of the flow list has this id{no_row}"
        else:
            fits = by_name.get((key.name or "", key.compartment or ""), [])
            if len(fits) == 1:
                mapped[key] = Mapped(fits[0], "name and compartment")
            elif fits:
                ambiguous.append(_ambiguous(key, fits))
            else:
                unmapped[key] = (
                    f"no elementary flow of the flow list has this name and compartment{no_row}"
                )
    if ambiguous:
        raise InvalidDataError(ambiguous)
    asked = set(keys)
    return MappedFlows(
        mapped=mapped,
        unmapped=unmapped,
        unused_rows=tuple(row for key, row in rows.items() if key not in asked),
    )


def _by_name(flows: Sequence[Flow]) -> dict[tuple[str, str], list[str]]:
    """The ids of the flows of each name and compartment, sorted."""
    found: dict[tuple[str, str], list[str]] = defaultdict(list)
    for flow in flows:
        found[flow.name, flow.compartment or ""].append(flow.id)
    return {key: sorted(ids) for key, ids in found.items()}


def _ambiguous(key: FlowKey, ids: Sequence[str]) -> Problem:
    return Problem(
        "ambiguous-flow",
        f"flow {key}",
        f"names {len(ids)} elementary flows of the flow list: {', '.join(ids)}. Its factor could "
        "map to any of them: say which in a row of the flow mapping.",
    )
