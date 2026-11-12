"""What a path or a release name opens: a release, or a folder of process files."""

import re
from pathlib import Path

from headwater.release import Release, find, pull
from headwater.release.manifest import FOLDER_NAME
from headwater.release.remote import is_url

_RELEASE_REF = re.compile(rf"{FOLDER_NAME[1:-1]}@[0-9a-f]{{12,64}}")


def open_source(path: Path | str) -> Release | Path:
    """The release that `path` names, or `path` itself as a folder of process files.

    A release is a folder that holds `release.json`, a string `name@hash` (at least 12
    characters of the hash) that is not a folder, which is read from the local cache, or the URL
    of a release folder, which is pulled into the cache first.
    """
    if is_url(path):
        return pull(str(path))
    folder = Path(path)
    if isinstance(path, str) and not folder.exists() and _RELEASE_REF.fullmatch(path):
        return find(path)
    if (folder / "release.json").is_file():
        return Release.open(folder)
    return folder
