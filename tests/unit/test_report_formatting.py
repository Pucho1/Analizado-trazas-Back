"""Spanish (es-ES) formatting of numbers, money and dates."""

from datetime import date
from decimal import Decimal

import pytest

from app.application.reports.formatting import (
    change,
    long_date,
    money,
    number,
    percent,
    plural,
    seconds,
    short_date,
)

D = Decimal


@pytest.mark.parametrize(
    ("value", "decimals", "expected"),
    [
        (12345, 0, "12.345"),
        (1234567, 0, "1.234.567"),
        (D("1.25"), 1, "1,3"),  # half up
        (D("0.5"), 0, "1"),
        (0, 0, "0"),
        (D("1234.5"), 2, "1.234,50"),
    ],
)
def test_number(value: int | Decimal, decimals: int, expected: str) -> None:
    assert number(value, decimals) == expected


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        ("0", "0 $"),
        ("1.40", "1,40 $"),
        ("1.405", "1,41 $"),
        ("1234.5", "1.234,50 $"),
        ("0.01", "0,01 $"),  # one cent: normal format
        ("0.000522", "0,00052 $"),  # below a cent: two significant digits
        ("0.0001605", "0,00016 $"),
        ("0.0004", "0,0004 $"),  # no trailing zero
        ("0.0040", "0,004 $"),
        ("0.033551099987", "0,03 $"),
    ],
)
def test_money(amount: str, expected: str) -> None:
    assert money(D(amount)) == expected


def test_money_never_shows_zero_for_tiny_costs() -> None:
    assert money(D("0.000000123")) == "0,00000012 $"


def test_seconds() -> None:
    assert seconds(D("1.2345")) == "1,2 s"
    assert seconds(D(10)) == "10,0 s"


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [("0.35", "35 %"), ("-0.2", "20 %"), ("1", "100 %"), ("0.004", "menos del 1 %"), ("0", "0 %")],
)
def test_percent(ratio: str, expected: str) -> None:
    assert percent(D(ratio)) == expected


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [
        (D("0.35"), "un 35 % más"),
        (D("-0.2"), "un 20 % menos"),
        (D(0), "lo mismo"),
        (None, "nuevo"),
        (D("1"), "un 100 % más"),  # twice: still a percentage
        (D("1.99"), "un 199 % más"),
        (D("2"), "3 veces más"),  # three times: a multiple
        (D("2.5"), "3,5 veces más"),
        (D("28.34"), "29 veces más"),  # from 10x without decimals
        (D("-0.99"), "un 99 % menos"),
    ],
)
def test_change(ratio: Decimal | None, expected: str) -> None:
    assert change(ratio) == expected


def test_plural() -> None:
    assert plural(1, "consulta", "consultas") == "1 consulta"
    assert plural(0, "consulta", "consultas") == "0 consultas"
    assert plural(1500, "consulta", "consultas") == "1.500 consultas"


def test_dates() -> None:
    assert long_date(date(2026, 10, 16)) == "viernes 16 de octubre de 2026"
    assert short_date(date(2026, 10, 18)) == "domingo 18"
    assert long_date(date(2027, 1, 4)) == "lunes 4 de enero de 2027"
