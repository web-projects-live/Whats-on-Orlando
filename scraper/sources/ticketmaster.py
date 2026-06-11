"""Ticketmaster Discovery API.

Single highest-leverage source: covers most arenas, amphitheaters,
clubs, and theaters across the corridor that use Ticketmaster/Live
Nation/AXS-via-TM ticketing (Kia Center, Addition Financial Arena,
Daytona International Speedway, House of Blues Orlando, The Beacham,
Hard Rock Live, etc.) without per-venue scrapers.

Requires a free API key from https://developer.ticketmaster.com/ set
as the TICKETMASTER_API_KEY environment variable / GitHub secret.
Free tier: 5000 requests/day, 5 requests/second.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import requests

from scraper.base import BaseScraper
from scraper.categories import classify_text
from scraper.errors import BlockedError, CredentialsMissingError, ParseError
from scraper.geo import search_centers
from scraper.models import NormalizedEvent
from scraper.utils import DEFAULT_HEADERS

logger = logging.getLogger(__name__)

DISCOVERY_URL = "https://app.ticketmaster.com/discovery/v2/events.json"
LOOKAHEAD_DAYS = 120
MAX_PAGES_PER_CENTER = 10
PAGE_SIZE = 200

SEGMENT_TO_CATEGORY = {
    "Music": "music",
    "Arts & Theatre": "theater",
    "Film": "arts_culture",
    "Sports": "sports",
    "Family": "family",
    "Miscellaneous": "other",
}

GENRE_TO_TAG = {
    "Rock": "rock",
    "Alternative Rock": "rock",
    "Metal": "metal",
    "Punk": "punk",
    "Reggae": "reggae",
    "Ska": "ska",
    "Hip-Hop/Rap": "hip_hop",
    "Rap": "hip_hop",
    "Country": "country",
    "Jazz": "jazz",
    "Blues": "blues",
    "Classical": "classical",
    "Latin": "latin",
    "Pop": "pop",
    "Dance/Electronic": "edm",
    "Comedy": "comedy",
}


class TicketmasterScraper(BaseScraper):
    def fetch_events(self) -> Iterator[NormalizedEvent]:
        api_key = os.environ.get("TICKETMASTER_API_KEY")
        if not api_key:
            raise CredentialsMissingError(
                "TICKETMASTER_API_KEY is not set.",
                hint=(
                    "Get a free key at https://developer.ticketmaster.com/ "
                    "and add it as a repo secret (and to .env locally) named TICKETMASTER_API_KEY."
                ),
            )

        start = datetime.now(timezone.utc)
        end = start + timedelta(days=LOOKAHEAD_DAYS)
        seen_ids: set[str] = set()
        any_results = False

        for center in search_centers():
            for event in self._fetch_center(api_key, center, start, end):
                any_results = True
                tm_id = event.get("id")
                if not tm_id or tm_id in seen_ids:
                    continue
                seen_ids.add(tm_id)
                normalized = self._normalize(event)
                if normalized:
                    yield normalized

        if not any_results:
            # _fetch_center already raises on hard errors; reaching here
            # with zero results across all centers but no exception
            # means the API responded but matched nothing.
            return

    def _fetch_center(self, api_key: str, center: dict, start: datetime, end: datetime) -> Iterator[dict]:
        page = 0
        while page < MAX_PAGES_PER_CENTER:
            params = {
                "apikey": api_key,
                "latlong": f"{center['lat']},{center['lon']}",
                "radius": center["radius_miles"],
                "unit": "miles",
                "startDateTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "endDateTime": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "size": PAGE_SIZE,
                "page": page,
                "sort": "date,asc",
            }
            resp = requests.get(DISCOVERY_URL, params=params, headers=DEFAULT_HEADERS, timeout=30)

            if resp.status_code == 401:
                raise CredentialsMissingError(
                    "Ticketmaster API returned 401 Unauthorized.",
                    hint="TICKETMASTER_API_KEY is set but invalid/expired - check the key value.",
                )
            if resp.status_code == 429:
                logger.warning("Ticketmaster rate limited, backing off 2s")
                time.sleep(2)
                continue
            if resp.status_code in (403, 451):
                raise BlockedError(
                    f"Ticketmaster API returned {resp.status_code}.",
                    hint="Request was blocked - verify the API key's allowed domains/quota in the developer portal.",
                )
            if resp.status_code >= 400:
                raise ParseError(
                    f"Ticketmaster API returned HTTP {resp.status_code}: {resp.text[:200]}",
                    hint="Check the request params (latlong/radius/date format) against the Discovery API docs.",
                )

            try:
                data = resp.json()
            except ValueError as exc:
                raise ParseError(
                    f"Ticketmaster API response was not valid JSON: {exc}",
                    hint="The API may be returning an HTML error page - inspect the raw response.",
                ) from exc

            events = data.get("_embedded", {}).get("events", [])
            yield from events

            total_pages = data.get("page", {}).get("totalPages", 1)
            page += 1
            if page >= total_pages:
                return
            time.sleep(0.25)  # stay well under 5 req/s

    def _normalize(self, event: dict) -> NormalizedEvent | None:
        try:
            name = event["name"]
            dates = event.get("dates", {})
            start_info = dates.get("start", {})
            dt_str = start_info.get("dateTime")
            if dt_str:
                start_dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                is_all_day = False
            else:
                local_date = start_info.get("localDate")
                if not local_date:
                    logger.debug("Ticketmaster event %s has no usable start date", event.get("id"))
                    return None
                start_dt = datetime.fromisoformat(local_date + "T00:00:00-04:00")
                is_all_day = True

            venues = event.get("_embedded", {}).get("venues", [])
            venue = venues[0] if venues else {}
            venue_name = venue.get("name")
            location = venue.get("location", {})
            lat = float(location["latitude"]) if location.get("latitude") else None
            lon = float(location["longitude"]) if location.get("longitude") else None
            city = venue.get("city", {}).get("name")
            address = venue.get("address", {}).get("line1")

            classifications = event.get("classifications", [])
            segment = genre = sub_genre = None
            if classifications:
                c = classifications[0]
                segment = c.get("segment", {}).get("name")
                genre = c.get("genre", {}).get("name")
                sub_genre = c.get("subGenre", {}).get("name")

            category = SEGMENT_TO_CATEGORY.get(segment, "other")
            tags: set[str] = set()
            for g in (genre, sub_genre):
                if g and g in GENRE_TO_TAG:
                    tags.add(GENRE_TO_TAG[g])

            extra_tags, hint = classify_text(name, genre, sub_genre)
            tags |= extra_tags
            if "edm" in tags:
                category = "nightlife_edm"
            elif category == "other" and hint:
                category = hint

            price_min = price_max = None
            price_ranges = event.get("priceRanges")
            if price_ranges:
                price_min = price_ranges[0].get("min")
                price_max = price_ranges[0].get("max")

            image_url = None
            images = event.get("images", [])
            if images:
                image_url = max(images, key=lambda i: i.get("width", 0)).get("url")

            status = "active"
            tm_status = dates.get("status", {}).get("code")
            if tm_status == "cancelled":
                status = "cancelled"
            elif tm_status in ("postponed", "rescheduled"):
                status = "postponed"

            return NormalizedEvent(
                title=name,
                description=event.get("info") or event.get("pleaseNote"),
                category=category,
                tags=sorted(tags),
                venue_name=venue_name,
                address=address,
                city=city,
                latitude=lat,
                longitude=lon,
                start_datetime=start_dt,
                end_datetime=None,
                is_all_day=is_all_day,
                price_min=price_min,
                price_max=price_max,
                price_text=None,
                is_free=False,
                ticket_url=event.get("url"),
                event_url=event.get("url"),
                image_url=image_url,
                status=status,
                source_event_id=event["id"],
                source_url=event.get("url"),
                raw_data=event,
            )
        except Exception:
            logger.exception("Failed to normalize Ticketmaster event %s", event.get("id"))
            return None
