"""
Notification Manager - Orchestrates sending notifications through all configured service providers
"""

import logging
import os
import threading
from time import monotonic

from miramedia.config import MiraMediaConfig
from miramedia.notifications.schemas import MessageNotification
from miramedia.notifications.service_providers.abstract_notification_service_provider import (
    AbstractNotificationServiceProvider,
)
from miramedia.notifications.service_providers.email import (
    EmailNotificationServiceProvider,
)
from miramedia.notifications.service_providers.gotify import (
    GotifyNotificationServiceProvider,
)
from miramedia.notifications.service_providers.ntfy import (
    NtfyNotificationServiceProvider,
)
from miramedia.notifications.service_providers.pushover import (
    PushoverNotificationServiceProvider,
)
from miramedia.settings.reload import get_local_committed_revision

logger = logging.getLogger(__name__)

_SUPPRESS_WINDOW_S = float(os.environ.get("MIRAMEDIA_NOTIFY_SUPPRESS_SECONDS", "900"))
_MAX_RECENT_SENDS = 256


class NotificationManager:
    """
    Manages and orchestrates notifications across all configured service providers.
    """

    def __init__(self) -> None:
        self._providers_lock = threading.Lock()
        self._cached_revision: int | None = None
        self._cached_providers: list[AbstractNotificationServiceProvider] | None = None

        self._suppress_lock = threading.Lock()
        self._recent_sends: dict[tuple[str, str], float] = {}
        self._suppressed_counts: dict[tuple[str, str], int] = {}

    def _build_providers_uncached(self) -> list[AbstractNotificationServiceProvider]:
        config = MiraMediaConfig().notifications
        providers: list[AbstractNotificationServiceProvider] = []

        # Email provider
        if config.email_notifications.enabled:
            try:
                providers.append(EmailNotificationServiceProvider())
                logger.debug("Email notification provider initialized")
            except Exception:
                logger.exception("Failed to initialize Email provider")

        # Gotify provider
        if config.gotify.enabled:
            try:
                providers.append(GotifyNotificationServiceProvider())
                logger.debug("Gotify notification provider initialized")
            except Exception:
                logger.exception("Failed to initialize Gotify provider")

        # Ntfy provider
        if config.ntfy.enabled:
            try:
                providers.append(NtfyNotificationServiceProvider())
                logger.debug("Ntfy notification provider initialized")
            except Exception:
                logger.exception("Failed to initialize Ntfy provider")

        # Pushover provider
        if config.pushover.enabled:
            try:
                providers.append(PushoverNotificationServiceProvider())
                logger.debug("Pushover notification provider initialized")
            except Exception:
                logger.exception("Failed to initialize Pushover provider")

        logger.debug("Initialized %d notification providers", len(providers))
        return providers

    def _build_providers(self) -> list[AbstractNotificationServiceProvider]:
        injected = getattr(self, "providers", None)
        if injected is not None:
            return injected

        revision = get_local_committed_revision()
        with self._providers_lock:
            if self._cached_revision == revision and self._cached_providers is not None:
                return self._cached_providers

            providers = self._build_providers_uncached()
            self._cached_revision = revision
            self._cached_providers = providers
            return providers

    def _prune_recent_sends(self, now: float) -> None:
        if len(self._recent_sends) <= _MAX_RECENT_SENDS:
            return
        cutoff = now - _SUPPRESS_WINDOW_S
        stale = [key for key, sent_at in self._recent_sends.items() if sent_at < cutoff]
        for key in stale:
            del self._recent_sends[key]
            self._suppressed_counts.pop(key, None)

    def send_notification(self, title: str, message: str) -> None:
        # No-op silently when no external providers are configured — the in-app
        # native notification is saved separately by NotificationService.
        dedup_key = (title, message)
        now = monotonic()

        providers = self._build_providers()
        if not providers:
            return

        with self._suppress_lock:
            last_sent = self._recent_sends.get(dedup_key, float("-inf"))
            if now - last_sent < _SUPPRESS_WINDOW_S:
                self._suppressed_counts[dedup_key] = (
                    self._suppressed_counts.get(dedup_key, 0) + 1
                )
                return

            suppressed = self._suppressed_counts.pop(dedup_key, 0)
            self._recent_sends[dedup_key] = now
            self._prune_recent_sends(now)

        prefix = (MiraMediaConfig().notifications.subject_prefix or "").strip()
        if prefix:
            title = f"{prefix} {title}"

        if suppressed:
            window_minutes = int(_SUPPRESS_WINDOW_S / 60)
            message = (
                f"{message} (+{suppressed} similar suppressed in the last "
                f"{window_minutes} min)"
            )

        notification = MessageNotification(title=title, message=message)

        for provider in providers:
            provider_name = provider.__class__.__name__
            try:
                success = provider.send_notification(notification)
                if success:
                    logger.info("Notification sent successfully via %s", provider_name)
                else:
                    logger.warning("Failed to send notification via %s", provider_name)

            except Exception:
                logger.exception("Error sending notification via %s", provider_name)

    def get_configured_providers(self) -> list[str]:
        return [provider.__class__.__name__ for provider in self._build_providers()]

    def is_configured(self) -> bool:
        return len(self._build_providers()) > 0


notification_manager = NotificationManager()
