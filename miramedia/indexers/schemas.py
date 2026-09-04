import re
import typing
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

import pydantic
from pydantic import BaseModel, ConfigDict, computed_field

from miramedia.settings.validation import SECRET_MASK
from miramedia.torrents.models import Quality

IndexerQueryResultId = typing.NewType("IndexerQueryResultId", UUID)
IndexerSiteId = typing.NewType("IndexerSiteId", UUID)


class IndexerQueryResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: IndexerQueryResultId = pydantic.Field(
        default_factory=lambda: IndexerQueryResultId(uuid4())
    )
    title: str
    download_url: str = pydantic.Field(
        exclude=True,
        description="This can be a magnet link or URL to the .torrent file",
    )
    seeders: int | None = None
    flags: list[str]
    size: int

    usenet: bool
    age: int

    score: int = 0

    indexer: str | None

    @computed_field
    @property
    def quality(self) -> Quality:
        # Explicit resolution tokens (e.g. "1080p") are authoritative and win
        # over loose descriptive tokens like "UHD"/"4K"/"HD" that often appear
        # in show names or edition labels alongside a different actual
        # resolution (e.g. a "UHD Edition" upscale released at 1080p).
        explicit_uhd = r"\b2160p?\b"
        explicit_fullhd = r"\b(1080p?|1440p?)\b"
        explicit_hd = r"\b720p?\b"
        explicit_sd = r"\b(480p?|360p?|240p?|576p?)\b"

        if re.search(explicit_uhd, self.title, re.IGNORECASE):
            return Quality.uhd
        if re.search(explicit_fullhd, self.title, re.IGNORECASE):
            return Quality.fullhd
        if re.search(explicit_hd, self.title, re.IGNORECASE):
            return Quality.hd
        if re.search(explicit_sd, self.title, re.IGNORECASE):
            return Quality.sd

        # Fall back to descriptive tokens only when no explicit resolution
        # is present in the title.
        descriptive_uhd = r"\b(4k|uhd)\b"
        descriptive_fullhd = r"\b(full[ ._-]?hd|fhd)\b"
        descriptive_hd = r"\bhd(?:tv|rip)?\b"
        descriptive_sd = r"\b(sd(?:tv)?|dvd(?:rip|scr)?|cam(?:rip)?)\b"

        if re.search(descriptive_uhd, self.title, re.IGNORECASE):
            return Quality.uhd
        if re.search(descriptive_fullhd, self.title, re.IGNORECASE):
            return Quality.fullhd
        if re.search(descriptive_hd, self.title, re.IGNORECASE):
            return Quality.hd
        if re.search(descriptive_sd, self.title, re.IGNORECASE):
            return Quality.sd

        return Quality.unknown

    @computed_field
    @property
    def season(self) -> list[int]:
        title = self.title.lower()

        # 1) S01E01 / S1E2
        m = re.search(r"s(\d{1,2})e\d{1,3}", title)
        if m:
            return [int(m.group(1))]

        # 1b) NxNN form (1x05, 12x07) — common on Nyaa/scene releases. Without
        # this both season and episode return [] and the episode is dropped.
        m = re.search(r"\b(\d{1,2})x\d{1,3}\b", title)
        if m:
            return [int(m.group(1))]

        # 2) Range S01-S03 / S1-S3
        m = re.search(r"s(\d{1,2})\s*(?:-|\u2013)\s*s?(\d{1,2})", title)
        if m:
            start, end = int(m.group(1)), int(m.group(2))
            if start <= end:
                return list(range(start, end + 1))
            return []

        # 3) Pack S01 / S1
        m = re.search(r"\bs(\d{1,2})\b", title)
        if m:
            return [int(m.group(1))]

        # 4) Season 01 / Season 1
        m = re.search(r"\bseason\s*(\d{1,2})\b", title)
        if m:
            return [int(m.group(1))]

        return []

    @computed_field(return_type=list[int])
    @property
    def episode(self) -> list[int]:
        title = self.title.lower()

        # 1) S##E## directly adjacent (most common: S05E07, S05E05-E07, S05E05-S05E07)
        match = re.search(
            r"s\d{1,2}e(\d{1,3})(?:\s*-\s*(?:(?:s\d{1,2})?e)?(\d{1,3}))?", title
        )

        # 1b) NxNN form (1x05, 1x05-1x07) — matches the season NxNN pattern.
        if not match:
            match = re.search(
                r"\b\d{1,2}x(\d{1,3})(?:\s*-\s*(?:\d{1,2}x)?(\d{1,3}))?", title
            )

        # 2) Standalone E## with gap after S## (e.g., S05.Vol.2.E05-E07)
        if not match and self.season:
            match = re.search(r"\be(\d{1,3})(?:\s*-\s*e?(\d{1,3}))?", title)

        if not match:
            return []

        start = int(match.group(1))
        end = match.group(2)

        if end:
            end_int = int(end)
            if end_int >= start:
                return list(range(start, end_int + 1))
            return []

        return [start]

    def __gt__(self, other: "IndexerQueryResult") -> bool:
        if self.quality.value != other.quality.value:
            return self.quality.value < other.quality.value
        if self.score != other.score:
            return self.score > other.score
        if self.usenet != other.usenet:
            return self.usenet
        if self.usenet and other.usenet:
            return self.age > other.age
        if not self.usenet and not other.usenet:
            return (self.seeders or 0) > (other.seeders or 0)

        return self.size < other.size

    def __lt__(self, other: "IndexerQueryResult") -> bool:
        if self.quality.value != other.quality.value:
            return self.quality.value > other.quality.value
        if self.score != other.score:
            return self.score < other.score
        if self.usenet != other.usenet:
            return not self.usenet
        if self.usenet and other.usenet:
            return self.age < other.age
        if not self.usenet and not other.usenet:
            return (self.seeders or 0) < (other.seeders or 0)

        return self.size > other.size


