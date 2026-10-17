"""Spend totals, period comparisons and cost drivers (pure domain)."""

from datetime import date
from decimal import Decimal

import pytest

from app.core.constants import MIN_BASELINE_DAYS
from app.domain.spend import (
    CostDriver,
    DayRange,
    PeriodComparison,
    PeriodKind,
    SpendTotals,
    comparison_ranges,
    cost_driver,
    relative_change,
    share,
    typical_value,
)

D = Decimal


def totals(queries: int = 0, cost: str = "0", inp: int = 0, out: int = 0) -> SpendTotals:
    """Totals with defaults."""
    return SpendTotals(
        queries=queries, cost=D(cost), input_tokens=inp, output_tokens=out, total_tokens=inp + out
    )


# --- totals -------------------------------------------------------------------------


def test_zero_and_addition() -> None:
    assert SpendTotals.zero() == totals()
    assert totals(2, "0.5", 10, 1) + totals(3, "0.25", 5, 4) == totals(5, "0.75", 15, 5)


def test_cost_per_query() -> None:
    assert totals().cost_per_query is None
    assert totals(4, "0.0006").cost_per_query == D("0.00015")


def test_input_share() -> None:
    assert totals().input_share is None
    assert totals(1, inp=900, out=100).input_share == D("0.9")
    assert totals(1, inp=0, out=50).input_share == D(0)


@pytest.mark.parametrize(
    ("part", "whole", "expected"),
    [("1", "4", D("0.25")), ("0", "4", D(0)), ("1", "0", None)],
)
def test_share(part: str, whole: str, expected: Decimal | None) -> None:
    assert share(D(part), D(whole)) == expected


@pytest.mark.parametrize(
    ("current", "previous", "expected"),
    [
        ("135", "100", D("0.35")),
        ("100", "100", D(0)),
        ("0", "100", D(-1)),
        ("5", "0", None),  # nothing to compare with: "new", never +infinity
        ("0", "0", None),
    ],
)
def test_relative_change(current: str, previous: str, expected: Decimal | None) -> None:
    assert relative_change(D(current), D(previous)) == expected


# --- cost driver --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "previous", "expected"),
    [
        (("20", "2"), ("10", "1"), CostDriver.VOLUME),  # twice the queries, same unit cost
        (("10", "2"), ("10", "1"), CostDriver.PRICE),  # same queries, twice the unit cost
        (("110", "165"), ("100", "100"), CostDriver.PRICE),  # +10 % queries, +50 % unit cost
        (("150", "160"), ("100", "100"), CostDriver.VOLUME),  # +50 % queries, ~+7 % unit cost
        (("5", "0.5"), ("10", "1"), CostDriver.VOLUME),  # drops count as well
        (("2", "6"), ("1", "2"), CostDriver.VOLUME),  # tie (2 and 2): volume
    ],
)
def test_cost_driver(
    current: tuple[str, str], previous: tuple[str, str], expected: CostDriver
) -> None:
    assert cost_driver(D(current[0]), D(current[1]), D(previous[0]), D(previous[1])) is expected


@pytest.mark.parametrize(
    ("current", "previous"),
    [
        (("0", "0"), ("10", "1")),  # no queries now
        (("10", "1"), ("0", "0")),  # no queries before
        (("10", "1"), ("10", "1")),  # nothing changed
    ],
)
def test_cost_driver_undefined(current: tuple[str, str], previous: tuple[str, str]) -> None:
    assert cost_driver(D(current[0]), D(current[1]), D(previous[0]), D(previous[1])) is None


# --- typical day --------------------------------------------------------------------


def test_typical_value_needs_enough_days() -> None:
    assert typical_value([D(5)] * (MIN_BASELINE_DAYS - 1)) is None


def test_typical_value_is_the_median() -> None:
    assert typical_value([D(v) for v in (1, 2, 3, 4, 100, 5, 6)]) == D(4)


