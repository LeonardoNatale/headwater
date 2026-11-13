"""The two tables of a database or a background folder: `processes.csv` and `exchanges.csv` (D48).

Example: the toy T-shirt is one row of processes.csv and three rows of exchanges.csv.

    processes.csv
    id,name,location,kind
    tshirt,T-shirt,GLO,ordinary_transforming_activity

    exchanges.csv
    process,line,role,flow,amount,unit,supplier
    tshirt,e01,reference,tshirt:tshirt,1,unit,
    tshirt,e02,input,toy:cotton-fabric,0.25,kg,toy:fabric-production
    tshirt,e03,input,toy:electricity,0.5,kWh,toy:electricity-production

`id` and `process` are the part of the process id after `<namespace>:`. Rows may come in any
order: the processes are read sorted by id, and each process's lines in the order of their rows.
"""

from collections import defaultdict
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from headwater.model import Exchange, InvalidDataError, Problem, Process, ProcessDetails
from headwater.study._exchanges import (
    EXCHANGE_COLUMNS,
    EXCHANGE_OPTIONAL,
    Formulas,
    read_exchange,
)
from headwater.study._files import Row, problems_from_validation, read_csv

PROCESSES = "processes.csv"
EXCHANGES = "exchanges.csv"
OLD_FOLDER = "processes"
"""The folder of the layout before D48: one YAML and one CSV per process."""

PROCESS_COLUMNS = ("id", "name", "location", "kind")
PROCESS_OPTIONAL = ("time_start", "time_end", "comment")
"""`comment` is text for people, as a comment in a process YAML was: it is not read into the
records."""
TABLE_COLUMNS = ("process", *EXCHANGE_COLUMNS)
TABLE_OPTIONAL = EXCHANGE_OPTIONAL

CONVERT = "headwater database convert"


def old_layout(root: Path) -> Problem | None:
    """The problem of a folder that still holds `processes/`, the layout before D48, which no
    command reads: it names the command that converts it."""
    if not (root / OLD_FOLDER).is_dir():
        return None
    if (root / PROCESSES).exists() or (root / EXCHANGES).exists():
        message = (
            f"{root} holds processes/ next to {PROCESSES} and {EXCHANGES}. The two tables are "
            "the processes (D48): remove processes/."
        )
    else:
        message = (
            f"{root} holds processes/, one YAML and one CSV per process: the layout before "
            f"headwater 0.2.0. Convert it once with `{CONVERT} {root}`, which writes "
            f"{PROCESSES} and {EXCHANGES} and checks that every process reads the same (D48)."
        )
    return Problem("old-layout", "processes/", message, location="processes")


def refuse_old_layout(root: Path) -> None:
    found = old_layout(root)
    if found is not None:
        raise InvalidDataError([found])


class Layout(Protocol):
    """Where a folder's processes are read from."""

    def ids(self) -> list[str]:
        """The ids of its processes, the part after `<namespace>:`, sorted."""
        ...

    def processes(self, namespace: str, formulas: Formulas | None) -> list[Process]:
        """Its processes, sorted by id. Each problem goes into the list it was made with."""
        ...


