import requests

from miramedia.config import MiraMediaConfig
from miramedia.notifications.schemas import MessageNotification
from miramedia.notifications.service_providers.abstract_notification_service_provider import (
    AbstractNotificationServiceProvider,
)


class PushoverNotificationServiceProvider(AbstractNotificationServiceProvider):
    def __init__(self) -> None:
        self.config = MiraMediaConfig().notifications.pushover

    def send_notification(self, message: MessageNotification) -> bool:
        response = requests.post(
            url="https://api.pushover.net/1/messages.json",
            params={
                "token": self.config.api_key,
                "user": self.config.user,
                "message": message.message,
                "title": "MiraMedia - " + message.title,
            },
            timeout=60,
        )
        if response.status_code not in range(200, 300):
            return False
        return True
