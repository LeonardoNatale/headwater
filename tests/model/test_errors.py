import pickle

from headwater.model import InvalidDataError, Problem, RecordError, Report, Severity


def problems() -> list[Problem]:
    return [Problem("a", "tshirt:tshirt line e02", "first"), Problem("b", "flow toy:co2", "second")]


def test_invalid_data_error_survives_pickling() -> None:
    # Errors cross process boundaries, for example from a process pool.
    error = InvalidDataError(problems())

    back = pickle.loads(pickle.dumps(error))

    assert back.problems == error.problems
    assert str(back) == str(error)


def test_record_error_survives_pickling() -> None:
    error = RecordError(problems())

    back = pickle.loads(pickle.dumps(error))

    assert back.problems == error.problems
    assert str(back) == str(error)


def test_problem_str_names_item_message_and_location() -> None:
    problem = Problem("x", "tshirt:tshirt line e02", "bad", location="processes/tshirt.csv:3")

    assert str(problem) == "tshirt:tshirt line e02: bad [processes/tshirt.csv:3]"


def test_a_report_prints_what_it_holds() -> None:
    assert repr(Report()) == "Report(no problems)"
    report = Report(
        (
            Problem("w", "study tshirt: goal", "is still a placeholder", Severity.warning),
            *problems(),
        )
    )
    assert repr(report) == "\n".join(
        [
            "Report(2 errors, 1 warning)",
            "  error: tshirt:tshirt line e02: first",
            "  error: flow toy:co2: second",
            "  warning: study tshirt: goal: is still a placeholder",
        ]
    )


def test_a_long_report_prints_its_first_lines_and_counts_the_rest() -> None:
    report = Report(tuple(Problem("a", f"line e{i:02}", "bad") for i in range(1, 9)))

    lines = repr(report).splitlines()

    assert lines[0] == "Report(8 errors)"
    assert lines[1:] == [*(f"  error: line e{i:02}: bad" for i in range(1, 6)), "  and 3 more"]
