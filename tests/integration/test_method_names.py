"""The default id and version of every method of ecoinvent's 3.12 LCIA workbook, from its name.

Runs only when `ECOINVENT_LCIA` is set to `LCIA Implementation 3.12.xlsx`. The table below is what
the workbook's reader gave before the naming rules moved to `headwater.io.method`, so a change to
them that moves any of these ids is caught. Method names are public; no value of the workbook is
here.
"""

import os
from pathlib import Path

import openpyxl
import pytest

from headwater.io.method import method_id, method_version

ECOINVENT_LCIA = os.environ.get("ECOINVENT_LCIA")

pytestmark = pytest.mark.skipif(not ECOINVENT_LCIA, reason="ECOINVENT_LCIA is not set")

EXPECTED: dict[str, tuple[str | None, str | None]] = {
    "CML v4.8 2016": ("CML-4.8-2016", "4.8"),
    "CML v4.8 2016 no LT": ("CML-4.8-2016-no-LT", "4.8"),
    "Crustal Scarcity Indicator 2020": ("Crustal-Scarcity-Indicator-2020", "2020"),
    "Cumulative Energy Demand (CED)": (None, None),
    "Cumulative Exergy Demand (CExD)": (None, None),
    "EF v3.0": ("EF-3.0", "3.0"),
    "EF v3.0 no LT": ("EF-3.0-no-LT", "3.0"),
    "EF v3.1": ("EF-3.1", "3.1"),
    "EF v3.1 no LT": ("EF-3.1-no-LT", "3.1"),
    "EN15804+A2 - Additional impact categories and indicators": (None, None),
    "EN15804+A2 - Core impact categories and indicators": (None, None),
    "EN15804+A2 - Indicators describing output flows": (None, None),
    "EN15804+A2 - Indicators describing resource use": (None, None),
    "EN15804+A2 - Indicators describing waste categories": (None, None),
    "EPS 2020d": (None, None),
    "EPS 2020d no LT": (None, None),
    "Ecological Footprint": (None, None),
    "Ecological Scarcity 2021": ("Ecological-Scarcity-2021", "2021"),
    "Ecological Scarcity 2021 no LT": ("Ecological-Scarcity-2021-no-LT", "2021"),
    "Ecosystem Damage Potential": (None, None),
    "IMPACT World+ v2.1, footprint version": ("IMPACT-World-2.1-footprint-version", "2.1"),
    "IPCC 2013": ("IPCC-2013", "2013"),
    "IPCC 2013 no LT": ("IPCC-2013-no-LT", "2013"),
    "IPCC 2021": ("IPCC-2021", "2021"),
    "IPCC 2021 (incl. biogenic CO2)": ("IPCC-2021-incl-biogenic-CO2", "2021"),
    "IPCC 2021 (incl. biogenic CO2) no LT": ("IPCC-2021-incl-biogenic-CO2-no-LT", "2021"),
    "IPCC 2021 no LT": ("IPCC-2021-no-LT", "2021"),
    "Inventory results and indicators": (None, None),
    "ReCiPe 2016 v1.03, endpoint (E)": ("ReCiPe-2016-1.03-endpoint-E", "1.03"),
    "ReCiPe 2016 v1.03, endpoint (E) no LT": ("ReCiPe-2016-1.03-endpoint-E-no-LT", "1.03"),
    "ReCiPe 2016 v1.03, endpoint (H)": ("ReCiPe-2016-1.03-endpoint-H", "1.03"),
    "ReCiPe 2016 v1.03, endpoint (H) no LT": ("ReCiPe-2016-1.03-endpoint-H-no-LT", "1.03"),
    "ReCiPe 2016 v1.03, endpoint (I)": ("ReCiPe-2016-1.03-endpoint-I", "1.03"),
    "ReCiPe 2016 v1.03, endpoint (I) no LT": ("ReCiPe-2016-1.03-endpoint-I-no-LT", "1.03"),
    "ReCiPe 2016 v1.03, midpoint (E)": ("ReCiPe-2016-1.03-midpoint-E", "1.03"),
    "ReCiPe 2016 v1.03, midpoint (E) no LT": ("ReCiPe-2016-1.03-midpoint-E-no-LT", "1.03"),
    "ReCiPe 2016 v1.03, midpoint (H)": ("ReCiPe-2016-1.03-midpoint-H", "1.03"),
    "ReCiPe 2016 v1.03, midpoint (H) no LT": ("ReCiPe-2016-1.03-midpoint-H-no-LT", "1.03"),
    "ReCiPe 2016 v1.03, midpoint (I)": ("ReCiPe-2016-1.03-midpoint-I", "1.03"),
    "ReCiPe 2016 v1.03, midpoint (I) no LT": ("ReCiPe-2016-1.03-midpoint-I-no-LT", "1.03"),
    "TRACI v2.1": ("TRACI-2.1", "2.1"),
    "TRACI v2.1 no LT": ("TRACI-2.1-no-LT", "2.1"),
    "USEtox v2.13, endpoint": ("USEtox-2.13-endpoint", "2.13"),
    "USEtox v2.13, endpoint no LT": ("USEtox-2.13-endpoint-no-LT", "2.13"),
    "USEtox v2.13, midpoint": ("USEtox-2.13-midpoint", "2.13"),
    "USEtox v2.13, midpoint no LT": ("USEtox-2.13-midpoint-no-LT", "2.13"),
}
"""Each method name of the 3.12 workbook, with its default id and version, or None for a name
that holds no version and needs `--version`."""


def test_every_method_name_gives_the_id_and_version_it_gave_before() -> None:
    assert ECOINVENT_LCIA
    book = openpyxl.load_workbook(Path(ECOINVENT_LCIA), read_only=True, data_only=True)
    try:
        rows = book["CFs"].iter_rows(values_only=True)
        header = list(next(rows))
        at = header.index("Method")
        names = sorted({str(r[at]) for r in rows if r[at]})
    finally:
        book.close()

    found = {
        name: (method_id(name, version) if version else None, version)
        for name in names
        for version in [method_version(name)]
    }

    assert found == EXPECTED
