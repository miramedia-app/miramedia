import types

from miramedia.auth.users import _cookie_secure


def _make_config(*, frontend_url: str, cookie_secure: bool | None) -> object:
    """Build a minimal fake MiraMediaConfig for injection."""
    return types.SimpleNamespace(
        auth=types.SimpleNamespace(cookie_secure=cookie_secure),
        misc=types.SimpleNamespace(frontend_url=frontend_url),
    )


def test_explicit_true_overrides_http_url() -> None:
    cfg = _make_config(frontend_url="http://media.example.com/", cookie_secure=True)
    assert _cookie_secure(cfg) is True


def test_auto_https_url_returns_true() -> None:
    cfg = _make_config(frontend_url="https://media.example.com/", cookie_secure=None)
    assert _cookie_secure(cfg) is True


def test_auto_http_url_returns_false() -> None:
    cfg = _make_config(frontend_url="http://localhost:5555/", cookie_secure=None)
    assert _cookie_secure(cfg) is False
