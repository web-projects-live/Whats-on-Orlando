"""Small shared helpers used across scrapers."""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from dateutil import parser as dateparser

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
    "WhatsOnOrlandoBot/1.0 (+https://github.com/web-projects-live/whats-on-orlando)"
)

DEFAULT_HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json, text/html, */*"}

EASTERN = ZoneInfo("America/New_York")

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(value: str | None) -> str | None:
    """Strip HTML tags and collapse whitespace from a text blob."""
    if not value:
        return None
    text = _TAG_RE.sub(" ", value)
    text = _WS_RE.sub(" ", text).strip()
    return text or None


def to_eastern(dt: datetime) -> datetime:
    """Attach America/New_York if the datetime is naive."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=EASTERN)
    return dt


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return to_eastern(dateparser.parse(value))
    except (ValueError, OverflowError):
        return None


def absolutize(url: str | None, base_url: str) -> str | None:
    if not url:
        return None
    return urljoin(base_url, url)


def element_text(node, selector: str | None) -> str | None:
    """Get the trimmed inner text of `selector` within a Playwright
    element handle, or of the node itself if `selector` is falsy."""
    if not selector:
        target = node
    else:
        target = node.query_selector(selector)
    if target is None:
        return None
    text = target.inner_text()
    return text.strip() or None


_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    value = _SLUG_NON_ALNUM.sub("-", value.lower()).strip("-")
    return _WS_RE.sub("-", value)


def element_attr(node, selector: str | None, attr: str) -> str | None:
    """Get an attribute from `selector` within a Playwright element
    handle, or of the node itself if `selector` is falsy."""
    if not selector:
        target = node
    else:
        target = node.query_selector(selector)
    if target is None:
        return None
    return target.get_attribute(attr)
