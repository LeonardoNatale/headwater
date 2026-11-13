"""Building and changing a database from Python, as Brightway's `new_activity`, `new_exchange` and
`copy` do, while the database's files stay what the database is.

Example: the toy T-shirt in a few calls.

    database = hw.Database.new("tshirt", bg, pin="toy-background@fixture", methods=["toy-climate"])
    tshirt = database.new_process("tshirt", name="T-shirt", location="GLO", product="T-shirt",
                               unit="unit")
    tshirt.new_exchange("input", "toy:cotton-fabric", 0.25, "kg", supplier="toy:fabric-production")
    tshirt.new_exchange("input", "toy:electricity", 0.5, "kWh",
                        supplier="toy:electricity-production")
    database.save()

Each call changes the text of a file in memory, as a person would by hand: a new row of
processes.csv or exchanges.csv at its place, a row written again, a YAML entry replaced on its own
lines. `save` writes the changed files into a copy
of the folder, reads the copy as `hw.open` does and checks it against the background as
`Database.validate` does. Only then are the files moved into place, so an edit that makes the
database invalid raises, naming the problem, and writes nothing.
"""

import copy
import functools
import math
import os
import re
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, ValidationError

from headwater.model import (
    Database,
    Exchange,
    Flow,
    InvalidDataError,
    Parameter,
    Problem,
    ProcessDetails,
    Role,
    did_you_mean,
)
from headwater.study._cut_offs import COLUMNS as CUT_OFF_COLUMNS
from headwater.study._cut_offs import FILE as CUT_OFFS
from headwater.study._exchanges import EXCHANGE_OPTIONAL
from headwater.study._files import number_or_formula, parse_yaml, problems_from_validation
from headwater.study._overrides import COLUMNS as OVERRIDE_COLUMNS
from headwater.study._parameters import FILE as PARAMETERS
from headwater.study._tables import EXCHANGES, PROCESSES
from headwater.study._uses import on_disk, users_of, uses_on_disk
from headwater.study._write import (
    Cells,
    csv_rows,
    csv_text,
    database_fields,
    database_yaml,
    edit_csv,
    entry_text,
    exchanges_csv,
    flow_cells,
    flows_csv,
    number,
    process_cells,
    processes_csv,
    put_yaml,
    used_columns,
    yaml_value,
)
from headwater.study.folders import in_used
from headwater.study.reader import read_database, read_uses

if TYPE_CHECKING:
    from headwater.query.background import Background
    from headwater.query.database import Database as OpenDatabase

_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_LINE = re.compile(r"e(\d+)")


