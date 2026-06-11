"""Common interface implemented by every source scraper."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any

from scraper.models import NormalizedEvent


class BaseScraper(ABC):
    """Base class for all event source scrapers.

    Subclasses receive the `sources` table row (as a dict) for their
    source and must implement `fetch_events()`, yielding
    `NormalizedEvent` instances. Any per-source configuration lives in
    `source_row["scrape_config"]` (mirrors `config/sources.yaml`'s
    `config:` block for that source).
    """

    def __init__(self, source_row: dict[str, Any]):
        self.source_row = source_row
        self.slug: str = source_row["slug"]
        self.name: str = source_row["name"]
        self.base_url: str | None = source_row.get("base_url")
        self.default_category: str = source_row.get("default_category") or "other"
        self.config: dict[str, Any] = source_row.get("scrape_config") or {}

    @abstractmethod
    def fetch_events(self) -> Iterator[NormalizedEvent]:
        """Yield NormalizedEvent records found by this source."""
        raise NotImplementedError
