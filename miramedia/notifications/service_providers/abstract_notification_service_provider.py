from abc import ABC, abstractmethod

from miramedia.notifications.schemas import MessageNotification


class AbstractNotificationServiceProvider(ABC):
    @abstractmethod
    def send_notification(self, message: MessageNotification) -> bool:
        """
        Sends a notification with the given message.

        :param message: The message to send in the notification.
        :return: True if the notification was sent successfully, False otherwise.
        """
