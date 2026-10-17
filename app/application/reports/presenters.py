"""Presenters: lay a channel-neutral `Report` out for one channel. Formatting only.

- Email: a self-contained HTML document with inline styles (mail clients ignore
  stylesheets).
- Telegram: short text in Telegram's HTML parse mode (<b>, <i>), within its length
  limit; sections that do not fit are dropped from the end, with a note.
"""

from html import escape
from typing import ClassVar, Protocol

from app.application.reports.content import (
    Block,
    Fact,
    Headline,
    Note,
    RenderedReport,
    Report,
    Section,
    Subheading,
    Table,
    Tone,
)
from app.core.constants import TELEGRAM_MAX_LENGTH
from app.domain.reporting import Channel


class ReportPresenter(Protocol):
    """Lays a report out for one channel."""

    channel: ClassVar[Channel]

    def render(self, report: Report) -> RenderedReport:
        """The report as the channel needs it."""
        ...


def _subject(report: Report) -> str:
    """The report title, flagged when something needs attention."""
    flags = {Tone.CRITICAL: "[Requiere atención] ", Tone.ATTENTION: "[A vigilar] "}
    return f"{flags.get(report.tone, '')}{report.title}"


_COLORS = {
    Tone.NEUTRAL: "#24292f",
    Tone.OK: "#1a7f37",
    Tone.ATTENTION: "#9a6700",
    Tone.CRITICAL: "#cf222e",
}
_FONT = "font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;"


class EmailPresenter:
    """Renders reports as HTML email."""

    channel: ClassVar[Channel] = Channel.EMAIL

    def render(self, report: Report) -> RenderedReport:
        """A complete HTML document."""
        sections = "".join(self._section(section) for section in report.sections)
        body = (
            '<!doctype html><html lang="es"><head><meta charset="utf-8">'
            f"<title>{escape(report.title)}</title></head>"
            f'<body style="margin:0;padding:24px;background:#f6f8fa;{_FONT}">'
            '<div style="max-width:640px;margin:0 auto;background:#ffffff;'
            'border:1px solid #d0d7de;border-radius:8px;padding:24px;">'
            f'<h1 style="font-size:20px;margin:0 0 16px;color:#24292f;">'
            f"{escape(report.title)}</h1>{sections}</div></body></html>"
        )
        return RenderedReport(_subject(report), "text/html; charset=utf-8", body)

    def _section(self, section: Section) -> str:
        """One section with its title."""
        blocks = "".join(self._block(block) for block in section.blocks)
        return (
            '<h2 style="font-size:16px;margin:24px 0 8px;color:#24292f;'
            f'border-bottom:1px solid #d0d7de;padding-bottom:4px;">{escape(section.title)}</h2>'
            f"{blocks}"
        )

    def _block(self, block: Block) -> str:
        """One content block."""
        match block:
            case Headline(text=text, tone=tone):
                return (
                    f'<p style="margin:8px 0;padding:8px 12px;border-left:4px solid '
                    f'{_COLORS[tone]};font-size:15px;color:#24292f;">{escape(text)}</p>'
                )
            case Subheading(text=text):
                return f'<h3 style="font-size:14px;margin:16px 0 4px;">{escape(text)}</h3>'
            case Fact(label=label, value=value, detail=detail, tone=tone):
                extra = (
                    f'<br><span style="color:#57606a;font-size:13px;">{escape(detail)}</span>'
                    if detail
                    else ""
                )
                return (
                    f'<p style="margin:6px 0;"><span style="color:{_COLORS[tone]};">'
                    f"<strong>{escape(label)}:</strong></span> {escape(value)}{extra}</p>"
                )
            case Note(text=text, tone=tone):
                return (
                    f'<p style="margin:6px 0;color:{_COLORS[tone]};font-size:13px;">'
                    f"{escape(text)}</p>"
                )
            case Table():
                return self._table(block)
        raise TypeError(f"Unknown block {block!r}")

    @staticmethod
    def _table(table: Table) -> str:
        """A table; numbers right-aligned, the total row in bold."""
        cell = "padding:4px 8px;border-bottom:1px solid #d0d7de;font-size:13px;"
        header = "".join(
            f'<th style="{cell}text-align:{"left" if i == 0 else "right"};">{escape(c)}</th>'
            for i, c in enumerate(table.columns)
        )
        rows = []
        for index, row in enumerate(table.rows):
            weight = (
                "font-weight:bold;" if table.has_total_row and index == len(table.rows) - 1 else ""
            )
            cells = "".join(
                f'<td style="{cell}{weight}text-align:{"left" if i == 0 else "right"};">'
                f"{escape(value)}</td>"
                for i, value in enumerate(row)
            )
            rows.append(f"<tr>{cells}</tr>")
        return (
            '<table style="border-collapse:collapse;width:100%;margin:8px 0;">'
            f"<thead><tr>{header}</tr></thead><tbody>{''.join(rows)}</tbody></table>"
        )


_MARKS = {Tone.NEUTRAL: "•", Tone.OK: "✅", Tone.ATTENTION: "⚠️", Tone.CRITICAL: "🔴"}
_TRUNCATED = "\n\n<i>(Resumen recortado: el informe completo llega por email.)</i>"


def _text(value: str) -> str:
    """Text escaped for Telegram's HTML parse mode."""
    return escape(value, quote=False)


class TelegramPresenter:
    """Renders reports as a short Telegram message (HTML parse mode)."""

    channel: ClassVar[Channel] = Channel.TELEGRAM

    def __init__(self, max_length: int = TELEGRAM_MAX_LENGTH) -> None:
        """Keep messages within `max_length` characters."""
        self._max_length = max_length

    def render(self, report: Report) -> RenderedReport:
        """The message text; sections that do not fit are dropped from the end."""
        title = f"<b>{_text(report.title)}</b>"
        parts = [self._section(section) for section in report.sections]
        body = "\n\n".join([title, *parts])
        while len(body) > self._max_length and parts:
            parts.pop()
            body = "\n\n".join([title, *parts]) + _TRUNCATED
        return RenderedReport(
            _subject(report), "text/html; charset=utf-8", body[: self._max_length]
        )

    def _section(self, section: Section) -> str:
        """One section: bold title and one line per block."""
        lines = [f"<b>{_text(section.title)}</b>"]
        lines.extend(self._block(block) for block in section.blocks)
        return "\n".join(lines)

    @staticmethod
    def _block(block: Block) -> str:
        """One block as one or more lines."""
        match block:
            case Headline(text=text, tone=tone):
                return f"{_MARKS[tone]} {_text(text)}"
            case Subheading(text=text):
                return f"<i>{_text(text)}</i>"
            case Fact(label=label, value=value, detail=detail, tone=tone):
                extra = f" — {_text(detail)}" if detail else ""
                return f"{_MARKS[tone]} {_text(label)}: <b>{_text(value)}</b>{extra}"
            case Note(text=text, tone=tone):
                mark = "" if tone is Tone.NEUTRAL else f"{_MARKS[tone]} "
                return f"{mark}<i>{_text(text)}</i>"
            case Table(columns=columns, rows=rows):
                return "\n".join(
                    f"• {_text(row[0])}: "
                    + " · ".join(
                        f"{_text(c.lower())} {_text(v)}"
                        for c, v in zip(columns[1:], row[1:], strict=True)
                    )
                    for row in rows
                )
        raise TypeError(f"Unknown block {block!r}")
