class ObservaError(Exception):
    """Base class for all application-specific errors."""


class TraceSourceError(ObservaError):
    """The trace source could not provide the requested traces."""


class TraceSourceAuthenticationError(TraceSourceError):
    """The trace source rejected the configured credentials."""
