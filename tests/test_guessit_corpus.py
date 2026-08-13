"""Parse-behaviour corpus pinning the guessit 4.x contract.

Each expectation was hand-reviewed against the release name (plan 341):
the row states what the name MEANS, frozen from observed guessit 4.4.0
output only after human review. A failure here after a guessit bump means
the import pipeline's parse contract changed — investigate before re-freezing.
"""

from dataclasses import dataclass, field

import pytest

from miramedia.torrents.parsing import normalize_codec, normalize_source, parse_release
from miramedia.torrents.schemas import Quality


@dataclass
class Expected:
    title: str | None = None
    type: str | None = None
    year: int | None = None
    seasons: list[int] = field(default_factory=list)
    episodes: list[int] = field(default_factory=list)
    absolute_episode: int | None = None
    quality: Quality = Quality.unknown
    # None = "don't assert this field for this row"
    source_slug: str | None = None
    codec_slug: str | None = None
    release_group: str | None = None
    container: str | None = None
    raw_other: str | None = None


CORPUS: list[tuple[str, Expected]] = [
    # --- 1. Standard movies ---
    (
        "Movie.Name.2023.1080p.BluRay.x264-GROUP.mkv",
        Expected(
            title="Movie Name",
            type="movie",
            year=2023,
            quality=Quality.fullhd,
            source_slug="bluray",
            codec_slug="h264",
            release_group="GROUP",
            container="mkv",
        ),
    ),
    (
        "The.Matrix.1999.1080p.BluRay.x264-SPARKS.mkv",
        Expected(
            title="The Matrix",
            type="movie",
            year=1999,
            quality=Quality.fullhd,
            source_slug="bluray",
            codec_slug="h264",
            release_group="SPARKS",
            container="mkv",
        ),
    ),
    (
        "Inception.2010.720p.BluRay.x264-YIFY.mp4",
        Expected(
            title="Inception",
            type="movie",
            year=2010,
            quality=Quality.hd,
            source_slug="bluray",
            codec_slug="h264",
            release_group="YIFY",
            container="mp4",
        ),
    ),
    # --- 2. Movie year in title vs release year ---
    (
        "2001.A.Space.Odyssey.1968.2160p.UHD.BluRay.x265.mkv",
        Expected(
            title="2001 A Space Odyssey",
            type="movie",
            year=1968,
            quality=Quality.uhd,
            codec_slug="h265",
            container="mkv",
        ),
    ),
    (
        "Blade.Runner.2049.2017.1080p.WEB-DL.mkv",
        Expected(
            title="Blade Runner 2049",
            type="movie",
            year=2017,
            quality=Quality.fullhd,
            source_slug="web",
            container="mkv",
        ),
    ),
    (
        "Apollo.13.1995.1080p.BluRay.x264-GROUP.mkv",
        Expected(
            title="Apollo 13",
            type="movie",
            year=1995,
            quality=Quality.fullhd,
            source_slug="bluray",
            codec_slug="h264",
            release_group="GROUP",
            container="mkv",
        ),
    ),
    # --- 3. Standard episodes ---
    (
        "Show.Name.S03E07.720p.HDTV.x264-GRP.mkv",
        Expected(
            title="Show Name",
            type="episode",
            seasons=[3],
            episodes=[7],
            quality=Quality.hd,
            source_slug="hdtv",
            codec_slug="h264",
            release_group="GRP",
            container="mkv",
        ),
    ),
    (
        "Breaking.Bad.S05E14.1080p.WEB-DL.x265-NTb.mkv",
        Expected(
            title="Breaking Bad",
            type="episode",
            seasons=[5],
            episodes=[14],
            quality=Quality.fullhd,
            source_slug="web",
            codec_slug="h265",
            release_group="NTb",
            container="mkv",
        ),
    ),
    (
        "The.Office.US.S03E12.720p.HDTV.x264-CTU.mkv",
        Expected(
            # guessit 4.x quirk: drops regional qualifier "US" from title
            title="The Office",
            type="episode",
            seasons=[3],
            episodes=[12],
            quality=Quality.hd,
            source_slug="hdtv",
            codec_slug="h264",
            release_group="CTU",
            container="mkv",
        ),
    ),
    # --- 4. Multi-episode ---
    (
        "Show.Name.S01E01E02.1080p.mkv",
        Expected(
            title="Show Name",
            type="episode",
            seasons=[1],
            episodes=[1, 2],
            quality=Quality.fullhd,
            container="mkv",
        ),
    ),
    (
        "Show.Name.S01E01-E02.1080p.WEB.mkv",
        Expected(
            title="Show Name",
            type="episode",
            seasons=[1],
            episodes=[1, 2],
            quality=Quality.fullhd,
            source_slug="web",
            container="mkv",
        ),
    ),
    (
        "Show.Name.S02E05E06E07.720p.HDTV.mkv",
        Expected(
            title="Show Name",
            type="episode",
            seasons=[2],
            episodes=[5, 6, 7],
            quality=Quality.hd,
            source_slug="hdtv",
            container="mkv",
        ),
    ),
    # --- 5. 1x01 style ---
    (
        "Show Name 1x01 HDTV.mkv",
        Expected(
            title="Show Name",
            type="episode",
            seasons=[1],
            episodes=[1],
            source_slug="hdtv",
            container="mkv",
        ),
    ),
    (
        "Show.Name.2x13.1080p.WEB-DL.mkv",
        Expected(
            title="Show Name",
            type="episode",
            seasons=[2],
            episodes=[13],
            quality=Quality.fullhd,
            source_slug="web",
            container="mkv",
        ),
    ),
    (
        "Doctor Who 5x01 720p HDTV.mkv",
        Expected(
            title="Doctor Who",
            type="episode",
            seasons=[5],
            episodes=[1],
            quality=Quality.hd,
            source_slug="hdtv",
            container="mkv",
        ),
    ),
    # --- 6. Season packs ---
    (
        "Show.Name.S02.COMPLETE.1080p.WEB-DL.mkv",
        Expected(
            title="Show Name",
            type="episode",
            seasons=[2],
            quality=Quality.fullhd,
            source_slug="web",
            container="mkv",
            raw_other="Complete",
        ),
    ),
    (
        "Breaking.Bad.S05.COMPLETE.1080p.BluRay.x265-RARBG",
        Expected(
            title="Breaking Bad",
            type="episode",
            seasons=[5],
            quality=Quality.fullhd,
            source_slug="bluray",
            codec_slug="h265",
            release_group="RARBG",
            raw_other="Complete",
        ),
    ),
    (
        "Anime.Series.S01.1080p.WEB-DL.x264-GROUP",
        Expected(
            title="Anime Series",
            type="episode",
            seasons=[1],
            quality=Quality.fullhd,
            source_slug="web",
            codec_slug="h264",
            release_group="GROUP",
        ),
    ),
    # --- 7. Anime absolute numbering ---
    (
        "[SubsGroup] Anime Title - 145 [1080p][HEVC].mkv",
        Expected(
            title="Anime Title",
            type="episode",
            episodes=[145],
            quality=Quality.fullhd,
            codec_slug="h265",
            release_group="SubsGroup",
            container="mkv",
        ),
    ),
    (
        "[Group] Anime Title - 07 (BD 1080p) [ABC12345].mkv",
        Expected(
            title="Anime Title",
            type="episode",
            episodes=[7],
            quality=Quality.fullhd,
            source_slug="bluray",
            release_group="Group",
            container="mkv",
        ),
    ),
    (
        "[Erai-raws] Jujutsu Kaisen - 24 [1080p][HEVC x265 10bit][AAC][Multi-Sub].mkv",
        Expected(
            title="Jujutsu Kaisen",
            type="episode",
            episodes=[24],
            quality=Quality.fullhd,
            codec_slug="h265",
            release_group="Erai-raws",
            container="mkv",
        ),
    ),
    # --- 8. List-valued source (multi-token property) ---
    (
        "Movie.Name.2020.WORKPRINT.WEB-DL.1080p.mkv",
        Expected(
            title="Movie Name",
            type="movie",
            year=2020,
            quality=Quality.fullhd,
            source_slug="web",
            container="mkv",
        ),
    ),
    (
        "Show.Name.S01E01.HDTV.WEB-DL.720p.mkv",
        Expected(
            title="Show Name",
            type="episode",
            seasons=[1],
            episodes=[1],
            quality=Quality.hd,
            source_slug="hdtv",
            container="mkv",
        ),
    ),
    # --- 9. Dotted / spaced / bracketed / underscored ---
    (
        "Show.Name.S01E05.1080p.WEB-DL.mkv",
        Expected(
            title="Show Name",
            type="episode",
            seasons=[1],
            episodes=[5],
            quality=Quality.fullhd,
            source_slug="web",
            container="mkv",
        ),
    ),
    (
        "Show Name S01E05 1080p WEB-DL.mkv",
        Expected(
            title="Show Name",
            type="episode",
            seasons=[1],
            episodes=[5],
            quality=Quality.fullhd,
            source_slug="web",
            container="mkv",
        ),
    ),
    (
        "Show_Name_S01E05_720p_HDTV_x264.mkv",
        Expected(
            title="Show Name",
            type="episode",
            seasons=[1],
            episodes=[5],
            quality=Quality.hd,
            source_slug="hdtv",
            codec_slug="h264",
            container="mkv",
        ),
    ),
    (
        "[Group] Show Name S01E05 [1080p].mkv",
        Expected(
            title="Show Name",
            type="episode",
            seasons=[1],
            episodes=[5],
            quality=Quality.fullhd,
            release_group="Group",
            container="mkv",
        ),
    ),
    # --- 10. [imdb-tt...] folder form ---
    (
        "Movie Name (2021) [imdb-tt1234567]",
        Expected(title="Movie Name", type="movie", year=2021),
    ),
    (
        "Dune Part Two (2024) [imdb-tt15239678]",
        Expected(
            # guessit 4.x quirk: drops "Part Two" subtitle from folder title
            title="Dune",
            type="movie",
            year=2024,
        ),
    ),
    # --- 11. Sample / proper / repack ---
    (
        "Show.Name.S01E01.PROPER.1080p.WEB.mkv",
        Expected(
            title="Show Name",
            type="episode",
            seasons=[1],
            episodes=[1],
            quality=Quality.fullhd,
            source_slug="web",
            container="mkv",
            raw_other="Proper",
        ),
    ),
    (
        "Show.Name.S01E01.REPACK.720p.HDTV.mkv",
        Expected(
            title="Show Name",
            type="episode",
            seasons=[1],
            episodes=[1],
            quality=Quality.hd,
            source_slug="hdtv",
            container="mkv",
            # guessit 4.x quirk: REPACK surfaced as other='Proper'
            raw_other="Proper",
        ),
    ),
    (
        "Movie.Name.2023.1080p.BluRay.sample.mkv",
        Expected(
            title="Movie Name",
            type="movie",
            year=2023,
            quality=Quality.fullhd,
            source_slug="bluray",
            container="mkv",
            raw_other="Sample",
        ),
    ),
    # --- 12. Quality ladder ---
    (
        "Movie.2023.2160p.UHD.BluRay.x265.mkv",
        Expected(
            title="Movie",
            type="movie",
            year=2023,
            quality=Quality.uhd,
            codec_slug="h265",
            container="mkv",
        ),
    ),
    (
        "Movie.2023.1080p.BluRay.x264.mkv",
        Expected(
            title="Movie",
            type="movie",
            year=2023,
            quality=Quality.fullhd,
            source_slug="bluray",
            codec_slug="h264",
            container="mkv",
        ),
    ),
    (
        "Movie.2023.1440p.WEB-DL.x265.mkv",
        Expected(
            title="Movie",
            type="movie",
            year=2023,
            quality=Quality.fullhd,
            source_slug="web",
            codec_slug="h265",
            container="mkv",
        ),
    ),
    (
        "Movie.2023.720p.HDTV.x264.mkv",
        Expected(
            title="Movie",
            type="movie",
            year=2023,
            quality=Quality.hd,
            source_slug="hdtv",
            codec_slug="h264",
            container="mkv",
        ),
    ),
    (
        "Movie.2023.480p.DVDRip.xvid.mkv",
        Expected(
            title="Movie",
            type="movie",
            year=2023,
            quality=Quality.sd,
            source_slug="dvd",
            codec_slug="xvid",
            container="mkv",
            raw_other="Rip",
        ),
    ),
    (
        "Movie.2023.BluRay.x264.mkv",
        Expected(
            title="Movie",
            type="movie",
            year=2023,
            source_slug="bluray",
            codec_slug="h264",
            container="mkv",
        ),
    ),
    # --- 13. Release group + container (and extra source variants) ---
    (
        "Show.Name.S01E01.1080p.WEB-DL.x265-GROUP.mkv",
        Expected(
            title="Show Name",
            type="episode",
            seasons=[1],
            episodes=[1],
            quality=Quality.fullhd,
            source_slug="web",
            codec_slug="h265",
            release_group="GROUP",
            container="mkv",
        ),
    ),
    (
        "Movie.Name.2023.1080p.BluRay.x264-SPARKS.mkv",
        Expected(
            title="Movie Name",
            type="movie",
            year=2023,
            quality=Quality.fullhd,
            source_slug="bluray",
            codec_slug="h264",
            release_group="SPARKS",
            container="mkv",
        ),
    ),
    (
        "Anime.Title.S01E01.720p.WEB-DL.x264-SubsPlease.mkv",
        Expected(
            title="Anime Title",
            type="episode",
            seasons=[1],
            episodes=[1],
            quality=Quality.hd,
            source_slug="web",
            codec_slug="h264",
            release_group="SubsPlease",
            container="mkv",
        ),
    ),
    (
        "Show.Name.S01E01.1080p.BluRay.REMUX.x264-GROUP.mkv",
        Expected(
            title="Show Name",
            type="episode",
            seasons=[1],
            episodes=[1],
            quality=Quality.fullhd,
            source_slug="bluray",
            codec_slug="h264",
            release_group="GROUP",
            container="mkv",
            raw_other="Remux",
        ),
    ),
    (
        "Show.Name.S01E01.1080p.WEBRip.x264-GROUP.mkv",
        Expected(
            title="Show Name",
            type="episode",
            seasons=[1],
            episodes=[1],
            quality=Quality.fullhd,
            source_slug="web",
            codec_slug="h264",
            release_group="GROUP",
            container="mkv",
            raw_other="Rip",
        ),
    ),
    (
        "Show.Name.S01E01.1080p.BDrip.x264-GROUP.mkv",
        Expected(
            title="Show Name",
            type="episode",
            seasons=[1],
            episodes=[1],
            quality=Quality.fullhd,
            source_slug="bluray",
            codec_slug="h264",
            release_group="GROUP",
            container="mkv",
            raw_other="Rip",
        ),
    ),
    (
        "Show.Name.S01E01.1080p.DVDRip.x264-GROUP.avi",
        Expected(
            title="Show Name",
            type="episode",
            seasons=[1],
            episodes=[1],
            quality=Quality.fullhd,
            source_slug="dvd",
            codec_slug="h264",
            release_group="GROUP",
            container="avi",
            raw_other="Rip",
        ),
    ),
    (
        "Show.Name.S00E01.Special.1080p.WEB-DL.mkv",
        Expected(
            title="Show Name",
            type="episode",
            seasons=[0],
            episodes=[1],
            quality=Quality.fullhd,
            source_slug="web",
            container="mkv",
        ),
    ),
    (
        "Show.Name.S01.1080p.WEB-DL.mkv",
        Expected(
            title="Show Name",
            type="episode",
            seasons=[1],
            quality=Quality.fullhd,
            source_slug="web",
            container="mkv",
        ),
    ),
    (
        "Show.Name.2024.S01E01.1080p.WEB-DL.mkv",
        Expected(
            title="Show Name",
            type="episode",
            year=2024,
            seasons=[1],
            episodes=[1],
            quality=Quality.fullhd,
            source_slug="web",
            container="mkv",
        ),
    ),
    (
        "Show.Name.S01E01.576p.HDTV.mkv",
        Expected(
            title="Show Name",
            type="episode",
            seasons=[1],
            episodes=[1],
            quality=Quality.sd,
            source_slug="hdtv",
            container="mkv",
        ),
    ),
    (
        "Show.Name.S01E01.4K.UHD.BluRay.x265-GROUP.mkv",
        Expected(
            title="Show Name",
            type="episode",
            seasons=[1],
            episodes=[1],
            quality=Quality.uhd,
            codec_slug="h265",
            release_group="GROUP",
            container="mkv",
        ),
    ),
]


