from datetime import datetime
from zoneinfo import ZoneInfo

from scraper.categories import classify_text
from scraper.dedupe import compute_dedup_hash, normalize_text
from scraper.geo import in_corridor


def test_normalize_text_strips_punctuation_and_case():
    assert normalize_text("The Beacham!") == "the beacham"
    assert normalize_text("  Will's   Pub ") == "will s pub"
    assert normalize_text(None) == ""


def test_dedup_hash_is_stable_and_sensitive_to_inputs():
    dt = datetime(2026, 7, 4, 20, 0, tzinfo=ZoneInfo("America/New_York"))
    h1 = compute_dedup_hash("Some Band Live", "The Beacham", dt)
    h2 = compute_dedup_hash("some band live", "the beacham", dt)
    assert h1 == h2  # case/punctuation differences collapse

    h3 = compute_dedup_hash("Some Band Live", "The Social", dt)
    assert h1 != h3  # different venue -> different hash


def test_classify_text_edm():
    tags, hint = classify_text("Saturday Night Rave at Conduit", None)
    assert "edm" in tags
    assert hint == "nightlife_edm"


def test_classify_text_kids_friendly():
    tags, hint = classify_text("Family Storytime at the Library", None)
    assert "kids_friendly" in tags
    assert hint == "family"


def test_classify_text_no_match():
    tags, hint = classify_text("A Generic Event Title", None)
    assert tags == set()
    assert hint is None


def test_in_corridor_bounding_box():
    # Downtown Orlando
    assert in_corridor(28.5384, -81.3789, None) is True
    # Daytona Beach
    assert in_corridor(29.2108, -81.0228, None) is True
    # Tampa - well outside the corridor
    assert in_corridor(27.9506, -82.4572, None) is False


def test_in_corridor_city_fallback():
    assert in_corridor(None, None, "Kissimmee") is True
    assert in_corridor(None, None, "Tampa") is False


def test_in_corridor_unknown_location_kept():
    assert in_corridor(None, None, None) is True
