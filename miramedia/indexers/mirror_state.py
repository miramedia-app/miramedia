"""Pure logic for a native indexer site's mirror list.

The mirror list is an ordered list of :class:`MirrorEntry`. ``source`` splits
code-shipped mirrors (``seeded`` — reorderable and toggleable but never
deletable) from user-added ones (``user`` — fully deletable). Only *enabled*
mirrors, in order, drive the live search and connectivity probe.

Everything here is DB-free so it can be unit tested in isolation. The
repository and the seeder call into it; they own persistence.
"""

from __future__ import annotations

from typing import Literal

from miramedia.indexers.schemas import MirrorEntry

MirrorSource = Literal["seeded", "user"]


class MirrorRuleError(ValueError):
    """A mirror update violated a rule (deleting a seeded mirror, or leaving the
    active URL absent/disabled). Surfaced to the API as a 400."""


def derive_available_urls(mirrors: list[MirrorEntry]) -> list[str]:
    """Enabled mirror URLs, in order, de-duplicated — the live failover list."""
    seen: set[str] = set()
    urls: list[str] = []
    for mirror in mirrors:
        if not mirror.enabled:
            continue
        if mirror.url in seen:
            continue
        seen.add(mirror.url)
        urls.append(mirror.url)
    return urls


def mirrors_from_urls(
    urls: list[str],
    active_url: str,
    *,
    source: MirrorSource,
) -> list[MirrorEntry]:
    """Build a fresh mirror list from a flat URL list, ``active_url`` first.

    Every entry is enabled and tagged ``source``. Used when creating a site or
    backfilling a row that has no structured mirror list yet.
    """
    ordered: list[str] = []
    seen: set[str] = set()
    for url in [active_url, *urls]:
        if url and url not in seen:
            seen.add(url)
            ordered.append(url)
    return [MirrorEntry(url=url, enabled=True, source=source) for url in ordered]


def load_entries(
    stored: list[dict] | None,
    available_urls: list[str] | None,
    active_url: str,
) -> list[MirrorEntry]:
    """Read a row's structured mirror list, backfilling from ``available_urls``
    for rows written before the ``mirrors`` column existed."""
    if stored:
        return [MirrorEntry.model_validate(m) for m in stored]
    return mirrors_from_urls(list(available_urls or []), active_url, source="user")


def reconcile_seeded(
    existing: list[MirrorEntry],
    seeded_urls: list[str],
) -> list[MirrorEntry]:
    """Merge code-shipped ``seeded_urls`` into a site's existing mirror list.

    - Existing entries keep their order and ``enabled`` state (so a user's
      disable / reorder survives upgrades).
    - Each existing entry is (re)classified by current code: in ``seeded_urls``
      → ``seeded``; otherwise → ``user`` (a mirror dropped from code becomes a
      plain deletable user mirror).
    - Any ``seeded_urls`` not already present are appended as enabled ``seeded``
      entries (new code mirrors propagate).
    """
    seeded_set = set(seeded_urls)
    result: list[MirrorEntry] = []
    present: set[str] = set()
    for mirror in existing:
        source = "seeded" if mirror.url in seeded_set else "user"
        result.append(
            MirrorEntry(url=mirror.url, enabled=mirror.enabled, source=source)
        )
        present.add(mirror.url)
    for url in seeded_urls:
        if url not in present:
            result.append(MirrorEntry(url=url, enabled=True, source="seeded"))
            present.add(url)
    return result


def apply_user_update(
    existing: list[MirrorEntry],
    incoming: list[MirrorEntry],
    active_url: str,
) -> list[MirrorEntry]:
    """Reconcile a user-supplied mirror list against what is stored.

    Enforces the rules a client cannot override:

    - ``source`` is authoritative from storage — an existing URL keeps its
      stored source; a URL not seen before is ``user``. (A client cannot
      relabel a seeded mirror as ``user`` to delete it.)
    - Every stored ``seeded`` mirror must still be present — dropping one raises
      ``ValueError``. User mirrors may be dropped freely.
    - The active ``url`` must remain present and enabled.

    Returns the reconciled list (incoming order, de-duplicated by URL).
    """
    existing_by_url = {m.url: m for m in existing}

    result: list[MirrorEntry] = []
    seen: set[str] = set()
    for mirror in incoming:
        if mirror.url in seen:
            continue
        seen.add(mirror.url)
        stored = existing_by_url.get(mirror.url)
        source = stored.source if stored is not None else "user"
        result.append(
            MirrorEntry(url=mirror.url, enabled=mirror.enabled, source=source)
        )

    dropped_seeded = [
        m.url for m in existing if m.source == "seeded" and m.url not in seen
    ]
    if dropped_seeded:
        msg = f"Cannot delete seeded mirror(s): {', '.join(dropped_seeded)}"
        raise MirrorRuleError(msg)

    active_entry = next((m for m in result if m.url == active_url), None)
    if active_entry is None or not active_entry.enabled:
        msg = "The active mirror URL must remain present and enabled"
        raise MirrorRuleError(msg)

    return result
