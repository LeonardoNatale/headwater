"""Releases: frozen, hashed sets of Parquet files that background data is imported into.

`build` freezes records into a source release and `build_tables` the same from Arrow tables,
`build_indicators` freezes solved scores into an indicators release that pins its source,
`Release.open` checks and reads one, and `pull` and `find` keep them in the local cache. The
format is in docs/design.md, "A release".

The names load their module when first used: reading a release does not load the checks that
`build` runs.
"""

from typing import TYPE_CHECKING

from headwater._lazy import lazy_exports

if TYPE_CHECKING:
    from headwater.release.cache import find, home, pull, releases_dir
    from headwater.release.content import method_hash, methods_hash, set_hash
    from headwater.release.manifest import MethodEntry, ReleaseInfo, ReleaseKind, ReleaseRef
    from headwater.release.read import Release, content_hash
    from headwater.release.write import build, build_indicators, build_tables

__getattr__, __dir__ = lazy_exports(
    globals(),
    {
        "headwater.release.cache": ["find", "home", "pull", "releases_dir"],
        "headwater.release.content": ["method_hash", "methods_hash", "set_hash"],
        "headwater.release.manifest": ["MethodEntry", "ReleaseInfo", "ReleaseKind", "ReleaseRef"],
        "headwater.release.read": ["Release", "content_hash"],
        "headwater.release.write": ["build", "build_indicators", "build_tables"],
    },
)

__all__ = [
    "MethodEntry",
    "Release",
    "ReleaseInfo",
    "ReleaseKind",
    "ReleaseRef",
    "build",
    "build_indicators",
    "build_tables",
    "content_hash",
    "find",
    "home",
    "method_hash",
    "methods_hash",
    "pull",
    "releases_dir",
    "set_hash",
]
