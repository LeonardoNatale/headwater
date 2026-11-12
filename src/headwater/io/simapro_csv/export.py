"""A whole SimaPro CSV export, process or method, read into typed records.

`read_export(path)` decodes the file (UTF-8, else Windows-1252, the code page SimaPro writes on
Windows), reads the header, splits the rest into blocks, and reads each block it knows into
records. Blocks it reads and does not keep: `Quantities`, `System description`, `Literature
reference` and the parameter blocks. Everything it does not understand stops the read, with every
problem at once.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from itertools import islice
from pathlib import Path

from headwater.io.simapro_csv.blocks import Block, Row, read_blocks, sections
from headwater.io.simapro_csv.header import Header, read_header
from headwater.io.simapro_csv.records import (
    LINE_SECTIONS,
    METHOD_SECTIONS,
    PROCESS_METADATA,
    SUBSTANCE_BLOCKS,
    MethodRecord,
    Parse,
    ProcessRecord,
    Substance,
    UnitDefinition,
    read_method,
    read_process,
    read_substance,
    read_unit,
)
from headwater.model import InvalidDataError, Problem

PROCESS = "Process"
METHOD = "Method"
UNITS = "Units"

_NOT_KEPT = frozenset(
    {
        "Quantities",
        "System description",
        "Literature reference",
        "Database Input parameters",
        "Database Calculated parameters",
        "Project Input parameters",
        "Project Calculated parameters",
    }
)

KEYWORDS = frozenset({PROCESS, METHOD, UNITS, *SUBSTANCE_BLOCKS, *_NOT_KEPT})
"""Every block a SimaPro export may hold."""

_PROCESS_SECTIONS = PROCESS_METADATA | set(LINE_SECTIONS)


@dataclass(frozen=True)
class Export:
    file: str
    header: Header
    processes: tuple[ProcessRecord, ...]
    methods: tuple[MethodRecord, ...]
    substances: Mapping[tuple[str, str], Substance]
    """By compartment and name: `("air", "Carbon dioxide, fossil")`."""
    units: tuple[UnitDefinition, ...]
    warnings: tuple[Problem, ...]


def read_export(path: Path) -> Export:
    """Read the export at `path`. Raises `InvalidDataError` naming every problem."""
    file = path.name
    lines = _decode(path).split("\n")
    header = read_header(lines, file)
    problems: list[Problem] = []
    blocks = read_blocks(
        islice(lines, header.size, None),
        first_line=header.size + 1,
        separator=header.csv_separator,
        keywords=KEYWORDS,
        file=file,
        problems=problems,
    )
    parse = Parse(file, header.decimal_separator, header.constants, problems)
    processes: list[ProcessRecord] = []
    methods: list[MethodRecord] = []
    substances: dict[tuple[str, str], Substance] = {}
    units: list[UnitDefinition] = []
    for block in blocks:
        if block.keyword == PROCESS:
            found = read_process(
                block.line, sections(block, _PROCESS_SECTIONS, file, problems), parse
            )
            if found is not None:
                processes.append(found)
        elif block.keyword == METHOD:
            method = read_method(
                block.line, sections(block, METHOD_SECTIONS, file, problems), parse
            )
            if method is not None:
                methods.append(method)
        elif block.keyword in SUBSTANCE_BLOCKS:
            for row in _filled(block):
                substance = read_substance(row, SUBSTANCE_BLOCKS[block.keyword])
                if substance is not None:
                    substances.setdefault((substance.compartment, substance.name), substance)
        elif block.keyword == UNITS:
            units += [u for row in _filled(block) if (u := read_unit(row, parse)) is not None]
    _check_identifiers(processes, parse)
    if parse.problems:
        raise InvalidDataError(parse.problems)
    return Export(
        file=file,
        header=header,
        processes=tuple(processes),
        methods=tuple(methods),
        substances=substances,
        units=tuple(units),
        warnings=tuple(parse.warnings),
    )


def _decode(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError as error:
        raise InvalidDataError(
            [Problem("unreadable-file", f"file {path.name}", f"cannot be read: {error}")]
        ) from None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp1252")
    except UnicodeDecodeError as error:
        raise InvalidDataError(
            [
                Problem(
                    "unreadable-file",
                    f"file {path.name}",
                    f"is neither UTF-8 nor Windows-1252 text: {error}",
                )
            ]
        ) from None


def _filled(block: Block) -> list[Row]:
    return [row for row in block.rows if row.cells]


def _check_identifiers(processes: list[ProcessRecord], parse: Parse) -> None:
    seen: dict[str, ProcessRecord] = {}
    for process in processes:
        first = seen.setdefault(process.identifier, process)
        if first is not process:
            parse.problems.append(
                Problem(
                    "duplicate-process",
                    f"process {process.identifier}",
                    f"is in the export twice: at {first.where} and at {process.where}",
                    location=process.where,
                )
            )
