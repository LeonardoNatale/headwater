"""Pulling a release from a URL: fsspec's memory filesystem and a local HTTP server.

Everything runs on the toy release. A `Remote` is a folder of releases that a URL reaches: edit
its `host` folder (corrupt a file, delete one), then ask for the URL.
"""

import json
import shutil
import sys
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import fsspec
import pytest
from typer.testing import CliRunner

import headwater as hw
from headwater.cli import app
from headwater.model import InvalidDataError, Method
from headwater.release import Release, build_indicators, pull
from headwater.release.read import file_sha256

from .conftest import Records

runner = CliRunner()

SOLVER = {"solver": "scipy.sparse.linalg.splu", "tolerance": 1e-12}


@dataclass
class Remote:
    kind: str
    host: Path
    """The folder the URLs reach. Edit it before asking for a URL."""
    base: str
    requests: list[str] = field(default_factory=list)
    """The paths a GET asked for, for an HTTP remote."""

    def publish(self, release: Release) -> Path:
        """A copy of the release in the host folder, which a test may change."""
        return Path(shutil.copytree(release.path, self.host / release.path.name))

    def url(self, folder: str) -> str:
        if self.kind == "memory":
            _upload(self.host)
        return f"{self.base}/{folder}"


def _upload(host: Path) -> None:
    fs: Any = fsspec.filesystem("memory")
    for path in host.rglob("*"):
        if path.is_file():
            fs.pipe_file(f"/releases/{path.relative_to(host).as_posix()}", path.read_bytes())


class _Handler(SimpleHTTPRequestHandler):
    requests: list[str]

    def do_GET(self) -> None:
        self.requests.append(self.path)
        super().do_GET()

    def log_message(self, format: str, *args: Any) -> None:
        pass


