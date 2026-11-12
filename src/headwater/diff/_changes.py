"""What differs between two readings of a study, record by record.

A line is found by its process and line id, a parameter by its id, an override line by the
process it changes and its line id. For each difference that can change a score, `apply` makes the
old study with that one difference in it, so the score it alone would cause can be calculated.
"""

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, cast

from pydantic import BaseModel

from headwater.model import (
    Exchange,
    Flow,
    FlowProperty,
    HeadwaterError,
    LineChange,
    Override,
    Parameter,
    ParameterChange,
    ParameterSet,
    Process,
    Scenario,
    Unit,
)
from headwater.study import DatabaseFolder, NoFactor, StudyFolder

type Apply = Callable[[DatabaseFolder], DatabaseFolder]
type InWords = Callable[["Names"], str]


@dataclass(frozen=True)
class Names:
    """Words for the ids a description names: flows by their names, processes by their names and
    locations. Apart, since a study's process and its reference flow share one id."""

    flows: Mapping[str, str] = field(default_factory=dict[str, str])
    processes: Mapping[str, str] = field(default_factory=dict[str, str])


_LINE_FIELDS = {
    "role": "role",
    "flow_id": "flow",
    "flow_name": "flow_name",
    "amount": "amount",
    "formula": "formula",
    "unit": "unit",
    "supplier_id": "supplier",
    "comment": "comment",
    "uncertainty": "uncertainty",
    "pedigree": "pedigree",
    "properties": "properties",
    "link_status": "link status",
}
"""The fields of a line a person can change, with the name a file or a message gives them."""


class Change(StrEnum):
    added = "added"
    removed = "removed"
    changed = "changed"


@dataclass(frozen=True)
class FieldChange:
    """One field that is not the same in the two readings."""

    name: str
    old: object
    new: object


def no_effect(folder: DatabaseFolder) -> DatabaseFolder:
    """For a difference that cannot change a score alone, such as a process nothing buys from.
    Its effect is 0 by construction, so it is not calculated."""
    return folder


@dataclass(frozen=True)
class Difference:
    """One thing that differs between the old and the new study.

    Example: line e02 of the T-shirt, fabric 0.25 kg in the old study and 0.22 kg in the new one.
    """

    id: str
    """Stable: the kind and the item, such as `line tshirt:tshirt line e02`."""
    kind: str
    """`line`, `process`, `parameter`, `override`, `flow`, `flow_property`, `unit`, `cut_off`,
    `scenario`, `no_factor`, `study` or `comment`. A `comment` is the comment of a line, an override
    line or a parameter: it never moves a score, so it is apart from what changed in the record."""
    change: Change
    item: str
    """What is named in messages: `tshirt:tshirt line e02`, `fabric_kg`, `clean-grid`."""
    description: str
    """What is different, in one short phrase: `amount 0.25 → 0.22 kg`."""
    fields: tuple[FieldChange, ...] = ()
    affects: tuple[str, ...] = ()
    """The lines whose amounts a changed parameter moves."""
    flow_id: str | None = None
    apply: Apply | None = field(default=None, compare=False, repr=False)
    """The old study with only this difference made, or None when it cannot change a score."""
    database: str | None = None
    """The database the difference is in, when it is one that the database compared uses, or
    for a study, any database its products reach: `dyes`. None for the folder's own."""
    in_words: InWords | None = field(default=None, compare=False, repr=False)
    """The description with the ids of processes and flows replaced by the words given for them.
    None when the description names none."""

    @property
    def scored(self) -> bool:
        """True when `apply` can make a study whose score may differ from the old one."""
        return self.apply is not None

    def described(self, names: "Names") -> str:
        """The description with each supplier and flow in `names` named in words:
        `supplier electricity production (GLO) → solar electricity (FR)`."""
        return self.description if self.in_words is None else self.in_words(names)


def differences(old: DatabaseFolder, new: DatabaseFolder) -> tuple[Difference, ...]:
    """Every record that is not the same in `old` and `new`, in the order a person reads them:
    parameters, processes and their lines, overrides, flow and unit definitions, then what a score
    does not depend on (cut-offs, no_factor.csv rows, database.yaml).

    Then the same for each database they both use, by name. Its line is named by its full id,
    `dyes:reactive-dye line e03`, and anything else with its database, `kwh_per_kg in dyes`. A
    database only one side uses differs by the `uses` of a database.yaml, and by the lines that
    buy from it."""
    used_old = {u.database.name: u for u in old.used}
    used_new = {u.database.name: u for u in new.used}
    return (
        *_differences(old, new),
        *(
            named_in(name, d)
            for name in sorted(used_old.keys() & used_new.keys())
            for d in _differences(used_old[name], used_new[name])
        ),
    )


