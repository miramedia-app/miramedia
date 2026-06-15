from pydantic_settings import BaseSettings


class EmailConfig(BaseSettings):
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    from_email: str = ""
    use_tls: bool = False


class EmailNotificationsConfig(BaseSettings):
    enabled: bool = False
    emails: list[str] = []  # the email addresses to send notifications to


class GotifyConfig(BaseSettings):
    enabled: bool = False
    api_key: str | None = None
    url: str | None = (
        None  # e.g. https://gotify.example.com (note lack of trailing slash)
    )


class NtfyConfig(BaseSettings):
    enabled: bool = False
    url: str | None = (
        None  # e.g. https://ntfy.sh/your-topic (note lack of trailing slash)
    )


class PushoverConfig(BaseSettings):
    enabled: bool = False
    api_key: str | None = None
    user: str | None = None


class NativeNotificationConfig(BaseSettings):
    enabled: bool = True  # In-app notifications page and bell icon
    retention_days: int = 30  # Delete read notifications older than this


class NotificationConfig(BaseSettings):
    subject_prefix: str = (
        ""  # Optional prefix prepended to every notification title, e.g. "[MiraMedia]".
    )
    native: NativeNotificationConfig = NativeNotificationConfig()
    smtp_config: EmailConfig = EmailConfig()
    email_notifications: EmailNotificationsConfig = EmailNotificationsConfig()
    gotify: GotifyConfig = GotifyConfig()
    ntfy: NtfyConfig = NtfyConfig()
    pushover: PushoverConfig = PushoverConfig()
