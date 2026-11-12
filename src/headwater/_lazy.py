"""Lazy exports for a package's `__init__`: `from headwater import open` loads `headwater.query`
only when `open` is first asked for, so importing the package, or a light submodule such as the
command line, loads no numpy, scipy or pyarrow."""

import importlib
from collections.abc import Callable, Mapping, Sequence
from typing import Any


def lazy_exports(
    namespace: dict[str, Any], modules: Mapping[str, Sequence[str]]
) -> tuple[Callable[[str], object], Callable[[], list[str]]]:
    """The `__getattr__` and `__dir__` of a package, given its `globals()`.

    `modules` maps a module to the names the package exports from it. Write the same imports under
    `if TYPE_CHECKING:` in the package, so editors and pyright see the names, and set
    `__getattr__, __dir__ = lazy_exports(globals(), {...})`.
    """
    package = namespace["__name__"]
    defined_in = {name: module for module, names in modules.items() for name in names}

    def __getattr__(name: str) -> object:
        module = defined_in.get(name)
        if module is None:
            raise AttributeError(f"module {package!r} has no attribute {name!r}")
        value = getattr(importlib.import_module(module), name)
        namespace[name] = value
        return value

    def __dir__() -> list[str]:
        return sorted({*namespace, *defined_in})

    return __getattr__, __dir__
