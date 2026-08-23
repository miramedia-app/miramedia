"""Cross-task enqueue seam populated by scheduler registration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

enqueue_import_all: Callable[[], Awaitable[None]] | None = None
