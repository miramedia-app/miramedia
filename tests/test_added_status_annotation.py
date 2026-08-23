"""Regression tests for the search-result "Add" vs "View" annotation.

Native-provider (and thus scan-imported) shows/movies store the IMDb id in
``external_id`` and leave the ``imdb_id`` column NULL, while their folder is
still tagged ``[imdb-tt...]``. The old annotation queried only the ``imdb_id``
column, so those rows were wrongly flagged as not-added ("Add" instead of
"View"). ``_annotate_added_status`` now mirrors the scan's three-way match.

These exercise the pure matching logic against a stub repository — no DB.
"""

import asyncio

from miramedia.metadata.schemas import MetaDataProviderSearchResult
from miramedia.movies.service import MovieService
from miramedia.shows.service import ShowService


def _result(
    *, external_id, imdb_id, provider, name="X"
) -> MetaDataProviderSearchResult:
    return MetaDataProviderSearchResult(
        poster_path=None,
        overview=None,
        name=name,
        external_id=external_id,
        imdb_id=imdb_id,
        year=2020,
        metadata_provider=provider,
        added=False,
    )


class _StubShowRepo:
    """Returns ``(imdb_id, external_id, metadata_provider, id)`` rows like the
    real ``shows_existing_by_identifiers``, but from an in-memory library."""

    def __init__(self, library):
        self.library = library

    async def shows_existing_by_identifiers(self, imdb_ids, provider_keys):
        imdb_set = set(imdb_ids)
        key_set = set(provider_keys)
        out = []
        for imdb_id, ext, prov, sid in self.library:
            if (
                (imdb_id is not None and imdb_id in imdb_set)
                or ext in imdb_set
                or (ext, prov) in key_set
            ):
                out.append((imdb_id, ext, prov, sid))
        return out


class _StubMovieRepo(_StubShowRepo):
    async def movies_existing_by_identifiers(self, imdb_ids, provider_keys):
        return await self.shows_existing_by_identifiers(imdb_ids, provider_keys)


def test_annotate_does_not_mutate_input_results():
    repo = _StubShowRepo([(None, "tt1234567", "native", "show-id-1")])
    svc = ShowService(repo, None, None, None)
    original = _result(external_id="tt1234567", imdb_id="tt1234567", provider="native")
    results = [original]

    [annotated] = asyncio.run(svc._annotate_added_status(results))

    assert annotated is not original
    assert annotated.added is True
    assert annotated.id == "show-id-1"
    assert original.added is False
    assert original.id is None


def test_native_show_imdb_in_external_id_marks_added():
    # Library row: native/scan import — IMDb id lives in external_id, imdb_id NULL.
    repo = _StubShowRepo([(None, "tt1234567", "native", "show-id-1")])
    svc = ShowService(repo, None, None, None)
    # Cinemeta-style discovery result carries imdb_id == external_id.
    results = [_result(external_id="tt1234567", imdb_id="tt1234567", provider="native")]

    [annotated] = asyncio.run(svc._annotate_added_status(results))

    assert annotated.added is True
    assert annotated.id == "show-id-1"


def test_provider_key_marks_added_when_result_lacks_imdb():
    # Library row added via TMDB; TMDB search results carry no imdb_id.
    repo = _StubShowRepo([("tt9999999", "77169", "tmdb", "show-id-2")])
    svc = ShowService(repo, None, None, None)
    results = [_result(external_id="77169", imdb_id=None, provider="tmdb")]

    [annotated] = asyncio.run(svc._annotate_added_status(results))

    assert annotated.added is True
    assert annotated.id == "show-id-2"


def test_unrelated_result_not_marked_added():
    repo = _StubShowRepo([(None, "tt1234567", "native", "show-id-1")])
    svc = ShowService(repo, None, None, None)
    results = [_result(external_id="tt7654321", imdb_id="tt7654321", provider="native")]

    [annotated] = asyncio.run(svc._annotate_added_status(results))

    assert annotated.added is False
    assert annotated.id is None


def test_native_movie_imdb_in_external_id_marks_added():
    repo = _StubMovieRepo([(None, "tt2222222", "native", "movie-id-1")])
    svc = MovieService(repo, None, None, None)
    results = [_result(external_id="tt2222222", imdb_id="tt2222222", provider="native")]

    [annotated] = asyncio.run(svc._annotate_added_status(results))

    assert annotated.added is True
    assert annotated.id == "movie-id-1"
