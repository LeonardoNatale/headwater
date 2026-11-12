"""A database's `background:` pin is checked against the release it is calculated on.

The pin is `name@version`. For a release, the name is the release's name and the version a
prefix of its hash, at least 12 characters. A background read from a folder has no hash.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

import headwater as hw
from headwater.model import InvalidDataError
from tests.conftest import open_database

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"

Pinned = Callable[[str], hw.Database]


@pytest.fixture
def pin_database(toy: Path, edit) -> Pinned:
    """A copy of the T-shirt database with its pin set to `pin`."""

    def make(pin: str) -> hw.Database:
        edit(toy / "tshirt" / "tshirt" / "database.yaml", "toy-background@fixture", pin)
        return open_database(toy / "tshirt" / "tshirt")

    return make


def refused(database: hw.Database, background: hw.Background) -> str:
    with pytest.raises(InvalidDataError) as error:
        database.calc(background, "tshirt:tshirt")
    assert [p.code for p in error.value.problems] == ["background-pin-mismatch"]
    return str(error.value)


def test_a_database_pinned_to_another_release_name_is_refused(
    pin_database: Pinned, release_background: hw.Background
) -> None:
    message = refused(pin_database("other-background@3f9a1c2d4e5f"), release_background)

    assert "other-background@3f9a1c2d4e5f" in message  # what the database pins
    assert "toy-background" in message  # what it was given
    assert release_background.release is not None
    assert release_background.release.hash[:12] in message


def test_a_database_pinned_to_another_hash_is_refused(
    pin_database: Pinned, release_background: hw.Background
) -> None:
    assert release_background.release is not None
    assert release_background.release.hash[:12] != "000000000000"

    message = refused(pin_database("toy-background@000000000000"), release_background)

    assert "toy-background@000000000000" in message
    assert f"toy-background@{release_background.release.hash[:12]}" in message


def test_a_label_names_no_release_and_is_refused_with_the_pin_to_write(
    pin_database: Pinned, release_background: hw.Background
) -> None:
    # `fixture` is what the toy databases pin. On a release, the message says what to pin instead.
    assert release_background.release is not None
    short = f"toy-background@{release_background.release.hash[:12]}"

    message = refused(pin_database("toy-background@latest"), release_background)

    assert "toy-background@latest" in message
    assert f"background: {short}" in message


def test_a_hash_shorter_than_12_characters_is_refused(
    pin_database: Pinned, release_background: hw.Background
) -> None:
    assert release_background.release is not None

    refused(
        pin_database(f"toy-background@{release_background.release.hash[:11]}"), release_background
    )


@pytest.mark.parametrize("length", [12, 20, 64])
def test_the_hash_or_a_prefix_of_12_or_more_characters_is_accepted(
    pin_database: Pinned, release_background: hw.Background, length: int
) -> None:
    assert release_background.release is not None
    database = pin_database(f"toy-background@{release_background.release.hash[:length]}")

    result = database.calc(release_background, "tshirt:tshirt")

    assert result.score == pytest.approx(1.19205, rel=1e-9)
    assert result.provenance.pin == f"toy-background@{release_background.release.hash[:length]}"


def test_a_background_read_from_a_folder_has_no_hash_to_check(
    pin_database: Pinned, background: hw.Background
) -> None:
    assert background.release is None

    # The toy databases pin the label `toy-background@fixture`; any pin goes.
    assert open_database(TOY / "tshirt" / "tshirt").calc(
        background, "tshirt:tshirt"
    ).score == pytest.approx(1.19205, rel=1e-9)
    assert pin_database("anything@at-all").calc(background, "tshirt:tshirt").score == pytest.approx(
        1.19205, rel=1e-9
    )


def test_the_pin_is_checked_before_the_database(
    toy: Path, edit, release_background: hw.Background
) -> None:
    # Whatever else is wrong with a database, a calculation on the wrong release is the first
    # thing to know: nothing it says about the database holds for another release.
    edit(
        toy / "tshirt" / "tshirt" / "database.yaml",
        "toy-background@fixture",
        "toy-background@latest",
    )
    edit(toy / "tshirt" / "tshirt" / "processes" / "tshirt.csv", "toy:cotton-fabric", "toy:nope")
    database = open_database(toy / "tshirt" / "tshirt")

    with pytest.raises(InvalidDataError) as error:
        database.calc(release_background, "tshirt:tshirt")

    assert [p.code for p in error.value.problems] == ["background-pin-mismatch"]
