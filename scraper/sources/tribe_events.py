"""Generic scraper for sites running the WordPress "The Events
Calendar" plugin, which exposes a public read-only REST API at
`<site>/wp-json/tribe/events/v1/events`.

This single scraper covers any number of small/medium venues, theaters,
and arts orgs by just adding a `base_url` entry in
config/sources.yaml - no per-site code needed. If a site doesn't run
this plugin, the endpoint 404s and we raise EndpointNotFoundError so
it's clear that source needs a different `scraper` type
(`playwright_html` or `ics_calendar`).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import requests

from scraper.base import BaseScraper
from scraper.categories import classify_text
from scraper.errors import BlockedError, EndpointNotFoundError, ParseError
from scraper.models import NormalizedEvent
from scraper.utils import DEFAULT_HEADERS, parse_datetime, strip_html

logger = logging.getLogger(__name__)

PER_PAGE = 50
MAX_PAGES = 5


class TribeEventsScraper(BaseScraper):
    def fetch_events(self) -> Iterator[NormalizedEvent]:
        if not self.base_url:
            from scraper.errors import ConfigError

            raise ConfigError(
                f"Source '{self.slug}' has no base_url configured.",
                hint="Add `base_url: https://example.com` to this source in config/sources.yaml.",
            )

        endpoint = self.base_url.rstrip("/") + "/wp-json/tribe/events/v1/events"
        page = 1
        while page <= MAX_PAGES:
            params = {"per_page": PER_PAGE, "page": page}
            resp = requests.get(endpoint, params=params, headers=DEFAULT_HEADERS, timeout=30)

            if resp.status_code == 404:
                raise EndpointNotFoundError(
                    f"{endpoint} returned 404.",
                    hint=(
                        "This site likely doesn't run WordPress 'The Events Calendar' plugin. "
                        "Switch this source's `scraper` to `playwright_html` or `ics_calendar` "
                        "in config/sources.yaml, or set `is_active: false`."
                    ),
                )
            if resp.status_code in (403, 429):
                raise BlockedError(
                    f"{endpoint} returned HTTP {resp.status_code}.",
                    hint=(
                        "The site is blocking automated requests (likely Cloudflare). "
                        "Try `playwright_html` (real browser) for this source, or skip it."
                    ),
                )
            if resp.status_code >= 400:
                raise ParseError(
                    f"{endpoint} returned HTTP {resp.status_code}: {resp.text[:200]}",
                    hint="Inspect the response body for details.",
                )

            try:
                # Some sites prepend garbage bytes (debug echo, BOM, etc.) before
                # the JSON — strip everything up to the first { or [
                raw = resp.text
                first_brace = min(
                    (raw.find(c) for c in ('{', '[') if raw.find(c) != -1),
                    default=-1,
                )
                if first_brace > 0:
                    raw = raw[first_brace:]
                import json as _json
                data = _json.loads(raw)
            except ValueError as exc:
                raise ParseError(
                    f"{endpoint} did not return valid JSON.",
                    hint="The site may have redirected to an HTML page - verify the URL in a browser.",
                ) from exc

            events = data.get("events")
            if events is None:
                raise ParseError(
                    f"{endpoint} response had no 'events' key (keys: {list(data.keys())}).",
                    hint="The Tribe Events REST API shape may differ for this site - inspect the raw response.",
                )

            for event in events:
                normalized = self._normalize(event)
                if normalized:
                    yield normalized

            total_pages = data.get("total_pages", 1)
            if not events or page >= total_pages:
                return
            page += 1

    def _normalize(self, event: dict) -> NormalizedEvent | None:
        try:
            title = strip_html(event.get("title"))
            if not title:
                return None

            start_dt = parse_datetime(event.get("start_date"))
            if not start_dt:
                logger.debug("%s: event %s has no parseable start_date", self.slug, event.get("id"))
                return None
            end_dt = parse_datetime(event.get("end_date"))

            venue = event.get("venue") or {}
            venue_name = venue.get("venue") or self.config.get("venue_name") or self.name
            address = venue.get("address")
            city = venue.get("city") or self.config.get("city")
            lat = _to_float(venue.get("geo_lat"))
            lon = _to_float(venue.get("geo_lng"))

            description = strip_html(event.get("description") or event.get("excerpt"))

            cost = event.get("cost")
            is_free = bool(cost) and cost.strip().lower() in {"free", "$0", "0", "0.00"}

            cat_names = [c.get("name", "") for c in (event.get("categories") or [])]
            tag_names = [t.get("name", "") for t in (event.get("tags") or [])]
            extra_tags, hint = classify_text(title, description, *cat_names, *tag_names)

            category = hint or self.default_category or "other"
            if "edm" in extra_tags:
                category = "nightlife_edm"

            image = event.get("image")
            image_url = image.get("url") if isinstance(image, dict) else None

            event_url = event.get("url") or event.get("website")

            return NormalizedEvent(
                title=title,
                description=description,
                category=category,
                tags=sorted(extra_tags | set(self.config.get("default_tags", []))),
                venue_name=venue_name,
                address=address,
                city=city,
                latitude=lat,
                longitude=lon,
                start_datetime=start_dt,
                end_datetime=end_dt,
                is_all_day=bool(event.get("all_day")),
                price_text=cost,
                is_free=is_free,
                ticket_url=event.get("website") or event_url,
                event_url=event_url,
                image_url=image_url,
                status="active",
                source_event_id=str(event.get("id")),
                source_url=event_url,
                raw_data=event,
            )
        except Exception:
            logger.exception("%s: failed to normalize event %s", self.slug, event.get("id"))
            return None


def _to_float(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
