"""Email and Telegram presenters: layout, escaping, subject flags and length limit."""

from datetime import date

from app.application.reports.content import (
    Fact,
    Headline,
    Note,
    Report,
    Section,
    Subheading,
    Table,
    Tone,
)
from app.application.reports.presenters import EmailPresenter, TelegramPresenter

DAY = date(2026, 10, 16)


def report(*sections: Section) -> Report:
    """A report about `DAY`."""
    return Report(title="Observa · viernes 16 de octubre de 2026", day=DAY, sections=sections)


RICH = report(
    Section("overview", "Resumen", (Headline("«Web <beta>», ok & bien.", Tone.OK),)),
    Section(
        "alerts",
        "Alertas",
        (
            Subheading("Web"),
            Fact("Más lento", "10,0 s", "Qué hacer: revisa.", Tone.CRITICAL),
            Note("Nota <i>", Tone.ATTENTION),
        ),
    ),
    Section(
        "spend_by_profile",
        "Gasto",
        (
            Table(
                ("Herramienta", "Consultas", "Coste"),
                (("Web", "230", "1,40 $"), ("Total", "230", "1,40 $")),
                has_total_row=True,
            ),
        ),
    ),
)


def test_email_is_a_complete_escaped_html_document() -> None:
    rendered = EmailPresenter().render(RICH)

    assert rendered.content_type == "text/html; charset=utf-8"
    assert rendered.body.startswith('<!doctype html><html lang="es">')
    assert "«Web &lt;beta&gt;», ok &amp; bien." in rendered.body
    assert "<beta>" not in rendered.body
    assert "<h2" in rendered.body and ">Alertas</h2>" in rendered.body
    assert "font-weight:bold;" in rendered.body  # total row
    assert "#cf222e" in rendered.body  # critical colour


def test_subject_flags_attention_and_critical() -> None:
    calm = report(Section("overview", "Resumen", (Headline("ok", Tone.OK),)))
    attention = report(Section("o", "R", (Note("x", Tone.ATTENTION),)))

    assert EmailPresenter().render(calm).subject == "Observa · viernes 16 de octubre de 2026"
    assert EmailPresenter().render(attention).subject.startswith("[A vigilar] ")
    assert EmailPresenter().render(RICH).subject.startswith("[Requiere atención] ")


def test_telegram_message() -> None:
    rendered = TelegramPresenter().render(RICH)

    assert rendered.body.splitlines() == [
        "<b>Observa · viernes 16 de octubre de 2026</b>",
        "",
        "<b>Resumen</b>",
        "✅ «Web &lt;beta&gt;», ok &amp; bien.",
        "",
        "<b>Alertas</b>",
        "<i>Web</i>",
        "🔴 Más lento: <b>10,0 s</b> — Qué hacer: revisa.",
        "⚠️ <i>Nota &lt;i&gt;</i>",
        "",
        "<b>Gasto</b>",
        "• Web: consultas 230 · coste 1,40 $",
        "• Total: consultas 230 · coste 1,40 $",
    ]


def test_telegram_drops_sections_that_do_not_fit() -> None:
    long_note = Note("x" * 300)
    big = report(*(Section(f"s{i}", f"Sección {i}", (long_note,)) for i in range(10)))

    rendered = TelegramPresenter(max_length=1000).render(big)

    assert len(rendered.body) <= 1000
    assert "<b>Sección 0</b>" in rendered.body
    assert "<b>Sección 9</b>" not in rendered.body
    assert rendered.body.endswith(
        "<i>(Resumen recortado: el informe completo llega por email.)</i>"
    )


def test_empty_report() -> None:
    assert TelegramPresenter().render(report()).body == (
        "<b>Observa · viernes 16 de octubre de 2026</b>"
    )
    assert "<h1" in EmailPresenter().render(report()).body
