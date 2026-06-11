"""Shared data model produced by every scraper.

Every scraper's `fetch_events()` yields `NormalizedEvent` instances.
The pipeline (scraper/main.py) then geo-filters, computes a dedup
hash, resolves/creates a venue row, and upserts into `events` +
`event_sources`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class NormalizedEvent:
    title: str
    start_datetime: datetime

    description: str | None = None

    # Standardized top-level category - see scraper/categories.py
    category: str = "other"
    tags: list[str] = field(default_factory=list)

    venue_name: str | None = None
    address: str | None = None
    city: str | None = None
    latitude: float | None = None
    longitude: float | None = None

    end_datetime: datetime | None = None
    is_all_day: bool = False
    timezone: str = "America/New_York"

    price_min: float | None = None
    price_max: float | None = None
    price_text: str | None = None
    is_free: bool = False
    age_restriction: str | None = None

    ticket_url: str | None = None
    event_url: str | None = None
    image_url: str | None = None

    status: str = "active"

    # Identifies this record within its source (for event_sources upsert).
    source_event_id: str | None = None
    source_url: str | None = None
    # Original payload, stored as-is for audit/debugging.
    raw_data: dict[str, Any] = field(default_factory=dict)

    # Populated by the pipeline before upsert.
    dedup_hash: str | None = None
