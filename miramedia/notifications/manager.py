"""
Notification Manager - Orchestrates sending notifications through all configured service providers
"""

import logging

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

logger = logging.getLogger(__name__)


class NotificationManager:
    """
    Manages and orchestrates notifications across all configured service providers.
    """

    def __init__(self) -> None:
        pass

    def _build_providers(self) -> list[AbstractNotificationServiceProvider]:
        injected = getattr(self, "providers", None)
        if injected is not None:
            return injected

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

    def send_notification(self, title: str, message: str) -> None:
        # No-op silently when no external providers are configured — the in-app
        # native notification is saved separately by NotificationService.
        # Re-read config so subject_prefix changes apply without a restart.
        prefix = (MiraMediaConfig().notifications.subject_prefix or "").strip()
        if prefix:
            title = f"{prefix} {title}"

        notification = MessageNotification(title=title, message=message)
        providers = self._build_providers()

        for provider in providers:
            provider_name = provider.__class__.__name__
            try:
                success = provider.send_notification(notification)
                if success:
                    logger.info(f"Notification sent successfully via {provider_name}")
                else:
                    logger.warning(f"Failed to send notification via {provider_name}")

            except Exception:
                logger.exception(f"Error sending notification via {provider_name}")

    def get_configured_providers(self) -> list[str]:
        return [provider.__class__.__name__ for provider in self._build_providers()]

    def is_configured(self) -> bool:
        return len(self._build_providers()) > 0


notification_manager = NotificationManager()