def _differences(old: DatabaseFolder, new: DatabaseFolder) -> tuple[Difference, ...]:
    cut_offs = _cut_offs(old, new)
    parameters = _parameters(old, new)
    return tuple(
        [
            *parameters,
            *_processes(
                old,
                new,
                derived=bool(parameters),
                cut_off_lines={d.item for d in cut_offs},
            ),
            *_overrides(old, new, derived=bool(parameters)),
            *_flows(old, new),
            *_properties(old, new),
            *_units(old, new),
            *cut_offs,
            *_no_factor(old, new),
            *_study_fields(old, new),
        ]
    )


_PROCESS_ITEMS = ("line", "process", "cut_off")
"""The kinds whose item is a process of the study, or one of its lines: its id names the study."""


def _names_a_process(d: Difference) -> bool:
    """True when the item of `d` is a process of the study or one of its lines, so its id names
    the study."""
    if d.kind == "comment":
        return not d.item.startswith((_OVERRIDE_OF, _PARAMETER))
    return d.kind in _PROCESS_ITEMS


def named_in(name: str, d: Difference) -> Difference:
    """A difference of the used study `name`, as a difference of the study that uses it: named
    with its study, and made to that study in the old study that uses it. A line keeps its full
    id, `dyes:reactive-dye line e03`. Anything else gets the study after it, `kwh_per_kg in dyes`,
    so its id cannot be one of the study's own."""
    item = d.item if _names_a_process(d) else f"{d.item} in {name}"
    rest = d.id.removeprefix(f"{d.kind} ")
    assert rest.startswith(d.item), d.id  # every id is `<kind> <item>`, then for some more
    apply = d.apply if d.apply is None or d.apply is no_effect else _lifted(name, d.apply)
    identity = f"{d.kind} {item}{rest.removeprefix(d.item)}"
    return replace(d, id=identity, item=item, apply=apply, database=name)


def _lifted(name: str, apply: Apply) -> Apply:
    def lifted(folder: DatabaseFolder) -> DatabaseFolder:
        used = tuple(apply(u) if u.database.name == name else u for u in folder.used)
        return replace(folder, used=used)

    return lifted


# --- showing -----------------------------------------------------------------------------------


def _show(value: object) -> str:
    if value is None:
        return "none"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _distribution(value: object) -> str:
    """A distribution as a study file writes it: `lognormal scale 0.1`, `uniform 0.2 to 0.3`."""
    if not isinstance(value, Mapping):
        return _show(value)
    fields = cast(Mapping[str, Any], value)
    text = str(fields.get("distribution"))
    if fields.get("scale") is not None:
        text += f" scale {_show(fields['scale'])}"
    if fields.get("minimum") is not None:
        text += f" {_show(fields['minimum'])} to {_show(fields.get('maximum'))}"
    return text


def _arrow(name: str, old: object, new: object) -> str:
    show = _distribution if name == "uncertainty" else _show
    return f"{name} {show(old)} → {show(new)}"


def _quoted(value: object) -> str:
    """A comment, in quotes so where it starts and ends is clear: `"Grid mix"`, or `none`."""
    return "none" if value is None else f'"{value}"'


_OVERRIDE_OF = "override of "
_PARAMETER = "parameter "


def _comment(item: str, old: str | None, new: str | None, flow_id: str | None) -> Difference:
    """The comment of `item` edited: listed apart, since it never changes a score."""
    return Difference(
        id=f"comment {item}",
        kind="comment",
        change=Change.changed,
        item=item,
        description=f"comment {_quoted(old)} → {_quoted(new)}",
        fields=(FieldChange("comment", old, new),),
        flow_id=flow_id,
    )


def _field_changes(
    old: Mapping[str, Any], new: Mapping[str, Any], names: Mapping[str, str] | None = None
) -> tuple[FieldChange, ...]:
    """The keys whose values differ, named as `names` says, in the order of `names` (or sorted)."""
    keys = list(names) if names is not None else sorted(set(old) | set(new))
    return tuple(
        FieldChange((names or {}).get(k, k), old.get(k), new.get(k))
        for k in keys
        if old.get(k) != new.get(k)
    )


def _dump(record: BaseModel, exclude: set[str] | None = None) -> dict[str, Any]:
    return record.model_dump(mode="json", exclude=exclude)


# --- lines -------------------------------------------------------------------------------------


def _line_text(e: Exchange, names: Names) -> str:
    """`input 0.25 kg of toy:cotton-fabric, supplier cotton fabric production (GLO)`. The flow
    keeps its id: a label names it in words already."""
    text = f"{e.role.value} {e.amount:g} {e.unit} of {e.flow_id}"
    supplier = e.supplier_id
    return text + (f", supplier {names.processes.get(supplier, supplier)}" if supplier else "")