class DatabaseEditor:
    """Changes to the files of one database, kept in memory until `save` checks and writes them.

    Get one with `hw.open(path).edit(background)`, or `hw.Database.new(path, background)` for
    a database that does not exist yet. `changed` lists the files `save` would write.
    """

    def __init__(
        self, path: Path | str, background: "Background", *, database_yaml: str | None = None
    ) -> None:
        self.path = Path(path)
        self.background = background
        self._files = _Files(self.path)
        if database_yaml is not None:
            self._files.put("database.yaml", database_yaml)

    def __repr__(self) -> str:
        return f"DatabaseEditor({self.name!r} at {self.path}, {len(self.changed)} files changed)"

    @property
    def name(self) -> str:
        return _database(self._files.text("database.yaml") or "").name

    @property
    def changed(self) -> tuple[str, ...]:
        """The files `save` would write or remove, as paths in the folder."""
        return self._files.changed

    # --- database.yaml and flows ------------------------------------------------------------------

    def set(self, **fields: object) -> None:
        """Change fields of database.yaml: `set(goal="...", uses=["dyes"])`. None takes out an
        optional field. Each field is replaced on its own line, and the rest of the file stays."""
        text = _set_fields(
            self._files.text("database.yaml") or "",
            "database.yaml",
            Database,
            fields,
            database_fields,
        )
        self._files.put("database.yaml", text)

    def new_flow(
        self,
        code: str,
        /,
        *,
        name: str,
        kind: str,
        unit: str,
        compartment: str | None = None,
        cas: str | None = None,
    ) -> str:
        """Add a flow to flows.csv and return its id: `new_flow("cod", ...)` in the database
        `tshirt` is `tshirt:cod`. A code with a colon is the whole id."""
        flow_id = code if ":" in code else f"{self.name}:{code}"
        fields = {"id": flow_id, "name": name, "kind": kind, "unit": unit}
        flow = _validated(Flow, {**fields, "compartment": compartment, "cas": cas}, flow_id)
        text = self._files.text("flows.csv")
        if text is not None and any(row.get("id") == flow_id for _, row in csv_rows(text)):
            raise ValueError(f"flow {flow_id} is already in flows.csv")
        cells = flow_cells(flow)
        self._files.put(
            "flows.csv", flows_csv([flow]) if text is None else edit_csv(text, add=[cells])
        )
        return flow_id

    # --- processes -----------------------------------------------------------------------------

    def new_process(
        self,
        code: str,
        /,
        *,
        name: str,
        location: str,
        unit: str,
        product: str | None = None,
        flow: str | None = None,
        kind: str = "ordinary_transforming_activity",
        amount: float = 1,
    ) -> "ProcessEditor":
        """Add a process, with its reference line e01, as Brightway's `new_activity`.

        `code` is its id in the tables and ends its id: `tshirt` is `tshirt:tshirt`. Its row of
        processes.csv goes before the first row with a greater id, and its line into
        exchanges.csv before the first row of a process with a greater id, so tables sorted by
        id stay sorted. It makes `amount` `unit` of `product`, a new flow of that name with the
        process's id, or of `flow`, the id of a flow that exists.
        """
        if (product is None) == (flow is None):
            raise ValueError(
                "give product, the name of a new flow the process makes, or flow, the id of a "
                "flow it makes, and not both"
            )
        if not _CODE.fullmatch(code):
            raise ValueError(
                f"code {code!r} is an id: use letters, digits, dots, underscores and hyphens"
            )
        process = ProcessEditor(self, self._files, code)
        if code in self._codes() or any(
            row.get("process") == code for _, row in self._files.rows(EXCHANGES)
        ):
            raise ValueError(f"process {process.id} is already in the database")
        fields = {"name": name, "location": location, "kind": kind}
        details = _validated(ProcessDetails, fields, f"process {process.id}")
        if flow is None:
            flow = self.new_flow(code, name=str(product), kind="product", unit=unit)
        reference = {"process": code, "line": "e01", "role": Role.reference.value, "flow": flow}
        reference |= {"amount": _amount(amount), "unit": unit, "supplier": ""}
        self._files.insert(PROCESSES, process_cells(code, details), processes_csv, "id", code)
        self._files.insert(EXCHANGES, reference, exchanges_csv, "process", code)
        return process

    def _codes(self) -> list[str]:
        """The ids in processes.csv as it is now."""
        return [row.get("id", "") for _, row in self._files.rows(PROCESSES)]

    def process(self, process_id: str) -> "ProcessEditor":
        """The process of this database with this id, `tshirt:tshirt`, or this code, `tshirt`."""
        code = process_id.removeprefix(f"{self.name}:")
        codes = self._codes()
        if code not in codes:
            raise ValueError(
                f"database {self.name} has no process {process_id!r}.{did_you_mean(code, codes)} "
                f"Its processes: {', '.join(codes)}"
            )
        return ProcessEditor(self, self._files, code)

    def remove_process(self, process_id: str) -> None:
        """Take out a process, its row of processes.csv and its rows of exchanges.csv, as
        Brightway's `act.delete()`. A process that another line buys from, a cut-off or a
        parameter names is refused, naming them."""
        process = self.process(process_id)
        named = [
            f"{EXCHANGES}:{n}"
            for n, row in self._files.rows(EXCHANGES)
            if row.get("process") != process.code and row.get("supplier") == process.id
        ]
        named += [
            f"{CUT_OFFS}:{n}"
            for n, row in self._files.rows(CUT_OFFS)
            if row.get("process") == process.id
        ]
        if process.id in (_parameters(self._files.text(PARAMETERS)).get("processes") or {}):
            named.append(PARAMETERS)
        _refuse_if_named(f"process {process.id}", named)
        self._files.edit(PROCESSES, lambda c: None if _key(c, "id") == (process.code,) else c)
        self._files.edit(EXCHANGES, lambda c: None if _key(c, "process") == (process.code,) else c)

    # --- parameters ----------------------------------------------------------------------------

    def set_parameter(
        self,
        name: str,
        value: float | str,
        *,
        process_id: str | None = None,
        unit: str | None = None,
        comment: str | None = None,
        distribution: str | None = None,
        scale: float | None = None,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> None:
        """Add a parameter, or change one: `set_parameter("fabric_kg", 0.3)`. A number is a
        value, any other text a formula. `process_id` makes it a parameter of that process.

        A parameter that exists keeps what is not given, and `""` takes out its unit, comment or
        a field of its distribution. Only the lines of what changed are written again.
        """
        number, formula = number_or_formula(value, f"parameter {name}")
        key, given = ("value", number) if number is not None else ("formula", str(formula))
        _validated(Parameter, {"name": name, "scope": process_id, key: given}, f"parameter {name}")
        path = ["database", name] if process_id is None else ["processes", process_id, name]
        text = self._files.text(PARAMETERS) or ""
        data = _parameters(text)
        old = _at(data, path)
        before = _entry_fields(old)
        # the value or the formula, in its place
        fields = {(key if k in ("value", "formula") else k): v for k, v in before.items()}
        fields[key] = given
        more = {"unit": unit, "comment": comment, "distribution": distribution}
        more |= {"scale": scale, "minimum": minimum, "maximum": maximum}
        for k, v in more.items():
            if v == "":
                fields.pop(k, None)
            elif v is not None:
                fields[k] = v
        entry: object = fields if len(fields) > 1 else given
        if entry == old:
            return
        expected = _with(data, path, entry)
        new = text
        if isinstance(old, Mapping) and len(fields) > 1:  # field by field
            for k in dict.fromkeys([*before, *fields]):
                if fields.get(k) != before.get(k):
                    line = f"{k}: {yaml_value(fields[k])}\n" if k in fields else None
                    new = put_yaml(new, [*path, k], line)
        if not _reads_as(new, expected):
            new = put_yaml(text, path, entry_text(name, entry))
        if not _reads_as(new, expected):
            raise _layout(PARAMETERS)
        self._files.put(PARAMETERS, new)

    def remove_parameter(self, name: str, *, process_id: str | None = None) -> None:
        """Take out a parameter. The file goes with the last one."""
        path = ["database", name] if process_id is None else ["processes", process_id, name]
        text = self._files.text(PARAMETERS) or ""
        data = _parameters(text)
        if _at(data, path) is None:
            where = f" of {process_id}" if process_id else ""
            raise ValueError(f"the database has no parameter {name!r}{where}")
        expected = _with(data, path, None)
        new = put_yaml(text, path, None)
        if not _reads_as(new, expected):
            raise _layout(PARAMETERS)
        self._files.put(PARAMETERS, new if _without_empty(expected) else None)

    # --- cut-offs and overrides ----------------------------------------------------------------

    def cut_off(
        self,
        process_id: str,
        line: str,
        *,
        criterion: str,
        share: float,
        reason: str,
        who: str,
        when: date | str,
    ) -> None:
        """Leave out a line that has no supplier, with a row of cut_offs.csv: why, its share of
        the criterion, who decided and when."""
        process = self.process(process_id)
        flow = process.exchange(line)["flow"]
        for n, row in self._files.rows(CUT_OFFS):
            if _key(row, "process", "line") == (process.id, line):
                raise ValueError(f"{process.id} line {line} is already cut off at {CUT_OFFS}:{n}")
        cells = {"process": process.id, "line": line, "flow": flow, "criterion": criterion}
        cells |= {"share": number(share), "reason": reason, "who": who}
        cells["when"] = when.isoformat() if isinstance(when, date) else when
        self._files.add(CUT_OFFS, CUT_OFF_COLUMNS, cells)

    def remove_cut_off(self, process_id: str, line: str) -> None:
        process_id = self.process(process_id).id
        key = (process_id, line)
        if not any(_key(row, "process", "line") == key for _, row in self._files.rows(CUT_OFFS)):
            raise ValueError(f"{process_id} line {line} is not cut off")
        self._files.edit(CUT_OFFS, lambda c: None if _key(c, "process", "line") == key else c)

    def override(self, process_id: str, *, file: str | None = None) -> "OverrideEditor":
        """The local override of a background process, as Brightway's `act.copy()`: the database
        buys from its own copy, with the lines this changes, and the background stays as it is.

        New rows go to `overrides/<file>.csv`, by default the process id with hyphens for colons,
        `toy-electricity-production.csv`. A row that exists stays in its file.
        """
        if process_id.startswith(f"{self.name}:"):
            raise ValueError(
                f"{process_id} is a process of the database: change its own lines with "
                f"process({process_id!r}). An override changes a process of the background."
            )
        name = (file or process_id.replace(":", "-")).removesuffix(".csv")
        if not _CODE.fullmatch(name):
            raise ValueError(
                f"file {name!r} names a file in overrides/: use letters, digits, dots, "
                "underscores and hyphens"
            )
        lines = self.background.processes([process_id])[process_id].exchanges
        return OverrideEditor(self._files, process_id, lines, f"overrides/{name}.csv")

    # --- save ----------------------------------------------------------------------------------

    def save(self) -> "OpenDatabase":
        """Check the database as changed and write the changed files. Returns the database as saved.

        The changed files are written into a copy of the folder first, and the copy is read as
        `hw.open` reads a database and checked against the background as `Database.validate` checks
        it, the databases it uses included, and so is each database next to it that uses it. Any
        error raises `InvalidDataError` naming it, and nothing is written. So does a file that
        changed on disk after it was read: a hand edit is never overwritten. A file that did not
        change is not written.
        """
        from headwater.query._internals import internals
        from headwater.query.database import Database as OpenDatabase

        changes = {rel: self._files.texts[rel] for rel in self.changed}
        if not changes:
            return OpenDatabase(read_database(self.path))
        moved = [rel for rel in changes if self._files.on_disk(rel) != self._files.disk[rel]]
        if moved:
            raise InvalidDataError(
                Problem(
                    "file-changed",
                    rel,
                    "changed on disk after this editor read it, so saving would overwrite that "
                    "change. Open the database again, and make the edit again.",
                    location=rel,
                )
                for rel in moved
            )
        made = next((p for p in reversed(self.path.parents) if not p.exists()), None)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        holder = Path(tempfile.mkdtemp(prefix=f".tmp-{self.path.name}-", dir=self.path.parent))
        temp = holder / self.path.name  # the same name, for the databases it uses
        try:
            if self.path.is_dir():
                shutil.copytree(self.path, temp, symlinks=True)
            _write(temp, changes)
            folder = read_database(temp, sibling=on_disk(self.path))
            internals(self.background).require_pin(folder.database.background, folder.database.name)
            OpenDatabase(folder).validate(self.background).raise_if_errors()
            self._check_users(temp)
            if self.path.is_dir() and any(self.path.iterdir()):
                for rel, text in changes.items():
                    if text is not None:
                        (self.path / rel).parent.mkdir(parents=True, exist_ok=True)
                        os.replace(temp / rel, self.path / rel)
                _write(self.path, {r: t for r, t in changes.items() if t is None})
            else:
                if self.path.is_dir():
                    self.path.rmdir()
                temp.rename(self.path)
        except BaseException:
            if made is not None:
                shutil.rmtree(made, ignore_errors=True)
            raise
        finally:
            shutil.rmtree(holder, ignore_errors=True)
        self._files.texts.update(changes)
        self._files.disk.update(changes)
        return OpenDatabase(replace(folder, path=self.path))

    def _check_users(self, temp: Path) -> None:
        """Check each database next to this one that uses it, with this one as changed in `temp`.
        Raises InvalidDataError naming their problems, at their place: `../tshirt/...`."""
        from headwater.query.database import Database as OpenDatabase

        name = self.path.name
        found = on_disk(self.path)

        def sibling(other: str) -> Path | None:
            return temp if other == name else found(other)

        parent = self.path.resolve().parent
        for user, _ in users_of(name, uses_on_disk(parent, read_uses)):
            try:
                folder = read_database(parent / user, sibling=sibling)
                OpenDatabase(folder).validate(self.background).raise_if_errors()
            except InvalidDataError as error:
                raise InvalidDataError(
                    replace(p, location=in_used(user, p.location)) for p in error.problems
                ) from None


class _Files:
    """The text of each file of a database folder read so far: as it is on disk, and as changed."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.disk: dict[str, str | None] = {}
        """Each file as it is on disk, or None when it is not there."""
        self.texts: dict[str, str | None] = {}
        """Each file as it is now, or None when it is removed."""

    @property
    def changed(self) -> tuple[str, ...]:
        return tuple(sorted(r for r, text in self.texts.items() if text != self.disk[r]))

    def text(self, rel: str) -> str | None:
        if rel not in self.texts:
            self.disk[rel] = self.texts[rel] = self.on_disk(rel)
        return self.texts[rel]

    def put(self, rel: str, text: str | None) -> None:
        self.text(rel)
        self.texts[rel] = text

    def listed(self, folder: str, suffix: str) -> list[str]:
        """The files of a folder of the database with this suffix, as they are now."""
        here = self.root / folder
        names = {
            f"{folder}/{p.name}"
            for p in (here.iterdir() if here.is_dir() else ())
            if p.suffix == suffix and p.is_file() and not p.name.startswith(".")
        }
        names |= {r for r in self.texts if r.startswith(f"{folder}/") and r.endswith(suffix)}
        return sorted(r for r in names if self.text(r) is not None)

    def rows(self, rel: str) -> list[tuple[int, Cells]]:
        return csv_rows(self.text(rel) or "")

    def add(
        self, rel: str, columns: tuple[str, ...], cells: Cells, optional: tuple[str, ...] = ()
    ) -> None:
        """Add a row at the end of a CSV file, or write the file with it."""
        text = self.text(rel)
        if text is None or not text.strip():
            text = csv_text(used_columns(columns, optional, [cells]), [cells])
        else:
            text = edit_csv(text, add=[cells])
        self.put(rel, text)

    def insert(
        self,
        rel: str,
        cells: Cells,
        new: Callable[[Sequence[Cells]], str],
        column: str,
        code: str,
        after: int | None = None,
    ) -> None:
        """Add a row to a table of processes, or write the table with it. The row goes after the
        line `after` when it is given, or else before the first row whose `column` is greater
        than `code`, or at the end."""
        text = self.text(rel)
        if text is None or not text.strip():
            self.put(rel, new([cells]))
            return

        def before(line: int, row: Cells) -> bool:
            if after is not None:
                return line > after
            return row.get(column, "").strip() > code

        self.put(rel, edit_csv(text, add=[cells], before=before))

    def on_disk(self, rel: str) -> str | None:
        """The file as it is on disk now, which may not be what was read."""
        path = self.root / rel
        return path.read_bytes().decode("utf-8") if path.is_file() else None

    def edit(self, rel: str, edit: Callable[[Cells], Cells | None]) -> None:
        """Change the rows of a CSV file, as `edit_csv` does. The file goes with its last row."""
        new = edit_csv(self.text(rel) or "", edit)
        self.put(rel, new if csv_rows(new) else None)


class ProcessEditor:
    """One process of a database: its row of processes.csv and its rows of exchanges.csv."""

    def __init__(self, database: DatabaseEditor, files: _Files, code: str) -> None:
        self._database = database
        self._files = files
        self.code = code

    def __repr__(self) -> str:
        return f"ProcessEditor({self.id!r}, {len(self.exchanges)} exchanges)"

    @property
    def id(self) -> str:
        return f"{self._database.name}:{self.code}"

    @property
    def exchanges(self) -> tuple[Cells, ...]:
        """Each line as written in exchanges.csv, column by column, without its `process`:
        `{"line": "e02", "amount": "fabric_kg", ...}`. An amount is its text, a number or a
        formula."""
        return tuple({k: v for k, v in row.items() if k != "process"} for _, row in self._mine())

    def _mine(self) -> list[tuple[int, Cells]]:
        """The rows of exchanges.csv of this process, with their lines."""
        return [(n, r) for n, r in self._files.rows(EXCHANGES) if r.get("process") == self.code]

    def _is(self, cells: Mapping[str, str], line: str | None = None) -> bool:
        if line is None:
            return _key(cells, "process") == (self.code,)
        return _key(cells, "process", "line") == (self.code, line)

    def exchange(self, line: str) -> Cells:
        """The exchange with this line id, as `exchanges` gives it."""
        found = {row.get("line", ""): row for row in self.exchanges}
        if line not in found:
            raise ValueError(
                f"{self.id} has no line {line!r}.{did_you_mean(line, found)} "
                f"Its lines: {', '.join(found)}"
            )
        return found[line]

    def set(self, **details: object) -> None:
        """Change the name, location, kind, time_start or time_end of the process: the cells of
        its row of processes.csv that change, and no other."""
        [row] = [r for _, r in self._files.rows(PROCESSES) if _key(r, "id") == (self.code,)]
        what = f"process {self.id}"
        data = {c: row.get(c, "") for c in ("name", "location", "kind")}
        data |= {c: row[c] for c in ("time_start", "time_end") if row.get(c)}
        before = _details(_validated(ProcessDetails, data, what))
        merged = {k: v for k, v in {**data, **details}.items() if v is not None}
        new = _validated(ProcessDetails, merged, what)
        cells = process_cells(self.code, new)
        changes = {k: cells[k] for k in details if before.get(k) != _details(new).get(k)}
        self._files.edit(
            PROCESSES, lambda c: _changed(c, changes) if _key(c, "id") == (self.code,) else c
        )

    def new_exchange(
        self,
        role: str,
        flow: str,
        amount: float | str,
        unit: str,
        *,
        supplier: str | None = None,
        flow_name: str | None = None,
        comment: str | None = None,
        distribution: str | None = None,
        scale: float | None = None,
        minimum: float | None = None,
        maximum: float | None = None,
    ) -> str:
        """Add a line, as Brightway's `act.new_exchange(...).save()`, and return its id, the
        next free one: `e04` after e03. `role` is always given, never read from a sign: `input`,
        `emission`, `waste_to_treatment` and the others of the design. `amount` is a number or a
        formula of the database's parameters."""
        mine = self._mine()
        ids = [row.get("line", "") for _, row in mine]
        numbers = [int(m.group(1)) for i in ids if (m := _LINE.fullmatch(i))]
        n = max(numbers, default=0) + 1
        while f"e{n:02d}" in ids:
            n += 1
        cells = {"process": self.code, "line": f"e{n:02d}", "role": _role(role), "flow": flow}
        cells |= {"amount": _amount(amount), "unit": unit, "supplier": supplier or ""}
        cells |= _cells(flow_name=flow_name, comment=comment, distribution=distribution)
        cells |= _cells(scale=scale, minimum=minimum, maximum=maximum)
        last = max((n for n, _ in mine), default=None)
        self._files.insert(EXCHANGES, cells, exchanges_csv, "process", self.code, after=last)
        return cells["line"]

    def set_exchange(
        self,
        line: str,
        *,
        role: str | None = None,
        flow: str | None = None,
        amount: float | str | None = None,
        unit: str | None = None,
        supplier: str | None = None,
        flow_name: str | None = None,
        comment: str | None = None,
        distribution: str | None = None,
        scale: float | str | None = None,
        minimum: float | str | None = None,
        maximum: float | str | None = None,
    ) -> None:
        """Change cells of a line, as setting `exc["amount"]` and `exc.save()` in Brightway.
        What is not given stays, and `""` empties a cell: `set_exchange("e02", supplier="")`. An
        amount equal to the one written keeps its text."""
        self.exchange(line)
        changes = _cells(
            role=_role(role) if role is not None else None,
            flow=flow,
            amount=_amount(amount) if amount is not None else None,
            unit=unit,
            supplier=supplier,
            flow_name=flow_name,
            comment=comment,
            distribution=distribution,
            scale=scale,
            minimum=minimum,
            maximum=maximum,
            keep_blank=True,
        )
        self._files.edit(EXCHANGES, lambda c: _changed(c, changes) if self._is(c, line) else c)

    def remove_exchange(self, line: str) -> None:
        """Take out a line, as Brightway's `exc.delete()`. A line a cut-off names is refused,
        naming its row."""
        self.exchange(line)
        _refuse_if_named(
            f"{self.id} line {line}",
            [
                f"{CUT_OFFS}:{n}"
                for n, row in self._files.rows(CUT_OFFS)
                if _key(row, "process", "line") == (self.id, line)
            ],
        )
        self._files.edit(EXCHANGES, lambda c: None if self._is(c, line) else c)


class OverrideEditor:
    """The local override of one background process: the rows of `overrides/` that change its
    lines for this database only (design.md, Overrides and scenarios).

    It stands for Brightway's `act.copy()`. The database's purchases from the process use the copy
    with no relinking, and a copy cannot add or remove lines or change its reference line.
    """

    def __init__(
        self, files: _Files, process_id: str, lines: tuple[Exchange, ...], file: str
    ) -> None:
        self._files = files
        self.process_id = process_id
        self._lines = {e.line_id: e for e in lines}
        self.file = file
        """Where a new row goes."""

    def __repr__(self) -> str:
        return f"OverrideEditor({self.process_id!r}, {self.file})"

    def set_exchange(
        self,
        line: str,
        *,
        amount: float | str | None = None,
        unit: str | None = None,
        supplier: str | None = None,
        comment: str | None = None,
        distribution: str | None = None,
        scale: float | str | None = None,
        minimum: float | str | None = None,
        maximum: float | str | None = None,
    ) -> None:
        """Change a line of the copy: `set_exchange("e02", amount=0.3)`. The row says the whole new
        line, so its role, flow, unit and supplier come from the background's line unless they
        are given. A row that changes nothing is not written."""
        changes = _cells(
            amount=_amount(amount) if amount is not None else None,
            unit=unit,
            supplier=supplier,
            comment=comment,
            distribution=distribution,
            scale=scale,
            minimum=minimum,
            maximum=maximum,
            keep_blank=True,
        )
        found = self._file_of(line)
        if found is not None:
            self._files.edit(found, lambda c: _changed(c, changes) if self._is(c, line) else c)
            return
        original = self._lines.get(line)
        if original is None:
            raise ValueError(
                f"{self.process_id} has no line {line!r}.{did_you_mean(line, self._lines)} "
                f"Its lines: {', '.join(self._lines)}"
            )
        if original.role is Role.reference:
            raise ValueError(
                f"{original.label} is the reference line, which is what the process makes. An "
                "override changes the other lines."
            )
        base = {"process": self.process_id, "line": line, "role": original.role.value}
        base |= {"flow": original.flow_id, "amount": number(original.amount)}
        base |= {"unit": original.unit, "supplier": original.supplier_id or ""}
        row = _changed(base, changes)
        if row != base:
            self._files.add(self.file, OVERRIDE_COLUMNS, row, EXCHANGE_OPTIONAL)

    def reset_exchange(self, line: str) -> None:
        """Take out the row of a line: the copy has the background's line again. The file goes
        with its last row."""
        found = self._file_of(line)
        if found is None:
            raise ValueError(f"{self.process_id} line {line} is not overridden")
        self._files.edit(found, lambda c: None if self._is(c, line) else c)

    def _is(self, cells: Mapping[str, str], line: str) -> bool:
        return _key(cells, "process", "line") == (self.process_id, line)

    def _file_of(self, line: str) -> str | None:
        for rel in self._files.listed("overrides", ".csv"):
            if any(self._is(row, line) for _, row in self._files.rows(rel)):
                return rel
        return None


def new_database(
    path: Path | str, background: "Background", fields: Mapping[str, object]
) -> DatabaseEditor:
    """An editor for a database that does not exist yet, with its database.yaml in memory. The
    folder must not exist, or be empty."""
    root = Path(path)
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ValueError(f"{root} already holds files: a new database goes into an empty folder")
    data = {k: v for k, v in fields.items() if v is not None}
    data.setdefault("name", root.name)
    if "background" not in data:
        release = background.release
        if release is None:
            raise ValueError(
                "a background read from a folder has no hash to pin: give pin, for example "
                "pin='toy-background@fixture'"
            )
        data["background"] = f"{release.name}@{release.hash[:12]}"
    return DatabaseEditor(
        root, background, database_yaml=database_yaml(_validated(Database, data, "database.yaml"))
    )


# --- helpers -----------------------------------------------------------------------------------


def _validated[M: BaseModel](model: type[M], data: Mapping[str, object], what: str) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as error:
        raise InvalidDataError(problems_from_validation(error, prefix=what, model=model)) from None


@functools.lru_cache(maxsize=4)
def _database(text: str) -> Database:
    problems: list[Problem] = []
    data = parse_yaml(text, "database.yaml", problems)
    if data is None:
        raise InvalidDataError(problems)
    return _validated(Database, data, "database.yaml")


def _set_fields(
    text: str,
    rel: str,
    model: type[BaseModel],
    fields: Mapping[str, object],
    dump: Callable[[Any], dict[str, Any]],
) -> str:
    """The YAML `text` with each of `fields` replaced on its own line, checked by `model`."""
    problems: list[Problem] = []
    data = parse_yaml(text, rel, problems)
    if data is None:
        raise InvalidDataError(problems)
    merged = {**data, **fields}
    before = dump(_validated(model, data, rel))
    after = dump(_validated(model, {k: v for k, v in merged.items() if v is not None}, rel))
    for key in fields:
        if before.get(key) != after.get(key):
            line = f"{key}: {yaml_value(after[key])}\n" if key in after else None
            text = put_yaml(text, [key], line)
    read = parse_yaml(text, rel, problems)
    if read is None or dump(_validated(model, read, rel)) != after:
        raise _layout(rel)
    return text


def _details(details: ProcessDetails) -> dict[str, Any]:
    return details.model_dump(exclude_none=True)


def _layout(rel: str) -> InvalidDataError:
    return InvalidDataError(
        [
            Problem(
                "yaml-layout",
                rel,
                "cannot be changed in place. A field is found by its line, `key: value` at the "
                "start of a line, or under its parent, two spaces in. Write it that way, then "
                "make the change again.",
                location=rel,
            )
        ]
    )


def _parameters(text: str | None) -> dict[str, Any]:
    problems: list[Problem] = []
    data = parse_yaml(text or "", PARAMETERS, problems)
    if data is None:
        raise InvalidDataError(problems)
    return data


def _at(data: Mapping[str, Any], path: list[str]) -> object:
    found: object = data
    for key in path:
        if not isinstance(found, Mapping):
            return None
        found = cast(Mapping[str, object], found).get(key)
    return found


def _with(data: Mapping[str, Any], path: list[str], entry: object) -> dict[str, Any]:
    """`data` with the entry at `path` set, or taken out when `entry` is None."""
    out = copy.deepcopy(dict(data))
    node = out
    for key in path[:-1]:
        child = node.get(key)
        if not isinstance(child, dict):
            child = node[key] = {}
        node = cast(dict[str, Any], child)
    if entry is None:
        node.pop(path[-1], None)
    else:
        node[path[-1]] = entry
    return out


def _without_empty(data: object) -> object:
    """`data` without the keys that hold nothing: `database:` with no parameters under it."""
    if not isinstance(data, Mapping):
        return data
    kept = {k: _without_empty(v) for k, v in cast(Mapping[str, object], data).items()}
    return {k: v for k, v in kept.items() if v not in (None, {})}


def _reads_as(text: str, expected: Mapping[str, Any]) -> bool:
    """Whether the YAML `text` reads as `expected`: an edit in place changed only what it meant
    to."""
    problems: list[Problem] = []
    read = parse_yaml(text, PARAMETERS, problems)
    return read is not None and _without_empty(read) == _without_empty(expected)


def _entry_fields(entry: object) -> dict[str, object]:
    """The fields of a parameter's entry as read: `0.25` is `{"value": 0.25}`."""
    if isinstance(entry, Mapping):
        return {str(k): v for k, v in cast(Mapping[object, object], entry).items()}
    if entry is None:
        return {}
    return {"formula" if isinstance(entry, str) and _is_formula(entry) else "value": entry}


def _is_formula(value: float | str) -> bool:
    if isinstance(value, str):
        try:
            float(value)
        except ValueError:
            return True
    return False


def _amount(value: float | str) -> str:
    """The cell of an amount: a number as short as it reads back, or a formula as written."""
    if isinstance(value, bool):
        raise ValueError(f"an amount is a number or a formula, got {value!r}")
    if isinstance(value, str):
        return value.strip()
    if not math.isfinite(value):
        raise ValueError(f"an amount must be a finite number, got {value!r}")
    return number(value)


def _role(role: str) -> str:
    roles = [r.value for r in Role]
    if role not in roles:
        raise ValueError(
            f"unknown role {role!r}.{did_you_mean(role, roles)} Roles: {', '.join(roles)}"
        )
    return role


def _cells(*, keep_blank: bool = False, **values: object) -> Cells:
    """Cells from the values given: a number is written as short as it reads back. None is not
    given, and "" an empty cell, kept only with `keep_blank`."""
    cells: Cells = {}
    for column, value in values.items():
        if value is None or (value == "" and not keep_blank):
            continue
        cells[column] = number(value) if isinstance(value, int | float) else str(value).strip()
    return cells


def _changed(cells: Cells, changes: Cells) -> Cells:
    """`cells` with `changes` made. An amount equal to the one written keeps its text."""
    new = dict(cells)
    for column, value in changes.items():
        old = cells.get(column, "").strip()
        if column == "amount" and _same_number(old, value):
            continue
        if old != value:
            new[column] = value
    return new


def _same_number(a: str, b: str) -> bool:
    try:
        return float(a) == float(b)
    except ValueError:
        return False


def _key(cells: Mapping[str, str], *columns: str) -> tuple[str, ...]:
    """The cells of these columns, stripped: what a row is found by."""
    return tuple(cells.get(c, "").strip() for c in columns)


def _refuse_if_named(what: str, places: list[str]) -> None:
    if places:
        raise ValueError(
            f"{what} is named at {', '.join(places)}. Change or remove those rows first."
        )


def _write(root: Path, changes: Mapping[str, str | None]) -> None:
    """Write or remove each file under `root`. A folder a removal leaves empty goes too."""
    for rel, text in changes.items():
        path = root / rel
        if text is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode("utf-8"))
            continue
        path.unlink(missing_ok=True)
        if path.parent != root and path.parent.is_dir() and not any(path.parent.iterdir()):
            path.parent.rmdir()