@pytest.fixture(params=["memory", "http"])
def remote(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Remote]:
    host = tmp_path / "host"
    host.mkdir()
    if request.param == "memory":
        fs: Any = fsspec.filesystem("memory")
        fs.store.clear()
        fs.pseudo_dirs[:] = [""]
        yield Remote("memory", host, "memory://releases")
        fs.store.clear()
        fs.pseudo_dirs[:] = [""]
        return
    requests: list[str] = []
    handler = type("Handler", (_Handler,), {"requests": requests})
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(handler, directory=str(host)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield Remote("http", host, f"http://127.0.0.1:{server.server_port}", requests)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """HEADWATER_HOME in a temporary folder, so no test touches the real ~/.headwater."""
    path = tmp_path / "home"
    monkeypatch.setenv("HEADWATER_HOME", str(path))
    return path


def _cached(cache: Path) -> list[str]:
    """Everything in the cache's releases folder, hidden temporary folders too."""
    folder = cache / "releases"
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


def _codes(error: pytest.ExceptionInfo[InvalidDataError]) -> list[str]:
    return [p.code for p in error.value.problems]


def _derived(release: Release, method: Method, scores: Any) -> Release:
    ids, by_indicator = scores
    return build_indicators(release, method, product_ids=ids, scores=by_indicator, solver=SOLVER)


# --- the same release from every kind of URL ----------------------------------------------------


def test_the_toy_is_pulled_from_a_url_and_cached(
    remote: Remote, toy_release: Release, cache: Path, snapshot: Callable[[Path], Any]
) -> None:
    folder = remote.publish(toy_release)

    pulled = pull(remote.url(folder.name))

    assert pulled.hash == toy_release.hash
    assert pulled.path == cache / "releases" / folder.name
    assert snapshot(pulled.path) == snapshot(toy_release.path)
    assert _cached(cache) == [folder.name]
    assert Release.open(pulled.path).hash == toy_release.hash


def test_a_trailing_slash_is_the_same_release(
    remote: Remote, toy_release: Release, cache: Path
) -> None:
    folder = remote.publish(toy_release)

    assert pull(remote.url(folder.name) + "/").hash == toy_release.hash


def test_pulling_again_changes_nothing(
    remote: Remote, toy_release: Release, cache: Path, snapshot: Callable[[Path], Any]
) -> None:
    folder = remote.publish(toy_release)
    first = pull(remote.url(folder.name))
    before = snapshot(first.path)
    asked = len(remote.requests)

    second = pull(remote.url(folder.name))

    assert second.path == first.path
    assert snapshot(second.path) == before
    assert _cached(cache) == [folder.name]
    if remote.kind == "http":
        assert remote.requests[asked:] == [f"/{folder.name}/release.json"]


def test_a_url_with_an_at_sign_in_its_path_holds_no_credentials(
    remote: Remote, toy_release: Release, cache: Path
) -> None:
    folder = remote.publish(toy_release)

    assert "@" in remote.url(folder.name)
    assert pull(remote.url(folder.name)).hash == toy_release.hash


# --- a release that is not what it says ---------------------------------------------------------


def test_a_corrupted_file_is_refused_and_the_cache_stays_clean(
    remote: Remote, toy_release: Release, cache: Path
) -> None:
    folder = remote.publish(toy_release)
    file = folder / "flows.parquet"
    file.write_bytes(file.read_bytes() + b"\0")

    with pytest.raises(InvalidDataError, match=r"flows\.parquet") as raised:
        pull(remote.url(folder.name))

    assert _codes(raised) == ["changed-file"]
    assert _cached(cache) == []


def test_rows_changed_under_a_matching_sha256_are_refused(
    remote: Remote, toy_release: Release, records: Records, cache: Path, tmp_path: Path
) -> None:
    folder = remote.publish(toy_release)
    other = records.with_line("toy:fabric-production", "e02", amount=2.0).build(tmp_path / "other")
    swapped = folder / "exchanges.parquet"
    shutil.copyfile(other.path / "exchanges.parquet", swapped)
    manifest = json.loads((folder / "release.json").read_text())
    manifest["files"]["exchanges.parquet"] = file_sha256(swapped)
    (folder / "release.json").write_text(json.dumps(manifest))

    with pytest.raises(InvalidDataError, match="exchanges") as raised:
        pull(remote.url(folder.name))

    assert _codes(raised) == ["changed-content"]
    assert _cached(cache) == []


def test_a_listed_file_that_is_missing_is_refused_naming_it(
    remote: Remote, toy_release: Release, cache: Path
) -> None:
    folder = remote.publish(toy_release)
    (folder / "units.parquet").unlink()

    with pytest.raises(InvalidDataError, match=r"units\.parquet") as raised:
        pull(remote.url(folder.name))

    assert _codes(raised) == ["remote-file-missing"]
    assert remote.url(folder.name) in str(raised.value)
    assert _cached(cache) == []


def test_a_changed_manifest_is_refused_before_any_data_is_fetched(
    remote: Remote, toy_release: Release, cache: Path
) -> None:
    folder = remote.publish(toy_release)
    manifest = json.loads((folder / "release.json").read_text())
    manifest["licence"] = "something else"
    (folder / "release.json").write_text(json.dumps(manifest))

    with pytest.raises(InvalidDataError) as raised:
        pull(remote.url(folder.name))

    assert _codes(raised) == ["changed-manifest"]
    assert _cached(cache) == []
    if remote.kind == "http":
        assert remote.requests == [f"/{folder.name}/release.json"]


def test_a_url_that_names_another_release_than_the_one_it_holds_is_refused(
    remote: Remote, toy_release: Release, cache: Path
) -> None:
    folder = remote.publish(toy_release)
    wrong = shutil.copytree(folder, remote.host / "toy-background@aaaaaaaaaaaa")

    with pytest.raises(InvalidDataError, match="toy-background@aaaaaaaaaaaa") as raised:
        pull(remote.url(wrong.name))

    assert _codes(raised) == ["release-name"]
    assert _cached(cache) == []
    if remote.kind == "http":
        assert remote.requests == [f"/{wrong.name}/release.json"]


def test_nothing_at_the_url_says_so(remote: Remote, toy_release: Release, cache: Path) -> None:
    remote.publish(toy_release)
    missing = remote.url("toy-background@000000000000")

    with pytest.raises(InvalidDataError, match="release.json") as raised:
        pull(missing)

    assert _codes(raised) == ["remote-release-missing"]
    assert missing in str(raised.value)
    assert _cached(cache) == []


def test_a_url_that_does_not_end_in_a_release_folder_name_is_refused(cache: Path) -> None:
    with pytest.raises(InvalidDataError) as raised:
        pull("https://example.com/releases/latest")

    assert _codes(raised) == ["bad-url"]
    assert "name@hash" in str(raised.value)


def test_a_server_that_cannot_be_reached_is_an_error_not_a_traceback(cache: Path) -> None:
    with pytest.raises(InvalidDataError) as raised:
        pull("http://127.0.0.1:1/toy-background@000000000000")

    assert _codes(raised) == ["remote-error"]
    assert "http://127.0.0.1:1/toy-background@000000000000" in str(raised.value)
    assert _cached(cache) == []


# --- what a URL may hold, and what it needs installed -------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://reader:s3cr3t@example.com/releases/toy-background@000000000000",
        "https://reader@example.com/releases/toy-background@000000000000",
        "s3://AKIAKEY:s3cr3t@bucket/releases/toy-background@000000000000",
        "https://example.com/releases/toy-background@000000000000?token=s3cr3t",
        "https://example.com/releases/toy-background@000000000000?X-Amz-Signature=s3cr3t",
        "gs://bucket/releases/toy-background@000000000000#s3cr3t",
    ],
)
def test_a_url_with_credentials_is_refused_without_repeating_them(url: str, cache: Path) -> None:
    with pytest.raises(InvalidDataError) as raised:
        pull(url)

    assert _codes(raised) == ["url-credentials"]
    assert "s3cr3t" not in str(raised.value)
    assert "AKIAKEY" not in str(raised.value)
    assert "login" in str(raised.value)
    assert _cached(cache) == []


