"""Modern Events Calendar (MEC) AJAX scraper.

Used for WordPress sites that use the MEC plugin but don't expose a usable
REST API. Calls the wp-admin/admin-ajax.php endpoint with
action=mec_list_load_more month-by-month and parses the returned HTML.

config:
  city         - city name for venue fallback
  months_ahead - how many months forward to fetch (default 6)
  default_tags - list of tags applied to every event
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterator
from datetime import date, datetime, timedelta

import requests

from scraper.base import BaseScraper
from scraper.categories import classify_text
from scraper.errors import BlockedError, EndpointNotFoundError, ParseError
from scraper.models import NormalizedEvent
from scraper.utils import DEFAULT_HEADERS, EASTERN

logger = logging.getLogger(__name__)

_TITLE_RE = re.compile(
    r'class="mec-event-title"[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_LOC_RE = re.compile(
    r'class="mec-event-loc-place"[^>]*>(.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)
_OCCURRENCE_RE = re.compile(r'occurrence=(\d{4}-\d{2}-\d{2})')
_TIME_RE = re.compile(r'[?&]time=(\d+)')
_TAG_RE = re.compile(r'<[^>]+>')


def _clean(html: str) -> str:
    return _TAG_RE.sub('', html).strip().replace('&amp;', '&').replace('&#038;', '&').replace('&#8217;', '’').replace('&#039;', "'")


class MecAjaxScraper(BaseScraper):
    """Scrapes Modern Events Calendar via the mec_list_load_more AJAX action."""

    def fetch_events(self) -> Iterator[NormalizedEvent]:
        base_url = self.base_url.rstrip('/')
        ajax_url = f"{base_url}/wp-admin/admin-ajax.php"
        cfg = self.config or {}
        city = cfg.get('city', '')
        months_ahead = int(cfg.get('months_ahead', 6))
        default_tags = list(cfg.get('default_tags', []))
        default_category = self.default_category or 'community'

        today = date.today()
        seen: set[tuple[str, str]] = set()
        yielded = 0

        for offset_months in range(months_ahead):
            year = today.year
            month = today.month + offset_months
            while month > 12:
                month -= 12
                year += 1

            try:
                resp = requests.post(
                    ajax_url,
                    data={'action': 'mec_list_load_more', 'year': year, 'month': month},
                    headers=DEFAULT_HEADERS,
                    timeout=20,
                )
                resp.raise_for_status()
            except requests.HTTPError as exc:
                code = exc.response.status_code if exc.response is not None else 0
                if code == 403:
                    raise BlockedError(
                        f"MEC AJAX blocked (HTTP 403): {ajax_url}",
                        hint="Site may be blocking automated requests.",
                    ) from exc
                if code == 404:
                    raise EndpointNotFoundError(
                        f"MEC AJAX not found (HTTP 404): {ajax_url}",
                        hint="Site may not use Modern Events Calendar plugin.",
                    ) from exc
                raise

            try:
                data = resp.json()
            except ValueError as exc:
                raise ParseError(
                    f"MEC AJAX returned non-JSON for {year}/{month}",
                    hint="Check that the site uses MEC plugin and admin-ajax.php is accessible.",
                ) from exc

            html = data.get('html', '')
            if not html:
                time.sleep(0.5)
                continue

            articles = re.split(r'<article\b', html, flags=re.IGNORECASE)
            for article in articles[1:]:
                title_m = _TITLE_RE.search(article)
                if not title_m:
                    continue

                href = title_m.group(1).replace('&amp;', '&')
                title = _clean(title_m.group(2))
                if not title:
                    continue

                occ_m = _OCCURRENCE_RE.search(href)
                if not occ_m:
                    continue
                occ_date_str = occ_m.group(1)

                try:
                    occ_date = date.fromisoformat(occ_date_str)
                except ValueError:
                    continue
                if occ_date < today:
                    continue

                key = (title.lower(), occ_date_str)
                if key in seen:
                    continue
                seen.add(key)

                time_m = _TIME_RE.search(href)
                if time_m:
                    start_dt = datetime.fromtimestamp(int(time_m.group(1)), tz=EASTERN)
                else:
                    start_dt = datetime(occ_date.year, occ_date.month, occ_date.day,
                                        0, 0, 0, tzinfo=EASTERN)

                loc_m = _LOC_RE.search(article)
                venue_raw = _clean(loc_m.group(1)) if loc_m else ''

                venue_name = ''
                address = ''
                if '|' in venue_raw:
                    parts = venue_raw.split('|', 1)
                    venue_name = parts[0].strip()
                    address = parts[1].strip()
                elif venue_raw:
                    venue_name = venue_raw

                extra_tags, cat_hint = classify_text(title, '')
                category = cat_hint or default_category
                tags = list(dict.fromkeys(default_tags + list(extra_tags)))

                yield NormalizedEvent(
                    title=title,
                    start_datetime=start_dt,
                    venue_name_raw=venue_name or city or None,
                    address_raw=address or None,
                    event_url=href,
                    category=category,
                    tags=tags,
                    source_slug=self.slug,
                )
                yielded += 1

            time.sleep(0.5)

        logger.info('%s: yielded %d future events across %d months', self.slug, yielded, months_ahead)
