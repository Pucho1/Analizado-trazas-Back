"""Spend: totals per period, comparisons between periods and why the cost changed.

Pure and exact (`Decimal`). Periods are ranges of local calendar days; comparisons are
like-for-like (a week or month to date against the same days of the previous one).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum
from statistics import median

from app.core.constants import MIN_BASELINE_DAYS


@dataclass(frozen=True, slots=True)
class SpendTotals:
    """What a set of queries cost and how much text they read and generated."""

    queries: int
    cost: Decimal
    input_tokens: int
    output_tokens: int
    total_tokens: int

    @classmethod
    def zero(cls) -> SpendTotals:
        """Totals of no queries."""
        return cls(queries=0, cost=Decimal(0), input_tokens=0, output_tokens=0, total_tokens=0)

    def __add__(self, other: SpendTotals) -> SpendTotals:
        """Totals of both sets of queries."""
        return SpendTotals(
            queries=self.queries + other.queries,
            cost=self.cost + other.cost,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )

    @property
    def cost_per_query(self) -> Decimal | None:
        """Average cost of one query; None without queries."""
        return None if self.queries == 0 else self.cost / self.queries

    @property
    def input_share(self) -> Decimal | None:
        """Fraction (0-1) of the text that was read rather than generated; None if none."""
        text = self.input_tokens + self.output_tokens
        return None if text == 0 else Decimal(self.input_tokens) / text


def share(part: Decimal, whole: Decimal) -> Decimal | None:
    """`part` as a fraction (0-1) of `whole`; None if `whole` is zero."""
    return None if whole == 0 else part / whole


def relative_change(current: Decimal, previous: Decimal) -> Decimal | None:
    """(current - previous) / previous; None if previous is zero (nothing to compare)."""
    return None if previous == 0 else (current - previous) / previous


class CostDriver(StrEnum):
    """What mainly explains a change in cost."""

    # More or fewer queries.
    VOLUME = "volume"
    # Dearer or cheaper queries.
    PRICE = "price"


def cost_driver(
    current_queries: Decimal,
    current_cost: Decimal,
    previous_queries: Decimal,
    previous_cost: Decimal,
) -> CostDriver | None:
    """Whether volume or price explains most of the cost change.

    cost = queries x cost per query, so the change splits into a volume effect
    (q1 - q0) x c0 and a price effect q1 x (c1 - c0). None if either side has no
    queries or the cost did not change. On a tie, volume.
    """
    if current_queries <= 0 or previous_queries <= 0:
        return None
    previous_unit = previous_cost / previous_queries
    current_unit = current_cost / current_queries
    volume_effect = (current_queries - previous_queries) * previous_unit
    price_effect = current_queries * (current_unit - previous_unit)
    if volume_effect == 0 and price_effect == 0:
        return None
    return CostDriver.VOLUME if abs(volume_effect) >= abs(price_effect) else CostDriver.PRICE


def typical_value(daily_values: Sequence[Decimal]) -> Decimal | None:
    """What "a normal day" means for a metric, from past complete days.

    The median; if it is zero (mostly idle days) the mean; None if that is zero too
    or there are fewer than `MIN_BASELINE_DAYS` days.
    """
    if len(daily_values) < MIN_BASELINE_DAYS:
        return None
    middle = Decimal(median(daily_values))
    if middle != 0:
        return middle
    mean = sum(daily_values, start=Decimal(0)) / len(daily_values)
    return mean if mean != 0 else None


class PeriodKind(StrEnum):
    """Predefined comparison periods."""

    DAY = "day"
    WEEK = "week"
    MONTH = "month"


@dataclass(frozen=True, slots=True)
class DayRange:
    """Local calendar days from `first` to `last`, both included."""

    first: date
    last: date

    def __post_init__(self) -> None:
        """Reject inverted ranges."""
        if self.last < self.first:
            raise ValueError("A day range must not end before it starts")

    @property
    def days(self) -> int:
        """Number of days in the range."""
        return (self.last - self.first).days + 1


def comparison_ranges(day: date) -> dict[PeriodKind, tuple[DayRange, DayRange]]:
    """Current and previous range of each period, for a report about `day`.

    - day: `day` vs the day before.
    - week: Monday to `day` vs the same weekdays of the previous week.
    - month: the 1st to `day` vs the same days of the previous month, cut at its end.
    """
    monday = day - timedelta(days=day.weekday())
    month_start = day.replace(day=1)
    previous_month_end = month_start - timedelta(days=1)
    previous_month_start = previous_month_end.replace(day=1)
    previous_month_last = min(
        previous_month_start + timedelta(days=day.day - 1), previous_month_end
    )
    yesterday = day - timedelta(days=1)
    return {
        PeriodKind.DAY: (DayRange(day, day), DayRange(yesterday, yesterday)),
        PeriodKind.WEEK: (
            DayRange(monday, day),
            DayRange(monday - timedelta(days=7), day - timedelta(days=7)),
        ),
        PeriodKind.MONTH: (
            DayRange(month_start, day),
            DayRange(previous_month_start, previous_month_last),
        ),
    }


@dataclass(frozen=True, slots=True)
class PeriodComparison:
    """Totals of a period against the previous one."""

    period: PeriodKind
    current_range: DayRange
    previous_range: DayRange
    current: SpendTotals
    previous: SpendTotals

    @property
    def cost_change(self) -> Decimal | None:
        """Relative change in cost; None if the previous period cost nothing."""
        return relative_change(self.current.cost, self.previous.cost)

    @property
    def queries_change(self) -> Decimal | None:
        """Relative change in queries; None if the previous period had none."""
        return relative_change(Decimal(self.current.queries), Decimal(self.previous.queries))

    @property
    def driver(self) -> CostDriver | None:
        """Whether volume or price explains the cost change."""
        return cost_driver(
            Decimal(self.current.queries),
            self.current.cost,
            Decimal(self.previous.queries),
            self.previous.cost,
        )
