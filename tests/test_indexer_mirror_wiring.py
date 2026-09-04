"""The live failover mirror list must come from the DB list (UI-managed),
not the hardcoded class seed."""

from __future__ import annotations

from miramedia.indexers.sites.nyaa import NyaaSite


def test_mirror_list_defaults_to_class_seed() -> None:
    site = NyaaSite()

    mirrors = site._mirror_list()

    # url first, then the seeded class mirrors (deduped).
    assert mirrors[0] == "https://nyaa.si"
    assert "https://nyaa.land" in mirrors


def test_db_override_drives_live_mirror_list() -> None:
    site = NyaaSite()
    # Simulate what the native backend does with the DB (UI-edited) column:
    # user removed the seed mirrors and added a custom one.
    site.mirror_urls = ["https://nyaa.si", "https://my-mirror.example"]

    mirrors = site._mirror_list()

    assert mirrors == ("https://nyaa.si", "https://my-mirror.example")
    # A seed mirror the user removed must no longer be tried.
    assert "https://nyaa.land" not in mirrors


def test_failover_prefers_first_working_db_mirror() -> None:
    site = NyaaSite()
    site.url = "https://dead.example"
    site.mirror_urls = ["https://dead.example", "https://live.example"]
    calls: list[str] = []

    def fake_fetch(url: str, params: dict | None = None) -> str:  # noqa: ARG001
        calls.append(url)
        if url.startswith("https://dead.example"):
            msg = "connection refused"
            raise ConnectionError(msg)
        return "<ok/>"

    result = site._fetch_over_mirrors("/", fetch=fake_fetch)

    assert result == "<ok/>"
    assert calls == ["https://dead.example/", "https://live.example/"]
    # The working mirror is promoted for the next search.
    assert site._get_mirror_pref().ordered()[0] == "https://live.example"