def _line_change(old: Exchange, new: Exchange, ignore: frozenset[str]) -> tuple[FieldChange, ...]:
    names = {k: v for k, v in _LINE_FIELDS.items() if k not in ignore}
    return _field_changes(_dump(old), _dump(new), names)


def _named_arrow(change: FieldChange, names: Mapping[str, str]) -> str:
    """`supplier electricity production (GLO) → solar electricity (FR)`. Two ids with the same
    words keep their ids after them, so the two sides never read the same."""
    old, new = cast(str | None, change.old), cast(str | None, change.new)
    words = [None if i is None else names.get(i, i) for i in (old, new)]
    if words[0] == words[1] and old != new:
        words = [None if i is None else f"{names[i]} {i}" for i in (old, new)]
    return _arrow(change.name, *words)


def _describe_line(changes: tuple[FieldChange, ...], unit: str, names: Names | None = None) -> str:
    """`amount 0.25 → 0.22 kg`, then any other field that changed, a supplier or a flow in words
    where `names` has them."""
    names = names or Names()
    words = {"supplier": names.processes, "flow": names.flows}
    by_name = {c.name: c for c in changes}
    amount, new_unit = by_name.pop("amount", None), by_name.pop("unit", None)
    parts: list[str] = []
    if amount is not None and new_unit is not None:
        parts.append(
            f"amount {_show(amount.old)} {new_unit.old} → {_show(amount.new)} {new_unit.new}"
        )
    elif amount is not None:
        parts.append(f"amount {_show(amount.old)} → {_show(amount.new)} {unit}")
    elif new_unit is not None:
        parts.append(_arrow("unit", new_unit.old, new_unit.new))
    parts.extend(
        _named_arrow(c, words[c.name]) if c.name in words else _arrow(c.name, c.old, c.new)
        for c in by_name.values()
    )
    return ", ".join(parts)


def _rebuilt(process: Process, lines: Iterable[Exchange]) -> Process:
    """The process with these lines. Validators run again, so a reference line that went missing
    is refused."""
    return Process.model_validate(
        {**process.model_dump(exclude={"exchanges"}), "exchanges": tuple(lines)}
    )


def _with_needed(
    folder: DatabaseFolder, new: DatabaseFolder, lines: Iterable[Exchange]
) -> DatabaseFolder:
    """`folder` plus what `lines` need from `new` to stand on their own: the processes they buy
    from that only `new` has, and the flows, properties and units those bring, and the studies
    `new` uses that they buy from, with what those use."""
    lines = tuple(lines)
    have = {p.id for p in folder.processes}
    in_new = {p.id: p for p in new.processes}
    in_used = {p.id: u for u in new.used for p in u.processes}
    used = {u.database.name for u in folder.used}
    brought: list[str] = []
    added: list[Process] = []
    flows = {e.flow_id for e in lines}
    units = {e.unit for e in lines}
    queue = [e.supplier_id for e in lines if e.supplier_id]
    while queue:
        pid = queue.pop()
        if pid not in have and pid not in in_new and pid in in_used:
            name = in_used[pid].database.name
            if name not in used:
                used.add(name)
                brought.append(name)
            continue
        if pid in have or pid not in in_new:
            continue
        have.add(pid)
        process = in_new[pid]
        added.append(process)
        for e in process.exchanges:
            flows.add(e.flow_id)
            units.add(e.unit)
            queue.extend([e.supplier_id] if e.supplier_id else [])
    known_flows = {f.id for f in folder.flows}
    new_flows = [f for f in new.flows if f.id in flows and f.id not in known_flows]
    ids = {f.id for f in new_flows}
    known_properties = set(folder.flow_properties)
    properties = [p for p in new.flow_properties if p.flow_id in ids and p not in known_properties]
    units |= {f.unit for f in new_flows} | {p.unit for p in properties}
    known_units = {u.name for u in folder.units}
    new_units = [u for u in new.units if u.name in units and u.name not in known_units]
    if not (added or new_flows or properties or new_units or brought):
        return folder
    return replace(
        folder,
        processes=(*folder.processes, *added),
        flows=(*folder.flows, *new_flows),
        flow_properties=(*folder.flow_properties, *properties),
        units=(*folder.units, *new_units),
        **(_bringing(folder, new, brought) if brought else {}),
    )


def _bringing(folder: DatabaseFolder, new: DatabaseFolder, names: list[str]) -> dict[str, Any]:
    """The `study` and `used` of `folder` once it uses the studies `names` of `new`, and what
    they use in turn."""
    by_name = {u.database.name: u for u in new.used}
    used = {u.database.name: u for u in folder.used}
    queue = list(names)
    while queue:
        name = queue.pop()
        if name in used or name not in by_name:
            continue
        used[name] = by_name[name]
        queue.extend(by_name[name].database.uses)
    uses = (*folder.database.uses, *(n for n in names if n not in folder.database.uses))
    return {
        "database": folder.database.model_copy(update={"uses": uses}),
        "used": tuple(used[n] for n in sorted(used)),
    }


