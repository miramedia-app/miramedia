def filter_enabled_preferences(
    current: list[str] | None, enabled: set[str]
) -> list[str] | None:
    """Tri-state filter for per-title preference lists.

    Keep only still-enabled entries. If some entries survive, return the
    filtered list. If the list had entries but none survive, return ``None``
    so the title falls back to the global default. ``None`` and ``[]`` inputs
    pass through unchanged (explicit "Any" or unset).
    """
    if current is None or current == []:
        return current
    filtered = [n for n in current if n in enabled]
    if filtered == list(current):
        return current
    # Original had names but none survived → fall back to global default.
    return filtered or None
