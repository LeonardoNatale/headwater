from typing import Annotated

from pydantic import BeforeValidator, Field


def _refuse_yes_no(value: object) -> object:
    """YAML turns an unquoted yes, no, on or off into a bool, and Python counts a bool as 1 or 0."""
    if isinstance(value, bool):
        raise ValueError(
            "YAML read an unquoted word as a yes/no value, but a value is a number. "
            "Write the number, or put quotes around a formula"
        )
    return value


FiniteNumber = Annotated[float, BeforeValidator(_refuse_yes_no), Field(allow_inf_nan=False)]
"""A float that is finite, and never a yes/no value."""
