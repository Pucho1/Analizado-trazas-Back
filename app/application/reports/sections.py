"""Report sections: each turns `ReportData` into channel-neutral content.

Every section answers, for its topic: how much, compared with what, whether it is
worrying and what to do. A section returns None when it has nothing to say, and the
report then leaves it out. The words are business Spanish: never "traza", "span",
"observación" or "tokens" (a test enforces it).
"""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import ClassVar, Protocol

from app.application.reports.content import (
    Block,
    Fact,
    Headline,
    Note,
    Section,
    Subheading,
    Table,
    Tone,
)
from app.application.reports.data import ProfileDay, ReportData
from app.application.reports.formatting import (
    change,
    money,
    number,
    percent,
    plural,
    seconds,
    short_date,
)
from app.core.constants import LEARNING_TRACES
from app.domain.events import EventCount
from app.domain.monitoring import Alert, AlertKind, AlertMetric, AlertScope, Severity
from app.domain.spend import CostDriver, PeriodComparison, PeriodKind, cost_driver, relative_change

# Below this relative difference a day counts as "in line with a normal day".
_IN_LINE = Decimal("0.10")


class ReportSection(Protocol):
    """Contract of every report section; register implementations in a registry."""

    key: ClassVar[str]
    title: ClassVar[str]

    def build(self, data: ReportData) -> Section | None:
        """The section's content for the report day, or None if there is nothing to say."""
        ...


def _queries(count: int) -> str:
    """The number of queries with its noun, e.g. "3 consultas"."""
    return plural(count, "consulta", "consultas")


def _quoted(name: str) -> str:
    """A profile name in Spanish quotes."""
    return f"«{name}»"


_DRIVER_PHRASES = {
    (CostDriver.VOLUME, True): ", por más consultas (no por consultas más caras)",
    (CostDriver.VOLUME, False): ", por menos consultas",
    (CostDriver.PRICE, True): ", por consultas más caras (no por más consultas)",
    (CostDriver.PRICE, False): ", por consultas más baratas",
}


def _driver_phrase(driver: CostDriver | None, increase: bool) -> str:
    """Why the cost moved, as a trailing clause starting with a comma (empty if unknown)."""
    return "" if driver is None else _DRIVER_PHRASES[(driver, increase)]


def _status(alerts: Sequence[Alert]) -> tuple[Tone, str]:
    """Whether the profile's day is worrying, and what to do about it."""
    if any(alert.severity is Severity.CRITICAL for alert in alerts):
        return Tone.CRITICAL, "Requiere atención: mira las alertas."
    if alerts:
        return Tone.ATTENTION, "Conviene vigilarlo: mira las alertas."
    return Tone.OK, "Todo en orden."


class OverviewSection:
    """One sentence per profile: how much, against a normal day, and whether it is fine."""

    key: ClassVar[str] = "overview"
    title: ClassVar[str] = "Resumen"

    def build(self, data: ReportData) -> Section | None:
        """Always says something, even on a quiet day or without profiles."""
        blocks: list[Block] = []
        for profile_day in data.profiles:
            blocks.extend(self._profile(profile_day, data.day))
        if not data.profiles:
            blocks.append(
                Note("Aún no hay perfiles: crea uno para empezar a vigilar tus herramientas.")
            )
        if data.unclassified.queries:
            blocks.append(
                Note(
                    f"{_queries(data.unclassified.queries)} sin perfil "
                    f"({money(data.unclassified.cost)}): asígnalas a un perfil para vigilarlas.",
                    Tone.ATTENTION,
                )
            )
        return Section(self.key, self.title, tuple(blocks))

    def _profile(self, day_data: ProfileDay, day: date) -> list[Block]:
        """Headline (and context notes) for one profile."""
        name, when = _quoted(day_data.profile.name), short_date(day)
        tone, status = _status(day_data.alerts)
        totals = day_data.totals
        if totals.queries == 0:
            headline = f"{name}, {when}: sin actividad."
            blocks: list[Block] = [Headline(headline, Tone.NEUTRAL if tone is Tone.OK else tone)]
        else:
            text = f"{name}, {when}: {_queries(totals.queries)}, {money(totals.cost)}."
            comparison = self._against_normal(day_data)
            blocks = [Headline(" ".join(filter(None, [text, comparison, status])), tone)]
        if day_data.learning:
            blocks.append(
                Note(
                    f"Aún está aprendiendo qué es normal: lleva {_queries(day_data.trace_count)} "
                    f"de {LEARNING_TRACES}."
                )
            )
        if day_data.events.events:
            listed = ", ".join(
                f"{e.label} ({number(e.occurrences)})" for e in day_data.events.events
            )
            blocks.append(Note(f"Eventos: {listed}."))
        return blocks

    @staticmethod
    def _against_normal(day_data: ProfileDay) -> str:
        """The day against a normal one, e.g. "Es un 35 % más que un día normal, ..."."""
        typical = day_data.typical
        if typical.cost is None:
            return ""
        ratio = relative_change(day_data.totals.cost, typical.cost)
        if ratio is None:
            return ""
        if abs(ratio) < _IN_LINE:
            return "En línea con un día normal."
        driver = None
        if typical.queries is not None:
            driver = cost_driver(
                Decimal(day_data.totals.queries),
                day_data.totals.cost,
                typical.queries,
                typical.cost,
            )
        return f"Es {change(ratio)} que un día normal{_driver_phrase(driver, ratio > 0)}."


