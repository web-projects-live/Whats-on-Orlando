"""Generic scraper for sources that publish events as a flat JSON array
(or a JSON object containing one) - e.g. the ScrapedDuck Pokemon GO
events feed (https://github.com/bigfoott/ScrapedDuck).

Because these feeds vary in field names, the scraper tries a list of
common field names for each piece of data and falls back to explicit
`config.<x>_field` overrides when the defaults don't match.

config:
  json_url (required)   - URL returning a JSON array of event objects.
  items_path            - dot-path to the list if the response is a
                           JSON object rather than a top-level array
                           (e.g. "data.events").
  title_field, start_field, end_field, url_field, image_field,
  description_field, id_field - override the field name used for that
                           attribute (otherwise a list of common names
                           is tried).
  venue_name, address, city, latitude, longitude - static location info
                           applied to every event (useful for
                           location-agnostic sources, e.g. mobile-game
                           live events that are still relevant to local
                           players).
  default_tags, lookahead_days (default 365)
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import datetime, timedelta

import requests

from scraper.base import BaseScraper
from scraper.categories import classify_text
from scraper.errors import BlockedError, ConfigError, EndpointNotFoundError, ParseError
from scraper.models import NormalizedEvent
from scraper.utils import DEFAULT_HEADERS, EASTERN, parse_datetime, strip_html

logger = logging.getLogger(__name__)

DEFAULT_LOOKAHEAD_DAYS = 365

_TITLE_FIELDS = ["title", "name", "summary"]
_START_FIELDS = ["start", "start_date", "startDate", "start_time", "startTime", "begin"]
_END_FIELDS = ["end", "end_date", "endDate", "end_time", "endTime"]
_URL_FIELDS = ["link", "url", "event_url", "permalink"]
_IMAGE_FIELDS = ["image", "image_url", "imageUrl", "thumbnail"]
_DESC_FIELDS = ["description", "heading", "summary", "details"]
_ID_FIELDS = ["eventID", "id", "uid", "slug"]


def _dig(obj, path: str):
    for part in path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(part)
        else:
            return None
    return obj


def _pick(item: dict, fields: list[str], override: str | None):
    if override:
        return item.get(override)
    for f in fields:
        value = item.get(f)
        if value:
            return value
    return None


class JsonFeedScraper(BaseScraper):
    def fetch_events(self) -> Iterator[NormalizedEvent]:
        json_url = self.config.get("json_url")
        if not json_url:
            raise ConfigError(
                f"Source '{self.slug}' has no config.json_url.",
                hint="Add `config.json_url: <feed url>` to this source in config/sources.yaml.",
            )

        resp = requests.get(json_url, headers=DEFAULT_HEADERS, timeout=30)
        if resp.status_code == 404:
            raise EndpointNotFoundError(
                f"{json_url} returned 404.",
                hint="Verify the feed URL is still correct.",
            )
        if resp.status_code in (403, 429):
            raise BlockedError(
                f"{json_url} returned HTTP {resp.status_code}.",
                hint="The feed is blocking automated requests.",
            )
        if resp.status_code >= 400:
            raise ParseError(
                f"{json_url} returned HTTP {resp.status_code}: {resp.text[:200]}",
                hint="Inspect the response body for details.",
            )

        try:
            data = resp.json()
        except ValueError as exc:
            raise ParseError(
                f"{json_url} did not return valid JSON.",
                hint="The feed may have moved or changed format - inspect it in a browser.",
            ) from exc

        items_path = self.config.get("items_path")
        if items_path:
            data = _dig(data, items_path)

        if not isinstance(data, list):
            raise ParseError(
                f"{json_url} did not contain a JSON array of events"
                + (f" at items_path '{items_path}'" if items_path else "")
                + f" (got {type(data).__name__}).",
                hint="Set `config.items_path` to the dot-path of the events array within the response.",
            )

        now = datetime.now(EASTERN)
        lookahead_days = self.config.get("lookahead_days", DEFAULT_LOOKAHEAD_DAYS)
        cutoff = now + timedelta(days=lookahead_days)

        for item in data:
            if not isinstance(item, dict):
                continue
            normalized = self._normalize(item, now, cutoff)
            if normalized:
                yield normalized

    def _normalize(self, item: dict, now: datetime, cutoff: datetime) -> NormalizedEvent | None:
        try:
            title = strip_html(_pick(item, _TITLE_FIELDS, self.config.get("title_field")))
            if not title:
                return None

            start_raw = _pick(item, _START_FIELDS, self.config.get("start_field"))
            start_dt = parse_datetime(start_raw) if isinstance(start_raw, str) else None
            if not start_dt:
                return None

            end_raw = _pick(item, _END_FIELDS, self.config.get("end_field"))
            end_dt = parse_datetime(end_raw) if isinstance(end_raw, str) else None

            effective_end = end_dt or start_dt
            if effective_end < now - timedelta(days=1) or start_dt > cutoff:
                return None

            description = strip_html(_pick(item, _DESC_FIELDS, self.config.get("description_field")))
            event_url = _pick(item, _URL_FIELDS, self.config.get("url_field"))
            image_url = _pick(item, _IMAGE_FIELDS, self.config.get("image_field"))
            source_id = _pick(item, _ID_FIELDS, self.config.get("id_field"))

            extra_tags, hint = classify_text(title, description)
            category = hint or self.default_category or "other"

            return NormalizedEvent(
                title=title,
                description=description,
                category=category,
                tags=sorted(extra_tags | set(self.config.get("default_tags", []))),
                venue_name=self.config.get("venue_name") or self.name,
                address=self.config.get("address"),
                city=self.config.get("city"),
                latitude=self.config.get("latitude"),
                longitude=self.config.get("longitude"),
                start_datetime=start_dt,
                end_datetime=end_dt,
                ticket_url=event_url,
                event_url=event_url,
                image_url=image_url,
                status="active",
                source_event_id=str(source_id) if source_id is not None else None,
                source_url=event_url,
                raw_data=item,
            )
        except Exception:
            logger.exception("%s: failed to normalize event", self.slug)
            return None
