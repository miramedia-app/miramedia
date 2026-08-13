"""Provider-Hub catalog plugins exposed as subliminal providers.

Vendored, keyless, pure-stdlib subtitle scrapers from the MIT-licensed
``LavX/bazarr-provider-catalog`` (https://github.com/LavX/bazarr-provider-catalog),
adapted to subliminal's provider interface via :mod:`.adapter`.

Importing this module registers every plugin provider with subliminal's
``provider_manager`` (idempotent). Each concrete provider class is also bound
as a module attribute so the entry-point string used for registration can
resolve it.
"""

from __future__ import annotations

import logging

from subliminal.extensions import provider_manager
from subliminal.video import Episode, Movie, Video

from miramedia.subtitles.plugins.adapter import (
    build_plugin_provider,
    install_bounded_vendored_extract_patches,
)
from miramedia.subtitles.plugins.vendored.embeddedsubtitles import (
    EmbeddedSubtitlesProvider,
)
from miramedia.subtitles.plugins.vendored.isubtitles import ISubtitlesProvider
from miramedia.subtitles.plugins.vendored.my_subs import MySubsProvider
from miramedia.subtitles.plugins.vendored.subf2m import SubF2MProvider
from miramedia.subtitles.plugins.vendored.subtitlecat import SubtitlecatProvider
from miramedia.subtitles.plugins.vendored.tvsubtitles import TvSubtitlesProvider

log = logging.getLogger(__name__)

# (registry id, Hub plugin class, ISO 639-3 languages, video types). Keyless,
# no-login providers only. The registry id is the subliminal provider name;
# the subtitles config field maps to it via service.PROVIDER_MAP.
_MOVIE_AND_EPISODE: tuple[type[Video], ...] = (Episode, Movie)
_PLUGIN_SPECS: list[tuple[str, type, list[str], tuple[type[Video], ...]]] = [
    (
        "subtitlecat",
        SubtitlecatProvider,
        [
            "eng",
            "spa",
            "fra",
            "deu",
            "ita",
            "por",
            "pol",
            "rus",
            "tur",
            "ara",
            "hin",
            "ind",
            "nld",
            "zho",
            "jpn",
            "kor",
            "ces",
            "ell",
            "hun",
            "ron",
            "bul",
            "swe",
            "dan",
            "nor",
            "fin",
            "ukr",
            "slk",
            "hrv",
            "srp",
            "slv",
            "lit",
            "lav",
            "est",
            "vie",
            "tha",
            "msa",
            "fil",
            "heb",
            "fas",
            "urd",
            "ben",
            "tam",
            "tel",
            "mar",
            "kan",
            "mal",
            "sin",
            "kat",
            "hye",
            "aze",
            "kaz",
            "uzb",
        ],
        _MOVIE_AND_EPISODE,
    ),
    (
        "subf2m",
        SubF2MProvider,
        [
            "ara",
            "ben",
            "bul",
            "ces",
            "dan",
            "deu",
            "ell",
            "eng",
            "fas",
            "fin",
            "fra",
            "heb",
            "hrv",
            "hun",
            "ind",
            "isl",
            "ita",
            "jpn",
            "mkd",
            "msa",
            "nld",
            "nor",
            "pol",
            "por",
            "ron",
            "rus",
            "spa",
            "srp",
            "swe",
            "tha",
            "tur",
            "vie",
        ],
        _MOVIE_AND_EPISODE,
    ),
    (
        "isubtitles",
        ISubtitlesProvider,
        [
            "aze",
            "ara",
            "bel",
            "ben",
            "bos",
            "bul",
            "cat",
            "ces",
            "dan",
            "deu",
            "ell",
            "eng",
            "epo",
            "est",
            "eus",
            "fas",
            "fil",
            "fin",
            "fra",
            "heb",
            "hin",
            "hrv",
            "hun",
            "hye",
            "ind",
            "isl",
            "ita",
            "jpn",
            "kal",
            "kan",
            "kat",
            "khm",
            "kor",
            "kur",
            "lav",
            "lit",
            "mal",
            "mkd",
            "mon",
            "msa",
            "mya",
            "nep",
            "nld",
            "nor",
            "pan",
            "pol",
            "por",
            "pus",
            "ron",
            "rus",
            "sin",
            "slk",
            "slv",
            "som",
            "spa",
            "sqi",
            "srp",
            "sun",
            "swa",
            "swe",
            "tam",
            "tel",
            "tha",
            "tur",
            "ukr",
            "urd",
            "vie",
            "yor",
            "zho",
        ],
        _MOVIE_AND_EPISODE,
    ),
    (
        "my_subs",
        MySubsProvider,
        [
            "eng",
            "spa",
            "fra",
            "deu",
            "ita",
            "por",
            "pol",
            "rus",
            "tur",
            "ara",
            "hin",
            "ind",
            "nld",
            "zho",
            "jpn",
            "kor",
            "ces",
            "ell",
            "hun",
            "ron",
            "bul",
            "swe",
            "dan",
            "nor",
            "fin",
            "ukr",
            "slk",
            "hrv",
            "srp",
            "slv",
            "lit",
            "lav",
            "est",
            "vie",
            "tha",
            "msa",
            "fil",
            "heb",
            "fas",
            "urd",
            "ben",
            "sqi",
        ],
        _MOVIE_AND_EPISODE,
    ),
    # Replaces subliminal's built-in tvsubtitles, whose /search.php endpoint
    # now 404s. Registered under the SAME name "tvsubtitles" (the dead built-in
    # is evicted first in _register) so the name is consistent everywhere —
    # config, logs, UI.
    (
        "tvsubtitles",
        TvSubtitlesProvider,
        [
            "ara",
            "bul",
            "ces",
            "dan",
            "deu",
            "ell",
            "eng",
            "fin",
            "fra",
            "hun",
            "ita",
            "jpn",
            "kor",
            "nld",
            "pol",
            "por",
            "ron",
            "rus",
            "spa",
            "swe",
            "tur",
            "ukr",
            "zho",
        ],
        (Episode,),
    ),
    # Extracts subtitle tracks already embedded in the local video file via
    # ffmpeg/ffprobe — offline, exact match (scored as a hash hit). Reads the
    # file path from the video dict (subliminal.scan_video sets it).
    (
        "embeddedsubtitles",
        EmbeddedSubtitlesProvider,
        [
            "afr",
            "amh",
            "ara",
            "asm",
            "aze",
            "bak",
            "bel",
            "ben",
            "bod",
            "bos",
            "bre",
            "bul",
            "cat",
            "ces",
            "cym",
            "dan",
            "deu",
            "ell",
            "eng",
            "est",
            "eus",
            "fao",
            "fas",
            "fin",
            "fra",
            "glg",
            "guj",
            "hat",
            "hau",
            "haw",
            "heb",
            "hin",
            "hrv",
            "hun",
            "hye",
            "ind",
            "isl",
            "ita",
            "jav",
            "jpn",
            "kan",
            "kat",
            "kaz",
            "khm",
            "kor",
            "lao",
            "lat",
            "lav",
            "lin",
            "lit",
            "ltz",
            "mal",
            "mar",
            "mkd",
            "mlg",
            "mlt",
            "mon",
            "mri",
            "msa",
            "mya",
            "nep",
            "nld",
            "nno",
            "nor",
            "oci",
            "pan",
            "pol",
            "por",
            "pus",
            "ron",
            "rus",
            "san",
            "sin",
            "slk",
            "slv",
            "sna",
            "snd",
            "som",
            "spa",
            "sqi",
            "srp",
            "sun",
            "swa",
            "swe",
            "tam",
            "tat",
            "tel",
            "tgk",
            "tgl",
            "tha",
            "tuk",
            "tur",
            "ukr",
            "urd",
            "uzb",
            "vie",
            "yid",
            "yor",
            "zho",
        ],
        _MOVIE_AND_EPISODE,
    ),
]