def _apply_line(process_id: str, line_id: str, line: Exchange | None, new: DatabaseFolder) -> Apply:
    """Make the old study have `line` (or none, to remove it) at this place."""

    def apply(folder: DatabaseFolder) -> DatabaseFolder:
        processes: list[Process] = []
        for process in folder.processes:
            if process.id != process_id:
                processes.append(process)
                continue
            lines: list[Exchange] = []
            placed = False
            for e in process.exchanges:
                if e.line_id != line_id:
                    lines.append(e)
                elif line is not None:
                    lines.append(line)
                    placed = True
            if line is not None and not placed:
                lines.append(line)
            processes.append(_rebuilt(process, lines))
        result = replace(folder, processes=tuple(processes))
        return _with_needed(result, new, [line]) if line is not None else result

    return apply


def _processes(
    old: DatabaseFolder, new: DatabaseFolder, *, derived: bool, cut_off_lines: set[str]
) -> list[Difference]:
    found: list[Difference] = []
    before = {p.id: p for p in old.processes}
    after = {p.id: p for p in new.processes}
    for pid in sorted(set(before) | set(after)):
        o, n = before.get(pid), after.get(pid)
        if o is None and n is not None:
            found.append(
                _whole_process(
                    pid, Change.added, n, f"added, with {_count(len(n.exchanges), 'line')}"
                )
            )
        elif n is None and o is not None:
            found.append(
                _whole_process(
                    pid, Change.removed, o, f"removed, with {_count(len(o.exchanges), 'line')}"
                )
            )
        elif o is not None and n is not None:
            found.extend(_process(o, n, new, derived=derived, cut_off_lines=cut_off_lines))
    return found


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _whole_process(pid: str, change: Change, process: Process, text: str) -> Difference:
    return Difference(
        id=f"process {pid}",
        kind="process",
        change=change,
        item=pid,
        description=text,
        apply=no_effect,
    )


def _process(
    old: Process,
    new: Process,
    new_folder: DatabaseFolder,
    *,
    derived: bool,
    cut_off_lines: set[str],
) -> list[Difference]:
    found: list[Difference] = []
    details = _field_changes(_dump(old, {"exchanges"}), _dump(new, {"exchanges"}))
    if details:
        found.append(
            Difference(
                id=f"process {new.id}",
                kind="process",
                change=Change.changed,
                item=new.id,
                description=", ".join(_arrow(c.name, c.old, c.new) for c in details),
                fields=details,
                apply=_apply_details(new),
            )
        )
    before = {e.line_id: e for e in old.exchanges}
    after = {e.line_id: e for e in new.exchanges}
    for line_id in [*before, *(k for k in after if k not in before)]:
        item = f"{new.id} line {line_id}"
        found.extend(
            _line(
                "line",
                item,
                before.get(line_id),
                after.get(line_id),
                lambda line, line_id=line_id: _apply_line(new.id, line_id, line, new_folder),
                derived=derived,
                cut_off=item in cut_off_lines,
            )
        )
    return found


def _apply_details(new: Process) -> Apply:
    details = new.model_dump(exclude={"exchanges", "id"})

    def apply(folder: DatabaseFolder) -> DatabaseFolder:
        return replace(
            folder,
            processes=tuple(
                p.model_copy(update=details) if p.id == new.id else p for p in folder.processes
            ),
        )

    return apply


def _line(
    kind: str,
    item: str,
    old: Exchange | None,
    new: Exchange | None,
    make: Callable[[Exchange | None], Apply],
    *,
    derived: bool,
    cut_off: bool,
) -> list[Difference]:
    """The difference between two versions of one line, if there is one, and the edit of its
    comment apart. `make(line)` gives the function that puts `line`, or no line, in the old
    study."""
    if new is None:
        assert old is not None
        removed = old

        def words(names: Names) -> str:
            return f"removed: {_line_text(removed, names)}"

        return [
            Difference(
                id=f"{kind} {item}",
                kind=kind,
                change=Change.removed,
                item=item,
                description=words(Names()),
                flow_id=old.flow_id,
                apply=make(None),
                in_words=words,
            )
        ]
    if old is None:
        added = new

        def words(names: Names) -> str:
            return f"added: {_line_text(added, names)}"

        return [
            Difference(
                id=f"{kind} {item}",
                kind=kind,
                change=Change.added,
                item=item,
                description=words(Names()),
                flow_id=new.flow_id,
                apply=make(new),
                in_words=words,
            )
        ]
    # cut_offs.csv says the link status, and a comment is listed apart
    ignore = frozenset({"comment", "link_status"} if cut_off else {"comment"})
    changes = _line_change(old, new, ignore)
    found: list[Difference] = []
    moved_by_parameter = (
        derived
        and old.formula is not None
        and old.formula == new.formula
        and {c.name for c in changes} == {"amount"}
    )  # the parameter is the difference
    if changes and not moved_by_parameter:
        unit = new.unit
        found.append(
            Difference(
                id=f"{kind} {item}",
                kind=kind,
                change=Change.changed,
                item=item,
                description=_describe_line(changes, unit),
                fields=changes,
                flow_id=new.flow_id,
                apply=make(new),
                in_words=lambda names: _describe_line(changes, unit, names),
            )
        )
    if old.comment != new.comment:
        named = item if kind == "line" else f"{_OVERRIDE_OF}{item}"
        found.append(_comment(named, old.comment, new.comment, new.flow_id))
    return found


