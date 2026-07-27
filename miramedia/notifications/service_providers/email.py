import html
import logging

import miramedia.notifications.utils
from miramedia.config import MiraMediaConfig
from miramedia.notifications.schemas import MessageNotification
from miramedia.notifications.service_providers.abstract_notification_service_provider import (
    AbstractNotificationServiceProvider,
)

log = logging.getLogger(__name__)


class EmailNotificationServiceProvider(AbstractNotificationServiceProvider):
    def __init__(self) -> None:
        self.config = MiraMediaConfig().notifications.email_notifications

    def send_notification(self, message: MessageNotification) -> bool:
        safe_title = (
            message.title.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
        )
        subject = "MiraMedia - " + safe_title
        escaped_message = html.escape(message.message)
        html_body = f"""\
                <html>
                  <body>
                    <br>
                    {escaped_message}
                    <br>
                    <br>
                    This is an automated message from MiraMedia.
                  </body>
                </html>
                """

        ok = True
        for email in self.config.emails:
            try:
                miramedia.notifications.utils.send_email(
                    subject=subject, html=html_body, addressee=email
                )
            except Exception:
                log.warning("Email notification to one recipient failed", exc_info=True)
                ok = False
        return ok
