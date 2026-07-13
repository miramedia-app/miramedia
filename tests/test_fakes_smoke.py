"""Smoke test: fakes can instantiate services and exercise a read path."""

from tests.fakes import (
    FakeShowRepository,
    build_show_service,
    run_async,
)
from tests.fakes.repositories import make_show


def test_fake_show_service_instantiates_and_reads_show() -> None:
    repo = FakeShowRepository()
    show = make_show()
    repo.add_show(show)
    svc, _, _ = build_show_service(show_repo=repo)

    loaded = run_async(svc.show_repository.get_show_by_id(show_id=show.id))

    assert loaded is not None
    assert loaded.id == show.id
    assert loaded.name == "Test Show"
