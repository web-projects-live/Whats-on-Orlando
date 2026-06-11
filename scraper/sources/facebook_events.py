"""Best-effort scraper for public Facebook "Events" tabs of bar/venue
pages, using the lightweight mbasic.facebook.com interface.

HIGH MAINTENANCE / FRAGILE - this is the "selective social scraping"
source the project intentionally treats as supplemental:

- Requires a `FACEBOOK_COOKIE` env var: the raw `Cookie:` header value
  copied from a logged-in mbasic.facebook.com browser session
  (DevTools -> Network -> any request -> Cookie header). Facebook
  walls off almost everything from logged-out/automated clients.
- Disabled by default per-source in config/sources.yaml
  (`is_active: false`) - enable individual pages only after confirming
  they actually return event data, since mbasic's markup changes often
  and sessions expire.
- Prefer the venue's own website/calendar (tribe_events,
  ics_calendar, playwright_html) when available; use this only for
  small bars/venues that exclusively post on Facebook.

config:
  page_slug: <required> the Facebook page's username/slug, e.g. "willspub"
  venue_name, address, city, latitude, longitude: applied to every event
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterator
from datetime import datetime, timedelta
from urllib.parse import urljoin

from dateutil import parser as dateparser

from scraper.base import BaseScraper
from scraper.categories import classify_text
from scraper.errors import ConfigError, CredentialsMissingError, NoResultsError
from scraper.models import NormalizedEvent
from scraper.utils import EASTERN, USER_AGENT, to_eastern

logger = logging.getLogger(__name__)

MBASIC_BASE = "https://mbasic.facebook.com"
MAX_EVENTS_PER_PAGE = 25


class FacebookEventsScraper(BaseScraper):
    def fetch_events(self) -> Iterator[NormalizedEvent]:
        cookie = os.environ.get("FACEBOOK_COOKIE")
        if not cookie:
            raise CredentialsMissingError(
                "FACEBOOK_COOKIE is not set.",
                hint=(
                    "Log into mbasic.facebook.com in a browser, copy the request 'Cookie' header, "
                    "and set it as the FACEBOOK_COOKIE secret/env var. Until then this source is skipped."
                ),
            )

        page_slug = self.config.get("page_slug")
        if not page_slug:
            raise ConfigError(
                f"Source '{self.slug}' has no config.page_slug.",
                hint="Add `config.page_slug: <facebook page username>` in config/sources.yaml.",
            )

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ConfigError(
                "The 'playwright' package is not installed.",
                hint="Run `pip install playwright && playwright install chromium`.",
            ) from exc

        events_url = f"{MBASIC_BASE}/{page_slug}/events"
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent=USER_AGENT)
            context.add_cookies(_parse_cookie_header(cookie))
            page = context.new_page()
            try:
                page.goto(events_url, timeout=45000, wait_until="domcontentloaded")

                links = page.query_selector_all("a[href*='/events/']")
                event_urls: dict[str, str] = {}
                for link in links:
                    href = link.get_attribute("href") or ""
                    m = re.search(r"/events/(\d+)", href)
                    if m and m.group(1) not in event_urls:
                        event_urls[m.group(1)] = urljoin(MBASIC_BASE, href)
                    if len(event_urls) >= MAX_EVENTS_PER_PAGE:
                        break

                if not event_urls:
                    raise NoResultsError(
                        f"No event links found on {events_url}.",
                        hint=(
                            "Either this page has no upcoming public events, FACEBOOK_COOKIE has "
                            "expired/is logged out, or mbasic's markup changed. Verify by visiting "
                            f"{events_url} in a logged-in browser."
                        ),
                    )

                for event_id, event_url in event_urls.items():
                    normalized = self._scrape_event(page, event_id, event_url)
                    if normalized:
                        yield normalized
            finally:
                browser.close()

    def _scrape_event(self, page, event_id: str, event_url: str) -> NormalizedEvent | None:
        try:
            page.goto(event_url, timeout=45000, wait_until="domcontentloaded")
            title = page.title().split("|")[0].strip()
            if not title:
                return None

            body_text = page.inner_text("body")
            start_dt = _extract_date(body_text)
            if not start_dt:
                logger.debug("%s: could not find a date on %s", self.slug, event_url)
                return None

            extra_tags, hint = classify_text(title, body_text[:500])
            category = hint or self.default_category or "other"
            if "edm" in extra_tags:
                category = "nightlife_edm"

            return NormalizedEvent(
                title=title,
                description=None,
                category=category,
                tags=sorted(extra_tags | set(self.config.get("default_tags", []))),
                venue_name=self.config.get("venue_name") or self.name,
                address=self.config.get("address"),
                city=self.config.get("city"),
                latitude=self.config.get("latitude"),
                longitude=self.config.get("longitude"),
                start_datetime=start_dt,
                end_datetime=None,
                is_all_day=False,
                ticket_url=event_url,
                event_url=event_url,
                image_url=None,
                status="active",
                source_event_id=event_id,
                source_url=event_url,
                raw_data={"title": title},
            )
        except Exception:
            logger.exception("%s: failed to scrape event %s", self.slug, event_url)
            return None


def _parse_cookie_header(cookie_header: str) -> list[dict]:
    cookies = []
    for part in cookie_header.split(";"):
        if "=" not in part:
            continue
        name, value = part.strip().split("=", 1)
        cookies.append(
            {
                "name": name,
                "value": value,
                "domain": ".facebook.com",
                "path": "/",
            }
        )
    return cookies


_DATE_LINE_RE = re.compile(
    r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*,?\s+[A-Z][a-z]+\s+\d{1,2}"
    r"|\b[A-Z][a-z]+\s+\d{1,2}(?:,\s*\d{4})?\s*(?:at|@)?\s*\d{1,2}(:\d{2})?\s*(?:AM|PM|am|pm)",
)


def _extract_date(body_text: str) -> datetime | None:
    """Scan page text for the first line that looks like an event
    date/time and parse it. mbasic event pages typically show the
    date near the top, e.g. 'Sat, Jan 17, 2026 at 8:00 PM'."""
    now = datetime.now(EASTERN)
    horizon = now + timedelta(days=400)
    for line in body_text.splitlines():
        line = line.strip()
        if not line or not _DATE_LINE_RE.search(line):
            continue
        try:
            dt = to_eastern(dateparser.parse(line, fuzzy=True))
        except (ValueError, OverflowError):
            continue
        if now - timedelta(days=1) <= dt <= horizon:
            return dt
    return None
