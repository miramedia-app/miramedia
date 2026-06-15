import requests

from miramedia.config import MiraMediaConfig
from miramedia.notifications.schemas import MessageNotification
from miramedia.notifications.service_providers.abstract_notification_service_provider import (
    AbstractNotificationServiceProvider,
)


class NtfyNotificationServiceProvider(AbstractNotificationServiceProvider):
    """
    Ntfy Notification Service Provider
    """

    def __init__(self) -> None:
        self.config = MiraMediaConfig().notifications.ntfy

    def send_notification(self, message: MessageNotification) -> bool:
        response = requests.post(
            url=self.config.url,
            data=message.message.encode(encoding="utf-8"),
            headers={
                "Title": "MiraMedia - " + message.title,
            },
            timeout=60,
        )
        if response.status_code not in range(200, 300):
            return False
        return True
