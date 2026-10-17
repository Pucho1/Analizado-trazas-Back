"""Short messages sent outside the daily report: alerts, scan summaries and tests.

They reuse the report content blocks, so every presenter lays them out unchanged.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime

from app.application.reports.content import Block, Fact, Note, Report, Section, Tone
from app.application.reports.formatting import number, plural, short_date
from app.application.reports.sections import alert_blocks
from app.domain.models import SyncRun
from app.domain.monitoring import Alert

_UNKNOWN_PROFILE = "Perfil eliminado"


def _at(moment: datetime) -> str:
    """A local moment as weekday, day and time, e.g. "viernes 16, 14:05"."""
    return f"{short_date(moment.date())}, {moment:%H:%M}"


def alerts_message(
    alerts: Sequence[Alert], profile_names: Mapping[int, str], now: datetime
) -> Report:
    """All new alerts of a scan in one message, grouped by profile.

    `now` is the local time of the scan.
    """
    blocks: list[Block] = []
    for profile_id in dict.fromkeys(alert.profile_id for alert in alerts):
        own = [alert for alert in alerts if alert.profile_id == profile_id]
        blocks.extend(alert_blocks(profile_names.get(profile_id, _UNKNOWN_PROFILE), own))
    section = Section(
        "alerts", plural(len(alerts), "Alerta nueva", "Alertas nuevas"), tuple(blocks)
    )
    return Report(title=f"Observa · Alertas · {_at(now)}", day=now.date(), sections=(section,))


def scan_summary_message(run: SyncRun, new_alerts: int, now: datetime) -> Report:
    """A short note after a scan (only for channels that ask for it)."""
    alerts = (
        Fact("Alertas", "ninguna", "Todo en orden.", Tone.OK)
        if new_alerts == 0
        else Fact(
            "Alertas", plural(new_alerts, "nueva", "nuevas"), "Te las he enviado.", Tone.ATTENTION
        )
    )
    section = Section(
        "scan_summary",
        "Escaneo completado",
        (Fact("Consultas revisadas", number(run.trace_count)), alerts),
    )
    return Report(title=f"Observa · Escaneo · {_at(now)}", day=now.date(), sections=(section,))


def channel_test_message(now: datetime) -> Report:
    """A message to check that a channel works."""
    note = Note("Si lees esto, Observa ya puede escribirte por este canal.", Tone.OK)
    section = Section("test", "Mensaje de prueba", (note,))
    return Report(title=f"Observa · Prueba · {_at(now)}", day=now.date(), sections=(section,))