@pytest.mark.parametrize(
    ("scheme", "package", "extra"), [("gs", "gcsfs", "gcs"), ("s3", "s3fs", "s3")]
)
def test_a_missing_extra_names_the_extra_to_install(
    scheme: str, package: str, extra: str, cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, package, None)  # as if it were not installed

    with pytest.raises(InvalidDataError) as raised:
        pull(f"{scheme}://bucket/releases/toy-background@000000000000")

    assert _codes(raised) == ["missing-extra"]
    assert f"headwater[{extra}]" in str(raised.value)
    assert _cached(cache) == []


def test_a_scheme_that_is_not_supported_lists_those_that_are(cache: Path) -> None:
    with pytest.raises(InvalidDataError) as raised:
        pull("ftp://example.com/releases/toy-background@000000000000")

    assert _codes(raised) == ["url-scheme"]
    assert "ftp" in str(raised.value)
    assert "https" in str(raised.value)
    assert "gs" in str(raised.value)


# --- a derived release brings its parent or names it --------------------------------------------


def test_a_derived_release_brings_its_parent_from_the_same_place(
    remote: Remote, toy_release: Release, toy_method: Method, toy_scores: Any, cache: Path
) -> None:
    derived = _derived(toy_release, toy_method, toy_scores)
    remote.publish(toy_release)
    folder = remote.publish(derived)

    pulled = pull(remote.url(folder.name))

    assert pulled.hash == derived.hash
    assert _cached(cache) == sorted([toy_release.path.name, derived.path.name])
    assert Release.open(cache / "releases" / toy_release.path.name).hash == toy_release.hash


def test_a_derived_release_whose_parent_is_cached_fetches_no_parent(
    remote: Remote, toy_release: Release, toy_method: Method, toy_scores: Any, cache: Path
) -> None:
    derived = _derived(toy_release, toy_method, toy_scores)
    pull(toy_release.path)
    folder = remote.publish(derived)  # the parent is not on the remote

    pulled = pull(remote.url(folder.name))

    assert pulled.hash == derived.hash
    assert _cached(cache) == sorted([toy_release.path.name, derived.path.name])


