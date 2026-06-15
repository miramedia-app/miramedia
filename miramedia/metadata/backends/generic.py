import logging
from abc import ABC, abstractmethod

from miramedia.config import MiraMediaConfig
from miramedia.metadata.schemas import MetaDataProviderSearchResult
from miramedia.movies.schemas import Movie
from miramedia.shows.schemas import Show

log = logging.getLogger(__name__)


class AbstractMetadataProvider(ABC):
    storage_path = MiraMediaConfig().misc.image_directory

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def get_show_metadata(self, show_id: str, language: str | None = None) -> Show:
        raise NotImplementedError()

    @abstractmethod
    def get_movie_metadata(self, movie_id: str, language: str | None = None) -> Movie:
        raise NotImplementedError()

    def get_show_metadata_by_imdb(
        self, imdb_id: str, language: str | None = None
    ) -> Show | None:
        """Fetch show metadata by IMDb ID. Returns None if not supported or not found."""
        return None

    def get_movie_metadata_by_imdb(
        self, imdb_id: str, language: str | None = None
    ) -> Movie | None:
        """Fetch movie metadata by IMDb ID. Returns None if not supported or not found."""
        return None

    def get_show_imdb_id(self, external_id: str) -> str | None:
        """Resolve the IMDb id for a show search result.

        Search payloads from some providers (TMDB, TVDB) omit external ids, so
        the search results they return carry ``imdb_id=None``. That breaks the
        "Add vs View" cross-provider match against IMDb-keyed (native/scan)
        library rows. Providers that can cheaply resolve it override this; the
        default (and providers whose search already carries imdb_id) return
        None.
        """
        return None

    def get_movie_imdb_id(self, external_id: str) -> str | None:
        """Movie counterpart of :meth:`get_show_imdb_id`."""
        return None

    @abstractmethod
    def search_show(
        self, query: str | None = None, skip: int = 0
    ) -> list[MetaDataProviderSearchResult]:
        raise NotImplementedError()

    @abstractmethod
    def search_movie(
        self, query: str | None = None, skip: int = 0
    ) -> list[MetaDataProviderSearchResult]:
        raise NotImplementedError()

    @abstractmethod
    def download_show_poster_image(self, show: Show) -> bool:
        raise NotImplementedError()

    @abstractmethod
    def download_movie_poster_image(self, movie: Movie) -> bool:
        raise NotImplementedError()
