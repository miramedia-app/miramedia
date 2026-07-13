import logging
import os
import secrets

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

log = logging.getLogger(__name__)

PLACEHOLDER_TOKEN_SECRET = "CHANGE_ME_GENERATE_RANDOM_STRING"  # noqa: S105 -- known placeholder, not a secret


class OpenIdConfig(BaseSettings):
    client_id: str = ""
    client_secret: str = ""
    configuration_endpoint: str = ""
    enabled: bool = False
    name: str = "OAuth2"


class AuthConfig(BaseSettings):
    # to get a signing key run:
    # openssl rand -hex 32
    token_secret: str = Field(default_factory=secrets.token_hex)
    session_lifetime: int = 60 * 60 * 24
    admin_emails: list[str] = []
    email_password_resets: bool = False
    openid_connect: OpenIdConfig = OpenIdConfig()
    # None = auto: Secure iff misc.frontend_url is https. Set explicitly only
    # for setups where the scheme of frontend_url doesn't match what the
    # browser sees (e.g. TLS-terminating proxy in front of an http URL).
    cookie_secure: bool | None = None

    @field_validator("token_secret")
    @classmethod
    def validate_token_secret(cls, value: str) -> str:
        if value == PLACEHOLDER_TOKEN_SECRET or len(value) < 32:
            if os.environ.get("MIRAMEDIA_ALLOW_INSECURE_TOKEN_SECRET") == "1":
                log.warning(
                    "auth.token_secret is the placeholder or too short; "
                    "MIRAMEDIA_ALLOW_INSECURE_TOKEN_SECRET=1 is set — using an "
                    "ephemeral random secret for this process (sessions will not "
                    "survive restarts)."
                )
                return secrets.token_hex()
            msg = (
                "auth.token_secret must be a random string of at least 32 "
                "characters and must not be the placeholder value. Generate one "
                "with: openssl rand -hex 32 — then set [auth] token_secret in "
                "config.toml."
            )
            raise ValueError(msg)
        return value
