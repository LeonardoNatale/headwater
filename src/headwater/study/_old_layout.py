"""The layout before D48, `processes/<name>.yaml` with `processes/<name>.csv`, read only to convert
it (`headwater database convert`), and by `diff` through the converter. No other code reads it."""

from pathlib import Path

from pydantic import ValidationError

from headwater.model import Problem, Process, ProcessDetails
from headwater.study._exchanges import EXCHANGE_COLUMNS, EXCHANGE_OPTIONAL, Formulas, read_exchange
from headwater.study._files import problems_from_validation, read_csv, read_yaml, relative
from headwater.study._tables import OLD_FOLDER


class FileLayout:
    """The processes of a folder, read from `processes/`: each is `<name>.csv` (its exchanges)
    with `<name>.yaml` (its name, location, time and kind)."""

    def __init__(self, root: Path, problems: list[Problem]) -> None:
        self.root = root
        self.problems = problems
        self.folder = root / OLD_FOLDER
        self._stems = self._read()

    def ids(self) -> list[str]:
        return sorted(self._stems)

    def processes(self, namespace: str, formulas: Formulas | None) -> list[Process]:
        found: list[Process] = []
        for stem in self.ids():
            process = self._process(stem, f"{namespace}:{stem}", formulas)
            if process is not None:
                found.append(process)
        return found

    def _read(self) -> set[str]:
        """The names that have both files, after a problem for each file that does not pair."""
        root, problems, folder = self.root, self.problems, self.folder
        files = (
            sorted(f for f in folder.iterdir() if not f.name.startswith("."))
            if folder.is_dir()
            else []
        )
        stems: dict[str, set[str]] = {".csv": set(), ".yaml": set()}
        for f in files:
            if f.suffix in stems and f.is_file():
                stems[f.suffix].add(f.stem)
                continue
            rel = relative(f, root)
            problems.append(
                Problem(
                    "unknown-file",
                    rel,
                    "is not a process file. A process is a .csv file (its exchanges) with a "
                    ".yaml file of the same name (its name, location and kind).",
                    location=rel,
                )
            )
        if not stems[".csv"] and not stems[".yaml"]:
            problems.append(
                Problem(
                    "no-processes",
                    "processes/",
                    f"no process files in {root}. A process is processes/<name>.csv with "
                    "processes/<name>.yaml",
                )
            )
        for stem in sorted(stems[".csv"] - stems[".yaml"]):
            problems.append(_unpaired(f"processes/{stem}.csv", f"processes/{stem}.yaml"))
        for stem in sorted(stems[".yaml"] - stems[".csv"]):
            problems.append(_unpaired(f"processes/{stem}.yaml", f"processes/{stem}.csv"))
        return stems[".csv"] & stems[".yaml"]

    def _process(self, stem: str, process_id: str, formulas: Formulas | None) -> Process | None:
        root, problems = self.root, self.problems
        before = len(problems)
        csv_path = self.folder / f"{stem}.csv"
        rows = read_csv(
            csv_path, root, required=EXCHANGE_COLUMNS, optional=EXCHANGE_OPTIONAL, problems=problems
        )
        exchanges = [
            e
            for row in rows
            if (e := read_exchange(row, process_id, formulas, problems)) is not None
        ]
        details = _read_details(self.folder / f"{stem}.yaml", root, problems)
        if len(problems) > before or details is None:
            return None
        try:
            return Process(id=process_id, exchanges=tuple(exchanges), **dict(details))
        except ValidationError as error:
            problems.extend(problems_from_validation(error, location=relative(csv_path, root)))
            return None


def _unpaired(found: str, missing: str) -> Problem:
    return Problem(
        "missing-file",
        found,
        f"has no {missing} beside it. A process needs both: a .csv file for its exchanges "
        "and a .yaml file for its name, location and kind.",
        location=found,
    )


def _read_details(path: Path, root: Path, problems: list[Problem]) -> ProcessDetails | None:
    data = read_yaml(path, root, problems)
    if data is None:
        return None
    rel = relative(path, root)
    try:
        return ProcessDetails.model_validate(data)
    except ValidationError as error:
        problems.extend(
            problems_from_validation(error, prefix=rel, location=rel, model=ProcessDetails)
        )
        return None
