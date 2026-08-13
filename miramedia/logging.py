import logging
import os
import re
import sys
from datetime import UTC, datetime
from logging.config import dictConfig
from pathlib import Path
from typing import override

from pythonjsonlogger.json import JsonFormatter


class ISOJsonFormatter(JsonFormatter):
    @override
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        dt = datetime.fromtimestamp(record.created, tz=UTC)
        return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


LOG_LEVEL = os.getenv("MIRAMEDIA_LOG_LEVEL", "INFO").upper()
LOG_FILE = Path(os.getenv("MIRAMEDIA_LOG_FILE", "/app/config/miramedia.log"))
LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "correlation_id": {
            "()": "asgi_correlation_id.CorrelationIdFilter",
            "uuid_length": 32,
            "default_value": "-",
        },
    },
    "formatters": {
        "default": {
            "format": "%(asctime)s - [%(correlation_id)s] %(levelname)s - %(name)s - %(funcName)s(): %(message)s"
        },
        "json": {
            "()": ISOJsonFormatter,
            "format": "%(asctime)s %(correlation_id)s %(levelname)s %(name)s %(message)s",
            "rename_fields": {
                "levelname": "level",
                "asctime": "timestamp",
                "name": "module",
            },
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
            "filters": ["correlation_id"],
            "stream": sys.stdout,
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "json",
            "filters": ["correlation_id"],
            "filename": str(LOG_FILE),
            "maxBytes": 10485760,
            "backupCount": 5,
            "encoding": "utf-8",
        },
    },
    "root": {
        "level": LOG_LEVEL,
        "handlers": ["console", "file"],
    },
    "loggers": {
        "uvicorn": {"handlers": ["console", "file"], "level": "DEBUG"},
        "uvicorn.access": {"handlers": ["console", "file"], "level": "DEBUG"},
        "fastapi": {"handlers": ["console", "file"], "level": "DEBUG"},
    },
}


class _SubliminalRewriteFilter(logging.Filter):
    """Re-route subliminal logs into the miramedia.subtitles namespace and drop noise.

    - Drops INFO-level provider initialize/terminate lines (one pair per provider per search).
    - Drops the entire ``subliminal.score`` namespace (per-result score chatter).
    - Drops "Skipping provider 'X': not a valid video" (expected on every TV/movie split).
    - Rewrites ``record.name`` from ``subliminal.<rest>`` to
      ``miramedia.subtitles.subliminal.<rest>``.

    Installed handler-side (logger-level filters don't fire for propagated
    records). The same record object is reused across handlers, so this filter
    must be idempotent: drop checks match both pre- and post-rewrite names,
    and rewrite is a no-op once applied.
    """

    _ORIGIN_NAMESPACES = ("subliminal.score",)
    _REWRITTEN_NAMESPACES = ("miramedia.subtitles.subliminal.score",)
    _DROP_MESSAGE_PATTERNS = (
        re.compile(r"^(Initializing|Terminating) provider "),
        re.compile(r"^Skipping provider '[^']+': not a valid video$"),
        # opensubtitles provider logs this for every episode search because
        # the XML-RPC API only knows "movie" / "tvshow" kinds. Per-episode
        # spam, no real signal — drop it.
        re.compile(r"^'[a-zA-Z]+' is not a valid movie_kind$"),
    )
    # gestdown logs a missing title at ERROR/WARNING, but "this provider has no
    # entry for this show" is a normal per-title outcome (other providers may
    # have it), not a failure. Drop these regardless of level so they don't read
    # as errors. Matched only on the gestdown provider logger.
    _GESTDOWN_NOT_FOUND_PATTERNS = (
        re.compile(r"^No show id found for "),
        re.compile(r"^Show id not found"),
    )

    @staticmethod
    def _is_subliminal_origin(name: str) -> bool:
        return name == "subliminal" or name.startswith("subliminal.")

    @staticmethod
    def _is_subliminal_record(name: str) -> bool:
        return (
            name == "subliminal"
            or name.startswith(("subliminal.", "miramedia.subtitles.subliminal."))
            or name == "miramedia.subtitles.subliminal"
        )

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._is_subliminal_record(record.name):
            return True

        if record.name.startswith(self._ORIGIN_NAMESPACES) or record.name.startswith(
            self._REWRITTEN_NAMESPACES
        ):
            return False

        # Benign "provider has no entry for this title" lines that subliminal
        # logs at ERROR/WARNING. Drop at any level (the INFO gate below would
        # miss them). Name matches both pre- and post-rewrite forms.
        if record.name.endswith("providers.gestdown"):
            try:
                msg = record.getMessage()
            except Exception:
                msg = str(record.msg)
            if any(p.match(msg) for p in self._GESTDOWN_NOT_FOUND_PATTERNS):
                return False

        if record.levelno <= logging.INFO:
            try:
                msg = record.getMessage()
            except Exception:
                msg = str(record.msg)
            if any(p.match(msg) for p in self._DROP_MESSAGE_PATTERNS):
                return False

        if self._is_subliminal_origin(record.name):
            if record.name == "subliminal":
                record.name = "miramedia.subtitles.subliminal"
            else:
                record.name = "miramedia.subtitles." + record.name
        return True


