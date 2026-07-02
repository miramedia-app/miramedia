import logging
import re
import unicodedata

from miramedia.config import MiraMediaConfig
from miramedia.indexers.config import CodecOption, QualityOption, ScoringRuleSet
from miramedia.indexers.schemas import IndexerQueryResult
from miramedia.movies.schemas import Movie
from miramedia.shows.schemas import Show

log = logging.getLogger(__name__)


def evaluate_indexer_query_result(
    query_result: IndexerQueryResult, ruleset: ScoringRuleSet
) -> tuple[IndexerQueryResult, bool]:
    title_rules = MiraMediaConfig().indexers.title_scoring_rules
    indexer_flag_rules = MiraMediaConfig().indexers.indexer_flag_scoring_rules
    for rule_name in ruleset.rule_names:
        for rule in title_rules:
            if rule.name == rule_name:
                if not rule.enabled:
                    log.debug("Skipping disabled rule %s", rule.name)
                    continue
                log.debug("Applying rule %s to %s", rule.name, query_result.title)
                if any(
                    re.search(
                        rf"\b{re.escape(keyword)}\b",
                        query_result.title,
                        re.IGNORECASE,
                    )
                    for keyword in rule.keywords
                ):
                    log.debug(
                        "Rule %s with keywords %s matched for %s",
                        rule.name,
                        rule.keywords,
                        query_result.title,
                    )
                    query_result.score += rule.score_modifier
                else:
                    log.debug(
                        "Rule %s with keywords %s did not match for %s",
                        rule.name,
                        rule.keywords,
                        query_result.title,
                    )
        for rule in indexer_flag_rules:
            if rule.name == rule_name:
                if not rule.enabled:
                    log.debug("Skipping disabled rule %s", rule.name)
                    continue
                log.debug("Applying rule %s to %s", rule.name, query_result.title)
                if any(flag in query_result.flags for flag in rule.flags):
                    log.debug(
                        "Rule %s with flags %s matched for %s with flags %s",
                        rule.name,
                        rule.flags,
                        query_result.title,
                        query_result.flags,
                    )
                    query_result.score += rule.score_modifier
                else:
                    log.debug(
                        "Rule %s with flags %s did not match for %s with flags %s",
                        rule.name,
                        rule.flags,
                        query_result.title,
                        query_result.flags,
                    )
    if query_result.score <= 0:
        return query_result, False

    return query_result, True


def _match_option(
    title: str, options: list[QualityOption] | list[CodecOption]
) -> QualityOption | CodecOption | None:
    """Return the first enabled option whose keywords match `title`. None if
    no enabled option matches. List order resolves ambiguity when a title
    matches multiple options.

    Keyword matching uses word-boundary regex to avoid substring false-positives
    (e.g. "av1" must not match "Lavinia").
    """
    enabled = [opt for opt in options if opt.enabled]
    for opt in enabled:
        for kw in opt.keywords:
            if re.search(rf"\b{re.escape(kw)}\b", title, re.IGNORECASE):
                return opt
    return None


def _apply_quality_codec_scoring(
    result: IndexerQueryResult,
    quality_allowed: list[str] | None,
    codec_allowed: list[str] | None,
) -> bool:
    """Apply quality + codec scoring. Return False if the result should be dropped.

    Tri-state semantics for ``quality_allowed`` / ``codec_allowed``:
      - ``None``: no per-media override — keep every enabled-matching result
        and add the matched option's ``score_modifier`` (the implicit global
        default is the quality/codec rules themselves).
      - ``[]``: "Any" — keep every enabled-matching result, add no
        quality/codec score so other rules drive ranking.
      - non-empty list: whitelist — drop results whose matched option is not
        in the list. Matched option's ``score_modifier`` is added.

    Drop conditions:
      - no enabled quality / codec option matches the title at all,
      - whitelist set and matched option not in it.
    """
    config = MiraMediaConfig().indexers

    q_opt = _match_option(result.title, config.quality_options)
    if q_opt is None:
        log.debug("Drop %r: no enabled quality option matched", result.title)
        return False
    if quality_allowed and q_opt.name not in quality_allowed:
        log.debug(
            "Drop %r: matched quality %r not in allowed %r",
            result.title,
            q_opt.name,
            quality_allowed,
        )
        return False
    # None or non-empty list → add score_modifier. Empty list → neutralize.
    if quality_allowed is None or quality_allowed:
        result.score += q_opt.score_modifier

    c_opt = _match_option(result.title, config.codec_options)
    if c_opt is None:
        log.debug("Drop %r: no enabled codec option matched", result.title)
        return False
    if codec_allowed and c_opt.name not in codec_allowed:
        log.debug(
            "Drop %r: matched codec %r not in allowed %r",
            result.title,
            c_opt.name,
            codec_allowed,
        )
        return False
    if codec_allowed is None or codec_allowed:
        result.score += c_opt.score_modifier

    return True


