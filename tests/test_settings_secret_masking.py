"""Tests for masking third-party credentials in settings read/export/write paths."""

from __future__ import annotations

import uuid
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from miramedia.settings.validation import (
    LIST_PATH_WILDCARD,
    SECRET_MASK,
    SECRET_OVERRIDE_PATHS,
    mask_secret_values,
    resolve_masked_config,
    sanitize_export_overrides,
    strip_masked_values,
)
from miramedia.subtitles.config import NativeSubtitleConfig, ProviderConfig
from tests.fakes.repositories import FakeSettingsRepository

LEGACY_SECRET_OVERRIDE_PATHS: frozenset[tuple[str, ...]] = frozenset(
    {
        ("auth", "openid_connect", "client_secret"),
        ("notifications", "smtp_config", "smtp_password"),
        ("notifications", "gotify", "api_key"),
        ("notifications", "pushover", "api_key"),
        ("torrents", "qbittorrent", "password"),
        ("torrents", "transmission", "password"),
        ("torrents", "sabnzbd", "api_key"),
        ("indexers", "prowlarr", "api_key"),
        ("indexers", "jackett", "api_key"),
        ("metadata", "tmdb", "api_key"),
        ("metadata", "tvdb", "api_key"),
        ("requests", "seerr", "api_key"),
        ("subtitles", "bazarr", "api_key"),
        ("subtitles", "bazarr", "shim_api_key"),
        ("subtitles", "native", "opensubtitlescom", "password"),
        ("subtitles", "native", "addic7ed", "password"),
        ("subtitles", "native", "subdl", "api_key"),
        ("subtitles", "native", "subsource", "api_key"),
        ("cloudflare", "firecrawl", "api_key"),
        ("cloudflare", "browser_run", "api_token"),
    }
)

SETTINGS_PREFIX = "/api/v1/system/settings"


@contextmanager
def settings_client(
    *,
    repo: FakeSettingsRepository | None = None,
) -> Generator[tuple[TestClient, FakeSettingsRepository]]:
    from miramedia.auth.users import current_superuser
    from miramedia.database import get_session
    from miramedia.main import app
    from miramedia.settings.dependencies import get_settings_repository

    fake_repo = repo or FakeSettingsRepository()

    async def _stub_session() -> Any:
        yield None

    async def _superuser() -> Any:
        user = MagicMock()
        user.id = uuid.uuid4()
        user.is_superuser = True
        return user

    def _repo_dep() -> FakeSettingsRepository:
        return fake_repo

    prior_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_session] = _stub_session
    app.dependency_overrides[current_superuser] = _superuser
    app.dependency_overrides[get_settings_repository] = _repo_dep
    try:
        with patch(
            "miramedia.settings.router.refresh_dynamic_schedules",
            new_callable=AsyncMock,
            create=True,
        ):
            client = TestClient(app, raise_server_exceptions=False)
            try:
                yield client, fake_repo
            finally:
                client.close()
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(prior_overrides)


def test_mask_secret_values_masks_every_secret_path() -> None:
    tree = {
        "auth": {"openid_connect": {"client_secret": "real-secret"}},
        "notifications": {
            "smtp_config": {"smtp_password": "smtp-pass"},
            "gotify": {"api_key": "gotify-key"},
        },
        "misc": {"development": True},
    }
    masked = mask_secret_values(tree)
    assert masked["auth"]["openid_connect"]["client_secret"] == SECRET_MASK
    assert masked["notifications"]["smtp_config"]["smtp_password"] == SECRET_MASK
    assert masked["notifications"]["gotify"]["api_key"] == SECRET_MASK
    assert masked["misc"]["development"] is True
    assert tree["auth"]["openid_connect"]["client_secret"] == "real-secret"


def test_mask_secret_values_skips_empty_strings() -> None:
    tree = {"auth": {"openid_connect": {"client_secret": ""}}}
    masked = mask_secret_values(tree)
    assert masked["auth"]["openid_connect"]["client_secret"] == ""


