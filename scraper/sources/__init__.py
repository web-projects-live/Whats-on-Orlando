"""Registry mapping `scraper:` values in config/sources.yaml to their
implementation classes."""

from __future__ import annotations

from scraper.sources.eventbrite import EventbriteScraper
from scraper.sources.facebook_events import FacebookEventsScraper
from scraper.sources.ics_calendar import IcsCalendarScraper
from scraper.sources.json_feed import JsonFeedScraper
from scraper.sources.localist import LocalistScraper
from scraper.sources.playwright_html import PlaywrightHtmlScraper
from scraper.sources.songkick import SongkickScraper
from scraper.sources.startgg import StartggScraper
from scraper.sources.ticketmaster import TicketmasterScraper
from scraper.sources.tribe_events import TribeEventsScraper

SCRAPER_REGISTRY = {
    "ticketmaster": TicketmasterScraper,
    "eventbrite": EventbriteScraper,
    "tribe_events": TribeEventsScraper,
    "ics_calendar": IcsCalendarScraper,
    "playwright_html": PlaywrightHtmlScraper,
    "facebook_events": FacebookEventsScraper,
    "json_feed": JsonFeedScraper,
    "localist": LocalistScraper,
    "start_gg": StartggScraper,
    "songkick": SongkickScraper,
}