_ALERT_WHAT = {
    (AlertKind.ANOMALY, AlertScope.TRACE, AlertMetric.LATENCY): "Consultas más lentas de lo normal",
    (AlertKind.ANOMALY, AlertScope.TRACE, AlertMetric.COST): "Consultas más caras de lo normal",
    (AlertKind.ANOMALY, AlertScope.DAY, AlertMetric.QUERY_COUNT): "Más consultas de lo normal",
    (AlertKind.ANOMALY, AlertScope.DAY, AlertMetric.COST): "Gasto del día por encima de lo normal",
    (AlertKind.BUDGET, AlertScope.DAY, AlertMetric.COST): "Presupuesto diario superado",
}
_ALERT_TODO = {
    AlertMetric.LATENCY: "revisa si el proveedor del modelo va lento.",
    AlertMetric.QUERY_COUNT: "comprueba si es uso real o un abuso.",
}


class AlertsSection:
    """The thresholds crossed on the report day, with what to do."""

    key: ClassVar[str] = "alerts"
    title: ClassVar[str] = "Alertas"

    def build(self, data: ReportData) -> Section | None:
        """Nothing if no alerts: silence means everything went well."""
        blocks: list[Block] = []
        for profile_day in data.profiles:
            blocks.extend(alert_blocks(profile_day.profile.name, profile_day.alerts))
        return Section(self.key, self.title, tuple(blocks)) if blocks else None


def alert_blocks(profile_name: str, alerts: Sequence[Alert]) -> list[Block]:
    """A profile's alerts under its name (nothing if there are none).

    Shared by the daily report and the alert messages sent after each scan.
    """
    if not alerts:
        return []
    return [Subheading(profile_name), *(alert_fact(alert) for alert in alerts)]


def alert_fact(alert: Alert) -> Fact:
    """One alert in business words: what, how much, what is normal and what to do."""
    what = _ALERT_WHAT.get((alert.kind, alert.scope, alert.metric), "Valor fuera de lo normal")
    tone = Tone.CRITICAL if alert.severity is Severity.CRITICAL else Tone.ATTENTION
    if alert.kind is AlertKind.BUDGET:
        return Fact(
            what,
            money(alert.observed),
            f"Presupuesto: {money(alert.threshold)}. Qué hacer: sube el presupuesto o "
            "limita el uso.",
            tone,
        )
    normal = alert.baseline_p95 if alert.baseline_p95 is not None else alert.threshold
    if alert.metric is AlertMetric.LATENCY:
        value, limit = f"la peor tardó {seconds(alert.observed)}", seconds(normal)
    elif alert.metric is AlertMetric.QUERY_COUNT:
        value, limit = _queries(int(alert.observed)), number(normal)
    else:
        value, limit = money(alert.observed), money(normal)
    affected = (
        f"{plural(alert.affected_count, 'consulta afectada', 'consultas afectadas')}. "
        if alert.scope is AlertScope.TRACE
        else ""
    )
    todo = _ALERT_TODO.get(
        alert.metric, "revisa si cambió el modelo o si llegan preguntas mucho más largas."
    )
    return Fact(what, value, f"{affected}Lo normal: hasta {limit}. Qué hacer: {todo}", tone)


class BusinessEventsSection:
    """Business events (as the user named them) and technical problems of the day."""

    key: ClassVar[str] = "business_events"
    title: ClassVar[str] = "Eventos"

    def build(self, data: ReportData) -> Section | None:
        """Nothing if no profile had events or technical problems."""
        blocks: list[Block] = []
        for profile_day in data.profiles:
            summary = profile_day.events
            if summary.empty:
                continue
            blocks.append(Subheading(profile_day.profile.name))
            blocks.extend(self._event(event) for event in summary.events)
            if summary.technical_errors.occurrences:
                blocks.append(
                    self._event(
                        summary.technical_errors,
                        Tone.ATTENTION,
                        "Qué hacer: revisa los registros del agente.",
                    )
                )
            if summary.technical_warnings.occurrences:
                blocks.append(self._event(summary.technical_warnings))
        return Section(self.key, self.title, tuple(blocks)) if blocks else None

    @staticmethod
    def _event(event: EventCount, tone: Tone = Tone.NEUTRAL, advice: str = "") -> Fact:
        """One event count."""
        detail = f"en {_queries(event.queries)}." + (f" {advice}" if advice else "")
        return Fact(event.label, number(event.occurrences), detail, tone)


