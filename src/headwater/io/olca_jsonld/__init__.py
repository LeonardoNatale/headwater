"""Import an openLCA JSON-LD package: its processes into a release, its impact methods into
method folders.

Example: `headwater release import bafu.zip --source bafu --version 2026 --licence ...` reads the
package's processes, and `headwater method import bafu.zip --method "EF 3.1" --flow-list <release>`
reads one of its methods. Suppliers link by the UUID of each exchange's default provider, and
factors map by flow UUID.
"""

from headwater.io.olca_jsonld.inventory import read
from headwater.io.olca_jsonld.method import read_method
from headwater.io.olca_jsonld.package import detect
from headwater.io.source import Aspect, Format

FORMAT = Format(
    name="olca-jsonld",
    title="openLCA JSON-LD package (schema 2): a folder, a .zip or a .zolca",
    detect=detect,
    read_inventory=read,
    inventory_options=frozenset({"version", "source", "licence"}),
    read_method=read_method,
    method_options=frozenset({"method", "categories", "id", "version", "variant"}),
    cannot_carry=frozenset({Aspect.process_kind}),
)

__all__ = ["FORMAT", "read", "read_method"]