def _install_rewrite_on_handler(handler: logging.Handler) -> None:
    """Attach the subliminal rewrite filter to a handler if not already there.

    Handler-level filters run on every record the handler processes, including
    propagated ones. Logger-level filters only run at the originating logger,
    so we deliberately attach here rather than to ``logging.getLogger("subliminal")``.
    """
    if not any(isinstance(f, _SubliminalRewriteFilter) for f in handler.filters):
        # Run rewrite FIRST so subsequent prefix filters see the new name.
        handler.filters.insert(0, _SubliminalRewriteFilter())


def setup_logging() -> None:
    dictConfig(LOGGING_CONFIG)
    logging.basicConfig(
        level=LOG_LEVEL,
        format="%(asctime)s - %(levelname)s - %(name)s - %(funcName)s(): %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("transmission_rpc").setLevel(logging.WARNING)
    logging.getLogger("qbittorrentapi").setLevel(logging.WARNING)
    logging.getLogger("taskiq").setLevel(logging.WARNING)
    # Protocol byte-trace spam: websockets keepalive PING/PONG (~every 2s) and
    # httpcore connection traces carry zero app-diagnostic value. They stay
    # silent at INFO, but development mode lowers the root to DEBUG and unleashes
    # them — half the NAS startup-storm log was these. Cap explicitly so dev mode
    # still yields miramedia.* DEBUG without the flood. httpx stays at INFO (its
    # "HTTP Request: ..." line is useful), so don't cap the whole tree.
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("nodriver").setLevel(logging.INFO)
    logging.getLogger("subliminal").setLevel(logging.DEBUG)

    for handler in logging.getLogger().handlers:
        _install_rewrite_on_handler(handler)


def apply_development_log_level(development: bool) -> None:
    """Force DEBUG end-to-end when development mode is on.

    The root logger level gates records *before* any handler sees them, so
    source loggers (``miramedia.*``, which set no level of their own) inherit
    the root level — INFO by default. A DEBUG-capable handler is therefore
    inert unless the root level is also lowered.

    The ``development`` toggle is meant to mean "more logs", but on its own it
    only set the DB handler's capture level — records never reached it. Call
    this after DB config overrides are applied at startup: the UI toggle lands
    as a DB override *after* ``setup_logging`` + ``attach_db_handler`` have run
    with the boot-time env value, and even a ``config.toml`` toggle never
    touched the root level. ``MIRAMEDIA_LOG_LEVEL`` still wins when it's
    already DEBUG; this only ever lowers, never raises.
    """
    if not development:
        return
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    for handler in root_logger.handlers:
        # console/file carry no explicit level (gated by root). The DB handler
        # attaches at INFO when development read false at boot — lower it too.
        if handler.level > logging.DEBUG:
            handler.setLevel(logging.DEBUG)


def attach_db_handler() -> None:
    """Attach the database log handler to capture miramedia.* logs.

    Subliminal records are rewritten to ``miramedia.subtitles.subliminal.*``
    by ``_SubliminalRewriteFilter`` (installed handler-side) before the
    prefix filter runs, so they survive the ``miramedia`` gate.

    Capture level tracks development mode: DEBUG when on so noisy SSE /
    parser logs surface on the logs page, INFO in production to keep the
    table small.
    """
    from miramedia.config import MiraMediaConfig
    from miramedia.logs.handler import DatabaseLogHandler

    root_logger = logging.getLogger()
    if any(isinstance(h, DatabaseLogHandler) for h in root_logger.handlers):
        return

    level = logging.DEBUG if MiraMediaConfig().misc.development else logging.INFO
    handler = DatabaseLogHandler(level=level)
    _install_rewrite_on_handler(handler)
    handler.addFilter(logging.Filter("miramedia"))
    root_logger.addHandler(handler)
