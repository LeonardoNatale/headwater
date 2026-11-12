"""A release folder at a URL, read through fsspec: `release.json` first, then the files it lists.

Example: `https://example.com/releases/toy-background@9f2c1a0b3d4e`, or the same folder name under
`gs://bucket/releases/` or `s3://bucket/releases/`. Nothing is listed: a web server has no
listing, and the manifest says which files the release holds.
"""

import importlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import SplitResult, unquote, urlsplit, urlunsplit

from headwater.model import InvalidDataError, Problem
from headwater.release.manifest import FOLDER_NAME
from headwater.release.read import MANIFEST

_FOLDER = re.compile(rf"({FOLDER_NAME[1:-1]})@[0-9a-f]{{12}}")

_EXTRAS = {
    "gs": ("gcsfs", "gcs"),
    "gcs": ("gcsfs", "gcs"),
    "s3": ("s3fs", "s3"),
    "s3a": ("s3fs", "s3"),
}
"""The package a scheme needs and the extra of headwater that installs it."""

SCHEMES = ("file", "http", "https", "gs", "gcs", "s3", "s3a", "memory")
"""The URLs `pull` reads. `memory` is fsspec's in-process filesystem, used by the tests."""

_LOGIN = (
    "Sign in with the cloud's own login instead: `gcloud auth application-default login` for "
    "gs://, an AWS profile (`AWS_PROFILE`, `aws sso login`) for s3://"
)


def is_url(source: object) -> bool:
    """Whether `source` is written as a URL (`scheme://...`) and not as a path."""
    return isinstance(source, str) and "://" in source


@dataclass(frozen=True)
class Location:
    """The folder of one release at a URL."""

    url: str
    """Without a trailing slash."""
    name: str
    """The release's name."""
    folder: str
    """`name@hash12`: the last part of the URL, and the name of the folder in the cache."""

    @property
    def scheme(self) -> str:
        return urlsplit(self.url).scheme

    def beside(self, folder: str) -> "Location":
        """Another release folder in the same place."""
        parts = urlsplit(self.url)
        parent = parts.path.rsplit("/", 1)[0]
        return _location(parts._replace(path=f"{parent}/{folder}"))


def locate(url: str) -> Location:
    """Check a URL and say which release folder it names. Reads nothing.

    Raises InvalidDataError, without repeating what a credential says, for a URL that holds
    one, a scheme that is not supported, or a last part that is not `name@hash12`.
    """
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        raise _error("bad-url", "url", "cannot be read as a URL") from None
    if "@" in parts.netloc or parts.query or parts.fragment:
        raise _error(
            "url-credentials",
            f"{parts.scheme}://{parts.hostname or ''}",
            "the URL holds a credential: a user name or password, or a query or fragment, which "
            "can carry a signed token. Take it out of the URL and out of any file. " + _LOGIN + ".",
        )
    if parts.scheme not in SCHEMES:
        raise _error(
            "url-scheme",
            f"{parts.scheme}://",
            "is not supported. Pull from a folder, or a URL that starts with file://, http://, "
            "https://, gs:// or s3://",
        )
    if parts.scheme == "file":  # as Path.as_uri() writes it: `@` is %40
        parts = parts._replace(path=unquote(parts.path))
    return _location(parts)


def _location(parts: SplitResult) -> Location:
    folder = unquote(parts.path.rstrip("/").rsplit("/", 1)[-1])  # http servers read %40 as @
    found = _FOLDER.fullmatch(folder)
    if found is None:
        raise _error(
            "bad-url",
            f"{parts.scheme}://{parts.hostname or ''}",
            f"{urlunsplit(parts)} does not end in a release folder name. A release is in a "
            "folder named name@hash with 12 characters of its hash, for example "
            "https://example.com/releases/toy-background@9f2c1a0b3d4e",
        )
    return Location(urlunsplit(parts).rstrip("/"), found.group(1), folder)


class Remote:
    """Read the files of one release folder."""

    _fs: Any
    _path: str

    def __init__(self, location: Location) -> None:
        fsspec: Any = importlib.import_module("fsspec")  # no type stubs; loaded on first pull
        self.location = location
        extra = _EXTRAS.get(location.scheme)
        if extra is not None:
            try:
                importlib.import_module(extra[0])
            except ImportError:
                raise _error(
                    "missing-extra",
                    f"{location.scheme}://",
                    f"needs {extra[0]}, which is not installed. Install it with "
                    f"`pip install 'headwater[{extra[1]}]'`.",
                ) from None
        try:
            self._fs, self._path = fsspec.url_to_fs(location.url)
        except Exception as exc:  # the filesystem could not be made: a missing package, say
            raise _failed(location.url, exc) from None

    def get(self, name: str, into: Path, *, missing: str) -> None:
        """Download `name` of the folder to `into/name`, in blocks.

        `missing` is the code of the error when the file is not there.
        """
        target = into / name
        target.parent.mkdir(parents=True, exist_ok=True)
        url = f"{self.location.url}/{name}"
        try:
            self._fs.get_file(f"{self._path}/{name}", str(target))
        except FileNotFoundError:
            what = (
                f"has no {MANIFEST}, so there is no release at {self.location.url}"
                if name == MANIFEST
                else f"is listed in {MANIFEST} but is not at {url}"
            )
            raise _error(missing, name, what) from None
        except Exception as exc:  # a status, a timeout, a dropped connection, a refused login
            raise _failed(url, exc) from None


def _failed(url: str, exc: Exception) -> InvalidDataError:
    return _error("remote-error", url, f"cannot be read: {type(exc).__name__}: {exc}")


def _error(code: str, item: str, message: str) -> InvalidDataError:
    return InvalidDataError([Problem(code, item, message)])
