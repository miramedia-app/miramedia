import miramedia.notifications.utils
from miramedia.config import MiraMediaConfig
from miramedia.notifications.schemas import MessageNotification
from miramedia.notifications.service_providers.abstract_notification_service_provider import (
    AbstractNotificationServiceProvider,
)


class EmailNotificationServiceProvider(AbstractNotificationServiceProvider):
    def __init__(self) -> None:
        self.config = MiraMediaConfig().notifications.email_notifications

    def send_notification(self, message: MessageNotification) -> bool:
        subject = "MiraMedia - " + message.title
        html = f"""\
                <html>
                  <body>
                    <br>
                    {message.message}
                    <br>
                    <br>
                    This is an automated message from MiraMedia.</p>
                  </body>
                </html>
                """

        for email in self.config.emails:
            miramedia.notifications.utils.send_email(
                subject=subject, html=html, addressee=email
            )

        return True
