"""Baseline smoke tests: the application composes without a database."""


def test_app_imports_and_builds() -> None:
    from miramedia.main import app

    assert app.title  # FastAPI app constructed


def test_openapi_schema_builds() -> None:
    from miramedia.main import app

    schema = app.openapi()
    assert schema["info"]["title"]
    assert any(p.startswith("/api/v1/") for p in schema["paths"])


def test_config_loads_with_defaults() -> None:
    from miramedia.config import MiraMediaConfig

    config = MiraMediaConfig()
    assert config.database.dbname