def test_strip_masked_values_removes_only_exact_mask() -> None:
    patch = {
        "auth": {"openid_connect": {"client_secret": SECRET_MASK}},
        "misc": {"development": True},
        "notifications": {"smtp_config": {"smtp_password": "new-pass"}},
    }
    stripped = strip_masked_values(patch)
    assert "client_secret" not in stripped.get("auth", {}).get("openid_connect", {})
    assert stripped["misc"]["development"] is True
    assert stripped["notifications"]["smtp_config"]["smtp_password"] == "new-pass"


def test_strip_masked_values_preserves_non_secret_mask_literal() -> None:
    patch = {"notifications": {"subject_prefix": SECRET_MASK}}
    stripped = strip_masked_values(patch)
    assert stripped["notifications"]["subject_prefix"] == SECRET_MASK


def test_strip_masked_values_removes_secret_path_mask() -> None:
    patch = {"torrents": {"qbittorrent": {"password": SECRET_MASK, "host": "qb.local"}}}
    stripped = strip_masked_values(patch)
    assert "password" not in stripped["torrents"]["qbittorrent"]
    assert stripped["torrents"]["qbittorrent"]["host"] == "qb.local"


def test_strip_masked_values_preserves_empty_nested_dict() -> None:
    patch = {"torrents": {}}
    stripped = strip_masked_values(patch)
    assert stripped == {"torrents": {}}


def test_resolve_masked_config_leaves_non_credential_sentinel() -> None:
    config = {
        "host": "qb.local",
        "category_name": SECRET_MASK,
        "password": "explicit-pass",
    }
    effective = {
        "host": "qb.local",
        "category_name": "movies",
        "password": "stored-qb-pass",
    }
    resolved = resolve_masked_config(config, effective, ("torrents", "qbittorrent"))
    assert resolved["category_name"] == SECRET_MASK
    assert resolved["password"] == "explicit-pass"


def test_resolve_masked_config_resolves_credential_leaf() -> None:
    config = {"host": "qb.local", "password": SECRET_MASK}
    effective = {"host": "qb.local", "password": "stored-qb-pass"}
    resolved = resolve_masked_config(config, effective, ("torrents", "qbittorrent"))
    assert resolved["password"] == "stored-qb-pass"
    assert resolved["host"] == "qb.local"


def test_export_omits_all_secret_paths() -> None:
    overrides = {
        "auth": {"openid_connect": {"client_secret": "oidc-secret"}},
        "notifications": {"smtp_config": {"smtp_password": "smtp-pass"}},
        "torrents": {"qbittorrent": {"password": "qb-pass"}},
        "metadata": {"tmdb": {"api_key": "tmdb-key"}},
    }
    exported = sanitize_export_overrides(overrides)
    assert "client_secret" not in exported.get("auth", {}).get("openid_connect", {})
    assert "smtp_password" not in exported.get("notifications", {}).get(
        "smtp_config", {}
    )
    assert "password" not in exported.get("torrents", {}).get("qbittorrent", {})
    assert "api_key" not in exported.get("metadata", {}).get("tmdb", {})


@pytest.mark.parametrize("path", sorted(SECRET_OVERRIDE_PATHS))
def test_every_secret_path_is_covered_by_mask_helper(path: tuple[str, ...]) -> None:
    tree: dict = {}
    node: Any = tree
    for i, key in enumerate(path[:-1]):
        if key == LIST_PATH_WILDCARD:
            continue
        next_key = path[i + 1]
        if next_key == LIST_PATH_WILDCARD:
            item: dict = {}
            node = node.setdefault(key, [item])[0]
        else:
            node = node.setdefault(key, {})
    node[path[-1]] = "credential-value"
    masked = mask_secret_values(tree)
    leaf: Any = masked
    index = 0
    while index < len(path):
        key = path[index]
        if key == LIST_PATH_WILDCARD:
            leaf = leaf[0]
            index += 1
        elif index + 1 < len(path) and path[index + 1] == LIST_PATH_WILDCARD:
            leaf = leaf[key][0]
            index += 2
        else:
            leaf = leaf[key]
            index += 1
    assert leaf == SECRET_MASK


