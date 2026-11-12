"""The local cache of releases: `$HEADWATER_HOME/releases/<name>@<hash12>/`."""

import os
import re
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path

from headwater.model import InvalidDataError, Problem
from headwater.release.manifest import FOLDER_NAME, Manifest
from headwater.release.read import (
    MANIFEST,
    Release,
    check_files,
    load_manifest,
    open_expecting,
)
from headwater.release.remote import Remote, is_url, locate

_PREFIX = re.compile(r"[0-9a-f]{12,64}")


def home() -> Path:
    """`HEADWATER_HOME`, or `~/.headwater` when it is not set."""
    value = os.environ.get("HEADWATER_HOME")
    return Path(value).expanduser() if value else Path.home() / ".headwater"


def releases_dir() -> Path:
    return home() / "releases"


def temporary_folder(into: Path, name: str) -> Path:
    """A new hidden folder beside where a release will go. Its name has no `@`, so it is never
    taken for a release."""
    into.mkdir(parents=True, exist_ok=True)
    temp = into / f".tmp-{name}-{uuid.uuid4().hex}"
    temp.mkdir()
    return temp


def pull(source: Path | str) -> Release:
    """Check a release and copy it into the cache, from a folder or from a URL.

    A folder is `<name>@<hash12>`. A URL is that folder name under `file://`, `http://`,
    `https://`, `gs://` or `s3://`. Every file and the content hash are checked before the
    release is moved into place, so a bad or half-finished pull leaves nothing in the cache.
    Pulling a release the cache already has changes nothing.

    An indicators release needs the source release it was solved from: that one is pulled too,
    from the same folder or URL, unless the cache has it. If it is nowhere, this raises and
    names it.

    Example: `pull("https://example.com/releases/toy-background@9f2c1a0b3d4e")`.
    """
    if is_url(source):
        return _pull_url(str(source))
    return _pull_folder(Path(source))


def _pull_folder(source: Path) -> Release:
    release = Release.open(source)
    release.verify()
    into = releases_dir()
    target = into / release.path.name
    if target.exists():
        return open_expecting(target, release.hash)

    def beside(folder: str) -> Release | None:
        return _pull_folder(source.parent / folder) if (source.parent / folder).is_dir() else None

    _bring_parent(release.manifest, beside, f"in {source.parent}")
    temp = temporary_folder(into, release.name)
    try:
        for name in [MANIFEST, *release.manifest.files]:
            (temp / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(release.path / name, temp / name)
        check_files(temp, load_manifest(temp))
        _move(temp, target)
    except BaseException:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    return open_expecting(target, release.hash)


def _pull_url(url: str) -> Release:
    """The release folder at `url`, downloaded into a hidden folder of the cache and moved into
    place only when it checks out: the manifest against its own hash, every file against its
    sha256, and every table against its digest."""
    location = locate(url)
    remote = Remote(location)
    into = releases_dir()
    target = into / location.folder
    temp = temporary_folder(into, location.name)
    try:
        remote.get(MANIFEST, temp, missing="remote-release-missing")
        manifest = load_manifest(temp)  # before any file is asked for: its names are checked
        if f"{manifest.name}@{manifest.hash[:12]}" != location.folder:
            raise _error(
                "release-name",
                location.folder,
                f"{location.url} holds release {manifest.name} with hash {manifest.hash}, which "
                f"is {manifest.name}@{manifest.hash[:12]}, not {location.folder}",
            )
        if target.exists():
            shutil.rmtree(temp)
            return open_expecting(target, manifest.hash)
        for name in manifest.files:
            remote.get(name, temp, missing="remote-file-missing")
        check_files(temp, manifest)
        Release(temp, manifest).verify()

        def beside(folder: str) -> Release | None:
            try:
                return _pull_url(location.beside(folder).url)
            except InvalidDataError as error:
                if any(p.code == "remote-release-missing" for p in error.problems):
                    return None
                raise

        _bring_parent(manifest, beside, f"at {location.url.rsplit('/', 1)[0]}")
        _move(temp, target)
    except BaseException:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    return open_expecting(target, manifest.hash)


def _bring_parent(manifest: Manifest, beside: Callable[[str], Release | None], where: str) -> None:
    """Make sure the cache has the source release of an indicators release, pulling it from
    beside the release when it does not. Raises naming it when it is nowhere."""
    parent = manifest.parent
    if parent is None:
        return
    folder = f"{parent.name}@{parent.hash[:12]}"
    cached = releases_dir() / folder
    if cached.is_dir():
        open_expecting(cached, parent.hash)
        return
    if beside(folder) is None:
        raise _error(
            "parent-missing",
            f"release {manifest.name}",
            f"is solved from {parent}, which is not in the cache and not {where}. Pull it first.",
        )
    open_expecting(cached, parent.hash)


def _move(temp: Path, target: Path) -> None:
    """Rename a checked folder into place. Another process may have pulled it first."""
    try:
        temp.rename(target)
    except OSError:
        if not target.exists():
            raise
        shutil.rmtree(temp)


def find(ref: str) -> Release:
    """The cached release written `name@hash`, with at least 12 characters of the hash.

    Example: `find("toy-background@9f2c1a0b3d4e")`.
    """
    name, at, prefix = ref.partition("@")
    if not at or not re.fullmatch(FOLDER_NAME, name) or not prefix:
        raise _error("bad-ref", ref, "write a release as name@hash, for example toy@9f2c1a0b3d4e")
    if not _PREFIX.fullmatch(prefix):
        raise _error(
            "short-hash", ref, "give at least the first 12 characters of the hash, in lower case"
        )
    path = releases_dir() / f"{name}@{prefix[:12]}"
    if not path.is_dir():
        raise _error(
            "release-not-found",
            ref,
            f"{path.name} is not in {releases_dir()}. Pull it first with release.pull(source).",
        )
    release = Release.open(path)
    if not release.hash.startswith(prefix):
        raise _error("hash-mismatch", ref, f"the release in {path} has hash {release.hash}")
    return release


def _error(code: str, item: str, message: str) -> InvalidDataError:
    return InvalidDataError([Problem(code, item, message)])
