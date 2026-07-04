"""CivicEngage (CivicPlus) city government calendar scraper.

CivicEngage is the CMS used by many FL municipalities. Calendar pages at
/calendar.aspx?view=list&category=0 are server-rendered and include clean
schema.org Event markup — no API key or Playwright required.

config:
  list_url   - override the default /calendar.aspx?view=list&category=0
  city       - fallback city name when schema.org addressLocality is absent
  default_tags - list of tags applied to every event from this source
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from datetime import datetime, timezone

import requests

from scraper.base import BaseScraper
from scraper.categories import classify_text
from scraper.errors import BlockedError, EndpointNotFoundError, ParseError, ScraperError
from scraper.models import NormalizedEvent
from scraper.utils import DEFAULT_HEADERS, EASTERN

logger = logging.getLogger(__name__)

_ITEMPROP = re.compile(r'itemprop="([^"]+)"[^>]*>([^<]*)<', re.IGNORECASE)
_HREF = re.compile(r'<h3[^>]*>.*?<a[^>]+href="([^"]+)"', re.IGNORECASE | re.DOTALL)


def _extract(li_html: str, prop: str) -> str:
    """Return the first itemprop value matching `prop` within a LI block."""
    for m in _ITEMPROP.finditer(li_html):
        if m.group(1).lower() == prop.lower():
            return m.group(2).strip()
    return ""


def _extract_venue_name(li_html: str) -> str:
    """Return the venue name (itemprop=name inside itemprop=location)."""
    loc_m = re.search(r'itemprop="location"[^>]*>(.*?)(?:</span>|</div>)', li_html, re.DOTALL | re.IGNORECASE)
    if not loc_m:
        return ""
    return _extract(loc_m.group(1), "name")


class CivicEngageScraper(BaseScraper):

    def fetch_events(self) -> Iterator[NormalizedEvent]:
        cfg = self.config
        list_url = cfg.get("list_url") or f"{self.base_url}/calendar.aspx?view=list&category=0"
        default_city = cfg.get("city", "")
        default_tags = set(cfg.get("default_tags", []))

        try:
            resp = requests.get(list_url, headers=DEFAULT_HEADERS, timeout=20)
            resp.raise_for_status()
        except requests.HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else 0
            if code == 403:
                raise BlockedError(
                    f"CivicEngage calendar blocked (HTTP 403): {list_url}",
                    hint="The city site may be blocking non-browser requests. Try playwright_html instead.",
                ) from exc
            if code == 404:
                raise EndpointNotFoundError(
                    f"CivicEngage calendar not found (HTTP 404): {list_url}",
                    hint="This site may not use CivicEngage, or the URL path has changed.",
                ) from exc
            raise ScraperError(f"HTTP {code} fetching {list_url}") from exc

        # CivicEngage pages often declare UTF-8 but serve Windows-1252 bytes
        html = resp.content.decode("windows-1252", errors="replace")

        # Split into LI blocks that contain schema.org event markup
        li_blocks = re.split(r"<li\b[^>]*>", html, flags=re.IGNORECASE)
        yielded = 0

        for li_html in li_blocks:
            if 'itemprop="startDate"' not in li_html:
                continue

            title = _extract(li_html, "name")
            if not title:
                continue

            start_raw = _extract(li_html, "startDate")
            if not start_raw:
                continue

            # Parse naive datetime, attach Eastern timezone
            try:
                naive = datetime.fromisoformat(start_raw)
                start_dt = naive.replace(tzinfo=EASTERN)
            except ValueError:
                logger.debug("%s: could not parse startDate %r", self.slug, start_raw)
                continue

            # Skip past events (shouldn't appear on the list page, but be safe)
            if start_dt < datetime.now(tz=timezone.utc):
                continue

            venue_name = _extract_venue_name(li_html)
            address = _extract(li_html, "streetAddress")
            city = _extract(li_html, "addressLocality") or default_city
            description = _extract(li_html, "description")

            # Event URL from <h3><a href="...">
            event_url = ""
            href_m = _HREF.search(li_html)
            if href_m:
                href = href_m.group(1)
                if href.startswith("/"):
                    event_url = self.base_url.rstrip("/") + href
                elif href.startswith("http"):
                    event_url = href

            extra_tags, cat_hint = classify_text(title, description)
            category = cat_hint or self.default_category or "community"

            yield NormalizedEvent(
                title=title,
                description=description or None,
                category=category,
                tags=sorted(extra_tags | default_tags),
                venue_name=venue_name or None,
                address=address or None,
                city=city or None,
                latitude=None,
                longitude=None,
                start_datetime=start_dt,
                end_datetime=None,
                is_all_day=False,
                ticket_url=event_url or None,
                event_url=event_url or None,
                image_url=None,
                status="active",
                source_event_id=href_m.group(1) if href_m else title[:80],
                source_url=list_url,
                raw_data={"title": title, "startDate": start_raw},
            )
            yielded += 1

        logger.debug("%s: yielded %d events from %s", self.slug, yielded, list_url)