class SpendByProfileSection:
    """What each profile spent, its share and how it moved against the day before."""

    key: ClassVar[str] = "spend_by_profile"
    title: ClassVar[str] = "Gasto por herramienta"
    columns: ClassVar[tuple[str, ...]] = (
        "Herramienta",
        "Consultas",
        "Coste",
        "% del gasto",
        "Frente al día anterior",
    )

    def build(self, data: ReportData) -> Section | None:
        """Nothing if nothing was spent on the day nor the day before."""
        total, previous_total = data.total, data.previous_total
        if total.queries == 0 and previous_total.queries == 0:
            return None
        rows = [
            self._row(p.profile.name, p.totals.queries, p.totals.cost, p.previous.cost, total.cost)
            for p in data.profiles
            if p.totals.queries or p.previous.queries
        ]
        if data.unclassified.queries or data.unclassified_previous.queries:
            rows.append(
                self._row(
                    "Sin perfil",
                    data.unclassified.queries,
                    data.unclassified.cost,
                    data.unclassified_previous.cost,
                    total.cost,
                )
            )
        rows.append(self._row("Total", total.queries, total.cost, previous_total.cost, total.cost))
        table = Table(self.columns, tuple(rows), has_total_row=True)
        return Section(self.key, self.title, (table,))

    @staticmethod
    def _row(
        name: str, queries: int, cost: Decimal, previous: Decimal, total: Decimal
    ) -> tuple[str, ...]:
        """One table row."""
        cost_share = "—" if total == 0 else percent(cost / total)
        moved = "—" if cost == 0 and previous == 0 else change(relative_change(cost, previous))
        return (name, number(queries), money(cost), cost_share, moved)


class PeriodComparisonSection:
    """Day, week and month against the previous ones (same number of days)."""

    key: ClassVar[str] = "period_comparison"
    title: ClassVar[str] = "Evolución"

    def build(self, data: ReportData) -> Section | None:
        """Nothing if there was no activity in any of the periods."""
        if all(c.current.queries == 0 and c.previous.queries == 0 for c in data.comparisons):
            return None
        facts = tuple(self._fact(comparison, data.day) for comparison in data.comparisons)
        return Section(self.key, self.title, facts)

    @staticmethod
    def _fact(comparison: PeriodComparison, day: date) -> Fact:
        """One period against the previous one."""
        labels = {
            PeriodKind.DAY: (short_date(day).capitalize(), "el día anterior"),
            PeriodKind.WEEK: (
                f"Semana hasta el {short_date(day)}",
                "los mismos días de la semana anterior",
            ),
            PeriodKind.MONTH: (
                f"Mes hasta el día {day.day}",
                "los mismos días del mes anterior",
            ),
        }
        label, previous_label = labels[comparison.period]
        current, previous = comparison.current, comparison.previous
        value = f"{money(current.cost)} · {_queries(current.queries)}"
        if previous.queries == 0:
            return Fact(label, value, f"Sin actividad en {previous_label}.")
        ratio = comparison.cost_change
        driver = _driver_phrase(comparison.driver, ratio is not None and ratio > 0)
        detail = (
            f"{change(ratio).capitalize()} que {previous_label} ({money(previous.cost)}){driver}."
        )
        return Fact(label, value, detail)


class TextVolumeSection:
    """How much text each profile's model read and generated."""

    key: ClassVar[str] = "text_volume"
    title: ClassVar[str] = "Volumen de texto"

    def build(self, data: ReportData) -> Section | None:
        """Nothing if no profile had activity."""
        facts: list[Block] = [
            Fact(p.profile.name, self._volume(p), self._detail(p))
            for p in data.profiles
            if p.totals.queries
        ]
        if not facts:
            return None
        intro = Note(
            "Unidades de texto que el modelo lee (preguntas y contexto) y escribe (respuestas)."
        )
        return Section(self.key, self.title, (intro, *facts))

    @staticmethod
    def _volume(profile_day: ProfileDay) -> str:
        """Units of text read and generated."""
        totals = profile_day.totals
        return f"{number(totals.input_tokens)} leídas · {number(totals.output_tokens)} generadas"

    @staticmethod
    def _detail(profile_day: ProfileDay) -> str | None:
        """Share of text read and average per query."""
        input_share = profile_day.totals.input_share
        if input_share is None:
            return None
        per_query = Decimal(profile_day.totals.total_tokens) / profile_day.totals.queries
        return f"{percent(input_share)} es texto leído; {number(per_query)} por consulta de media."


DEFAULT_SECTIONS: tuple[ReportSection, ...] = (
    OverviewSection(),
    AlertsSection(),
    BusinessEventsSection(),
    SpendByProfileSection(),
    PeriodComparisonSection(),
    TextVolumeSection(),
)
