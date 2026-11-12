"""Move a study from one release to the next: `headwater upgrade`.

Every process of the old release a study names gets a successor in the new one, by the first
rule that decides it: a reviewed mapping row (who and when), the same ids, a row of ecoinvent's
correspondence files, the same dataset (name, product and location); else it is unmapped, with
its reason and suggestions that are never applied. Links to RoW and markets that changed are
flagged to read. The upgrade is written in place, cell by cell, only once everything maps, so
the pull request shows what moved. `headwater.diff` then tells what the release changed apart
from what the study changed.

Example, the toy T-shirt from toy-1 to toy-2: `plan("studies/tshirt", to="toy-2@bd46d2b98059",
mapping="upgrade-mapping.csv").write()` changes the supplier of line e02 from `toy:1:fabric-a`
to `toy:2:fabric-b`, the same dataset, and the pin in database.yaml.
"""

from headwater.upgrade._datasets import Dataset
from headwater.upgrade._flags import Flag
from headwater.upgrade._plan import Plan, UpgradeError, plan
from headwater.upgrade._rules import Correspondence, Match, Rule

__all__ = [
    "Correspondence",
    "Dataset",
    "Flag",
    "Match",
    "Plan",
    "Rule",
    "UpgradeError",
    "plan",
]
