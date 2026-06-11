"""Supabase access layer.

All writes go through here so the upsert/dedup/venue-matching logic
lives in one place. Reads/writes use the service-role key (set via
SUPABASE_SERVICE_ROLE_KEY), which bypasses Row Level Security - this
must only run in trusted contexts (the nightly GitHub Action), never
client-side.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from supabase import Client, create_client

from scraper.models import NormalizedEvent
from scraper.utils import slugify


@lru_cache
def get_client() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return create_client(url, key)


def sync_sources(client: Client, source_configs: list[dict[str, Any]]) -> dict[str, dict]:
    """Upsert the `sources` registry from config/sources.yaml and
    return {slug: source_row} for every configured source."""
    rows = []
    for cfg in source_configs:
        rows.append(
            {
                "slug": cfg["slug"],
                "name": cfg["name"],
                "source_type": cfg["source_type"],
                "base_url": cfg.get("base_url"),
                "homepage_url": cfg.get("homepage_url"),
                "default_category": cfg.get("default_category", "other"),
                "is_active": cfg.get("is_active", True),
                "scrape_config": cfg.get("config", {}),
                "notes": cfg.get("notes"),
            }
        )
    client.table("sources").upsert(rows, on_conflict="slug").execute()
    result = client.table("sources").select("*").execute()
    return {row["slug"]: row for row in result.data}


def get_or_create_venue(
    client: Client,
    name: str | None,
    city: str | None = None,
    address: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    venue_type: str | None = None,
) -> str | None:
    if not name:
        return None

    slug = slugify(name)
    payload: dict[str, Any] = {"name": name, "slug": slug}
    if city:
        payload["city"] = city
    if address:
        payload["address"] = address
    if latitude is not None:
        payload["latitude"] = latitude
    if longitude is not None:
        payload["longitude"] = longitude
    if venue_type:
        payload["venue_type"] = venue_type

    # Upsert on slug. Fields omitted from `payload` (e.g. lat/lon when
    # this particular source doesn't have them) are left untouched on
    # conflict, so an enriched record from one source won't be
    # clobbered with NULLs by a sparser record from another.
    result = client.table("venues").upsert(payload, on_conflict="slug").execute()
    return result.data[0]["id"]


def upsert_event(client: Client, event: NormalizedEvent, source_id: str) -> tuple[str, bool]:
    venue_id = get_or_create_venue(
        client,
        name=event.venue_name,
        city=event.city,
        address=event.address,
        latitude=event.latitude,
        longitude=event.longitude,
    )

    existing = (
        client.table("events").select("id").eq("dedup_hash", event.dedup_hash).limit(1).execute()
    )
    is_new = not existing.data

    payload = {
        "dedup_hash": event.dedup_hash,
        "title": event.title,
        "description": event.description,
        "category": event.category,
        "tags": event.tags,
        "venue_id": venue_id,
        "venue_name_raw": event.venue_name,
        "address_raw": event.address,
        "city": event.city,
        "latitude": event.latitude,
        "longitude": event.longitude,
        "start_datetime": event.start_datetime.isoformat(),
        "end_datetime": event.end_datetime.isoformat() if event.end_datetime else None,
        "is_all_day": event.is_all_day,
        "timezone": event.timezone,
        "price_min": event.price_min,
        "price_max": event.price_max,
        "price_text": event.price_text,
        "is_free": event.is_free,
        "age_restriction": event.age_restriction,
        "ticket_url": event.ticket_url,
        "event_url": event.event_url,
        "image_url": event.image_url,
        "status": event.status,
        "last_seen_at": datetime.now(timezone.utc).isoformat(),
    }

    result = client.table("events").upsert(payload, on_conflict="dedup_hash").execute()
    event_id = result.data[0]["id"]

    if event.source_event_id:
        client.table("event_sources").upsert(
            {
                "event_id": event_id,
                "source_id": source_id,
                "source_event_id": event.source_event_id,
                "source_url": event.source_url,
                "raw_data": event.raw_data,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="source_id,source_event_id",
        ).execute()

    return event_id, is_new


def log_scrape_run(
    client: Client,
    source_id: str,
    started_at: datetime,
    finished_at: datetime,
    status: str,
    events_found: int,
    events_new: int,
    events_updated: int,
    error_message: str | None = None,
    error_category: str | None = None,
) -> None:
    client.table("scrape_runs").insert(
        {
            "source_id": source_id,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "status": status,
            "events_found": events_found,
            "events_new": events_new,
            "events_updated": events_updated,
            "error_message": error_message,
            "error_category": error_category,
        }
    ).execute()
