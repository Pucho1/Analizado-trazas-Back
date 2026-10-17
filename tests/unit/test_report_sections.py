"""Report sections on hand-made data: wording, tones and when they stay silent."""

from decimal import Decimal

import pytest

from app.application.reports.content import Fact, Headline, Note, Subheading, Table, Tone
from app.application.reports.data import TypicalDay
from app.application.reports.registry import SectionRegistry, default_registry
from app.application.reports.sections import (
    AlertsSection,
    BusinessEventsSection,
    OverviewSection,
    PeriodComparisonSection,
    SpendByProfileSection,
    TextVolumeSection,
)
from app.domain.monitoring import AlertKind, AlertMetric, AlertScope, Severity
from tests.unit.report_samples import (
    alert,
    comparisons,
    events,
    profile_day,
    report_data,
    totals,
)

D = Decimal


# --- overview -----------------------------------------------------------------------


def test_overview_sentence_answers_the_four_questions() -> None:
    section = OverviewSection().build(report_data(profile_day()))

    assert section is not None
    (headline,) = section.blocks
    assert headline == Headline(
        "«Asistente del portafolio», viernes 16: 230 consultas, 1,40 $. "
        "Es un 35 % más que un día normal, por más consultas (no por consultas más caras). "
        "Todo en orden.",
        Tone.OK,
    )


def test_overview_price_driven_increase() -> None:
    day = profile_day(
        today=totals(170, "2.074"), typical=TypicalDay(queries=D(170), cost=D("1.037"))
    )

    (headline,) = OverviewSection().build(report_data(day)).blocks  # type: ignore[union-attr]

    assert isinstance(headline, Headline)
    assert "Es un 100 % más que un día normal, por consultas más caras" in headline.text


def test_overview_drop() -> None:
    day = profile_day(today=totals(85, "0.5185"), typical=TypicalDay(D(170), D("1.037")))

    (headline,) = OverviewSection().build(report_data(day)).blocks  # type: ignore[union-attr]

    assert isinstance(headline, Headline)
    assert "Es un 50 % menos que un día normal, por menos consultas." in headline.text


def test_overview_in_line_with_normal() -> None:
    day = profile_day(today=totals(170, "1.10"))

    (headline,) = OverviewSection().build(report_data(day)).blocks  # type: ignore[union-attr]

    assert isinstance(headline, Headline)
    assert "En línea con un día normal." in headline.text


def test_overview_without_a_normal_day_makes_no_comparison() -> None:
    day = profile_day(typical=TypicalDay(queries=None, cost=None))

    (headline,) = OverviewSection().build(report_data(day)).blocks  # type: ignore[union-attr]

    assert isinstance(headline, Headline)
    assert "normal" not in headline.text


def test_overview_quiet_day() -> None:
    day = profile_day(today=totals())

    (headline,) = OverviewSection().build(report_data(day)).blocks  # type: ignore[union-attr]

    assert headline == Headline("«Asistente del portafolio», viernes 16: sin actividad.")


@pytest.mark.parametrize(
    ("severity", "tone", "status"),
    [
        (Severity.CRITICAL, Tone.CRITICAL, "Requiere atención: mira las alertas."),
        (Severity.WARNING, Tone.ATTENTION, "Conviene vigilarlo: mira las alertas."),
    ],
)
def test_overview_status_follows_the_alerts(severity: Severity, tone: Tone, status: str) -> None:
    day = profile_day(alerts=(alert(Severity.WARNING), alert(severity)))

    (headline,) = OverviewSection().build(report_data(day)).blocks  # type: ignore[union-attr]

    assert isinstance(headline, Headline)
    assert headline.tone is tone
    assert headline.text.endswith(status)


def test_overview_learning_and_events_notes() -> None:
    day = profile_day(
        trace_count=21,
        summary=events(("Intentos de manipulación", 3, 3), ("Respuestas de reserva", 2, 2)),
    )

    blocks = OverviewSection().build(report_data(day)).blocks  # type: ignore[union-attr]

    assert blocks[1:] == (
        Note("Aún está aprendiendo qué es normal: lleva 21 consultas de 100."),
        Note("Eventos: Intentos de manipulación (3), Respuestas de reserva (2)."),
    )


def test_overview_without_profiles_and_with_unclassified_traffic() -> None:
    data = report_data(unclassified=totals(11, "0.00028"))

    blocks = OverviewSection().build(data).blocks  # type: ignore[union-attr]

    assert blocks == (
        Note("Aún no hay perfiles: crea uno para empezar a vigilar tus herramientas."),
        Note(
            "11 consultas sin perfil (0,00028 $): asígnalas a un perfil para vigilarlas.",
            Tone.ATTENTION,
        ),
    )


# --- alerts -------------------------------------------------------------------------


def test_no_alerts_no_section() -> None:
    assert AlertsSection().build(report_data(profile_day())) is None