# IDs of every plugin provider, for the service's provider map / config.
PLUGIN_PROVIDER_IDS: list[str] = [spec[0] for spec in _PLUGIN_SPECS]


def _evict_provider(name: str) -> None:
    """Remove any provider already registered under ``name`` from subliminal's
    manager so we can register our replacement under the same name.

    Used to drop the dead built-in ``tvsubtitles`` (its endpoint 404s) and
    replace it with the catalog version under the same name. Touches the
    stevedore ``ExtensionManager`` internals (``names()`` reads ``extensions``;
    ``register`` rejects a name already there), guarded so an internals change
    in a future subliminal can't break import.
    """
    pm = provider_manager
    try:
        if getattr(pm, "_extensions_by_name", None):
            pm._extensions_by_name.pop(name, None)
        pm.extensions = [e for e in pm.extensions if getattr(e, "name", None) != name]
        pm.internal_extensions = [
            e for e in pm.internal_extensions if not e.strip().startswith(f"{name} ")
        ]
        pm.registered_extensions = [
            e for e in pm.registered_extensions if not e.strip().startswith(f"{name} ")
        ]
    except Exception:
        log.debug("Could not evict existing provider %s", name, exc_info=True)


def _register() -> None:
    install_bounded_vendored_extract_patches()
    for provider_id, hub_class, languages, video_types in _PLUGIN_SPECS:
        cls = build_plugin_provider(provider_id, hub_class, languages, video_types)
        # Bind as a module attribute so the entry-point string resolves it.
        globals()[cls.__name__] = cls
        entry_point = f"{provider_id} = miramedia.subtitles.plugins:{cls.__name__}"
        # Evict any provider already holding this name (e.g. the dead built-in
        # tvsubtitles) so our replacement registers cleanly under it.
        if provider_id in provider_manager.names():
            _evict_provider(provider_id)
        try:
            provider_manager.register(entry_point)
        except ValueError:
            # Already registered (re-import) — leave as-is.
            log.debug("Plugin provider %s already registered", provider_id)


_register()