def test_a_derived_release_whose_parent_is_nowhere_is_refused_naming_it(
    remote: Remote, toy_release: Release, toy_method: Method, toy_scores: Any, cache: Path
) -> None:
    derived = _derived(toy_release, toy_method, toy_scores)
    folder = remote.publish(derived)  # the parent is neither cached nor beside it

    with pytest.raises(InvalidDataError) as raised:
        pull(remote.url(folder.name))

    assert _codes(raised) == ["parent-missing"]
    assert f"toy-background@{toy_release.hash}" in str(raised.value)
    assert _cached(cache) == []


def test_a_corrupted_derived_release_leaves_its_parent_out_of_the_cache(
    remote: Remote, toy_release: Release, toy_method: Method, toy_scores: Any, cache: Path
) -> None:
    derived = _derived(toy_release, toy_method, toy_scores)
    remote.publish(toy_release)
    folder = remote.publish(derived)
    file = folder / "indicators" / "toy-climate.parquet"
    file.write_bytes(file.read_bytes() + b"\0")

    with pytest.raises(InvalidDataError, match="toy-climate.parquet"):
        pull(remote.url(folder.name))

    assert _cached(cache) == []


def test_a_derived_folder_needs_its_parent_too(
    toy_release: Release,
    toy_method: Method,
    toy_scores: Any,
    cache: Path,
    tmp_path: Path,
) -> None:
    derived = _derived(toy_release, toy_method, toy_scores)
    alone = shutil.copytree(derived.path, tmp_path / "alone" / derived.path.name)

    with pytest.raises(InvalidDataError) as refused:
        pull(alone)

    assert _codes(refused) == ["parent-missing"]
    assert _cached(cache) == []
    assert pull(derived.path).hash == derived.hash  # its parent is beside it
    assert _cached(cache) == sorted([toy_release.path.name, derived.path.name])


# --- the command line and hw.load ---------------------------------------------------------------


def test_the_command_pulls_from_a_url(remote: Remote, toy_release: Release, cache: Path) -> None:
    folder = remote.publish(toy_release)

    result = runner.invoke(app, ["release", "pull", remote.url(folder.name)])

    assert result.exit_code == 0, result.output
    assert f"Pulled release {folder.name} into {cache / 'releases'}" in result.output
    assert _cached(cache) == [folder.name]


def test_the_command_still_pulls_a_folder(toy_release: Release, cache: Path) -> None:
    result = runner.invoke(app, ["release", "pull", str(toy_release.path)])

    assert result.exit_code == 0, result.output
    assert _cached(cache) == [toy_release.path.name]


def test_the_command_refuses_a_corrupted_pull_without_a_traceback(
    remote: Remote, toy_release: Release, cache: Path
) -> None:
    folder = remote.publish(toy_release)
    file = folder / "flows.parquet"
    file.write_bytes(file.read_bytes() + b"\0")

    result = runner.invoke(app, ["release", "pull", remote.url(folder.name)])

    assert result.exit_code == 1
    assert "flows.parquet" in result.output
    assert "Traceback" not in result.output
    assert _cached(cache) == []


def test_the_command_refuses_a_url_with_credentials(cache: Path) -> None:
    result = runner.invoke(
        app, ["release", "pull", "https://me:s3cr3t@example.com/r/toy-background@000000000000"]
    )

    assert result.exit_code == 1
    assert "s3cr3t" not in result.output
    assert "login" in result.output


def test_load_pulls_a_release_from_a_url_and_loads_it_from_the_cache(
    remote: Remote, toy_release: Release, cache: Path
) -> None:
    folder = remote.publish(toy_release)

    background = hw.load(remote.url(folder.name), method="toy-climate")

    assert background.release is not None
    assert background.release.hash == toy_release.hash
    assert background.release.path == cache / "releases" / folder.name
    assert _cached(cache) == [folder.name]


def test_a_file_url_is_pulled_like_a_folder(
    toy_release: Release, cache: Path, snapshot: Callable[[Path], Any]
) -> None:
    pulled = pull(toy_release.path.as_uri())

    assert pulled.hash == toy_release.hash
    assert snapshot(pulled.path) == snapshot(toy_release.path)
    assert _cached(cache) == [toy_release.path.name]
