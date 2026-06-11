"""Geographic scope helpers for the Central Florida corridor.

Configuration lives in config/geo.yaml so the corridor can be tuned
without touching code.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "geo.yaml"


@lru_cache
def load_geo_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text())


@lru_cache
def _city_set() -> frozenset[str]:
    return frozenset(c.strip().lower() for c in load_geo_config()["cities"])


def in_corridor(latitude: float | None, longitude: float | None, city: str | None) -> bool:
    """Return True if the event appears to be within the Central
    Florida corridor.

    - If lat/lon are present, check the bounding box.
    - Else if a city is present, check it against the known city list.
    - Else (no location info at all) keep the event - it's better to
      surface an unfiltered record than silently drop it.
    """
    cfg = load_geo_config()
    if latitude is not None and longitude is not None:
        bb = cfg["bounding_box"]
        return bb["min_lat"] <= latitude <= bb["max_lat"] and bb["min_lon"] <= longitude <= bb["max_lon"]

    if city:
        return city.strip().lower() in _city_set()

    return True


def search_centers() -> list[dict]:
    return load_geo_config()["search_centers"]