@pytest.mark.parametrize(("name", "expected"), CORPUS, ids=[n for n, _ in CORPUS])
def test_parse_release_corpus(name: str, expected: Expected) -> None:
    r = parse_release(name)
    assert r.title == expected.title
    assert r.type == expected.type
    assert r.year == expected.year
    assert r.seasons == expected.seasons
    assert r.episodes == expected.episodes
    assert r.absolute_episode == expected.absolute_episode
    assert r.quality == expected.quality
    if expected.source_slug is not None:
        assert normalize_source(r.source) == expected.source_slug
    if expected.codec_slug is not None:
        assert normalize_codec(r.video_codec) == expected.codec_slug
    if expected.release_group is not None:
        assert r.release_group == expected.release_group
    if expected.container is not None:
        assert r.container == expected.container
    if expected.raw_other is not None:
        assert r.raw.get("other") == expected.raw_other


def test_multi_token_source_is_list_and_normalizes() -> None:
    r = parse_release("Movie.Name.2020.WORKPRINT.WEB-DL.1080p.mkv")
    assert isinstance(r.source, list)
    assert normalize_source(r.source) == "web"


def test_multi_token_source_hdtv_webdl_prefers_hdtv() -> None:
    r = parse_release("Show.Name.S01E01.HDTV.WEB-DL.720p.mkv")
    assert isinstance(r.source, list)
    assert normalize_source(r.source) == "hdtv"
