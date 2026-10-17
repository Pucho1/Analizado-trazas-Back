class ObservaError(Exception):
    """Base class for all application-specific errors."""


class TraceSourceError(ObservaError):
    """The trace source could not provide the requested traces."""


class TraceSourceAuthenticationError(TraceSourceError):
    """The trace source rejected the configured credentials."""


class RepositoryError(ObservaError):
    """The database could not be reached or rejected an operation."""


class SyncAlreadyRunningError(ObservaError):
    """A synchronisation was requested while another one is still running."""
