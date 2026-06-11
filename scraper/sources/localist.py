"""Generic scraper for sites running the Localist events platform
(events.ucf.edu and similar Concept3D/Localist .edu calendars), via its
public read-only JSON API at `<site>/api/2/events`.

config:
  api_url - full API URL (default: <base_url>/api/2/events)
  days - how many days ahead to request (default 120)
  pp - events per page (default 50, Localist max is 100)
  default_tags
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import requests

from scraper.base import BaseScraper
from scraper.categories import classify_text
from scraper.errors import BlockedError, ConfigError, EndpointNotFoundError, ParseError
from scraper.models import NormalizedEvent
from scraper.utils import DEFAULT_HEADERS, parse_datetime, strip_html

logger = logging.getLogger(__name__)

MAX_PAGES = 10
DEFAULT_PER_PAGE = 50
DEFAULT_DAYS = 120


class LocalistScraper(BaseScraper):
    def fetch_events(self) -> Iterator[NormalizedEvent]:
        api_url = self.config.get("api_url")
        if not api_url:
            if not self.base_url:
                raise ConfigError(
                    f"Source '{self.slug}' has no base_url or config.api_url.",
                    hint="Add `base_url` (or `config.api_url`) pointing at a Localist site, e.g. https://events.ucf.edu",
                )
            api_url = self.base_url.rstrip("/") + "/api/2/events"

        per_page = self.config.get("pp", DEFAULT_PER_PAGE)
        days = self.config.get("days", DEFAULT_DAYS)

        page = 1
        while page <= MAX_PAGES:
            params = {"pp": per_page, "days": days, "page": page}
            resp = requests.get(api_url, params=params, headers=DEFAULT_HEADERS, timeout=30)

            if resp.status_code == 404:
                raise EndpointNotFoundError(
                    f"{api_url} returned 404.",
                    hint=(
                        "This site likely doesn't run the Localist events platform. "
                        "Switch this source's `scraper` to `tribe_events`, `ics_calendar`, "
                        "or `playwright_html` in config/sources.yaml."
                    ),
                )
            if resp.status_code in (403, 429):
                raise BlockedError(
                    f"{api_url} returned HTTP {resp.status_code}.",
                    hint="The site is blocking automated requests.",
                )
            if resp.status_code >= 400:
                raise ParseError(
                    f"{api_url} returned HTTP {resp.status_code}: {resp.text[:200]}",
                    hint="Inspect the response body for details.",
                )

            try:
                data = resp.json()
            except ValueError as exc:
                raise ParseError(
                    f"{api_url} did not return valid JSON.",
                    hint="Verify the URL points at a Localist `/api/2/events` endpoint.",
                ) from exc

            events = data.get("events")
            if events is None:
                raise ParseError(
                    f"{api_url} response had no 'events' key (keys: {list(data.keys())}).",
                    hint="The Localist API shape may differ for this site - inspect the raw response.",
                )

            for wrapper in events:
                event = wrapper.get("event") if isinstance(wrapper, dict) else None
                if not event:
                    continue
                yield from self._normalize(event)

            page_info = data.get("page") or {}
            total_pages = page_info.get("total", 1)
            if not events or page >= total_pages:
                return
            page += 1

    def _normalize(self, event: dict) -> Iterator[NormalizedEvent]:
        try:
            title = strip_html(event.get("title"))
            if not title:
                return

            description = strip_html(event.get("description_text") or event.get("description"))
            event_url = event.get("localist_url") or event.get("url")
            image_url = event.get("photo_url")

            venue_name = event.get("venue_name") or self.config.get("venue_name") or self.name
            address = event.get("address")
            city = event.get("city") or self.config.get("city")
            lat = _to_float(event.get("latitude"))
            lon = _to_float(event.get("longitude"))

            tag_names = event.get("tags") or []
            extra_tags, hint = classify_text(title, description, *tag_names)
            category = hint or self.default_category or "other"
            if "edm" in extra_tags:
                category = "nightlife_edm"

            instances = event.get("instances") or []
            for wrapper in instances:
                instance = wrapper.get("instance") if isinstance(wrapper, dict) else None
                if not instance:
                    continue
                start_dt = parse_datetime(instance.get("start"))
                if not start_dt:
                    continue
                end_dt = parse_datetime(instance.get("end"))

                yield NormalizedEvent(
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
                    is_all_day=bool(instance.get("all_day")),
                    ticket_url=event_url,
                    event_url=event_url,
                    image_url=image_url,
                    status="active",
                    source_event_id=str(instance.get("id") or event.get("id")),
                    source_url=event_url,
                    raw_data=event,
                )
        except Exception:
            logger.exception("%s: failed to normalize event %s", self.slug, event.get("id"))
            return


def _to_float(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
