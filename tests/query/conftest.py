import shutil
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

import headwater as hw
from headwater.model import (
    DEFAULT_UNITS,
    CutOffCriterion,
    Exchange,
    Flow,
    FlowKind,
    FlowProperty,
    Issue,
    IssueStatus,
    Method,
    Pin,
    Process,
    Units,
)
from headwater.release import Release, ReleaseInfo, build
from headwater.study import DatabaseFolder, read_method, read_process_folder
from tests.conftest import only_process, open_database, open_study_folder, toy_database

TOY_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "toy"


def pinned_study(study: hw.Study, background: hw.Background) -> hw.Study:
    """The study with each database it reaches pinned to the release `background` was read
    from, as `pinned` does for a database."""
    release = background.release
    if release is None:
        return study
    pin = Pin(name=release.name, version=release.hash[:12])

    def moved(folder: DatabaseFolder) -> DatabaseFolder:
        return replace(
            folder,
            database=folder.database.model_copy(update={"background": pin}),
            used=tuple(moved(u) for u in folder.used),
        )

    folder = study.folder
    return hw.Study._of(  # pyright: ignore[reportPrivateUsage]
        replace(folder, databases=tuple(moved(d) for d in folder.databases))
    )


def pinned(study: hw.Database, background: hw.Background) -> hw.Database:
    """The database pinned to the release `background` was read from, as a real one is.

    The toy databases pin the label `toy-background@fixture`, which names no release, so a
    database calculated on a release needs its pin changed. A background read from a folder has
    no hash: the database is returned as it is.
    """
    release = background.release
    if release is None:
        return study
    pin = Pin(name=release.name, version=release.hash[:12])
    folder = study.folder
    return hw.Database(
        replace(folder, database=folder.database.model_copy(update={"background": pin}))
    )


LoadToy = Callable[[Path], hw.Background]
CalcToy = Callable[..., hw.Result]
Edit = Callable[[Path, str, str], None]

INFO = ReleaseInfo(
    name="toy-background",
    source="toy",
    version="1",
    system_model="cutoff",
    licence="CC0-1.0",
    flow_list="toy",
)

RESOLVED_AT = datetime(2026, 10, 11, 9, 30, tzinfo=UTC)

DYE = Flow(id="toy:dye", name="indigo dye", kind=FlowKind.product, unit="kg")
GLUE = Flow(id="toy:glue", name="glue", kind=FlowKind.product, unit="kg")


@dataclass(frozen=True)
class Records:
    """The toy background as release records, which tests change one line at a time."""

    processes: tuple[Process, ...]
    flows: tuple[Flow, ...]
    flow_properties: tuple[FlowProperty, ...] = ()
    issues: tuple[Issue, ...] = ()
    methods: tuple[Method, ...] = ()
    units: Units = field(default=DEFAULT_UNITS)

    def build(self, into: Path, info: ReleaseInfo = INFO) -> Release:
        return build(
            info,
            processes=self.processes,
            flows=self.flows,
            flow_properties=self.flow_properties,
            units=self.units,
            issues=self.issues,
            methods=self.methods,
            into=into,
        )

    def with_line(self, process_id: str, line_id: str, **changes: object) -> "Records":
        """The same records with one line changed. Record validators run on the new line."""

        def change(e: Exchange) -> Exchange:
            if (e.process_id, e.line_id) != (process_id, line_id):
                return e
            return Exchange.model_validate({**e.model_dump(), **changes})

        return replace(
            self,
            processes=tuple(_with(p, [change(e) for e in p.exchanges]) for p in self.processes),
        )

    def with_new_line(self, process_id: str, line_id: str, **fields: object) -> "Records":
        """The same records with one more line in a process."""

        def add(p: Process) -> Process:
            if p.id != process_id:
                return p
            line = Exchange.model_validate({"process_id": process_id, "line_id": line_id, **fields})
            return _with(p, [*p.exchanges, line])

        return replace(self, processes=tuple(add(p) for p in self.processes))

    def with_flows(self, *flows: Flow) -> "Records":
        return replace(self, flows=(*self.flows, *flows))

    def with_issues(self, *issues: Issue) -> "Records":
        return replace(self, issues=(*self.issues, *issues))

    def with_properties(self, *properties: FlowProperty) -> "Records":
        return replace(self, flow_properties=(*self.flow_properties, *properties))

    def with_methods(self, *methods: Method) -> "Records":
        """The same records with these methods instead of the toy one."""
        return replace(self, methods=methods)


def _with(process: Process, exchanges: list[Exchange]) -> Process:
    return Process.model_validate(
        {**process.model_dump(exclude={"exchanges"}), "exchanges": exchanges}
    )


def toy_records() -> Records:
    folder = read_process_folder(TOY_DIR / "background", namespace="toy")
    method = read_method(TOY_DIR / "methods" / "toy-climate")
    return Records(
        processes=folder.processes,
        flows=folder.flows,
        flow_properties=folder.flow_properties,
        methods=(method,),
    )


