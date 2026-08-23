from pydantic_settings import BaseSettings


class NativeWatchlistsConfig(BaseSettings):
    enabled: bool = True  # Master: sidebar + all native watchlist surfaces
    custom_lists: bool = True  # User-created private lists
    watch_next: bool = True  # Watch Next queue card / page
    watch_next_include_specials: bool = False
    upcoming: bool = True  # Upcoming schedule card / page
    upcoming_default_past_days: int = 0
    upcoming_default_future_days: int = 30


class WatchlistsConfig(BaseSettings):
    # Shared / cross-backend policies
    auto_remove_watched: bool = False
    max_lists_per_user: int = 0  # 0 = unlimited
    max_items_per_list: int = 0  # 0 = unlimited

    native: NativeWatchlistsConfig = NativeWatchlistsConfig()

    @property
    def enabled(self) -> bool:
        """Derived: watchlists enabled if any backend is enabled."""
        return bool(self.native.enabled)

    @property
    def custom_lists_enabled(self) -> bool:
        return bool(self.native.enabled and self.native.custom_lists)

    @property
    def watch_next_enabled(self) -> bool:
        return bool(self.native.enabled and self.native.watch_next)

    @property
    def upcoming_enabled(self) -> bool:
        return bool(self.native.enabled and self.native.upcoming)