def _apply_language_and_recency_scoring(result: IndexerQueryResult) -> None:
    config = MiraMediaConfig().indexers
    title_lower = result.title.lower()

    if config.rejected_languages and any(
        re.search(rf"\b{re.escape(lang)}\b", title_lower)
        for lang in config.rejected_languages
    ):
        result.score -= 10000

    if config.preferred_languages and any(
        re.search(rf"\b{re.escape(lang)}\b", title_lower)
        for lang in config.preferred_languages
    ):
        result.score += 100

    if config.recency_bonus > 0 and config.recency_decay_days > 0:
        if result.age <= config.recency_decay_days:
            bonus = int(
                config.recency_bonus * (1 - result.age / config.recency_decay_days)
            )
            result.score += max(0, bonus)


def preview_score(
    title: str,
    *,
    flags: list[str] | None = None,
    seeders: int = 1,
    age_days: int = 0,
) -> dict:
    """Walk every scoring rule against a synthetic title and report the breakdown.

    Returns ``{total, breakdown[]}`` where each breakdown entry has ``rule``,
    ``matched``, ``delta``, ``reason``. Used by the settings page's "Scoring Preview"
    panel so users can see exactly why a torrent ranked the way it did.
    """
    cfg = MiraMediaConfig().indexers
    flags = flags or []
    title_lower = title.lower()
    breakdown: list[dict] = []
    total = 0

    def _record(rule_name: str, matched: bool, delta: int, reason: str) -> None:
        nonlocal total
        if matched and delta:
            total += delta
        breakdown.append(
            {
                "rule": rule_name,
                "matched": matched,
                "delta": delta if matched else 0,
                "reason": reason,
            }
        )

    for rule in cfg.title_scoring_rules:
        if not rule.enabled:
            _record(f"title:{rule.name}", False, rule.score_modifier, "disabled")
            continue
        matched = any(
            re.search(rf"\b{re.escape(kw)}\b", title, re.IGNORECASE)
            for kw in rule.keywords
        )
        _record(
            f"title:{rule.name}",
            matched,
            rule.score_modifier,
            f"keywords={rule.keywords}",
        )

    for rule in cfg.indexer_flag_scoring_rules:
        if not rule.enabled:
            _record(f"flag:{rule.name}", False, rule.score_modifier, "disabled")
            continue
        matched = any(flag in flags for flag in rule.flags)
        _record(
            f"flag:{rule.name}",
            matched,
            rule.score_modifier,
            f"flags={rule.flags}",
        )

    # Quality options — only the first-by-list-order match contributes a score.
    q_enabled = [opt for opt in cfg.quality_options if opt.enabled]
    q_first_match: int | None = None
    for idx, opt in enumerate(q_enabled):
        matched = any(
            re.search(rf"\b{re.escape(kw)}\b", title, re.IGNORECASE)
            for kw in opt.keywords
        )
        if matched and q_first_match is None:
            q_first_match = idx
        _record(
            f"quality:{opt.name}",
            matched and q_first_match == idx,
            opt.score_modifier if (q_first_match == idx) else 0,
            f"keywords={opt.keywords} priority_index={idx} score_modifier={opt.score_modifier}",
        )
    for opt in cfg.quality_options:
        if not opt.enabled:
            _record(f"quality:{opt.name}", False, 0, "disabled")
    if q_first_match is None and q_enabled:
        _record(
            "quality:_none_matched",
            True,
            -10_000_000,
            "no enabled quality option matched title → result dropped",
        )

    c_enabled = [opt for opt in cfg.codec_options if opt.enabled]
    c_first_match: int | None = None
    for idx, opt in enumerate(c_enabled):
        matched = any(
            re.search(rf"\b{re.escape(kw)}\b", title, re.IGNORECASE)
            for kw in opt.keywords
        )
        if matched and c_first_match is None:
            c_first_match = idx
        _record(
            f"codec:{opt.name}",
            matched and c_first_match == idx,
            opt.score_modifier if (c_first_match == idx) else 0,
            f"keywords={opt.keywords} priority_index={idx} score_modifier={opt.score_modifier}",
        )
    for opt in cfg.codec_options:
        if not opt.enabled:
            _record(f"codec:{opt.name}", False, 0, "disabled")
    if c_first_match is None and c_enabled:
        _record(
            "codec:_none_matched",
            True,
            -10_000_000,
            "no enabled codec option matched title → result dropped",
        )

    if cfg.rejected_languages:
        hit_lang = next(
            (
                lang
                for lang in cfg.rejected_languages
                if re.search(rf"\b{re.escape(lang)}\b", title_lower)
            ),
            None,
        )
        _record(
            "language:rejected",
            hit_lang is not None,
            -10000,
            f"matched={hit_lang!r} from {cfg.rejected_languages}"
            if hit_lang
            else "no match",
        )

    if cfg.preferred_languages:
        hit_lang = next(
            (
                lang
                for lang in cfg.preferred_languages
                if re.search(rf"\b{re.escape(lang)}\b", title_lower)
            ),
            None,
        )
        _record(
            "language:preferred",
            hit_lang is not None,
            100,
            f"matched={hit_lang!r} from {cfg.preferred_languages}"
            if hit_lang
            else "no match",
        )

    if cfg.recency_bonus > 0 and cfg.recency_decay_days > 0:
        in_window = age_days <= cfg.recency_decay_days
        bonus = (
            max(0, int(cfg.recency_bonus * (1 - age_days / cfg.recency_decay_days)))
            if in_window
            else 0
        )
        _record(
            "recency",
            in_window and bonus > 0,
            bonus,
            f"age_days={age_days} decay={cfg.recency_decay_days} bonus_max={cfg.recency_bonus}",
        )

    rejected = bool(cfg.minimum_seeders) and seeders < cfg.minimum_seeders
    _record(
        "minimum_seeders",
        rejected,
        -10_000_000 if rejected else 0,
        f"seeders={seeders} minimum={cfg.minimum_seeders}",
    )

    rejected_max = bool(cfg.maximum_seeders) and seeders > cfg.maximum_seeders
    _record(
        "maximum_seeders",
        rejected_max,
        -10_000_000 if rejected_max else 0,
        f"seeders={seeders} maximum={cfg.maximum_seeders}",
    )

    return {"total": total, "breakdown": breakdown}


