"""Numbers, money and dates written the Spanish way (es-ES).

Decimal comma, thousands dot, a space before "%" and "$" after the amount.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

_WEEKDAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
_MONTHS = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)
_CENT = Decimal("0.01")
# A change of +200 % or more (3x) is written as a multiple; from 10x without decimals.
_TIMES_FROM = Decimal(2)
_WHOLE_TIMES_FROM = Decimal(10)


def _spanish(rounded: Decimal, decimals: int) -> str:
    """`rounded` with `decimals` decimals, a thousands dot and a decimal comma."""
    text = f"{rounded:,.{decimals}f}"
    return text.replace(",", "\0").replace(".", ",").replace("\0", ".")


def number(value: int | Decimal, decimals: int = 0) -> str:
    """A quantity, e.g. 12345 -> "12.345", (1.25, 1) -> "1,3"."""
    quantum = Decimal(1).scaleb(-decimals)
    return _spanish(Decimal(value).quantize(quantum, rounding=ROUND_HALF_UP), decimals)


def money(amount: Decimal) -> str:
    """An amount in USD.

    From one cent up, two decimals ("1,40 $"); below, two significant digits
    ("0,00052 $") so that tiny costs never show as "0,00 $".
    """
    if amount == 0:
        return "0 $"
    if abs(amount) >= _CENT:
        return f"{number(amount, 2)} $"
    decimals = -amount.adjusted() + 1
    return f"{number(amount, decimals).rstrip('0')} $"


def seconds(value: Decimal) -> str:
    """A duration in seconds with one decimal, e.g. "1,2 s"."""
    return f"{number(value, 1)} s"


def percent(ratio: Decimal) -> str:
    """A fraction (0-1) as a whole percentage; "menos del 1 %" for tiny non-zero values."""
    value = (abs(ratio) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    if value == 0 and ratio != 0:
        return "menos del 1 %"
    return f"{number(value)} %"


def change(ratio: Decimal | None) -> str:
    """A relative change in words.

    "un 35 % más", "un 20 % menos", "lo mismo", "nuevo"; from three times the previous
    value on, as a multiple ("3,5 veces más", "29 veces más"), which reads better than
    "un 2.834 % más".
    """
    if ratio is None:
        return "nuevo"
    if ratio == 0:
        return "lo mismo"
    if ratio >= _TIMES_FROM:
        times = ratio + 1
        written = number(times, 0 if times >= _WHOLE_TIMES_FROM else 1).removesuffix(",0")
        return f"{written} veces más"
    direction = "más" if ratio > 0 else "menos"
    return f"un {percent(ratio)} {direction}"


def plural(count: int, singular: str, plural_form: str) -> str:
    """A count with its noun, e.g. "1 consulta", "3 consultas"."""
    return f"{number(count)} {singular if count == 1 else plural_form}"


def long_date(day: date) -> str:
    """The full date, e.g. "viernes 16 de octubre de 2026"."""
    return f"{_WEEKDAYS[day.weekday()]} {day.day} de {_MONTHS[day.month - 1]} de {day.year}"


def short_date(day: date) -> str:
    """Weekday and day of the month, e.g. "viernes 16"."""
    return f"{_WEEKDAYS[day.weekday()]} {day.day}"