# --- parameters --------------------------------------------------------------------------------


def _definition(p: Parameter) -> str:
    text = f"formula {p.formula}" if p.formula is not None else f"value {p.value:g}"
    return text + (f" {p.unit}" if p.unit else "")


def _formula_lines(folder: DatabaseFolder) -> Iterable[Exchange]:
    for process in folder.processes:
        yield from (e for e in process.exchanges if e.formula is not None)
    for override in folder.overrides:
        yield from (e for e in override.lines if e.formula is not None)


def _evaluated(e: Exchange, parameters: ParameterSet) -> Exchange:
    if e.formula is None:
        return e
    amount = parameters.evaluate(e.formula, e.process_id)
    if amount == e.amount:
        return e
    return Exchange.model_validate({**e.model_dump(), "amount": amount})


def _reevaluated(folder: DatabaseFolder, parameters: ParameterSet) -> DatabaseFolder:
    """`folder` with these parameters, and every formula amount worked out again."""
    processes = tuple(
        _rebuilt(p, (_evaluated(e, parameters) for e in p.exchanges))
        if any(e.formula is not None for e in p.exchanges)
        else p
        for p in folder.processes
    )
    overrides = tuple(
        Override(process_id=o.process_id, lines=tuple(_evaluated(e, parameters) for e in o.lines))
        for o in folder.overrides
    )
    return replace(folder, parameters=parameters, processes=processes, overrides=overrides)


def _apply_parameter(new: Parameter) -> Apply:
    def apply(folder: DatabaseFolder) -> DatabaseFolder:
        definition = new.formula if new.formula is not None else new.value
        assert definition is not None
        return _reevaluated(folder, folder.parameters.with_changes({new.id: definition}))

    return apply


def _moved_by(old: DatabaseFolder, new: Parameter) -> tuple[str, ...]:
    """The lines whose amounts change when only this parameter does."""
    try:
        after = _apply_parameter(new)(old)
    except (ValueError, KeyError, HeadwaterError):  # a formula that cannot be worked out
        return ()
    amounts = {e.label: e.amount for e in _formula_lines(old)}
    return tuple(sorted(e.label for e in _formula_lines(after) if amounts.get(e.label) != e.amount))


def _parameters(old: DatabaseFolder, new: DatabaseFolder) -> list[Difference]:
    found: list[Difference] = []
    before = {p.id: p for p in old.parameters}
    after = {p.id: p for p in new.parameters}
    for pid in sorted(set(before) | set(after)):
        o, n = before.get(pid), after.get(pid)
        if o == n:
            continue
        if o is None and n is not None:
            found.append(
                Difference(
                    id=f"parameter {pid}",
                    kind="parameter",
                    change=Change.added,
                    item=pid,
                    description=f"added: {_definition(n)}",
                    apply=no_effect,
                )
            )
        elif n is None and o is not None:
            found.append(
                Difference(
                    id=f"parameter {pid}",
                    kind="parameter",
                    change=Change.removed,
                    item=pid,
                    description=f"removed: {_definition(o)}",
                    apply=no_effect,
                )
            )
        elif o is not None and n is not None:
            changes = _field_changes(_dump(o), _dump(n), {k: k for k in _PARAMETER_FIELDS})
            moved = _moved_by(old, n) if changes else ()
            text = ", ".join(_arrow(c.name, c.old, c.new) for c in changes)
            if moved:
                shown = ", ".join(moved[:3]) + (
                    f" and {len(moved) - 3} more" if len(moved) > 3 else ""
                )
                text += f" (moves {shown})"
            if changes:
                found.append(
                    Difference(
                        id=f"parameter {pid}",
                        kind="parameter",
                        change=Change.changed,
                        item=pid,
                        description=text,
                        fields=changes,
                        affects=moved,
                        apply=_apply_parameter(n),
                    )
                )
            if o.comment != n.comment:
                found.append(_comment(f"{_PARAMETER}{pid}", o.comment, n.comment, None))
    return found