_RELEASE_MARKERS = re.compile(
    r"^(?:"
    r"\d{4}"
    r"|s\d{1,3}(?:e\d{1,4})?"
    r"|e\d{1,4}"
    r"|\d{3,4}p"
    r"|x26[45]|h26[45]|hevc|av1"
    r"|web|webrip|webdl"
    r"|bluray|brrip|bdrip|hdtv|hdrip|dvdrip"
    r"|repack|proper|extended|remastered|complete|uncut|multi"
    r"|part\d+"
    r")$"
)


def _is_title_relevant(title: str, media_name: str) -> bool:
    """Reject torrent titles whose leading segment doesn't equal the media name.

    A loose substring check accepted things like ``Masha and the Bear`` when
    asked for ``The Bear``, because every word from the query appeared
    somewhere in the title. Stricter rule: after stripping any leading
    [group] prefix and normalising separators, the title must START with the
    media name, immediately followed by a release marker (year, SxxEyy,
    resolution, codec, source, …) or the end of the string.

    Uses the same normalisation as the query sanitizer so a torrent fetched
    with ``Greys Anatomy`` is not rejected against the raw name ``Grey's
    Anatomy``.
    """
    norm_title = sanitize_search_query(title).lower()
    norm_name = sanitize_search_query(media_name).lower()

    if not norm_name:
        return False
    if norm_title == norm_name:
        return True
    if not norm_title.startswith(norm_name + " "):
        return False

    next_token = norm_title[len(norm_name) + 1 :].split(" ", 1)[0]
    return bool(_RELEASE_MARKERS.match(next_token))


_YEAR_TOKEN = re.compile(r"\b(19\d{2}|20\d{2})\b")


def _extract_years(title: str) -> set[int]:
    """All 4-digit 19xx/20xx tokens in a release title."""
    return {int(m) for m in _YEAR_TOKEN.findall(title)}


