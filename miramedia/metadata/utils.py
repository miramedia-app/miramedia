import logging
from collections.abc import Callable
from datetime import date
from functools import wraps
from pathlib import Path
from typing import Any
from uuid import UUID

import requests
from PIL import Image

from miramedia.exceptions import MetadataProviderUnavailableError

log = logging.getLogger(__name__)

_MAX_POSTER_DOWNLOAD_BYTES = 50 * 1024 * 1024

# Refuse decompression-bomb posters (PIL default is ~178M pixels but can be
# disabled globally elsewhere; pin an explicit ceiling for this decode path).
_MAX_POSTER_PIXELS = 64_000_000
Image.MAX_IMAGE_PIXELS = _MAX_POSTER_PIXELS


def get_year_from_date(first_air_date: str | None) -> int | None:
    if first_air_date:
        return int(first_air_date.split("-")[0])
    return None


def parse_iso_date(value: str | None) -> date | None:
    """Lenient ISO-date parser for provider responses.

    Returns ``None`` on missing / malformed input rather than raising — these
    fields are advisory display only, so a bad row shouldn't break the import.
    """
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except (ValueError, TypeError):
        return None


def is_provider_unreachable(exc: BaseException) -> bool:
    """True if ``exc`` is a transient network/DNS/connection failure while
    reaching an external metadata provider (vs a real code/data bug).

    Lets callers log a one-line WARNING ("provider unreachable") instead of a
    full multi-frame ERROR traceback when e.g. DNS can't resolve
    ``cinemeta-v3.strem.io`` or the host is briefly down — noise that is not
    actionable and buries genuine errors.
    """
    # requests.exceptions.ConnectionError/Timeout subclass RequestException;
    # socket.gaierror subclasses OSError.
    if isinstance(exc, (requests.exceptions.RequestException, OSError, TimeoutError)):
        return True
    # httpx (TMDB/TVDB backends) — match by name without importing httpx here.
    return type(exc).__module__.split(".", 1)[0] == "httpx" and type(exc).__name__ in {
        "ConnectError",
        "ConnectTimeout",
        "ReadTimeout",
        "TimeoutException",
        "RemoteProtocolError",
        "NetworkError",
        "ProxyError",
    }


def reraise_provider_unreachable[T](fn: Callable[..., T]) -> Callable[..., T]:
    """Decorator: translate a transient network/DNS/connection failure raised
    inside a provider method into :class:`MetadataProviderUnavailableError`
    (HTTP 503) so the API surfaces "provider unreachable" with a retry
    affordance instead of a bare 500. Genuine code/data bugs re-raise
    unchanged. Already-translated 503s pass straight through."""

    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> T:  # noqa: ANN401 — wraps arbitrary provider methods
        try:
            return fn(*args, **kwargs)
        except MetadataProviderUnavailableError:
            raise
        except Exception as exc:
            if is_provider_unreachable(exc):
                log.warning("%s: provider unreachable: %s", fn.__name__, exc)
                raise MetadataProviderUnavailableError from exc
            raise

    return wrapper


def poster_exists(storage_path: Path, uuid: UUID) -> bool:
    return storage_path.joinpath(str(uuid)).with_suffix(".jpg").exists()


def download_poster_image(storage_path: Path, poster_url: str, uuid: UUID) -> bool:
    res = requests.get(poster_url, stream=True, timeout=60)

    if res.status_code == 200:
        content_length = res.headers.get("Content-Length")
        if content_length is not None:
            try:
                if int(content_length) > _MAX_POSTER_DOWNLOAD_BYTES:
                    log.warning(
                        "Skipping poster download for %s: Content-Length %s exceeds 50 MiB",
                        uuid,
                        content_length,
                    )
                    return False
            except ValueError:
                pass

        image_file_path = storage_path.joinpath(str(uuid)).with_suffix(".jpg")
        bytes_written = 0
        with image_file_path.open("wb") as f:
            for chunk in res.iter_content(chunk_size=1 << 20):
                if chunk:
                    if bytes_written + len(chunk) > _MAX_POSTER_DOWNLOAD_BYTES:
                        log.warning(
                            "Skipping poster download for %s: streamed body exceeds 50 MiB",
                            uuid,
                        )
                        res.close()
                        image_file_path.unlink(missing_ok=True)
                        return False
                    f.write(chunk)
                    bytes_written += len(chunk)

        try:
            original_image = Image.open(image_file_path)
            original_image.save(image_file_path.with_suffix(".avif"), quality=50)
            original_image.save(image_file_path.with_suffix(".webp"), quality=50)
        except (
            Image.DecompressionBombError,
            OSError,
            Image.UnidentifiedImageError,
        ) as exc:
            log.warning("Skipping poster decode for %s: %s", uuid, exc)
            image_file_path.unlink(missing_ok=True)
            return False
        return True
    return False
