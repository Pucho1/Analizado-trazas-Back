"""Public response models of the v1 API.

They are the HTTP contract (exposed through OpenAPI) and are kept separate from the
domain models so the domain can evolve without breaking clients. Monetary amounts are
computed with `Decimal` in the domain and only converted to JSON numbers here.
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.domain.models import Summary, TimeRange, Trace


class TraceResponse(BaseModel):
    """A single trace as returned by `GET /api/v1/traces`."""

    id: str
    timestamp: datetime = Field(description="Start of the trace (ISO 8601, UTC offset included).")
    name: str = Field(description="Agent or tool that produced the trace.")
    latency: float | None = Field(description="Duration in seconds; null if still in progress.")
    cost: float = Field(description="Cost in USD.")
    input_tokens: int
    output_tokens: int
    total_tokens: int

    @classmethod
    def from_domain(cls, trace: Trace) -> TraceResponse:
        """Build the response model from a domain `Trace`."""
        return cls(
            id=trace.id,
            timestamp=trace.timestamp,
            name=trace.name,
            latency=trace.latency,
            cost=float(trace.cost),
            input_tokens=trace.input_tokens,
            output_tokens=trace.output_tokens,
            total_tokens=trace.total_tokens,
        )


class TraceListResponse(BaseModel):
    """Traces in the requested range, most recent first."""

    time_range: TimeRange
    traces: list[TraceResponse]

    @classmethod
    def from_domain(cls, time_range: TimeRange, traces: list[Trace]) -> TraceListResponse:
        """Build the response model from the requested range and its domain traces."""
        return cls(
            time_range=time_range,
            traces=[TraceResponse.from_domain(trace) for trace in traces],
        )


class SummaryResponse(BaseModel):
    """Aggregated metrics over the traces in the requested range."""

    time_range: TimeRange
    trace_count: int
    total_cost: float = Field(description="Total cost in USD.")
    total_tokens: int
    average_latency: float | None = Field(
        description="Mean latency in seconds over traces that report one; null if none do."
    )

    @classmethod
    def from_domain(cls, time_range: TimeRange, summary: Summary) -> SummaryResponse:
        """Build the response model from the requested range and its domain summary."""
        return cls(
            time_range=time_range,
            trace_count=summary.trace_count,
            total_cost=float(summary.total_cost),
            total_tokens=summary.total_tokens,
            average_latency=summary.average_latency,
        )


class ErrorCode(StrEnum):
    """Machine-readable error identifiers that clients can branch on."""

    TRACE_SOURCE_AUTH_FAILED = "trace_source_auth_failed"
    TRACE_SOURCE_UNAVAILABLE = "trace_source_unavailable"


class ErrorDetail(BaseModel):
    """Description of an error: a stable `code` plus a human-readable `message`."""

    code: ErrorCode
    message: str


class ErrorResponse(BaseModel):
    """Body of every error response produced by Observa's own exception handlers."""

    error: ErrorDetail
