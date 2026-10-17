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


class ProfileNotFoundError(ObservaError):
    """No profile has the requested id."""


class ProfileNameTakenError(ObservaError):
    """Another profile already has that name (names are compared ignoring case)."""


class InvalidThresholdsError(ObservaError):
    """The requested alert thresholds are inconsistent (e.g. warning above critical)."""


class UnknownReportSectionError(ObservaError):
    """A report setting names a section that is not registered."""


class ChannelNotConfiguredError(ObservaError):
    """The channel's variables are missing from `.env`."""


class ReportAlreadySentError(ObservaError):
    """The daily report about that day already reached that channel."""


class NotificationError(ObservaError):
    """A message could not be delivered; `reason` says why without leaking secrets."""

    def __init__(self, reason: str, message: str) -> None:
        """Keep the machine-readable `reason` (a `NotificationFailure` value)."""
        super().__init__(message)
        self.reason = reason