# --- overrides ---------------------------------------------------------------------------------


def _override_lines(folder: DatabaseFolder) -> dict[tuple[str, str], Exchange]:
    return {(o.process_id, e.line_id): e for o in folder.overrides for e in o.lines}


def _overrides_of(lines: Mapping[tuple[str, str], Exchange]) -> tuple[Override, ...]:
    by_process: dict[str, list[Exchange]] = {}
    for (pid, _), e in sorted(lines.items()):
        by_process.setdefault(pid, []).append(e)
    return tuple(Override(process_id=pid, lines=tuple(es)) for pid, es in by_process.items())


def _apply_override(key: tuple[str, str], line: Exchange | None, new: DatabaseFolder) -> Apply:
    def apply(folder: DatabaseFolder) -> DatabaseFolder:
        lines = _override_lines(folder)
        if line is None:
            lines.pop(key, None)
        else:
            lines[key] = line
        result = replace(folder, overrides=_overrides_of(lines))
        return _with_needed(result, new, [line]) if line is not None else result

    return apply


def _overrides(old: DatabaseFolder, new: DatabaseFolder, *, derived: bool) -> list[Difference]:
    before, after = _override_lines(old), _override_lines(new)
    found: list[Difference] = []
    for key in [*before, *(k for k in after if k not in before)]:
        found.extend(
            _line(
                "override",
                f"{key[0]} line {key[1]}",
                before.get(key),
                after.get(key),
                lambda line, key=key: _apply_override(key, line, new),
                derived=derived,
                cut_off=False,
            )
        )
    return found


# --- definitions -------------------------------------------------------------------------------


def _keyed[R: BaseModel](
    kind: str,
    before: Mapping[str, R],
    after: Mapping[str, R],
    replace_record: Callable[[DatabaseFolder, str, R], DatabaseFolder],
    flow_id: Callable[[R], str | None] = lambda record: None,
) -> list[Difference]:
    found: list[Difference] = []
    for key in sorted(set(before) | set(after)):
        o, n = before.get(key), after.get(key)
        if o == n:
            continue
        if o is None and n is not None:
            found.append(
                Difference(
                    id=f"{kind} {key}",
                    kind=kind,
                    change=Change.added,
                    item=key,
                    description="added",
                    flow_id=flow_id(n),
                    apply=no_effect,
                )
            )
        elif n is None and o is not None:
            found.append(
                Difference(
                    id=f"{kind} {key}",
                    kind=kind,
                    change=Change.removed,
                    item=key,
                    description="removed",
                    flow_id=flow_id(o),
                    apply=no_effect,
                )
            )
        elif o is not None and n is not None:
            changes = _field_changes(_dump(o), _dump(n))
            found.append(
                Difference(
                    id=f"{kind} {key}",
                    kind=kind,
                    change=Change.changed,
                    item=key,
                    description=", ".join(_arrow(c.name, c.old, c.new) for c in changes),
                    fields=changes,
                    flow_id=flow_id(n),
                    apply=_replacing(replace_record, key, n),
                )
            )
    return found


def _replacing[R: BaseModel](
    replace_record: Callable[[DatabaseFolder, str, R], DatabaseFolder], key: str, record: R
) -> Apply:
    return lambda folder: replace_record(folder, key, record)


def _flows(old: DatabaseFolder, new: DatabaseFolder) -> list[Difference]:
    def put(folder: DatabaseFolder, key: str, record: Flow) -> DatabaseFolder:
        return replace(folder, flows=tuple(record if f.id == key else f for f in folder.flows))

    return _keyed(
        "flow",
        {f.id: f for f in old.flows},
        {f.id: f for f in new.flows},
        put,
        lambda f: f.id,
    )


def _properties(old: DatabaseFolder, new: DatabaseFolder) -> list[Difference]:
    def key(p: FlowProperty) -> str:
        return f"{p.flow_id} {p.property.value}"

    def put(folder: DatabaseFolder, at: str, record: FlowProperty) -> DatabaseFolder:
        return replace(
            folder,
            flow_properties=tuple(record if key(p) == at else p for p in folder.flow_properties),
        )

    return _keyed(
        "flow_property",
        {key(p): p for p in old.flow_properties},
        {key(p): p for p in new.flow_properties},
        put,
        lambda p: p.flow_id,
    )


def _units(old: DatabaseFolder, new: DatabaseFolder) -> list[Difference]:
    def put(folder: DatabaseFolder, key: str, record: Unit) -> DatabaseFolder:
        return replace(folder, units=tuple(record if u.name == key else u for u in folder.units))

    return _keyed("unit", {u.name: u for u in old.units}, {u.name: u for u in new.units}, put)


