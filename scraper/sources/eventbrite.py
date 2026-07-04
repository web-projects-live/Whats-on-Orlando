"""Eventbrite Discovery API scraper.

Covers community events, family festivals, holiday/fireworks shows, and free
civic events across the Central Florida corridor -- the gap not covered by
Ticketmaster (which only handles paid, ticketed shows at major venues).

Requires a free API key from https://developer.eventbrite.com/ set as the
EVENTBRITE_API_KEY environment variable / GitHub secret.
Free tier: 1000 requests/day.
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
from scraper.errors import BlockedError, CredentialsMissingError, EndpointNotFoundError, ParseError
from scraper.geo import search_centers
from scraper.models import NormalizedEvent
from scraper.utils import DEFAULT_HEADERS, parse_datetime, strip_html

logger = logging.getLogger(__name__)

DISCOVERY_URL = "https://www.eventbriteapi.com/v3/events/search/"
LOOKAHEAD_DAYS = 120
MAX_PAGES_PER_CENTER = 8
PAGE_SIZE = 50

EB_CAT_TO_CATEGORY: dict[str, str] = {
    "103": "music",
    "104": "arts_culture",
    "105": "theater",
    "108": "sports",
    "109": "community",
    "110": "food_drink",
    "111": "community",
    "113": "community",
    "115": "family",
    "116": "community",  # holiday/seasonal
    "199": "other",
}

# Category IDs: music, film, arts, sports, charity, food, community, family/education, holiday/seasonal
CATEGORIES_FILTER = "103,104,105,108,109,110,113,115,116"


class EventbriteScraper(BaseScraper):

    def fetch_events(self) -> Iterator[NormalizedEvent]:
        token = os.environ.get("EVENTBRITE_API_KEY")
        if not token:
            raise CredentialsMissingError(
                "EVENTBRITE_API_KEY not set.",
                hint=(
                    "Get a free API key at https://developer.eventbrite.com/ "
                    "and add it as a GitHub secret named EVENTBRITE_API_KEY."
                ),
            )

        session = requests.Session()
        session.headers.update({**DEFAULT_HEADERS, "Authorization": f"Bearer {token}"})

        now = datetime.now(timezone.utc)
        end = now + timedelta(days=LOOKAHEAD_DAYS)
        range_start = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        range_end = end.strftime("%Y-%m-%dT%H:%M:%SZ")

        seen_ids: set[str] = set()

        for center in search_centers:
            lat = center["lat"]
            lon = center["lon"]
            radius = center["radius_miles"]

            for page in range(1, MAX_PAGES_PER_CENTER + 1):
                params = {
                    "location.latitude": lat,
                    "location.longitude": lon,
                    "location.within": f"{radius}mi",
                    "start_date.range_start": range_start,
                    "start_date.range_end": range_end,
                    "categories": CATEGORIES_FILTER,
                    "page_size": PAGE_SIZE,
                    "expand": "venue,category",
                    "sort_by": "date",
                    "page": page,
                }

                try:
                    resp = session.get(DISCOVERY_URL, params=params, timeout=30)
                except requests.RequestException as exc:
                    raise ParseError(
                        f"Eventbrite API request failed: {exc}",
                        hint="Check network connectivity and API endpoint.",
                    ) from exc

                if resp.status_code in (401, 403):
                    raise BlockedError(
                        f"Eventbrite API returned {resp.status_code}.",
                        hint=(
                            "EVENTBRITE_API_KEY may be invalid or expired. "
                            "Verify at https://developer.eventbrite.com/."
                        ),
                    )
                if resp.status_code == 404:
                    raise EndpointNotFoundError(
                        "Eventbrite API endpoint not found (404).",
                        hint="API URL may have changed; check Eventbrite developer docs.",
                    )
                if resp.status_code == 429:
                    logger.warning("Eventbrite rate limited, backing off 5s")
                    time.sleep(5)
                    continue
                if resp.status_code >= 400:
                    raise ParseError(
                        f"Eventbrite API returned HTTP {resp.status_code}: {resp.text[:200]}",
                        hint="Check request params against Eventbrite Discovery API docs.",
                    )

                try:
                    data = resp.json()
                except ValueError as exc:
                    raise ParseError(
                        f"Eventbrite API response not valid JSON: {exc}",
                        hint="The API may be returning an HTML error page.",
                    ) from exc

                for event in data.get("events", []):
                    event_id = event.get("id")
                    if not event_id or event_id in seen_ids:
                        continue
                    seen_ids.add(event_id)
                    normalized = self._normalize(event)
                    if normalized:
                        yield normalized

                pagination = data.get("pagination", {})
                if not pagination.get("has_more_items", False):
                    break

                time.sleep(0.2)

    def _normalize(self, event: dict) -> NormalizedEvent | None:
        try:
            title = (event.get("name") or {}).get("text", "").strip()
            if not title:
                return None

            start_utc = (event.get("start") or {}).get("utc")
            end_utc = (event.get("end") or {}).get("utc")
            start_dt = parse_datetime(start_utc)
            if not start_dt:
                logger.debug("Eventbrite event %s has no usable start date", event.get("id"))
                return None
            end_dt = parse_datetime(end_utc)

            cat_id = str((event.get("category") or {}).get("id", ""))
            category = EB_CAT_TO_CATEGORY.get(cat_id)
            if not category:
                desc_text = strip_html((event.get("description") or {}).get("html")) or ""
                _, hint = classify_text(title, desc_text[:500])
                category = hint or self.default_category or "other"

            desc_html = (event.get("description") or {}).get("html")
            description = strip_html(desc_html) if desc_html else None

            venue = event.get("venue") or {}
            venue_name = venue.get("name")
            address_obj = venue.get("address") or {}
            address = address_obj.get("localized_address_display")
            city = address_obj.get("city")
            lat = lon = None
            try:
                if address_obj.get("latitude"):
                    lat = float(address_obj["latitude"])
                    lon = float(address_obj["longitude"])
            except (TypeError, ValueError):
                pass

            is_free = bool(event.get("is_free", False))
            price_text = "Free" if is_free else None
            status = "active" if event.get("status") == "live" else "cancelled"
            logo = event.get("logo") or {}
            image_url = logo.get("url")

            return NormalizedEvent(
                title=title,
                description=description,
                category=category,
                tags=[],
                venue_name=venue_name,
                address=address,
                city=city,
                latitude=lat,
                longitude=lon,
                start_datetime=start_dt,
                end_datetime=end_dt,
                is_all_day=False,
                price_min=None,
                price_max=None,
                price_text=price_text,
                is_free=is_free,
                ticket_url=event.get("url"),
                event_url=event.get("url"),
                image_url=image_url,
                status=status,
                source_event_id=event["id"],
                source_url=event.get("url"),
                raw_data={"id": event["id"], "name": title},
            )
        except Exception:
            logger.exception("Failed to normalize Eventbrite event %s", event.get("id"))
            return None