def test_alerts_in_business_words() -> None:
    day = profile_day(
        alerts=(
            alert(Severity.CRITICAL),
            alert(
                metric=AlertMetric.COST, observed="0.05", threshold="0.02", p95="0.01", affected=1
            ),
            alert(
                scope=AlertScope.DAY,
                metric=AlertMetric.QUERY_COUNT,
                observed="36",
                threshold="24",
                p95="12",
            ),
            alert(
                Severity.CRITICAL,
                kind=AlertKind.BUDGET,
                scope=AlertScope.DAY,
                metric=AlertMetric.COST,
                observed="1.75",
                threshold="1.50",
                p95=None,
            ),
        )
    )

    blocks = AlertsSection().build(report_data(day)).blocks  # type: ignore[union-attr]

    assert blocks == (
        Subheading("Asistente del portafolio"),
        Fact(
            "Consultas más lentas de lo normal",
            "la peor tardó 10,0 s",
            "3 consultas afectadas. Lo normal: hasta 1,2 s. "
            "Qué hacer: revisa si el proveedor del modelo va lento.",
            Tone.CRITICAL,
        ),
        Fact(
            "Consultas más caras de lo normal",
            "0,05 $",
            "1 consulta afectada. Lo normal: hasta 0,01 $. Qué hacer: revisa si cambió el "
            "modelo o si llegan preguntas mucho más largas.",
            Tone.ATTENTION,
        ),
        Fact(
            "Más consultas de lo normal",
            "36 consultas",
            "Lo normal: hasta 12. Qué hacer: comprueba si es uso real o un abuso.",
            Tone.ATTENTION,
        ),
        Fact(
            "Presupuesto diario superado",
            "1,75 $",
            "Presupuesto: 1,50 $. Qué hacer: sube el presupuesto o limita el uso.",
            Tone.CRITICAL,
        ),
    )


# --- business events ----------------------------------------------------------------


def test_no_events_no_section() -> None:
    assert BusinessEventsSection().build(report_data(profile_day())) is None


def test_events_and_technical_problems() -> None:
    day = profile_day(
        summary=events(("Intentos de manipulación", 3, 3), errors=(40, 12), warnings=(2, 1))
    )

    blocks = BusinessEventsSection().build(report_data(day)).blocks  # type: ignore[union-attr]

    assert blocks == (
        Subheading("Asistente del portafolio"),
        Fact("Intentos de manipulación", "3", "en 3 consultas."),
        Fact(
            "Errores técnicos",
            "40",
            "en 12 consultas. Qué hacer: revisa los registros del agente.",
            Tone.ATTENTION,
        ),
        Fact("Avisos técnicos", "2", "en 1 consulta."),
    )


# --- spend by profile ---------------------------------------------------------------


def test_spend_table_with_unclassified_and_total() -> None:
    data = report_data(
        profile_day(),
        profile_day("Pruebas", today=totals(), previous=totals(5, "0.05"), profile_id=2),
        profile_day("Inactivo", today=totals(), previous=totals(), profile_id=3),
        unclassified=totals(10, "0.60"),
    )

    (table,) = SpendByProfileSection().build(data).blocks  # type: ignore[union-attr]

    assert isinstance(table, Table)
    assert table.has_total_row
    assert table.rows == (
        ("Asistente del portafolio", "230", "1,40 $", "70 %", "un 17 % más"),
        ("Pruebas", "0", "0 $", "0 %", "un 100 % menos"),
        ("Sin perfil", "10", "0,60 $", "30 %", "nuevo"),
        ("Total", "240", "2,00 $", "100 %", "un 60 % más"),
    )


def test_spend_table_silent_on_two_empty_days() -> None:
    data = report_data(profile_day(today=totals(), previous=totals()))

    assert SpendByProfileSection().build(data) is None


# --- period comparison --------------------------------------------------------------


def test_period_comparison() -> None:
    blocks = PeriodComparisonSection().build(report_data()).blocks  # type: ignore[union-attr]

    assert blocks[0] == Fact(
        "Viernes 16",
        "1,40 $ · 230 consultas",
        "Un 35 % más que el día anterior (1,04 $), por más consultas (no por consultas más caras).",
    )
    assert [fact.label for fact in blocks] == [  # type: ignore[union-attr]
        "Viernes 16",
        "Semana hasta el viernes 16",
        "Mes hasta el día 16",
    ]


def test_period_comparison_without_history() -> None:
    data = report_data(periods=comparisons(previous=totals()))

    first = PeriodComparisonSection().build(data).blocks[0]  # type: ignore[union-attr]

    assert first == Fact(
        "Viernes 16", "1,40 $ · 230 consultas", "Sin actividad en el día anterior."
    )


def test_period_comparison_silent_without_activity() -> None:
    data = report_data(periods=comparisons(current=totals(), previous=totals()))

    assert PeriodComparisonSection().build(data) is None


# --- text volume --------------------------------------------------------------------


def test_text_volume() -> None:
    blocks = TextVolumeSection().build(report_data(profile_day())).blocks  # type: ignore[union-attr]

    assert blocks[1] == Fact(
        "Asistente del portafolio",
        "150.000 leídas · 20.000 generadas",
        "88 % es texto leído; 739 por consulta de media.",
    )


def test_text_volume_silent_without_activity() -> None:
    assert TextVolumeSection().build(report_data(profile_day(today=totals()))) is None


# --- registry -----------------------------------------------------------------------


def test_default_registry_has_every_section_once() -> None:
    keys = [section.key for section in default_registry().all()]

    assert keys == [
        "overview",
        "alerts",
        "business_events",
        "spend_by_profile",
        "period_comparison",
        "text_volume",
    ]


def test_registry_rejects_duplicate_keys() -> None:
    registry = SectionRegistry([OverviewSection()])

    with pytest.raises(ValueError, match="already registered"):
        registry.register(OverviewSection())
    assert registry.get("missing") is None
