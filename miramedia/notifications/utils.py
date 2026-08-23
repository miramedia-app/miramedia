import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from miramedia.config import MiraMediaConfig

log = logging.getLogger(__name__)


def sanitize_notification_title(title: str) -> str:
    """Strip CR/LF and other control chars so titles are header-safe."""
    return "".join(ch for ch in title if ch >= " " and ch != "\x7f")


def send_email(subject: str, html: str, addressee: str) -> None:
    email_conf = MiraMediaConfig().notifications.smtp_config
    message = MIMEMultipart()
    message["From"] = email_conf.from_email
    message["To"] = addressee
    message["Subject"] = str(subject)
    message.attach(MIMEText(html, "html"))

    with smtplib.SMTP(email_conf.smtp_host, email_conf.smtp_port, timeout=60) as server:
        if email_conf.use_tls:
            server.starttls()
        server.login(email_conf.smtp_user, email_conf.smtp_password)
        server.sendmail(email_conf.from_email, addressee, message.as_string())

    log.info("Successfully sent email to %s with subject: %s", addressee, subject)
