"""Generic config-driven scraper for venue/org sites with no JSON or
.ics feed - uses a real headless browser (so it works against
JS-rendered pages and most Cloudflare "I'm under attack" challenges)
and CSS selectors supplied per-source in config/sources.yaml.

config:
  list_url: <required> page that lists upcoming events
  item_selector: <required> CSS selector for each event "card"
  title_selector: selector (relative to item) for the title - default: whole item text
  date_selector: selector (relative to item) for the date/time text
  link_selector: selector (relative to item) for the <a href> detail link
  image_selector: selector (relative to item) for an <img src>
  description_selector: selector (relative to item) for a description/summary
  wait_ms: extra time to wait after page load for JS to render (default 2000)
  venue_name, address, city, latitude, longitude: applied to every event

NOTE: selectors here are best-effort starting points. Venue sites get
redesigned; if this source starts raising `no_results` or
`parse_error`, use the hint in the run summary to inspect the live
page and update the selectors below - no code changes needed.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from dateutil import parser as dateparser

from scraper.base import BaseScraper
from scraper.categories import classify_text
from scraper.errors import BlockedError, ConfigError, NoResultsError, ParseError
from scraper.models import NormalizedEvent
from scraper.utils import EASTERN, USER_AGENT, absolutize, element_attr, element_text, to_eastern

logger = logging.getLogger(__name__)

BLOCK_INDICATORS = (
    "just a moment",
    "attention required",
    "access denied",
    "are you human",
    "captcha",
)


class PlaywrightHtmlScraper(BaseScraper):
    def fetch_events(self) -> Iterator[NormalizedEvent]:
        cfg = self.config
        list_url = cfg.get("list_url") or self.base_url
        item_selector = cfg.get("item_selector")
        if not list_url or not item_selector:
            raise ConfigError(
                f"Source '{self.slug}' is missing config.list_url and/or config.item_selector.",
                hint=(
                    "Add `config.list_url` (the events page) and `config.item_selector` "
                    "(CSS selector for each event card) in config/sources.yaml."
                ),
            )

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ConfigError(
                "The 'playwright' package is not installed.",
                hint="Run `pip install playwright && playwright install chromium`.",
            ) from exc

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=USER_AGENT)
            try:
                page.goto(list_url, timeout=45000, wait_until="domcontentloaded")
                page.wait_for_timeout(cfg.get("wait_ms", 2000))

                page_text_lower = page.inner_text("body").lower()
                if any(indicator in page_text_lower[:2000] for indicator in BLOCK_INDICATORS):
                    raise BlockedError(
                        f"{list_url} appears to show a bot-protection / challenge page.",
                        hint=(
                            "Cloudflare or similar is blocking this scrape. Consider a different "
                            "source for this venue, or set `is_active: false` for now."
                        ),
                    )

                items = page.query_selector_all(item_selector)
                if not items:
                    raise NoResultsError(
                        f"Selector '{item_selector}' matched 0 elements on {list_url}.",
                        hint=(
                            "The page structure has likely changed. Open the page in a browser, "
                            "find the event cards, and update `config.item_selector` "
                            f"for '{self.slug}' in config/sources.yaml."
                        ),
                    )

                normalize_errors = 0
                for item in items:
                    try:
                        normalized = self._normalize(item, list_url, cfg)
                    except Exception:
                        normalize_errors += 1
                        logger.exception("%s: failed to normalize an item", self.slug)
                        continue
                    if normalized:
                        yield normalized

                if normalize_errors == len(items):
                    raise ParseError(
                        f"All {len(items)} items matched by '{item_selector}' failed to parse.",
                        hint=(
                            "title_selector/date_selector probably don't match this page's markup. "
                            f"Update the selectors for '{self.slug}' in config/sources.yaml."
                        ),
                    )
            finally:
                browser.close()

    def _normalize(self, item, base_url: str, cfg: dict) -> NormalizedEvent | None:
        title = element_text(item, cfg.get("title_selector"))
        if not title:
            return None

        date_text = element_text(item, cfg.get("date_selector"))
        start_dt = _parse_event_date(date_text) if date_text else None
        if not start_dt:
            return None

        link = element_attr(item, cfg.get("link_selector"), "href") or base_url
        link = absolutize(link, base_url)
        image = element_attr(item, cfg.get("image_selector"), "src")
        image = absolutize(image, base_url) if image else None
        description = element_text(item, cfg.get("description_selector"))

        extra_tags, hint = classify_text(title, description)
        category = hint or self.default_category or "other"
        if "edm" in extra_tags:
            category = "nightlife_edm"

        return NormalizedEvent(
            title=title,
            description=description,
            category=category,
            tags=sorted(extra_tags | set(cfg.get("default_tags", []))),
            venue_name=cfg.get("venue_name") or self.name,
            address=cfg.get("address"),
            city=cfg.get("city"),
            latitude=cfg.get("latitude"),
            longitude=cfg.get("longitude"),
            start_datetime=start_dt,
            end_datetime=None,
            is_all_day=cfg.get("all_day", False),
            ticket_url=link,
            event_url=link,
            image_url=image,
            status="active",
            source_event_id=link,
            source_url=link,
            raw_data={"title": title, "date_text": date_text, "link": link},
        )


def _parse_event_date(text: str):
    """Best-effort parse of a free-text date/time string into a
    timezone-aware datetime. Handles ranges ("June 15 - June 16") by
    taking the first date found."""
    cleaned = text.strip()
    for sep in (" - ", " – ", " — ", " to "):
        if sep in cleaned:
            cleaned = cleaned.split(sep)[0].strip()
            break
    try:
        dt = dateparser.parse(cleaned, fuzzy=True)
    except (ValueError, OverflowError):
        return None
    return to_eastern(dt)
