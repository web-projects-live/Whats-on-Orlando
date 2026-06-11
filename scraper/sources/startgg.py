"""start.gg (smash.gg) GraphQL API - Super Smash Bros. Melee & Ultimate
tournament listings near the corridor.

Requires a free API token from
https://developer.start.gg/docs/authentication set as the
STARTGG_API_KEY environment variable / GitHub secret.

config:
  video_game_ids - list[int] of start.gg videogame IDs to search for
                    (default: [1, 1386] - Melee + Ultimate)
  lookahead_days - int (default 60)
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import requests

from scraper.base import BaseScraper
from scraper.categories import classify_text
from scraper.errors import BlockedError, CredentialsMissingError, ParseError
from scraper.geo import search_centers
from scraper.models import NormalizedEvent
from scraper.utils import DEFAULT_HEADERS

logger = logging.getLogger(__name__)

API_URL = "https://api.start.gg/gql/alpha"
DEFAULT_VIDEOGAME_IDS = [1, 1386]  # Melee, Ultimate
DEFAULT_LOOKAHEAD_DAYS = 60
PER_PAGE = 50

QUERY = """
query TournamentsByLocation($perPage: Int, $coordinates: String, $radius: String, $videogameIds: [ID], $afterDate: Timestamp, $beforeDate: Timestamp) {
  tournaments(query: {
    perPage: $perPage
    filter: {
      location: {distanceFrom: $coordinates, distance: $radius}
      videogameIds: $videogameIds
      afterDate: $afterDate
      beforeDate: $beforeDate
    }
  }) {
    nodes {
      id
      name
      slug
      startAt
      endAt
      timezone
      venueAddress
      venueName
      city
      lat
      lng
      url(relative: false)
      images {
        url
        type
      }
    }
  }
}
"""

GAME_TAGS = {
    1: "melee",
    1386: "smash_ultimate",
}


class StartggScraper(BaseScraper):
    def fetch_events(self) -> Iterator[NormalizedEvent]:
        api_key = os.environ.get("STARTGG_API_KEY")
        if not api_key:
            raise CredentialsMissingError(
                "STARTGG_API_KEY is not set.",
                hint=(
                    "Get a token at https://developer.start.gg/docs/authentication "
                    "and add it as a repo secret (and to .env locally) named STARTGG_API_KEY."
                ),
            )

        video_game_ids = self.config.get("video_game_ids", DEFAULT_VIDEOGAME_IDS)
        lookahead_days = self.config.get("lookahead_days", DEFAULT_LOOKAHEAD_DAYS)
        now = datetime.now(timezone.utc)
        after = int(now.timestamp())
        before = int((now + timedelta(days=lookahead_days)).timestamp())

        headers = {
            **DEFAULT_HEADERS,
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        seen_ids: set = set()
        for center in search_centers():
            coordinates = f"{center['lat']},{center['lon']}"
            radius = f"{center['radius_miles']}mi"
            variables = {
                "perPage": PER_PAGE,
                "coordinates": coordinates,
                "radius": radius,
                "videogameIds": video_game_ids,
                "afterDate": after,
                "beforeDate": before,
            }
            resp = requests.post(
                API_URL,
                json={"query": QUERY, "variables": variables},
                headers=headers,
                timeout=30,
            )

            if resp.status_code == 401:
                raise CredentialsMissingError(
                    "start.gg API returned 401 Unauthorized.",
                    hint="STARTGG_API_KEY is set but invalid/expired - check the token value.",
                )
            if resp.status_code == 429:
                logger.warning("start.gg rate limited, backing off 2s")
                time.sleep(2)
                continue
            if resp.status_code == 403:
                raise BlockedError(
                    f"start.gg API returned {resp.status_code}.",
                    hint="Request was blocked - verify the API token's scopes.",
                )
            if resp.status_code >= 400:
                raise ParseError(
                    f"start.gg API returned HTTP {resp.status_code}: {resp.text[:200]}",
                    hint="Check the GraphQL query/variables against the start.gg API docs.",
                )

            try:
                data = resp.json()
            except ValueError as exc:
                raise ParseError(
                    f"start.gg API response was not valid JSON: {exc}",
                    hint="The API may be returning an HTML error page - inspect the raw response.",
                ) from exc

            if data.get("errors"):
                raise ParseError(
                    f"start.gg API returned GraphQL errors: {data['errors']}",
                    hint="Check the GraphQL query against the current start.gg schema (it changes occasionally).",
                )

            nodes = (data.get("data") or {}).get("tournaments", {}).get("nodes") or []
            for node in nodes:
                tid = node.get("id")
                if tid is None or tid in seen_ids:
                    continue
                seen_ids.add(tid)
                normalized = self._normalize(node, video_game_ids)
                if normalized:
                    yield normalized

            time.sleep(0.5)

    def _normalize(self, node: dict, video_game_ids: list[int]) -> NormalizedEvent | None:
        try:
            name = node.get("name")
            start_at = node.get("startAt")
            if not name or not start_at:
                return None

            start_dt = datetime.fromtimestamp(start_at, tz=timezone.utc)
            end_at = node.get("endAt")
            end_dt = datetime.fromtimestamp(end_at, tz=timezone.utc) if end_at else None

            tags = {"esports", "tournament"}
            for vg_id in video_game_ids:
                if vg_id in GAME_TAGS:
                    tags.add(GAME_TAGS[vg_id])

            extra_tags, _hint = classify_text(name)
            tags |= extra_tags

            event_url = node.get("url")
            image_url = None
            images = node.get("images") or []
            if images:
                image_url = images[0].get("url")

            return NormalizedEvent(
                title=name,
                description=None,
                category="gaming",
                tags=sorted(tags),
                venue_name=node.get("venueName") or self.name,
                address=node.get("venueAddress"),
                city=node.get("city"),
                latitude=node.get("lat"),
                longitude=node.get("lng"),
                start_datetime=start_dt,
                end_datetime=end_dt,
                ticket_url=event_url,
                event_url=event_url,
                image_url=image_url,
                status="active",
                source_event_id=str(node.get("id")),
                source_url=event_url,
                raw_data=node,
            )
        except Exception:
            logger.exception("%s: failed to normalize start.gg tournament %s", self.slug, node.get("id"))
            return None
