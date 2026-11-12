from datetime import date

import pytest
from pydantic import ValidationError

from headwater.model import Exchange, Process, ProcessDetails, ProcessKind, Role

DETAILS = {
    "name": "cotton fabric production",
    "location": "IN",
    "kind": "ordinary_transforming_activity",
}


def test_the_kinds_are_the_ecospold2_special_activity_types() -> None:
    assert sorted(k.value for k in ProcessKind) == [
        "correction_activity",
        "export_activity",
        "import_activity",
        "io_activity",
        "market_activity",
        "market_group",
        "ordinary_transforming_activity",
        "production_mix",
        "re_export_activity",
        "residual_activity",
        "supply_mix",
    ]


def test_treatment_is_not_a_kind() -> None:
    assert "treatment" not in {k.value for k in ProcessKind}
    with pytest.raises(ValidationError, match="kind"):
        ProcessDetails.model_validate({**DETAILS, "kind": "treatment"})


def test_details() -> None:
    details = ProcessDetails.model_validate(
        {**DETAILS, "time_start": date(2024, 1, 1), "time_end": date(2024, 12, 31)}
    )

    assert details.kind is ProcessKind.ordinary_transforming_activity
    assert details.time_start == date(2024, 1, 1)


@pytest.mark.parametrize("field", ["name", "location", "kind"])
def test_name_location_and_kind_are_required(field: str) -> None:
    fields = {k: v for k, v in DETAILS.items() if k != field}

    with pytest.raises(ValidationError, match=field):
        ProcessDetails.model_validate(fields)


def test_time_end_may_not_be_before_time_start() -> None:
    with pytest.raises(
        ValidationError, match="time_end 2024-01-01 is before time_start 2024-12-31"
    ):
        ProcessDetails.model_validate(
            {**DETAILS, "time_start": date(2024, 12, 31), "time_end": date(2024, 1, 1)}
        )


def test_one_day_is_a_valid_period() -> None:
    day = date(2024, 5, 5)

    assert ProcessDetails.model_validate({**DETAILS, "time_start": day, "time_end": day})


def test_a_process_is_its_details_plus_an_id_and_exchanges() -> None:
    reference = Exchange(
        process_id="toy:fabric",
        line_id="e01",
        role=Role.reference,
        flow_id="toy:cotton-fabric",
        amount=1,
        unit="kg",
    )

    process = Process.model_validate({**DETAILS, "id": "toy:fabric", "exchanges": (reference,)})

    assert (process.id, process.name, process.location) == ("toy:fabric", DETAILS["name"], "IN")
    assert process.reference_exchange == reference