class TableLayout:
    """The processes of a folder, read from its two tables. The rows are read once, when it is
    made, and each problem goes into `problems`."""

    def __init__(self, root: Path, problems: list[Problem]) -> None:
        self.root = root
        self.problems = problems
        self._rows: dict[str, Row] = {}
        # Whether processes.csv read with no problem, and the processes with a row of
        # exchanges.csv that did not. A row that did not read is missing, so the errors it would
        # cause, a line with no process or a process with no reference line, would only confuse:
        # those processes are left out, as a process with a line that does not read is.
        self._processes_read = False
        self._failed: set[str] | None = set()  # None: exchanges.csv did not read at all
        self._lines: dict[str, list[Row]] = defaultdict(list)
        self._read()

    def ids(self) -> list[str]:
        """The ids in processes.csv, the part after `<namespace>:`, sorted."""
        return sorted(self._rows)

    def processes(self, namespace: str, formulas: Formulas | None) -> list[Process]:
        if self._failed is None:
            return []
        found: list[Process] = []
        for code in self.ids():
            if code in self._failed:
                continue
            process = self._process(code, f"{namespace}:{code}", formulas)
            if process is not None:
                found.append(process)
        return found

    def _read(self) -> None:
        root, problems = self.root, self.problems
        here = [name for name in (PROCESSES, EXCHANGES) if (root / name).is_file()]
        if not here:
            problems.append(
                Problem(
                    "no-processes",
                    PROCESSES,
                    f"no {PROCESSES} and no {EXCHANGES} in {root}. The processes are two tables: "
                    f"{PROCESSES}, one row per process, and {EXCHANGES}, one row per line.",
                )
            )
            return
        for name in (PROCESSES, EXCHANGES):
            if name not in here:
                problems.append(
                    Problem(
                        "missing-file",
                        name,
                        f"{name} is missing in {root}. The processes are two tables: "
                        f"{PROCESSES}, one row per process, and {EXCHANGES}, one row per line.",
                    )
                )
        if PROCESSES in here:
            self._read_processes()
        if EXCHANGES in here:
            self._read_lines()

    def _read_processes(self) -> None:
        before = len(self.problems)
        rows = read_csv(
            self.root / PROCESSES,
            self.root,
            required=PROCESS_COLUMNS,
            optional=PROCESS_OPTIONAL,
            problems=self.problems,
        )
        for row in rows:
            code = row.get("id")
            if not code:
                self.problems.append(
                    Problem(
                        "bad-row", f"file {PROCESSES}", "a row has no id", location=row.location
                    )
                )
                continue
            if ":" in code:
                self.problems.append(_full_id(code, "id", row))
                continue
            if code in self._rows:
                self.problems.append(
                    Problem(
                        "duplicate-process",
                        f"process {code}",
                        f"is listed twice, at {self._rows[code].location} and {row.location}",
                        location=row.location,
                    )
                )
                continue
            self._rows[code] = row
        self._processes_read = len(self.problems) == before

    def _read_lines(self) -> None:
        before = len(self.problems)
        rows = read_csv(
            self.root / EXCHANGES,
            self.root,
            required=TABLE_COLUMNS,
            optional=TABLE_OPTIONAL,
            problems=self.problems,
        )
        for row in rows:
            code = row.get("process")
            if not code:
                self.problems.append(
                    Problem(
                        "bad-row",
                        f"file {EXCHANGES}",
                        "a row has no process",
                        location=row.location,
                    )
                )
                continue
            if ":" in code:
                self.problems.append(_full_id(code, "process", row))
                continue
            self._lines[code].append(row)
        self._failed = self._processes_of(self.problems[before:])
        if not self._processes_read:
            return
        for code in sorted(set(self._lines) - set(self._rows)):
            first = self._lines[code][0]
            self.problems.append(
                Problem(
                    "unknown-process",
                    f"process {code}",
                    f"has {_rows(len(self._lines[code]))} in {EXCHANGES} and no row in "
                    f"{PROCESSES}, which gives its name, location and kind",
                    location=first.location,
                )
            )

    def _processes_of(self, problems: list[Problem]) -> set[str] | None:
        """The processes of the rows of exchanges.csv that these problems name, by the first
        cell of the row's line, or None when one is about the whole file."""
        lines: dict[str, str] = {}
        failed: set[str] = set()
        for problem in problems:
            where = (problem.location or "").removeprefix(f"{EXCHANGES}:")
            if not where.isdigit() or where == "1":
                return None  # the header, the file, or a quote that runs to its end
            if not lines:
                text = (self.root / EXCHANGES).read_bytes().decode("utf-8", "replace")
                lines = {
                    str(n): line.split(",", 1)[0].strip().strip('"')
                    for n, line in enumerate(text.splitlines(), 1)
                }
            failed.add(lines.get(where, ""))
        return failed

    def _process(self, code: str, process_id: str, formulas: Formulas | None) -> Process | None:
        problems = self.problems
        before = len(problems)
        row = self._rows[code]
        details = _details(row, process_id, problems)
        exchanges: list[Exchange] = []
        seen: dict[str, Row] = {}
        for line in self._lines.get(code, []):
            line_id = line.get("line")
            if line_id and line_id in seen:
                problems.append(
                    Problem(
                        "duplicate-line",
                        f"{process_id} line {line_id}",
                        f"line id {line_id!r} is used twice, at {seen[line_id].location} and "
                        f"{line.location}",
                        location=line.location,
                    )
                )
                continue
            seen[line_id] = line
            exchange = read_exchange(line, process_id, formulas, problems)
            if exchange is not None:
                exchanges.append(exchange)
        if len(problems) > before or details is None:
            # A line that failed would only cause confusing errors about the whole process.
            return None
        try:
            return Process(id=process_id, exchanges=tuple(exchanges), **dict(details))
        except ValidationError as error:
            problems.extend(problems_from_validation(error, location=row.location))
            return None


def _details(row: Row, process_id: str, problems: list[Problem]) -> ProcessDetails | None:
    data: dict[str, str] = {c: row.get(c) for c in ("name", "location", "kind")}
    data |= {c: row.get(c) for c in ("time_start", "time_end") if row.get(c)}
    try:
        return ProcessDetails.model_validate(data)
    except ValidationError as error:
        problems.extend(
            problems_from_validation(
                error, item=f"process {process_id}", location=row.location, model=ProcessDetails
            )
        )
        return None


def _rows(n: int) -> str:
    return "1 row" if n == 1 else f"{n} rows"


def _full_id(code: str, column: str, row: Row) -> Problem:
    short = code.split(":", 1)[1]
    return Problem(
        "bad-row",
        f"process {code}",
        f"{column} {code!r} is a whole id. The table holds the part after the database's name: "
        f"write {short!r}.",
        location=row.location,
    )
