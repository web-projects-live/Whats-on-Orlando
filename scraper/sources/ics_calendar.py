"""Generic scraper for any source publishing a standard iCalendar
(.ics) feed - common for venue/library/museum/community calendars
(Google Calendar exports, Spektrix, Eventbrite organizer feeds, etc.)

config:
  ics_url: <required> URL of the .ics feed (falls back to base_url)
  venue_name, city, latitude, longitude: applied to every event from
    this feed (an .ics feed is normally for a single venue)
  default_tags: list[str] applied to every event
  lookahead_days: int (default 180)
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import date, datetime, timedelta

import requests
from icalendar import Calendar

from scraper.base import BaseScraper
from scraper.categories import classify_text
from scraper.errors import BlockedError, ConfigError, ParseError
from scraper.models import NormalizedEvent
from scraper.utils import DEFAULT_HEADERS, EASTERN, strip_html, to_eastern

logger = logging.getLogger(__name__)

DEFAULT_LOOKAHEAD_DAYS = 180


class IcsCalendarScraper(BaseScraper):
    def fetch_events(self) -> Iterator[NormalizedEvent]:
        url = self.config.get("ics_url") or self.base_url
        if not url:
            raise ConfigError(
                f"Source '{self.slug}' has no ics_url/base_url configured.",
                hint="Add `config.ics_url: <feed url>` to this source in config/sources.yaml.",
            )

        resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=30)
        if resp.status_code in (403, 429):
            raise BlockedError(
                f"{url} returned HTTP {resp.status_code}.",
                hint="The feed is blocking automated requests - verify it's still publicly accessible.",
            )
        if resp.status_code >= 400:
            raise ParseError(
                f"{url} returned HTTP {resp.status_code}.",
                hint="Verify the ICS feed URL is still correct (calendar export links sometimes rotate).",
            )

        try:
            cal = Calendar.from_ical(resp.content)
        except ValueError as exc:
            raise ParseError(
                f"{url} did not parse as a valid iCalendar feed: {exc}",
                hint="Confirm the URL points directly at an .ics file (Content-Type: text/calendar).",
            ) from exc

        now = datetime.now(EASTERN)
        lookahead_days = self.config.get("lookahead_days", DEFAULT_LOOKAHEAD_DAYS)
        cutoff = now + timedelta(days=lookahead_days)

        for component in cal.walk("VEVENT"):
            normalized = self._normalize(component, now, cutoff)
            if normalized:
                yield normalized

    def _normalize(self, component, now: datetime, cutoff: datetime) -> NormalizedEvent | None:
        try:
            summary = strip_html(str(component.get("summary", "")))
            if not summary:
                return None

            dtstart_prop = component.get("dtstart")
            if dtstart_prop is None:
                return None
            dtstart = dtstart_prop.dt
            is_all_day = isinstance(dtstart, date) and not isinstance(dtstart, datetime)
            start_dt = _to_datetime(dtstart)

            # Skip events too far in the past or beyond our lookahead window.
            if start_dt < now - timedelta(days=1) or start_dt > cutoff:
                return None

            dtend_prop = component.get("dtend")
            end_dt = _to_datetime(dtend_prop.dt) if dtend_prop is not None else None

            description = strip_html(str(component.get("description", ""))) or None
            location = str(component.get("location", "")) or None

            extra_tags, hint = classify_text(summary, description)
            category = hint or self.default_category or "other"
            if "edm" in extra_tags:
                category = "nightlife_edm"

            url_prop = component.get("url")
            event_url = str(url_prop) if url_prop else self.base_url

            uid = str(component.get("uid", "")) or f"{summary}-{start_dt.isoformat()}"

            return NormalizedEvent(
                title=summary,
                description=description,
                category=category,
                tags=sorted(extra_tags | set(self.config.get("default_tags", []))),
                venue_name=self.config.get("venue_name") or location or self.name,
                address=None,
                city=self.config.get("city"),
                latitude=self.config.get("latitude"),
                longitude=self.config.get("longitude"),
                start_datetime=start_dt,
                end_datetime=end_dt,
                is_all_day=is_all_day,
                ticket_url=event_url,
                event_url=event_url,
                status="active",
                source_event_id=uid,
                source_url=event_url,
                raw_data={"summary": summary, "uid": uid, "location": location},
            )
        except Exception:
            logger.exception("%s: failed to normalize ICS event", self.slug)
            return None


def _to_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return to_eastern(value)
    return datetime(value.year, value.month, value.day, tzinfo=EASTERN)
