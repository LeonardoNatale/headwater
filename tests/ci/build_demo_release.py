"""Write the toy background as a frozen release into the demo's `releases/`, in the package.

    uv run python tests/ci/build_demo_release.py

The demo databases in src/headwater/demo pin the release this writes. Building the same records
gives the same hash, so the folder is only replaced when the toy background or the release format
changes. The release holds the toy method as well, because a release carries its methods.
"""

from pathlib import Path

from headwater.release import Release, ReleaseInfo, build
from headwater.study import read_method, read_process_folder

ROOT = Path(__file__).resolve().parents[2]
TOY = ROOT / "tests" / "fixtures" / "toy"
DEMO = ROOT / "src" / "headwater" / "demo"

INFO = ReleaseInfo(
    name="toy-background",
    source="toy",
    version="1",
    system_model="cutoff",
    licence="CC0-1.0",
    flow_list="toy",
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


if __name__ == "__main__":
    release = build_toy_release(DEMO / "releases")
    print(f"{release.path.name}  (pin studies with: background: {release.path.name})")