def test_get_settings_masks_credentials() -> None:
    repo = FakeSettingsRepository(
        overrides={
            "notifications": {"smtp_config": {"smtp_password": "super-secret"}},
            "metadata": {"tmdb": {"api_key": "tmdb-key-123"}},
        }
    )
    with settings_client(repo=repo) as (client, _repo):
        response = client.get(SETTINGS_PREFIX)
    assert response.status_code == 200
    body = response.json()
    assert body["notifications"]["smtp_config"]["smtp_password"] == SECRET_MASK
    assert body["metadata"]["tmdb"]["api_key"] == SECRET_MASK
    assert (
        body["overrides"]["notifications"]["smtp_config"]["smtp_password"]
        == SECRET_MASK
    )
    assert "super-secret" not in response.text
    assert "tmdb-key-123" not in response.text


def test_put_with_mask_sentinel_keeps_stored_secret() -> None:
    repo = FakeSettingsRepository(
        overrides={
            "notifications": {
                "smtp_config": {
                    "smtp_password": "stored-password",
                    "smtp_host": "mail.example.com",
                }
            }
        }
    )
    with settings_client(repo=repo) as (client, fake_repo):
        response = client.put(
            SETTINGS_PREFIX,
            json={
                "notifications": {
                    "smtp_config": {
                        "smtp_password": SECRET_MASK,
                        "smtp_host": "mail.updated.example.com",
                    }
                }
            },
        )
    assert response.status_code == 200
    stored = fake_repo.overrides["notifications"]["smtp_config"]
    assert stored["smtp_password"] == "stored-password"
    assert stored["smtp_host"] == "mail.updated.example.com"


def test_derived_secret_paths_are_superset_of_legacy_literals() -> None:
    assert LEGACY_SECRET_OVERRIDE_PATHS <= SECRET_OVERRIDE_PATHS


def test_every_provider_config_credential_field_is_derived() -> None:
    for provider_name, field in NativeSubtitleConfig.model_fields.items():
        if provider_name in {"enabled", "scan_interval_hours"}:
            continue
        nested = field.annotation
        assert nested is ProviderConfig, provider_name
        for credential_name in ("password", "api_key"):
            path = ("subtitles", "native", provider_name, credential_name)
            assert path in SECRET_OVERRIDE_PATHS, path


def test_native_subtitle_provider_credentials_masked_on_read() -> None:
    repo = FakeSettingsRepository(
        overrides={
            "subtitles": {
                "native": {
                    "napiprojekt": {
                        "password": "napi-pass",
                        "api_key": "napi-key",
                    }
                }
            }
        }
    )
    with settings_client(repo=repo) as (client, _repo):
        response = client.get(SETTINGS_PREFIX)
    assert response.status_code == 200
    provider = response.json()["subtitles"]["native"]["napiprojekt"]
    assert provider["password"] == SECRET_MASK
    assert provider["api_key"] == SECRET_MASK
    assert "napi-pass" not in response.text
    assert "napi-key" not in response.text


def test_replace_import_carries_forward_stored_secrets() -> None:
    repo = FakeSettingsRepository(
        overrides={
            "notifications": {
                "smtp_config": {
                    "smtp_password": "stored-password",
                    "smtp_host": "mail.example.com",
                }
            },
            "metadata": {"tmdb": {"api_key": "tmdb-key"}},
        }
    )
    exported = sanitize_export_overrides(repo.overrides)
    with settings_client(repo=repo) as (client, fake_repo):
        response = client.post(
            f"{SETTINGS_PREFIX}/import",
            json={"overrides": exported, "mode": "replace"},
        )
    assert response.status_code == 200
    assert (
        fake_repo.overrides["notifications"]["smtp_config"]["smtp_password"]
        == "stored-password"
    )
    assert fake_repo.overrides["metadata"]["tmdb"]["api_key"] == "tmdb-key"