# --- what a score does not depend on -----------------------------------------------------------


def _cut_offs(old: DatabaseFolder, new: DatabaseFolder) -> list[Difference]:
    before = {(i.process_id, i.line_id): i for i in old.cut_offs}
    after = {(i.process_id, i.line_id): i for i in new.cut_offs}
    found: list[Difference] = []
    for key in sorted(set(before) | set(after)):
        o, n = before.get(key), after.get(key)
        if o == n:
            continue
        item = f"{key[0]} line {key[1]}"
        if o is None and n is not None:
            text = f"added: cut off by {n.criterion.value if n.criterion else 'a criterion'}"
            if n.share is not None:
                text += f" ({n.share:.4g} of the inputs)"
            text += f", {n.reason}" if n.reason else ""
            found.append(
                Difference(
                    f"cut_off {item}", "cut_off", Change.added, item, text, flow_id=n.flow_id
                )
            )
        elif n is None and o is not None:
            found.append(
                Difference(
                    f"cut_off {item}",
                    "cut_off",
                    Change.removed,
                    item,
                    "removed: no longer cut off",
                    flow_id=o.flow_id,
                )
            )
        elif o is not None and n is not None:
            changes = _field_changes(_dump(o), _dump(n))
            found.append(
                Difference(
                    f"cut_off {item}",
                    "cut_off",
                    Change.changed,
                    item,
                    ", ".join(_arrow(c.name, c.old, c.new) for c in changes),
                    fields=changes,
                    flow_id=n.flow_id,
                )
            )
    return found


_PARAMETER_FIELDS = ("value", "formula", "unit", "uncertainty")
"""The fields of a parameter that can move a score. Its comment is listed apart."""

_NO_FACTOR_FIELDS = {k: k for k in ("reason", "who", "when", "flow_name")}
"""The fields of a row that can change, in the order a person reads them."""


def _decided(row: NoFactor) -> str:
    """`Sam, 2026-10-12`: who acknowledged the flow, and when."""
    return f"{row.who}, {row.when.date().isoformat()}"


def _no_factor(old: DatabaseFolder, new: DatabaseFolder) -> list[Difference]:
    return no_factor_rows(old.no_factor, new.no_factor)


def no_factor_rows(old: Iterable[NoFactor], new: Iterable[NoFactor]) -> list[Difference]:
    """The rows of `no_factor.csv`, found by flow and method. A row says why a flow counts 0, so
    it moves the input hash and no score."""
    before = {(r.flow_id, r.method): r for r in old}
    after = {(r.flow_id, r.method): r for r in new}
    found: list[Difference] = []
    for key in sorted(set(before) | set(after)):
        o, n = before.get(key), after.get(key)
        if o == n:
            continue
        flow_id, method = key
        fields: tuple[FieldChange, ...] = ()
        if o is None:
            assert n is not None
            change = Change.added
            text = f"added for method {method}: {n.reason} ({_decided(n)})"
        elif n is None:
            change = Change.removed
            text = f"removed for method {method}: it said {o.reason} ({_decided(o)})"
        else:
            change = Change.changed
            fields = _field_changes(_dump(o), _dump(n), _NO_FACTOR_FIELDS)
            text = f"for method {method}: " + ", ".join(
                _arrow(c.name, c.old, c.new) for c in fields
            )
        found.append(
            Difference(
                f"no_factor {flow_id} {method}",
                "no_factor",
                change,
                flow_id,
                text,
                fields=fields,
                flow_id=flow_id,
            )
        )
    return found


def _study_fields(old: DatabaseFolder, new: DatabaseFolder) -> list[Difference]:
    """The fields of database.yaml that differ: `database description`."""
    return [
        Difference(
            f"database {c.name}",
            "database",
            Change.changed,
            c.name,
            f"{_show(c.old)} → {_show(c.new)}",
            fields=(c,),
        )
        for c in _field_changes(_dump(old.database), _dump(new.database))
    ]


# --- a study -----------------------------------------------------------------------------------


def in_study(folder: DatabaseFolder) -> DatabaseFolder:
    """The `apply` of a product's amount, a difference of the study itself. Its effect is the
    change of the amount times what one unit scores, worked out from the scores: no database is
    changed to find it."""
    return folder


