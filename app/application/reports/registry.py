"""Registry of report sections: adding a new kind of data = registering a section."""

from collections.abc import Iterable

from app.application.reports.sections import DEFAULT_SECTIONS, ReportSection


class SectionRegistry:
    """Sections by key, in registration order."""

    def __init__(self, sections: Iterable[ReportSection] = ()) -> None:
        """Register `sections` in order."""
        self._sections: dict[str, ReportSection] = {}
        for section in sections:
            self.register(section)

    def register(self, section: ReportSection) -> None:
        """Add a section; its key must be new."""
        if section.key in self._sections:
            raise ValueError(f"A section with key {section.key!r} is already registered")
        self._sections[section.key] = section

    def get(self, key: str) -> ReportSection | None:
        """The section registered under `key`, or None."""
        return self._sections.get(key)

    def all(self) -> list[ReportSection]:
        """Every registered section, in registration order."""
        return list(self._sections.values())


def default_registry() -> SectionRegistry:
    """A registry with every built-in section."""
    return SectionRegistry(DEFAULT_SECTIONS)
