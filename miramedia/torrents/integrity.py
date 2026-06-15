"""Per-file SHA1 helpers for the Phase 6.5 integrity audit.

The integrity feature is opt-in (``misc.integrity_check_enabled``). When on:

* ``compute_sha1(path)`` is called immediately after a successful import to
  capture the canonical hash on the EpisodeFile/MovieFile row.
* The scheduled ``verify_imported_files_task`` re-hashes each row that has a
  stored ``sha1`` and logs a WARNING for any mismatch (and stamps
  ``import_error`` so the imports dashboard surfaces the problem).
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

log = logging.getLogger(__name__)

_CHUNK = 1024 * 1024  # 1 MiB


def compute_sha1(path: Path) -> str | None:
    """Return the SHA1 hex digest of ``path``, or ``None`` on I/O error.

    Reads the file in 1 MiB chunks so large media files don't pin memory.
    Returning ``None`` on failure keeps the audit non-fatal — the caller logs
    and moves on rather than aborting the whole sweep.
    """
    try:
        h = hashlib.sha1()  # noqa: S324 — used for change detection, not security
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(_CHUNK)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        log.warning("sha1 compute failed for %s", path, exc_info=True)
        return None