def cut_off_records() -> Records:
    """The toy background plus a cut-off dye line and a data-gap glue line in fabric production.

    Neither has a supplier. Each has a resolved issue, which `build` needs to accept it.
    """
    dye = {
        "role": "input",
        "flow_id": DYE.id,
        "amount": 0.002,
        "unit": "kg",
        "link_status": "cut_off",
    }
    glue = {**dye, "flow_id": GLUE.id, "amount": 0.001, "link_status": "data_gap"}
    return (
        toy_records()
        .with_flows(DYE, GLUE)
        .with_new_line("toy:fabric-production", "e05", **dye)
        .with_new_line("toy:fabric-production", "e06", **glue)
        .with_issues(
            Issue(
                process_id="toy:fabric-production",
                line_id="e05",
                flow_id=DYE.id,
                status=IssueStatus.cut_off,
                reason="no dye dataset; below 1% of the mass",
                criterion=CutOffCriterion.mass,
                share=0.002,
                cumulative_share=0.002,
                resolved_by="test",
                resolved_at=RESOLVED_AT,
            ),
            Issue(
                process_id="toy:fabric-production",
                line_id="e06",
                flow_id=GLUE.id,
                status=IssueStatus.data_gap,
                reason="no glue dataset",
                resolved_by="test",
                resolved_at=RESOLVED_AT,
            ),
        )
    )


Make = Callable[..., Release]


@pytest.fixture
def make_release(tmp_path: Path) -> Make:
    """Freeze records into a release under tmp_path: `make_release()` is the plain toy,
    `make_release(records)` any records. Each call goes to its own folder."""
    count = 0

    def make(records: Records | None = None, info: ReleaseInfo = INFO) -> Release:
        nonlocal count
        count += 1
        return (records or toy_records()).build(tmp_path / f"releases-{count}", info)

    return make


@pytest.fixture
def release(make_release: Make) -> Release:
    """The toy background as a release. Read only."""
    return make_release()


@pytest.fixture(scope="module")
def release_background(tmp_path_factory: pytest.TempPathFactory) -> hw.Background:
    """The toy background loaded from a release, factorized once. Read only."""
    into = tmp_path_factory.mktemp("releases")
    return hw.load(toy_records().build(into).path, method="toy-climate")


@pytest.fixture(params=["folder", "release"])
def either(
    request: pytest.FixtureRequest,
    background: hw.Background,
    release_background: hw.Background,
) -> hw.Background:
    """The toy background read from its folder, then from a release: a test that uses it runs
    on both."""
    return background if request.param == "folder" else release_background


@pytest.fixture
def toy(tmp_path: Path) -> Path:
    """A writable copy of the toy fixtures, for tests that change one thing."""
    target = tmp_path / "toy"
    shutil.copytree(TOY_DIR, target)
    return target


@pytest.fixture
def load_toy() -> LoadToy:
    def load(root: Path = TOY_DIR) -> hw.Background:
        return hw.load(
            root / "background", method=root / "methods" / "toy-climate", namespace="toy"
        )

    return load


@pytest.fixture(scope="module")
def background() -> hw.Background:
    """The toy background as committed, factorized once. Read only."""
    return hw.load(
        TOY_DIR / "background", method=TOY_DIR / "methods" / "toy-climate", namespace="toy"
    )


@pytest.fixture
def calc(background: hw.Background) -> CalcToy:
    """Score the one product of a toy database, pinned to the release of its background if it
    has one: `calc("tshirt")`, `calc("tshirt", other_background)`, on a changed copy of the toy
    with `root=`. The name is that of the case, `tshirt-waste`, or `dyes` (see `toy_database`).
    With `scenario=`, the T-shirt of the case's study `tshirt-footprint`, which holds the
    scenario, is scored in it."""

    def run(
        study: str,
        on: hw.Background = background,
        *,
        root: Path = TOY_DIR,
        background: hw.Background | None = None,
        indicator: str | None = None,
        scenario: str | None = None,
    ) -> hw.Result:
        used = background or on
        if scenario is not None:
            footprint = open_study_folder(root / study / "tshirt-footprint")
            [result] = pinned_study(footprint, used).calc(
                used, indicator=indicator, scenario=scenario
            )
            return result
        opened = pinned(open_database(toy_database(root, study)), used)
        return opened.calc(used, only_process(opened), indicator=indicator)

    return run


OpenStudy = Callable[..., hw.Database]


@pytest.fixture
def open_study() -> OpenStudy:
    """Open a toy database pinned to `background`'s release: `open_study("tshirt", background)`,
    by the name of its case (see `toy_database`)."""

    def open_(name: str, background: hw.Background, *, root: Path = TOY_DIR) -> hw.Database:
        return pinned(open_database(toy_database(root, name)), background)

    return open_


@pytest.fixture
def edit() -> Edit:
    """Replace text in a file. Fails if the text is not there, so a typo cannot pass silently."""

    def _edit(path: Path, old: str, new: str) -> None:
        text = path.read_text()
        assert old in text, f"{old!r} not found in {path}"
        path.write_text(text.replace(old, new, 1))

    return _edit


@pytest.fixture
def records() -> Records:
    return toy_records()


@pytest.fixture
def cut_off() -> Records:
    return cut_off_records()
