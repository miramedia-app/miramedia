"""Fuzzy title matching for the import flow.

Used by the library scanner and the manual-parse flow to rank a scanned
directory / release title against the shows and movies already in the library
(and against metadata-provider hits) on a single confidence scale.
"""

import re


def _normalize_title_for_matching(title: str) -> str:
    """Strip quality tags, season markers, and special chars for matching."""
    cleaned = re.sub(
        r"\b(720p|1080p|2160p|4k|uhd|hdr|bluray|blu-ray|web-?dl|web-?rip|hdtv|dvdrip|brrip|x264|x265|h\.?264|h\.?265|hevc|aac|ac3|dts|atmos|10bit|remux)\b",
        "",
        title,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"[sS]\d{1,2}[eE]\d{1,3}", "", cleaned)
    cleaned = re.sub(r"[sS]\d{1,2}", "", cleaned)
    cleaned = re.sub(r"\[.*?\]", "", cleaned)
    cleaned = re.sub(r"\(.*?\)", "", cleaned)
    cleaned = re.sub(r"[._\-]", " ", cleaned)
    cleaned = re.sub(r"[^a-zA-Z0-9\s]", "", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def find_candidate_media_matches(
    title: str,
    shows: list,
    movies: list,
    max_results: int = 10,
) -> list[dict]:
    """
    Fuzzy-match a torrent title against all shows and movies in the DB.
    Returns ranked candidates as dicts with media_type, media_id, media_name,
    media_year, confidence, and a ``breakdown`` describing how the score was
    derived (overlapping words, year boost, raw word counts) so the UI can
    show a "why this match?" tooltip.
    """
    normalized = _normalize_title_for_matching(title)
    normalized_words = set(normalized.lower().split())

    # Extract a year hint from the raw title (e.g. "Foo.2022" → 2022) so we
    # can penalize candidates whose year disagrees. Without this, the prior
    # symmetric scorer treated "Bend It Like Beckham 20 Years On 2022" as a
    # perfect match for the 2002 original.
    title_year_match = re.search(r"\b(19|20)\d{2}\b", title)
    title_year: int | None = int(title_year_match.group()) if title_year_match else None

    candidates = []

    def _score_one(media_type: str, media) -> dict | None:  # noqa: ANN001
        media_normalized = _normalize_title_for_matching(media.name)
        media_words = set(media_normalized.lower().split())
        if not media_words:
            return None
        overlap = normalized_words & media_words
        union = normalized_words | media_words
        # Jaccard similarity — symmetric in title vs media so a short library
        # name doesn't get a free 100% when it's a substring of a longer
        # release title with extra qualifiers.
        base_score = len(overlap) / len(union) if union else 0.0

        # Year handling: matching year is a small boost, an explicit mismatch
        # is a meaningful penalty. Missing-year on either side stays neutral.
        if media.year and title_year:
            year_boost = 0.2 if media.year == title_year else -0.4
        elif media.year and str(media.year) in title:
            year_boost = 0.2
        else:
            year_boost = 0.0

        score = base_score + year_boost
        if score <= 0.3:
            return None
        return {
            "media_type": media_type,
            "media_id": media.id,
            "media_name": media.name,
            "media_year": media.year,
            "confidence": max(0.0, min(score, 1.0)),
            "breakdown": {
                "overlap_words": sorted(overlap),
                "media_word_count": len(media_words),
                "title_word_count": len(normalized_words),
                "base_score": round(base_score, 3),
                "year_boost": year_boost,
            },
        }

    for show in shows:
        result = _score_one("show", show)
        if result is not None:
            candidates.append(result)

    for movie in movies:
        result = _score_one("movie", movie)
        if result is not None:
            candidates.append(result)

    candidates.sort(key=lambda c: c["confidence"], reverse=True)
    return candidates[:max_results]


def score_title_match_with_breakdown(
    query: str,
    query_year: int | None,
    candidate_name: str,
    candidate_year: int | None,
) -> tuple[float, dict]:
    """Confidence (0..1) that ``candidate_name``/year is the same title as the
    scanned dir's ``query``/year, plus a breakdown dict shaped like
    ``MatchBreakdown`` for surfacing in the UI tooltip. Mirrors
    ``find_candidate_media_matches``' scoring so provider hits rank on the same
    scale as existing-library hits.
    """
    q_words = set(_normalize_title_for_matching(query).lower().split())
    c_words = set(_normalize_title_for_matching(candidate_name).lower().split())
    if not c_words:
        return 0.0, {
            "overlap_words": [],
            "media_word_count": 0,
            "title_word_count": len(q_words),
            "base_score": 0.0,
            "year_boost": 0.0,
        }
    overlap = sorted(q_words & c_words)
    union = q_words | c_words
    base = (len(overlap) / len(union)) if union else 0.0
    if query_year and candidate_year:
        year_boost = 0.2 if query_year == candidate_year else -0.4
    else:
        year_boost = 0.0
    return max(0.0, min(base + year_boost, 1.0)), {
        "overlap_words": overlap,
        "media_word_count": len(c_words),
        "title_word_count": len(q_words),
        "base_score": base,
        "year_boost": year_boost,
    }
