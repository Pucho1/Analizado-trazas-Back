"""`ReportService` end to end over in-memory repositories."""

import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.application.reports.data import ReportDataBuilder
from app.application.reports.presenters import EmailPresenter, TelegramPresenter
from app.application.reports.registry import default_registry
from app.application.reports.service import ReportService
from app.application.spend_service import SpendService
from app.core.exceptions import UnknownReportSectionError
from app.domain.events import EventMapping
from app.domain.models import ObservationLevel, ProfileRule, ProfileStatus, Trace
from app.domain.monitoring import Alert, AlertKind, AlertMetric, AlertScope, Severity
from app.domain.reporting import Channel
from tests.unit.fakes import (
    InMemoryEventMappingRepository,
    InMemoryMonitoringRepository,
    InMemoryProfileRepository,
    InMemoryReportSettingsRepository,
    InMemorySpendRepository,
    make_observation,
    make_profile,
    make_trace,
)

pytestmark = pytest.mark.anyio

MADRID = ZoneInfo("Europe/Madrid")
NOW = datetime(2026, 10, 17, 8, 30, tzinfo=UTC)  # 10:30 in Madrid
YESTERDAY = date(2026, 10, 16)
# Words the user must never read (whole words, any case).
FORBIDDEN = re.compile(r"\b(trazas?|spans?|observaci[oó]n(es)?|tokens?)\b", re.IGNORECASE)


def trace(id: str, day_offset: int, *, cost: str = "0.006", steps: tuple[str, ...] = ()) -> Trace:
    """A trace of the web assistant `day_offset` days before today, 12:00 UTC."""
    at = datetime(2026, 10, 17, 12, tzinfo=UTC) - timedelta(days=day_offset)
    observations = tuple(
        make_observation(
            id=f"{id}-{i}",
            name=name,
            level=ObservationLevel.ERROR if name == "ChatOpenAI" else ObservationLevel.DEFAULT,
            start_time=at,
        )
        for i, name in enumerate(steps)
    )
    return make_trace(
        id=id,
        timestamp=at,
        environment="production",
        name="web",
        cost=Decimal(cost),
        input_tokens=700,
        output_tokens=80,
        observations=observations,
    )


class World:
    """A web assistant with 10 days of history and a busy yesterday."""

    def __init__(self) -> None:
        """Store traces, a profile, an event mapping and an alert."""
        traces = [trace(f"h{d}-{i}", d, cost="0.005") for d in range(2, 12) for i in range(10)]
        traces += [
            trace(f"y{i}", 1, steps=("manipulation-attempt",) if i < 3 else ("router",))
            for i in range(20)
        ]
        traces += [trace("y-error", 1, steps=("ChatOpenAI",))]
        traces += [
            make_trace(id="loose", timestamp=datetime(2026, 10, 16, 9, tzinfo=UTC), name="x")
        ]
        self.profiles = InMemoryProfileRepository(traces)
        self.profiles.profiles[1] = make_profile(
            id=1,
            name="Asistente de la web",
            rule=ProfileRule(environment="production", trace_name="web"),
            created_at=NOW - timedelta(days=60),
        )
        self.profiles.profiles[2] = make_profile(
            id=2,
            name="Archivado",
            status=ProfileStatus.ARCHIVED,
            created_at=NOW - timedelta(days=60),
        )
        self.profiles.profile_of.update(
            {trace_id: 1 for trace_id in self.profiles.traces if trace_id != "loose"}
        )
        self.monitoring = InMemoryMonitoringRepository(self.profiles)
        self.monitoring.alerts.append(
            Alert(
                profile_id=1,
                kind=AlertKind.ANOMALY,
                scope=AlertScope.DAY,
                metric=AlertMetric.QUERY_COUNT,
                severity=Severity.CRITICAL,
                observed=Decimal(21),
                threshold=Decimal(20),
                baseline_p95=Decimal(10),
                day=YESTERDAY,
                affected_count=21,
                worst_trace_id=None,
                created_at=datetime(2026, 10, 16, 18, tzinfo=UTC),
                id=1,
            )
        )
        self.mappings = InMemoryEventMappingRepository()
        self.mappings.mappings[1] = [
            EventMapping("manipulation-attempt", "Intentos de manipulación")
        ]
        self.settings = InMemoryReportSettingsRepository()
        spend = SpendService(
            InMemorySpendRepository(self.profiles), self.profiles, MADRID, lambda: NOW
        )
        self.service = ReportService(
            registry=default_registry(),
            settings=self.settings,
            data=ReportDataBuilder(
                profiles=self.profiles,
                spend=InMemorySpendRepository(self.profiles),
                monitoring=self.monitoring,
                mappings=self.mappings,
                spend_service=spend,
                timezone=MADRID,
            ),
            presenters={Channel.EMAIL: EmailPresenter(), Channel.TELEGRAM: TelegramPresenter()},
            spend=spend,
        )


