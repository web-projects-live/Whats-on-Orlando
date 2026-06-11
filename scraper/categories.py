"""Category & tag taxonomy shared by every scraper.

Standardized top-level categories (events.category):
    music, theater, comedy, family, festival, arts_culture, sports,
    nightlife_edm, community, food_drink, other

Tags (events.tags) are free-form text, but scrapers should prefer the
vocabulary below so the data stays consistent and queryable.
"""

from __future__ import annotations

CATEGORIES = {
    "music",
    "theater",
    "comedy",
    "family",
    "festival",
    "arts_culture",
    "sports",
    "nightlife_edm",
    "gaming",
    "community",
    "food_drink",
    "other",
}

# tag -> keywords/phrases matched case-insensitively against free text
# (title, description, source genre/category labels).
TAG_KEYWORDS: dict[str, list[str]] = {
    "edm": [
        "edm", "electronic dance", "rave", "bass music", "dubstep",
        "house music", "techno", "trance", "drum and bass", "dnb",
    ],
    "rock": ["rock"],
    "metal": ["metal", "hardcore"],
    "punk": ["punk"],
    "reggae": ["reggae", "dancehall"],
    "ska": ["ska"],
    "hip_hop": ["hip hop", "hip-hop", "rap"],
    "country": ["country"],
    "jazz": ["jazz"],
    "blues": ["blues"],
    "classical": ["classical", "symphony", "orchestra", "philharmonic"],
    "latin": ["latin", "reggaeton", "salsa", "bachata", "merengue"],
    "pop": ["pop"],
    "drag": ["drag show", "drag brunch", "drag queen", "drag bingo"],
    "comedy": ["comedy", "stand-up", "stand up", "improv"],
    "renaissance_faire": ["renaissance festival", "renaissance faire", "ren faire"],
    "comic_con": ["comic con", "comic-con", "anime convention", "fan expo", "fandom"],
    "film": ["film festival", "movie night", "screening"],
    "art_exhibit": ["exhibit", "exhibition", "art walk", "gallery"],
    "kids_friendly": [
        "kids", "children", "family-friendly", "family friendly",
        "storytime", "story time", "all ages",
    ],
    "free": ["free admission", "free event", "no cover", "free entry"],
    "outdoor": ["outdoor", "festival grounds", "in the park"],
    "21_plus": ["21+", "21 and up", "21 & up"],
    "holiday": ["holiday", "christmas", "halloween", "thanksgiving", "new year"],
    "burlesque": ["burlesque"],
    "trivia": ["trivia night", "trivia"],
    "karaoke": ["karaoke"],
    "esports": ["esports", "e-sports", "gaming tournament", "video game tournament"],
    "melee": ["super smash bros. melee", "smash bros melee", "smash melee", "ssbm"],
    "smash_ultimate": ["super smash bros. ultimate", "smash bros ultimate", "smash ultimate", "ssbu"],
    "pokemon_go": ["pokemon go", "pokémon go", "pokemon go community day", "raid hour", "pokemon go raid"],
    "tournament": ["tournament", "bracket"],
}

# tag -> top-level category it implies, used as a fallback when a
# scraper has no other category signal.
TAG_TO_CATEGORY: dict[str, str] = {
    "edm": "nightlife_edm",
    "rock": "music",
    "metal": "music",
    "punk": "music",
    "reggae": "music",
    "ska": "music",
    "hip_hop": "music",
    "country": "music",
    "jazz": "music",
    "blues": "music",
    "classical": "music",
    "latin": "music",
    "pop": "music",
    "comedy": "comedy",
    "renaissance_faire": "festival",
    "comic_con": "festival",
    "film": "arts_culture",
    "art_exhibit": "arts_culture",
    "kids_friendly": "family",
    "esports": "gaming",
    "melee": "gaming",
    "smash_ultimate": "gaming",
    "pokemon_go": "gaming",
}

# Order matters: earlier tags win when picking a single category_hint.
_HINT_PRIORITY = [
    "edm", "comedy", "pokemon_go", "melee", "smash_ultimate", "esports",
    "renaissance_faire", "comic_con", "kids_friendly",
    "art_exhibit", "film", "rock", "metal", "punk", "reggae", "ska",
    "hip_hop", "country", "jazz", "blues", "classical", "latin", "pop",
]


def classify_text(*texts: str | None) -> tuple[set[str], str | None]:
    """Scan the given text fields for known tag keywords.

    Returns `(tags, category_hint)` where `category_hint` is the
    top-level category implied by the highest-priority matched tag,
    or `None` if nothing matched.
    """
    haystack = " ".join(t for t in texts if t).lower()
    tags = {tag for tag, keywords in TAG_KEYWORDS.items() if any(kw in haystack for kw in keywords)}

    category_hint = None
    for tag in _HINT_PRIORITY:
        if tag in tags and tag in TAG_TO_CATEGORY:
            category_hint = TAG_TO_CATEGORY[tag]
            break

    return tags, category_hint
