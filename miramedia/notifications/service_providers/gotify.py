import requests

from miramedia.config import MiraMediaConfig
from miramedia.notifications.schemas import MessageNotification
from miramedia.notifications.service_providers.abstract_notification_service_provider import (
    AbstractNotificationServiceProvider,
)


class GotifyNotificationServiceProvider(AbstractNotificationServiceProvider):
    """
    Gotify Notification Service Provider
    """

    def __init__(self) -> None:
        self.config = MiraMediaConfig().notifications.gotify

    def send_notification(self, message: MessageNotification) -> bool:
        api_key = self.config.api_key or ""
        response = requests.post(
            url=f"{self.config.url}/message",
            headers={"X-Gotify-Key": api_key},
            json={
                "message": message.message,
                "title": message.title,
            },
            timeout=60,
        )
        if response.status_code not in range(200, 300):
            return False
        return True
