import logging
from typing import Any, Self

from pydantic import model_validator
from pydantic_settings import BaseSettings

log = logging.getLogger(__name__)

# Name shared by the default junk-rejecting Title Rule and the Rule Set that
# applies it — referenced in both default lists below.
_REJECT_JUNK_RULE = "Reject cam/workprint"


class ProwlarrConfig(BaseSettings):
    enabled: bool = False
    api_key: str = ""
    url: str = "http://localhost:9696"


class JackettConfig(BaseSettings):
    enabled: bool = False
    api_key: str = ""
    url: str = "http://localhost:9696"
    indexers: list[str] = ["all"]


class ScoringRule(BaseSettings):
    name: str
    score_modifier: int = 0
    enabled: bool = True


class TitleScoringRule(ScoringRule):
    keywords: list[str]


class IndexerFlagScoringRule(ScoringRule):
    flags: list[str]


class QualityOption(BaseSettings):
    """Allowed quality option.

    List order in `IndexerConfig.quality_options` controls:
      - display order in per-show/movie dropdowns,
      - tie-break when a title matches multiple options (first wins).

    `score_modifier` is added to a matched result's score — it drives result
    ranking, independent of list order.

    `enabled=False` removes the option from dropdowns AND drops any indexer
    result that only matches this option's keywords.
    """

    name: str
    keywords: list[str]
    score_modifier: int = 0
    enabled: bool = True


class CodecOption(BaseSettings):
    name: str
    keywords: list[str]
    score_modifier: int = 0
    enabled: bool = True


class ScoringRuleSet(BaseSettings):
    name: str
    libraries: list[str] = []
    rule_names: list[str] = []


class TorznabSiteConfig(BaseSettings):
    name: str = ""
    url: str = ""
    api_key: str = ""
    supports_tv: bool = True
    supports_movies: bool = True
    categories_tv: str = "5000"
    categories_movies: str = "2000"
    cloudflare_protected: bool = False


class NativeIndexerConfig(BaseSettings):
    enabled: bool = False
    max_concurrent_searches: int = 5
    custom_torznab_sites: list[TorznabSiteConfig] = []
    disabled_sites: list[str] = []  # names of preloaded sites to skip


class IndexerConfig(BaseSettings):
    timeout_seconds: int = 60  # shared HTTP timeout for all indexer providers
    prowlarr: ProwlarrConfig = ProwlarrConfig()
    jackett: JackettConfig = JackettConfig()
    native: NativeIndexerConfig = NativeIndexerConfig()
    # Ordered list of allowed qualities. First entry = highest priority.
    # Disabled entries are excluded from matching AND from per-show dropdowns.
    quality_options: list[QualityOption] = [
        QualityOption(
            name="4K (UHD)", keywords=["2160p", "4k", "uhd"], score_modifier=400
        ),
        QualityOption(
            name="1080p (Full HD)", keywords=["1080p", "1080i"], score_modifier=300
        ),
        QualityOption(name="720p (HD)", keywords=["720p"], score_modifier=200),
        QualityOption(
            name="SD", keywords=["480p", "576p", "sdtv", "dvdrip"], score_modifier=100
        ),
    ]
    codec_options: list[CodecOption] = [
        CodecOption(
            name="H.265 (HEVC)",
            keywords=["h265", "hevc", "x265", "h.265", "x.265"],
            score_modifier=300,
        ),
        CodecOption(
            name="H.264 (AVC)",
            keywords=["h264", "avc", "x264", "h.264", "x.264"],
            score_modifier=200,
        ),
        CodecOption(name="AV1", keywords=["av1"], score_modifier=100),
    ]
    # Default Title Rule + Rule Set that drop pre-retail junk (cam / telesync /
    # telecine / screener / workprint). Shipped as ordinary scoring config so
    # it's visible + editable on the Scores settings page like any other rule —
    # not a separate hardcoded gate. The -10000 modifier pushes a matching
    # result below the `score >= 0` floor in evaluate_indexer_query_results.
    # Bare "cam"/"ts" are omitted (the film *Cam*, ".ts" containers); word-
    # boundary matched, so "camrip"/"hdcam" are safe.
    title_scoring_rules: list[TitleScoringRule] = [
        TitleScoringRule(
            name=_REJECT_JUNK_RULE,
            keywords=[
                "workprint",
                "telesync",
                "telecine",
                "hdcam",
                "camrip",
                "hqcam",
                "screener",
                "dvdscr",
                "bdscr",
                "pdvd",
            ],
            score_modifier=-10000,
        ),
    ]
    indexer_flag_scoring_rules: list[IndexerFlagScoringRule] = []
    scoring_rule_sets: list[ScoringRuleSet] = [
        ScoringRuleSet(
            name="Reject low-quality sources",
            libraries=["ALL_TV", "ALL_MOVIES"],
            rule_names=[_REJECT_JUNK_RULE],
        ),
    ]
    minimum_seeders: int = 0
    maximum_seeders: int = 0  # 0 = no maximum
    # Hard size gate applied before scoring. 0 = no limit on that side.
    min_size_mb: int = 0
    max_size_mb: int = 0
    preferred_languages: list[str] = []
    rejected_languages: list[str] = []
    recency_bonus: int = 0  # max score bonus for recent results (0 = disabled)
    recency_decay_days: int = 30  # days over which the recency bonus decays to 0

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_scoring_rules(cls, data: Any) -> Any:  # noqa: ANN401 — pydantic mode="before" validator receives/returns arbitrary raw input
        """Map old `quality_scoring_rules` / `codec_scoring_rules` shapes onto
        the new `quality_options` / `codec_options` model. Preserves
        `score_modifier` from the legacy shape.
        """
        if not isinstance(data, dict):
            return data
        legacy_quality = data.pop("quality_scoring_rules", None)
        if legacy_quality and "quality_options" not in data:
            data["quality_options"] = [
                {
                    "name": r.get("name", ""),
                    "keywords": r.get("keywords", []),
                    "score_modifier": int(r.get("score_modifier", 0) or 0),
                    "enabled": r.get("enabled", True),
                }
                for r in legacy_quality
                if r.get("name")
            ]
        legacy_codec = data.pop("codec_scoring_rules", None)
        if legacy_codec and "codec_options" not in data:
            data["codec_options"] = [
                {
                    "name": r.get("name", ""),
                    "keywords": r.get("keywords", []),
                    "score_modifier": int(r.get("score_modifier", 0) or 0),
                    "enabled": r.get("enabled", True),
                }
                for r in legacy_codec
                if r.get("name")
            ]
        return data

    @model_validator(mode="after")
    def require_at_least_one_enabled(self) -> Self:
        if not any(opt.enabled for opt in self.quality_options):
            msg = "At least one quality option must be enabled in indexers.quality_options"
            raise ValueError(msg)
        if not any(opt.enabled for opt in self.codec_options):
            msg = "At least one codec option must be enabled in indexers.codec_options"
            raise ValueError(msg)
        return self