async def test_daily_report_defaults_to_yesterday_with_the_channel_sections() -> None:
    world = World()

    report = await world.service.build(
        world.service.yesterday(),
        [
            "overview",
            "alerts",
            "business_events",
            "spend_by_profile",
            "period_comparison",
            "text_volume",
        ],
    )

    assert report.day == YESTERDAY
    assert [s.key for s in report.sections] == [
        "overview",
        "alerts",
        "business_events",
        "spend_by_profile",
        "period_comparison",
        "text_volume",
    ]
    overview = report.sections[0].blocks[0]
    assert overview.text.startswith(  # type: ignore[union-attr]
        "«Asistente de la web», viernes 16: 21 consultas, 0,13 $. "
        "Es un 152 % más que un día normal, por más consultas (no por consultas más caras). "
        "Requiere atención"
    )


async def test_archived_profiles_and_unknown_keys_are_left_out() -> None:
    world = World()

    report = await world.service.build(YESTERDAY, ["overview", "gone"])

    text = " ".join(str(block) for block in report.sections[0].blocks)
    assert "Archivado" not in text
    assert "1 consulta sin perfil" in text
    assert [s.key for s in report.sections] == ["overview"]


async def test_telegram_daily_uses_its_own_sections() -> None:
    rendered = await World().service.daily(Channel.TELEGRAM)

    assert rendered.subject.startswith("[Requiere atención] Observa · viernes 16 de octubre")
    assert "<b>Resumen</b>" in rendered.body
    assert "<b>Alertas</b>" in rendered.body
    assert "Intentos de manipulación: <b>3</b>" in rendered.body
    assert "Errores técnicos: <b>1</b>" in rendered.body
    assert "<b>Gasto por herramienta</b>" not in rendered.body


@pytest.mark.parametrize("channel", list(Channel))
async def test_reports_never_use_technical_words(channel: Channel) -> None:
    world = World()
    await world.service.update_settings(
        channel,
        enabled=True,
        sections=[section.key for section in world.service.sections()],
    )

    rendered = await world.service.daily(channel, YESTERDAY)

    text = re.sub(r"<[^>]+>", " ", rendered.body)  # words, not markup
    assert FORBIDDEN.search(text) is None, FORBIDDEN.search(text)
    assert FORBIDDEN.search(rendered.subject) is None


async def test_quiet_day_report() -> None:
    world = World()

    report = await world.service.build(date(2026, 9, 1), ["overview", "alerts", "spend_by_profile"])

    assert [s.key for s in report.sections] == ["overview"]
    assert report.sections[0].blocks[0].text == (  # type: ignore[union-attr]
        "«Asistente de la web», martes 1: sin actividad."
    )


async def test_settings_can_be_changed_and_reject_unknown_sections() -> None:
    world = World()

    updated = await world.service.update_settings(
        Channel.TELEGRAM, enabled=False, sections=["alerts", "overview"]
    )

    assert (await world.service.settings(Channel.TELEGRAM)) == updated
    assert updated.sections == ("alerts", "overview")
    with pytest.raises(UnknownReportSectionError):
        await world.service.update_settings(Channel.EMAIL, enabled=True, sections=["nope"])
