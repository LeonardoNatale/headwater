"""Write the two toy backgrounds as frozen releases: the demo's, in the package, and the case
study's, next to notebook 03.

    uv run python tests/ci/build_demo_release.py

The demo databases in src/headwater/demo pin the first, and the backpack database in
notebooks/case-study pins the second. Building the same records gives the same hash, so a folder
is only replaced when its background or the release format changes. Each release holds its method
as well, because a release carries its methods.
"""

from pathlib import Path

from headwater.release import Release, ReleaseInfo, build
from headwater.study import read_method, read_process_folder

ROOT = Path(__file__).resolve().parents[2]
TOY = ROOT / "tests" / "fixtures" / "toy"
DEMO = ROOT / "src" / "headwater" / "demo"
CASE_STUDY = ROOT / "notebooks" / "case-study"

INFO = ReleaseInfo(
    name="toy-background",
    source="toy",
    version="1",
    system_model="cutoff",
    licence="CC0-1.0",
    flow_list="toy",
)
CASE_STUDY_INFO = ReleaseInfo(
    name="invented-background",
    source="invented",
    version="1",
    system_model="cutoff",
    licence="CC0-1.0",
    flow_list="invented",
)


def build_toy_release(into: Path) -> Release:
    folder = read_process_folder(TOY / "background", namespace="toy")
    return build(
        INFO,
        processes=folder.processes,
        flows=folder.flows,
        flow_properties=folder.flow_properties,
        methods=(read_method(TOY / "methods" / "toy-climate"),),
        into=into,
    )


def build_case_study_release(into: Path) -> Release:
    folder = read_process_folder(CASE_STUDY / "background", namespace="invented")
    return build(
        CASE_STUDY_INFO,
        processes=folder.processes,
        flows=folder.flows,
        flow_properties=folder.flow_properties,
        methods=(read_method(CASE_STUDY / "method"),),
        into=into,
    )


if __name__ == "__main__":
    for release in (
        build_toy_release(DEMO / "releases"),
        build_case_study_release(CASE_STUDY / "releases"),
    ):
        print(f"{release.path.name}  (pin databases with: background: {release.path.name})")
