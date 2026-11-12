"""The link stages: suppliers by id or by name, and the flows of a method's factors.

Each takes typed tables or records and returns new ones, with an issue and a reason for
everything that did not link or map. Nothing is changed in place.
"""

from headwater.link.by_id import link_by_id
from headwater.link.by_name import Supplier, Suppliers, link_by_name
from headwater.link.flows import Mapped, MappedFlows, map_flows
from headwater.link.lines import DataGap, Linked
from headwater.link.mapping import (
    Decision,
    FlowKey,
    FlowMapping,
    FlowRow,
    SupplierKey,
    SupplierMapping,
    SupplierRow,
    read_flow_mapping,
    read_supplier_mapping,
)

__all__ = [
    "DataGap",
    "Decision",
    "FlowKey",
    "MappedFlows",
    "FlowMapping",
    "FlowRow",
    "Linked",
    "Mapped",
    "Supplier",
    "SupplierKey",
    "SupplierMapping",
    "SupplierRow",
    "Suppliers",
    "link_by_id",
    "link_by_name",
    "map_flows",
    "read_flow_mapping",
    "read_supplier_mapping",
]
