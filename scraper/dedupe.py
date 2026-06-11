"""Cross-source deduplication.

Different sources frequently list the same event (e.g. a show at the
Beacham shows up via Ticketmaster AND the venue's own site). We
collapse these into a single `events` row by computing a stable hash
of the normalized title + venue + start date, and upserting on that
hash (`events.dedup_hash` is UNIQUE).
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    value = value.lower()
    value = _NON_ALNUM.sub(" ", value)
    return re.sub(r"\s+", " ", value).strip()


def compute_dedup_hash(title: str, venue_name: str | None, start_datetime: datetime) -> str:
    norm_title = normalize_text(title)
    norm_venue = normalize_text(venue_name)
    date_part = start_datetime.date().isoformat()
    key = f"{norm_title}|{norm_venue}|{date_part}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()