_TV_MARKER = re.compile(
    r"(?:\bs\d{1,3}(?:e\d{1,4})+\b"  # S05E12, S01E01E02
    r"|\bs\d{1,3}[ ._-]?-[ ._-]?s\d{1,3}\b"  # S01-S03 season range
    r"|\b\d{1,2}x\d{2,3}\b"  # 5x12
    r"|\bseason[ ._-]?\d{1,3}\b"  # Season 5
    r"|\bcomplete[ ._-]?series\b)",
    re.IGNORECASE,
)


def _looks_like_episode(title: str) -> bool:
    """True when a release title carries TV season/episode markers.

    Used to keep an episode/season pack (``Supergirl S05E12 ...``) from being
    grabbed for a movie of the same name.
    """
    return bool(_TV_MARKER.search(title))


def _is_year_relevant(title: str, media_year: int | None) -> bool:
    """Reject a release whose title carries a year that excludes ``media_year``.

    Tolerant by design: a title with no year is kept (many legit releases omit
    it), and a title that mentions several years (e.g. ``Blade Runner 2049
    (2017)``) is kept as long as ``media_year`` is among them. Only a title that
    states one or more years, none of which is the media's year, is rejected —
    that is the ``Supergirl 2026`` vs ``Supergirl 1984`` case.
    """
    if not media_year:
        return True
    years = _extract_years(title)
    if not years:
        return True
    return media_year in years


def evaluate_indexer_query_results(
    query_results: list[IndexerQueryResult],
    media: Show | Movie,
    is_tv: bool,
    quality_allowed: list[str] | None = None,
    codec_allowed: list[str] | None = None,
) -> list[IndexerQueryResult]:
    indexer_config = MiraMediaConfig().indexers
    scoring_rulesets: list[ScoringRuleSet] = indexer_config.scoring_rule_sets

    # Filter out torrents below minimum seeders threshold
    if indexer_config.minimum_seeders > 0:
        before_seed = len(query_results)
        query_results = [
            r
            for r in query_results
            if r.usenet or r.seeders >= indexer_config.minimum_seeders
        ]
        seed_filtered = before_seed - len(query_results)
        if seed_filtered:
            log.info(
                f"Filtered {seed_filtered}/{before_seed} results below {indexer_config.minimum_seeders} seeders"
            )

    # Filter out torrents above maximum seeders threshold
    if indexer_config.maximum_seeders > 0:
        before_max = len(query_results)
        query_results = [
            r
            for r in query_results
            if r.usenet or r.seeders <= indexer_config.maximum_seeders
        ]
        max_filtered = before_max - len(query_results)
        if max_filtered:
            log.info(
                f"Filtered {max_filtered}/{before_max} results above {indexer_config.maximum_seeders} seeders"
            )

    # Hard size gate (bytes -> MB)
    min_mb = indexer_config.min_size_mb
    max_mb = indexer_config.max_size_mb
    if min_mb > 0 or max_mb > 0:
        before_size = len(query_results)
        kept: list[IndexerQueryResult] = []
        for r in query_results:
            size_mb = r.size / (1024 * 1024) if r.size else 0
            if min_mb > 0 and size_mb < min_mb:
                continue
            if max_mb > 0 and size_mb > max_mb:
                continue
            kept.append(r)
        size_filtered = before_size - len(kept)
        if size_filtered:
            log.info(
                f"Filtered {size_filtered}/{before_size} results outside "
                f"[{min_mb},{max_mb}] MB"
            )
        query_results = kept

    # Filter out results that don't contain the media name at all
    before_count = len(query_results)
    query_results = [
        r for r in query_results if _is_title_relevant(r.title, media.name)
    ]
    filtered = before_count - len(query_results)
    if filtered:
        log.info(
            f"Filtered {filtered}/{before_count} results not matching '{media.name}'"
        )

    # TV-marker gate (movies only): a release tagged with SxxEyy / season packs
    # (e.g. "Supergirl S05E12 ...") is a TV episode and must never be grabbed for
    # a same-named movie.
    if not is_tv:
        before_tv = len(query_results)
        query_results = [r for r in query_results if not _looks_like_episode(r.title)]
        tv_filtered = before_tv - len(query_results)
        if tv_filtered:
            log.info(
                f"Filtered {tv_filtered}/{before_tv} TV-episode results for "
                f"movie '{media.name}'"
            )

    # Year gate (movies only): drop releases whose title states a wrong year so a
    # remake/older film (e.g. "Supergirl 1984") can't be picked for a different
    # year's movie ("Supergirl 2026"). TV release titles often carry per-episode
    # air years, so the gate is not applied to shows.
    if not is_tv and media.year:
        before_year = len(query_results)
        query_results = [
            r for r in query_results if _is_year_relevant(r.title, media.year)
        ]
        year_filtered = before_year - len(query_results)
        if year_filtered:
            log.info(
                f"Filtered {year_filtered}/{before_year} results not matching "
                f"year {media.year} for '{media.name}'"
            )

    for ruleset in scoring_rulesets:
        if (
            (media.library in ruleset.libraries)
            or ("ALL_TV" in ruleset.libraries and is_tv)
            or ("ALL_MOVIES" in ruleset.libraries and not is_tv)
        ):
            log.debug(
                f"Applying scoring ruleset {ruleset.name} for {media.name} ({media.year}) to {len(query_results)} results."
            )
            for result in query_results:
                log.debug(
                    f"Applying scoring ruleset {ruleset.name} for IndexerQueryResult {result.title} for {media.name} ({media.year})"
                )
                result, passed = evaluate_indexer_query_result(
                    query_result=result, ruleset=ruleset
                )
                if not passed:
                    log.debug(
                        f"Indexer query result {result.title} did not pass scoring ruleset {ruleset.name} with score {result.score}, removing from results."
                    )
                else:
                    log.debug(
                        f"Indexer query result {result.title} passed scoring ruleset {ruleset.name} with score {result.score}."
                    )

    q_allowed = list(quality_allowed) if quality_allowed is not None else None
    c_allowed = list(codec_allowed) if codec_allowed is not None else None
    kept_after_options: list[IndexerQueryResult] = []
    for result in query_results:
        if _apply_quality_codec_scoring(result, q_allowed, c_allowed):
            _apply_language_and_recency_scoring(result)
            kept_after_options.append(result)
    query_results = kept_after_options

    query_results = [result for result in query_results if result.score >= 0]
    query_results.sort(reverse=True)
    log.info(f"{len(query_results)} passed the scoring rulesets")
    return query_results