def study_differences(old: StudyFolder, new: StudyFolder) -> tuple[Difference, ...]:
    """What differs in the study itself, in the order a person reads them: the amount of each
    product in both, then what no score depends on (`no_factor.csv` rows, scenarios and the
    fields of study.yaml). A product only one side has is not a difference: the diff lists it as
    a new or removed product. Example: `product tshirt:tshirt`, `amount 1 → 2`."""
    before = {p.process: p.amount for p in old.products}
    found: list[Difference] = []
    for product in new.products:
        amount = before.get(product.process)
        if amount is None or amount == product.amount:
            continue
        found.append(
            Difference(
                f"product {product.process}",
                "product",
                Change.changed,
                product.process,
                f"amount {_show(float(amount))} → {_show(float(product.amount))}",
                fields=(FieldChange("amount", amount, product.amount),),
                flow_id=_made_by(new, product.process),
                apply=in_study,
            )
        )
    found += no_factor_rows(old.no_factor, new.no_factor)
    found += _scenarios(old, new)
    found += _study_yaml(old, new)
    return tuple(found)


def _made_by(study: StudyFolder, process_id: str) -> str | None:
    """The flow `process_id` makes, when it is a process of a database of `study`."""
    database = study.database_of(process_id)
    if database is None:
        return None
    return next(p for p in database.processes if p.id == process_id).reference_exchange.flow_id


def _scenarios(old: StudyFolder, new: StudyFolder) -> list[Difference]:
    """The scenario files, by name. A diff scores no scenario, so none moves a score."""
    before = {s.name: s for s in old.scenarios}
    after = {s.name: s for s in new.scenarios}
    found: list[Difference] = []
    for name in sorted(before.keys() | after.keys()):
        o, n = before.get(name), after.get(name)
        if o == n:
            continue
        if o is None:
            change, text = Change.added, "added"
        elif n is None:
            change, text = Change.removed, "removed"
        else:
            change, text = Change.changed, _scenario_text(o, n)
        found.append(Difference(f"scenario {name}", "scenario", change, name, text))
    return found


_LINE_CHANGE_FIELDS = {
    "amount": "amount",
    "formula": "formula",
    "unit": "unit",
    "supplier_id": "supplier",
}
_PARAMETER_CHANGE_FIELDS = {"value": "value", "formula": "formula"}


def _scenario_text(old: Scenario, new: Scenario) -> str:
    """What differs between two readings of a scenario file, on one line, each change as a line
    change of a database reads: `dyes:reactive-dye line e03 amount 3.5 → 3; description "..." →
    "..."`. A line change is matched by its process and line, a parameter change by its database
    and id. One only the new file has is `added`, with what it sets, and one only the old file
    has is `removed`, with what it set."""
    before = {_change_key(c): c for c in old.changes}
    after = {_change_key(c): c for c in new.changes}
    parts: list[str] = []
    for key, change in after.items():
        was = before.get(key)
        if was is None:
            parts.append(f"{_change_label(change)} added, {_change_sets(change)}")
        elif was != change:
            fields = _field_changes(_dump(was), _dump(change), _change_fields(change))
            arrows = ", ".join(_arrow(c.name, c.old, c.new) for c in fields)
            parts.append(f"{_change_label(change)} {arrows}")
    parts += [
        f"{_change_label(change)} removed, it set {_change_sets(change)}"
        for key, change in before.items()
        if key not in after
    ]
    if old.description != new.description:
        parts.append(f"description {_quoted(old.description)} → {_quoted(new.description)}")
    return "; ".join(parts) or "the same changes, in another order"


def _change_key(change: LineChange | ParameterChange) -> tuple[str, str | None, str]:
    if isinstance(change, LineChange):
        return ("line", change.process_id, change.line_id)
    return ("parameter", change.database, change.parameter)


def _change_label(change: LineChange | ParameterChange) -> str:
    """`dyes:reactive-dye line e03`, or `parameter kwh_per_kg of dyes`."""
    if isinstance(change, LineChange):
        return change.label
    held = f" of {change.database}" if change.database else ""
    return f"parameter {change.parameter}{held}"


def _change_fields(change: LineChange | ParameterChange) -> dict[str, str]:
    return _LINE_CHANGE_FIELDS if isinstance(change, LineChange) else _PARAMETER_CHANGE_FIELDS


def _change_sets(change: LineChange | ParameterChange) -> str:
    """What a change sets: `amount 3.5`, `supplier toy:solar, amount 1`."""
    values = _dump(change)
    return ", ".join(
        f"{name} {_show(values[key])}"
        for key, name in _change_fields(change).items()
        if values.get(key) is not None
    )


def _study_yaml(old: StudyFolder, new: StudyFolder) -> list[Difference]:
    """The fields of study.yaml that differ, its products apart: `study goal`."""
    if old.record is None or new.record is None:
        return []
    return [
        Difference(
            f"study {c.name}",
            "study",
            Change.changed,
            c.name,
            f"{_show(c.old)} → {_show(c.new)}",
            fields=(c,),
        )
        for c in _field_changes(
            _dump(old.record, exclude={"products"}), _dump(new.record, exclude={"products"})
        )
    ]