def test_typical_value_falls_back_to_the_mean_on_idle_days() -> None:
    values = [D(0)] * 5 + [D(14), D(21)]  # median 0, mean 5

    assert typical_value(values) == D(5)


def test_typical_value_of_idle_days_is_none() -> None:
    assert typical_value([D(0)] * 10) is None


# --- comparison ranges --------------------------------------------------------------


def test_ranges_for_a_friday() -> None:
    ranges = comparison_ranges(date(2026, 10, 16))

    assert ranges[PeriodKind.DAY] == (
        DayRange(date(2026, 10, 16), date(2026, 10, 16)),
        DayRange(date(2026, 10, 15), date(2026, 10, 15)),
    )
    assert ranges[PeriodKind.WEEK] == (
        DayRange(date(2026, 10, 12), date(2026, 10, 16)),
        DayRange(date(2026, 10, 5), date(2026, 10, 9)),
    )
    assert ranges[PeriodKind.MONTH] == (
        DayRange(date(2026, 10, 1), date(2026, 10, 16)),
        DayRange(date(2026, 9, 1), date(2026, 9, 16)),
    )


def test_ranges_on_a_monday_and_on_the_first() -> None:
    ranges = comparison_ranges(date(2026, 6, 1))  # a Monday and the 1st

    assert ranges[PeriodKind.WEEK] == (
        DayRange(date(2026, 6, 1), date(2026, 6, 1)),
        DayRange(date(2026, 5, 25), date(2026, 5, 25)),
    )
    assert ranges[PeriodKind.MONTH] == (
        DayRange(date(2026, 6, 1), date(2026, 6, 1)),
        DayRange(date(2026, 5, 1), date(2026, 5, 1)),
    )


@pytest.mark.parametrize(
    ("day", "previous_month"),
    [
        (date(2026, 3, 31), DayRange(date(2026, 2, 1), date(2026, 2, 28))),  # cut at Feb end
        (date(2028, 3, 30), DayRange(date(2028, 2, 1), date(2028, 2, 29))),  # leap year
        (date(2026, 5, 31), DayRange(date(2026, 4, 1), date(2026, 4, 30))),
        (date(2027, 1, 15), DayRange(date(2026, 12, 1), date(2026, 12, 15))),  # across years
    ],
)
def test_previous_month_is_cut_at_its_end(day: date, previous_month: DayRange) -> None:
    assert comparison_ranges(day)[PeriodKind.MONTH][1] == previous_month


def test_week_across_years() -> None:
    current, previous = comparison_ranges(date(2027, 1, 1))[PeriodKind.WEEK]  # a Friday

    assert current == DayRange(date(2026, 12, 28), date(2027, 1, 1))
    assert previous == DayRange(date(2026, 12, 21), date(2026, 12, 25))


def test_day_range() -> None:
    assert DayRange(date(2026, 10, 1), date(2026, 10, 16)).days == 16
    assert DayRange(date(2026, 10, 1), date(2026, 10, 1)).days == 1
    with pytest.raises(ValueError, match="must not end before"):
        DayRange(date(2026, 10, 2), date(2026, 10, 1))


def test_period_comparison() -> None:
    day = DayRange(date(2026, 10, 16), date(2026, 10, 16))
    comparison = PeriodComparison(
        period=PeriodKind.DAY,
        current_range=day,
        previous_range=day,
        current=totals(230, "1.40"),
        previous=totals(170, "1.0370"),
    )

    assert comparison.cost_change == relative_change(D("1.40"), D("1.0370"))
    assert comparison.queries_change == relative_change(D(230), D(170))
    assert comparison.driver is CostDriver.VOLUME


def test_period_comparison_without_history() -> None:
    day = DayRange(date(2026, 10, 16), date(2026, 10, 16))
    comparison = PeriodComparison(PeriodKind.DAY, day, day, totals(5, "0.1"), totals())

    assert (comparison.cost_change, comparison.queries_change, comparison.driver) == (
        None,
        None,
        None,
    )
