"""Diagnostic exceptions for scrapers.

Scrapers should raise these (instead of letting raw `requests` /
Playwright exceptions bubble up unannotated) whenever they detect a
*known* failure mode. Each carries a short `hint` describing the
likely fix, which the orchestrator (scraper/main.py) surfaces in its
run summary and writes to `scrape_runs.error_category` /
`error_message`. The goal is that a broken nightly source points
directly at "what to change in config/sources.yaml" instead of
requiring a debugging session.

If a scraper raises a plain (non-ScraperError) exception, it's caught
too, but is reported under the `unexpected_error` category with a
generic "check the traceback" hint.
"""

from __future__ import annotations


class ScraperError(Exception):
    """Base class for diagnostic scraper errors."""

    category = "unexpected_error"

    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.hint = hint

    def format(self) -> str:
        base = f"[{self.category}] {self}"
        return f"{base} -- HINT: {self.hint}" if self.hint else base


class ConfigError(ScraperError):
    """A required field is missing/invalid in config/sources.yaml."""

    category = "config_error"


class CredentialsMissingError(ScraperError):
    """A required API key / cookie / token isn't set in the environment."""

    category = "missing_credentials"


class EndpointNotFoundError(ScraperError):
    """The expected API endpoint returned 404 - this scraper type
    probably doesn't match the target site."""

    category = "not_found"


class BlockedError(ScraperError):
    """Request was blocked (403/429/captcha) by the target site."""

    category = "blocked"


class ParseError(ScraperError):
    """The response couldn't be parsed in the expected shape."""

    category = "parse_error"


class NoResultsError(ScraperError):
    """The request succeeded but produced zero events - usually a sign
    that selectors/endpoints/filters need adjustment (or the source is
    genuinely empty right now, in which case this can be ignored)."""

    category = "no_results"
