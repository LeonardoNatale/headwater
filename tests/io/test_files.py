"""What the importers share when they write a file."""

from headwater.io.files import yaml_comments


def test_each_line_is_a_comment() -> None:
    assert yaml_comments(["first", "second"]) == "# first\n# second\n"


def test_a_line_break_inside_a_line_does_not_end_the_comment() -> None:
    # Without this, `key: value` on the second line would be read as YAML.
    assert yaml_comments(["a name\nkey: value", "  spaced   out "]) == (
        "# a name key: value\n# spaced out\n"
    )


def test_no_lines_is_nothing() -> None:
    assert yaml_comments([]) == ""