def test_replace_import_explicit_secret_wins() -> None:
    repo = FakeSettingsRepository(
        overrides={"metadata": {"tmdb": {"api_key": "old-key"}}}
    )
    with settings_client(repo=repo) as (client, fake_repo):
        response = client.post(
            f"{SETTINGS_PREFIX}/import",
            json={
                "overrides": {"metadata": {"tmdb": {"api_key": "new-key"}}},
                "mode": "replace",
            },
        )
    assert response.status_code == 200
    assert fake_repo.overrides["metadata"]["tmdb"]["api_key"] == "new-key"


FAKE_TORZNAB_KEY = "FAKEKEY123"

TORZNAB_SITES_OVERRIDE = {
    "indexers": {
        "native": {
            "custom_torznab_sites": [
                {
                    "name": "site-one",
                    "url": "http://one.example/torznab",
                    "api_key": FAKE_TORZNAB_KEY,
                },
                {
                    "name": "site-two",
                    "url": "http://two.example/torznab",
                    "api_key": FAKE_TORZNAB_KEY,
                },
            ]
        }
    }
}


def test_custom_torznab_site_api_key_path_is_derived() -> None:
    assert (
        "indexers",
        "native",
        "custom_torznab_sites",
        LIST_PATH_WILDCARD,
        "api_key",
    ) in SECRET_OVERRIDE_PATHS


def test_get_settings_masks_custom_torznab_site_api_keys() -> None:
    repo = FakeSettingsRepository(overrides=TORZNAB_SITES_OVERRIDE)
    with settings_client(repo=repo) as (client, _repo):
        response = client.get(SETTINGS_PREFIX)
    assert response.status_code == 200
    sites = response.json()["indexers"]["native"]["custom_torznab_sites"]
    assert len(sites) == 2
    assert sites[0]["api_key"] == SECRET_MASK
    assert sites[1]["api_key"] == SECRET_MASK
    assert FAKE_TORZNAB_KEY not in response.text


def test_export_omits_custom_torznab_site_api_keys() -> None:
    exported = sanitize_export_overrides(TORZNAB_SITES_OVERRIDE)
    sites = exported["indexers"]["native"]["custom_torznab_sites"]
    assert len(sites) == 2
    assert "api_key" not in sites[0]
    assert "api_key" not in sites[1]
    assert FAKE_TORZNAB_KEY not in str(exported)


def test_put_with_mask_sentinel_keeps_custom_torznab_site_api_keys() -> None:
    repo = FakeSettingsRepository(overrides=TORZNAB_SITES_OVERRIDE)
    with settings_client(repo=repo) as (client, fake_repo):
        response = client.get(SETTINGS_PREFIX)
        assert response.status_code == 200
        sites = response.json()["indexers"]["native"]["custom_torznab_sites"]
        response = client.put(
            SETTINGS_PREFIX,
            json={
                "indexers": {
                    "native": {
                        "custom_torznab_sites": [
                            {
                                **sites[0],
                                "url": "http://one.updated.example/torznab",
                            },
                            sites[1],
                        ]
                    }
                }
            },
        )
    assert response.status_code == 200
    stored_sites = fake_repo.overrides["indexers"]["native"]["custom_torznab_sites"]
    assert stored_sites[0]["api_key"] == FAKE_TORZNAB_KEY
    assert stored_sites[1]["api_key"] == FAKE_TORZNAB_KEY
    assert stored_sites[0]["url"] == "http://one.updated.example/torznab"


def test_put_with_new_secret_updates_override() -> None:
    repo = FakeSettingsRepository(
        overrides={"notifications": {"smtp_config": {"smtp_password": "old-password"}}}
    )
    with settings_client(repo=repo) as (client, fake_repo):
        response = client.put(
            SETTINGS_PREFIX,
            json={"notifications": {"smtp_config": {"smtp_password": "new-password"}}},
        )
    assert response.status_code == 200
    assert (
        fake_repo.overrides["notifications"]["smtp_config"]["smtp_password"]
        == "new-password"
    )