class MirrorEntry(BaseModel):
    """One failover mirror for a native indexer site.

    ``source`` distinguishes code-shipped mirrors (``seeded`` — reorderable and
    toggleable but never deletable) from ones the user added (``user`` —
    fully deletable). ``enabled`` mirrors, in list order, drive the live search.
    """

    url: str
    enabled: bool = True
    source: Literal["seeded", "user"] = "user"


class IndexerSiteCreate(BaseModel):
    name: str
    site_type: str = "torznab"
    url: str
    available_urls: list[str] = []
    api_key: str = ""
    supports_tv: bool = True
    supports_movies: bool = True
    categories_tv: str = "5000"
    categories_movies: str = "2000"
    cloudflare_protected: bool = False
    enabled: bool = True
    priority: int = 100


class IndexerSiteUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    available_urls: list[str] | None = None
    # Authoritative mirror list (order + enabled + source). When present, the
    # backend reconciles it against the stored mirrors, enforcing that seeded
    # mirrors cannot be deleted or reclassified. ``available_urls`` is kept only
    # for backward compatibility and is ignored when ``mirrors`` is supplied.
    mirrors: list[MirrorEntry] | None = None
    api_key: str | None = None
    supports_tv: bool | None = None
    supports_movies: bool | None = None
    categories_tv: str | None = None
    categories_movies: str | None = None
    cloudflare_protected: bool | None = None
    enabled: bool | None = None
    priority: int | None = None


class IndexerSiteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: IndexerSiteId
    name: str
    site_type: str
    url: str
    available_urls: list[str] = []
    mirrors: list[MirrorEntry] = []
    api_key: str = ""
    supports_tv: bool
    supports_movies: bool
    categories_tv: str
    categories_movies: str
    cloudflare_protected: bool
    enabled: bool
    is_preloaded: bool
    priority: int = 100
    last_success_at: datetime | None = None
    last_test_status: str | None = None  # "ok" | "error"
    last_test_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


def mask_indexer_site_read(site: IndexerSiteRead) -> IndexerSiteRead:
    if site.api_key:
        return site.model_copy(update={"api_key": SECRET_MASK})
    return site


def strip_indexer_api_key_sentinel(data: IndexerSiteUpdate) -> IndexerSiteUpdate:
    if data.api_key == SECRET_MASK:
        return data.model_copy(update={"api_key": None})
    return data


class IndexerSiteTestResult(BaseModel):
    success: bool
    message: str
    cloudflare_detected: bool = False
    cloudflare_solved: bool = False
    result_count: int = 0


class SearchStreamChunk(BaseModel):
    """Single SSE payload emitted by ``/torrents/search/stream`` as each
    indexer (or native-backend site) completes. The frontend accumulates
    chunks until the matching ``done`` event closes the stream."""

    source: str
    results: list[IndexerQueryResult]
