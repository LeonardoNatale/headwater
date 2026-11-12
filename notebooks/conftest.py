"""pytest settings for the notebooks.

Notebooks 03 and 05 read a licensed ecoinvent 3.12 copy and its LCIA workbook, so they run only
when ECOINVENT_DIR and ECOINVENT_LCIA are set, as for the integration tests. Notebooks 04 and 05
run Brightway, so they run only when the `brightway` dependency group is installed. CI has no
licensed data and skips 03 and 05.
"""

import importlib.util
import os

import pytest

NEEDS_ECOINVENT = {
    "03-working-with-ecoinvent.ipynb",
    "05-brightway-and-headwater-on-ecoinvent.ipynb",
}
NEEDS_BRIGHTWAY = {
    "04-coming-from-brightway.ipynb",
    "05-brightway-and-headwater-on-ecoinvent.ipynb",
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    no_ecoinvent = pytest.mark.skip(reason="ECOINVENT_DIR and ECOINVENT_LCIA are not both set")
    no_brightway = pytest.mark.skip(
        reason="Brightway is not installed: uv sync --group notebooks --group brightway"
    )
    has_ecoinvent = all(os.environ.get(name) for name in ("ECOINVENT_DIR", "ECOINVENT_LCIA"))
    has_brightway = importlib.util.find_spec("bw2data") is not None
    for item in items:
        if item.path.name in NEEDS_ECOINVENT and not has_ecoinvent:
            item.add_marker(no_ecoinvent)
        elif item.path.name in NEEDS_BRIGHTWAY and not has_brightway:
            item.add_marker(no_brightway)
