import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "c8d2e3f4a5b6_persist_specials_skip.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("persist_specials_skip", _MIGRATION)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_specials_enabled_reads_config(monkeypatch: pytest.MonkeyPatch) -> None:
    migration = _load_migration()

    class StubConfig:
        def __init__(self) -> None:
            self.misc = SimpleNamespace(download_specials=True)

    monkeypatch.setattr("miramedia.config.MiraMediaConfig", StubConfig)
    assert migration._specials_enabled() is True


def test_config_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    migration = _load_migration()

    def boom() -> None:
        msg = "config unavailable"
        raise RuntimeError(msg)

    monkeypatch.setattr("miramedia.config.MiraMediaConfig", boom)
    with pytest.raises(RuntimeError, match="persist-specials-skip"):
        migration._specials_enabled()
