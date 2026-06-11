"""Songkick API - supplemental concert listings for the corridor.

Requires a free API key requested from
https://www.songkick.com/api_key_requests/new set as the
SONGKICK_API_KEY environment variable / GitHub secret.

Two-step lookup: resolve `config.metro_area_query` (default
"Orlando, FL") to a Songkick metro area ID via the locations search
endpoint (or set `config.metro_area_id` directly to skip that), then
page through that metro area's upcoming events calendar.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator

import requests

from scraper.base import BaseScraper
from scraper.categories import classify_text
from scraper.errors import BlockedError, CredentialsMissingError, ParseError
from scraper.models import NormalizedEvent
from scraper.utils import DEFAULT_HEADERS, parse_datetime, strip_html

logger = logging.getLogger(__name__)

BASE_URL = "https://api.songkick.com/api/3.0"
DEFAULT_METRO_QUERY = "Orlando, FL"
PER_PAGE = 50
MAX_PAGES = 5


class SongkickScraper(BaseScraper):
    def fetch_events(self) -> Iterator[NormalizedEvent]:
        api_key = os.environ.get("SONGKICK_API_KEY")
        if not api_key:
            raise CredentialsMissingError(
                "SONGKICK_API_KEY is not set.",
                hint=(
                    "Request a free key at https://www.songkick.com/api_key_requests/new "
                    "and add it as a repo secret (and to .env locally) named SONGKICK_API_KEY."
                ),
            )

        metro_area_id = self.config.get("metro_area_id")
        if not metro_area_id:
            metro_area_id = self._lookup_metro_area(api_key)

        page = 1
        while page <= MAX_PAGES:
            url = f"{BASE_URL}/metro_areas/{metro_area_id}/calendar.json"
            params = {"apikey": api_key, "per_page": PER_PAGE, "page": page}
            resp = requests.get(url, params=params, headers=DEFAULT_HEADERS, timeout=30)

            if resp.status_code == 404:
                raise ParseError(
                    f"{url} returned 404.",
                    hint="The metro area ID may be wrong - verify config.metro_area_id or metro_area_query.",
                )
            if resp.status_code in (401, 403, 429):
                raise BlockedError(
                    f"{url} returned HTTP {resp.status_code}.",
                    hint="SONGKICK_API_KEY may be invalid, or the API is rate-limiting/blocking requests.",
                )
            if resp.status_code >= 400:
                raise ParseError(
                    f"{url} returned HTTP {resp.status_code}: {resp.text[:200]}",
                    hint="Inspect the response body for details.",
                )

            try:
                data = resp.json()
            except ValueError as exc:
                raise ParseError(f"{url} did not return valid JSON.") from exc

            results_page = data.get("resultsPage", {})
            results = results_page.get("results", {}) or {}
            events = results.get("event") or []

            for event in events:
                normalized = self._normalize(event)
                if normalized:
                    yield normalized

            total_entries = results_page.get("totalEntries", 0)
            if not events or page * PER_PAGE >= total_entries:
                return
            page += 1

    def _lookup_metro_area(self, api_key: str) -> int:
        query = self.config.get("metro_area_query", DEFAULT_METRO_QUERY)
        url = f"{BASE_URL}/search/locations.json"
        params = {"apikey": api_key, "query": query}
        resp = requests.get(url, params=params, headers=DEFAULT_HEADERS, timeout=30)

        if resp.status_code in (401, 403, 429):
            raise BlockedError(
                f"{url} returned HTTP {resp.status_code}.",
                hint="SONGKICK_API_KEY may be invalid, or the API is rate-limiting/blocking requests.",
            )
        if resp.status_code >= 400:
            raise ParseError(f"{url} returned HTTP {resp.status_code}: {resp.text[:200]}")

        try:
            data = resp.json()
        except ValueError as exc:
            raise ParseError(f"{url} did not return valid JSON.") from exc

        locations = (data.get("resultsPage", {}).get("results", {}) or {}).get("location") or []
        for loc in locations:
            metro = loc.get("metroArea")
            if metro and metro.get("id"):
                return metro["id"]

        raise ParseError(
            f"No Songkick metro area found for '{query}'.",
            hint="Set `config.metro_area_id` directly (look it up at songkick.com) or adjust `config.metro_area_query`.",
        )

    def _normalize(self, event: dict) -> NormalizedEvent | None:
        try:
            title = strip_html(event.get("displayName"))
            if not title:
                return None

            start = event.get("start") or {}
            start_dt = parse_datetime(start.get("datetime") or start.get("date"))
            if not start_dt:
                return None
            is_all_day = not start.get("datetime")

            venue = event.get("venue") or {}
            venue_name = venue.get("displayName") or self.name
            lat = venue.get("lat")
            lon = venue.get("lng")
            metro_area = venue.get("metroArea") or {}
            city = metro_area.get("displayName")

            performances = event.get("performance") or []
            artist_names = [p.get("artist", {}).get("displayName") for p in performances if p.get("artist")]

            extra_tags, hint = classify_text(title, *artist_names)
            category = hint or self.default_category or "other"
            if "edm" in extra_tags:
                category = "nightlife_edm"

            event_url = event.get("uri")

            return NormalizedEvent(
                title=title,
                description=None,
                category=category,
                tags=sorted(extra_tags | set(self.config.get("default_tags", []))),
                venue_name=venue_name,
                address=None,
                city=city,
                latitude=lat,
                longitude=lon,
                start_datetime=start_dt,
                end_datetime=None,
                is_all_day=is_all_day,
                ticket_url=event_url,
                event_url=event_url,
                status="active",
                source_event_id=str(event.get("id")),
                source_url=event_url,
                raw_data=event,
            )
        except Exception:
            logger.exception("%s: failed to normalize Songkick event %s", self.slug, event.get("id"))
            return None
