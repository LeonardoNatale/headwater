"""Compare a database or a study at two git refs: the score changes, and the differences that
caused them."""

from headwater.diff._attribute import (
    UNCHANGED_RTOL,
    Driver,
    Product,
    Row,
    StudyDiff,
    diff_databases,
    require_same_pin,
    unknown_product,
)
from headwater.diff._changes import Change, Difference, FieldChange, differences
from headwater.diff._git import (
    DiffError,
    FolderAt,
    Source,
    StudyNotAtRef,
    read_at,
    uses_next_to,
)
from headwater.diff._render import FORMAT, new_database, product_label, render_json, render_text
from headwater.diff._study import diff_study

__all__ = [
    "FORMAT",
    "UNCHANGED_RTOL",
    "Change",
    "DiffError",
    "Difference",
    "Driver",
    "FieldChange",
    "FolderAt",
    "Product",
    "Row",
    "Source",
    "StudyDiff",
    "StudyNotAtRef",
    "diff_databases",
    "diff_study",
    "differences",
    "new_database",
    "product_label",
    "read_at",
    "render_json",
    "render_text",
    "require_same_pin",
    "unknown_product",
    "uses_next_to",
]