def sanitize_search_query(title: str) -> str:
    """
    Sanitize a title for use as an indexer search query.

    Scene/P2P release names elide diacritics, apostrophes, and most
    punctuation (``Grey's Anatomy`` → ``Greys.Anatomy``, ``Pokémon`` →
    ``Pokemon``), so any of those in the query causes trackers to return
    far fewer (or zero) results. We:

    * Strip bracketed/curly content and ``(YYYY)`` year suffixes.
    * Drop diacritics (``é`` → ``e``) via NFKD normalisation.
    * Drop contraction/possessive marks entirely (``Grey's`` → ``Greys``).
    * Expand ``&`` to ``and``.
    * Replace remaining punctuation with spaces and collapse whitespace.

    :param title: The original title.
    :return: A sanitized version of the title suitable for tracker search.
    """

    # Remove content within brackets
    sanitized = re.sub(r"\[.*?\]", "", title)

    # Remove content within curly brackets
    sanitized = re.sub(r"\{.*?\}", "", sanitized)

    # Remove year within parentheses
    sanitized = re.sub(r"\(\d{4}\)", "", sanitized)

    # Fold diacritics: "Pokémon" -> "Pokemon", "Pásha" -> "Pasha".
    # NFKD splits accented letters into base + combining mark; we then
    # drop the combining marks. Non-decomposable scripts (CJK, Cyrillic)
    # are left untouched.
    sanitized = "".join(
        c
        for c in unicodedata.normalize("NFKD", sanitized)
        if not unicodedata.combining(c)
    )

    # Drop contractions/possessives entirely so "Grey's" -> "Greys"
    sanitized = re.sub(r"[‘’'`]", "", sanitized)  # noqa: RUF001 — intentional curly-quote chars

    # Replace ampersand with " and " (matches common release naming)
    sanitized = sanitized.replace("&", " and ")

    # Replace remaining punctuation/separators with a space (unicode-aware:
    # keeps letters from non-Latin scripts intact)
    sanitized = re.sub(r"[^\w\s]+", " ", sanitized, flags=re.UNICODE)

    # Collapse multiple whitespace characters and trim the result
    return re.sub(r"\s+", " ", sanitized).strip()
