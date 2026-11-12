from pathlib import Path

import pytest

from headwater.model import InvalidDataError
from headwater.release import Release, build, find, home, pull


@pytest.fixture
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """HEADWATER_HOME in a temporary folder, so no test touches the real ~/.headwater."""
    path = tmp_path / "home"
    monkeypatch.setenv("HEADWATER_HOME", str(path))
    return path


def test_home_defaults_to_dot_headwater(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HEADWATER_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert home() == tmp_path / ".headwater"


def test_home_follows_headwater_home(cache: Path) -> None:
    assert home() == cache


def test_pull_copies_a_release_into_the_cache_by_hash(
    toy_release: Release, cache: Path, snapshot
) -> None:
    pulled = pull(toy_release.path)

    assert pulled.path == cache / "releases" / f"toy-background@{toy_release.hash[:12]}"
    assert pulled.hash == toy_release.hash
    assert snapshot(pulled.path) == snapshot(toy_release.path)
    assert [p.name for p in (cache / "releases").iterdir()] == [pulled.path.name]


def test_pulling_twice_keeps_one_copy(toy_release: Release, cache: Path) -> None:
    first = pull(toy_release.path)
    second = pull(str(toy_release.path))

    assert second.path == first.path
    assert [p.name for p in (cache / "releases").iterdir()] == [first.path.name]


def test_pull_refuses_a_tampered_release_and_caches_nothing(
    toy_release: Release, cache: Path
) -> None:
    path = toy_release.path / "flows.parquet"
    path.write_bytes(path.read_bytes() + b"\0")

    with pytest.raises(InvalidDataError, match="flows.parquet"):
        pull(toy_release.path)

    assert not (cache / "releases").exists() or not any((cache / "releases").iterdir())


def test_find_resolves_name_at_hash_in_the_cache(toy_release: Release, cache: Path) -> None:
    pulled = pull(toy_release.path)

    assert find(f"toy-background@{toy_release.hash[:12]}").path == pulled.path
    assert find(f"toy-background@{toy_release.hash}").path == pulled.path


@pytest.mark.parametrize(
    ("ref", "code"),
    [
        ("toy-background@000000000000", "release-not-found"),
        ("toy-background@abc", "short-hash"),
        ("toy-background", "bad-ref"),
        ("../toy-background@000000000000", "bad-ref"),
    ],
)
def test_find_fails_naming_what_it_looked_for(
    toy_release: Release, cache: Path, ref: str, code: str
) -> None:
    pull(toy_release.path)

    with pytest.raises(InvalidDataError) as raised:
        find(ref)

    assert [(p.code, p.item) for p in raised.value.problems] == [(code, ref)]


def test_find_refuses_a_full_hash_that_does_not_match(toy_release: Release, cache: Path) -> None:
    pull(toy_release.path)
    wrong = toy_release.hash[:12] + "0" * 52

    with pytest.raises(InvalidDataError) as raised:
        find(f"toy-background@{wrong}")

    assert [p.code for p in raised.value.problems] == ["hash-mismatch"]


def test_build_writes_into_the_cache_by_default(records, info, cache: Path) -> None:
    built = build(info, processes=records.processes, flows=records.flows, issues=records.issues)

    assert built.path.parent == cache / "releases"
