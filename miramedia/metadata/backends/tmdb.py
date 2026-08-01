import logging
from typing import override

import tmdbsimple

import miramedia.metadata.utils
from miramedia.config import MiraMediaConfig
from miramedia.metadata.backends.generic import AbstractMetadataProvider
from miramedia.metadata.cache import cached
from miramedia.metadata.schemas import MetaDataProviderSearchResult
from miramedia.movies.schemas import Movie
from miramedia.notifications.manager import notification_manager
from miramedia.shows.schemas import Episode, EpisodeNumber, Season, SeasonNumber, Show


def _extract_show_content_rating(ratings_data: dict, country: str = "US") -> str | None:
    """Extract content rating from TMDB content_ratings response."""
    results = ratings_data.get("results", [])
    for entry in results:
        if entry.get("iso_3166_1") == country:
            return entry.get("rating") or None
    return results[0].get("rating") if results else None


def _extract_movie_content_rating(
    release_dates_data: dict, country: str = "US"
) -> str | None:
    """Extract content rating from TMDB release_dates response."""
    results = release_dates_data.get("results", [])
    for entry in results:
        if entry.get("iso_3166_1") == country:
            for release in entry.get("release_dates", []):
                cert = release.get("certification")
                if cert:
                    return cert
    return None


ENDED_STATUS = {"Ended", "Canceled"}

log = logging.getLogger(__name__)


class TmdbMetadataProvider(AbstractMetadataProvider):
    name = "tmdb"

    def __init__(self) -> None:
        config = MiraMediaConfig().metadata.tmdb
        tmdbsimple.API_KEY = config.api_key
        self.primary_languages = config.primary_languages
        self.default_language = config.default_language

    def __get_language_param(self, original_language: str | None) -> str:
        """
        Determine the language parameter to use for TMDB API calls.
        Returns the original language if it's in primary_languages, otherwise returns default_language.

        :param original_language: The original language code (ISO 639-1) of the media
        :return: Language parameter (ISO 639-1 format, e.g., 'en', 'no')
        """
        if original_language and original_language in self.primary_languages:
            return original_language
        return self.default_language

    def __get_show_metadata(self, show_id: int, language: str | None = None) -> dict:
        if language is None:
            language = self.default_language
        try:
            tv = tmdbsimple.TV(id=show_id)
            return tv.info(language=language)
        except Exception as e:
            log.exception(f"TMDB API error getting show metadata for ID {show_id}")
            notification_manager.send_notification(
                title="TMDB API Error",
                message=f"Failed to fetch show metadata for ID {show_id} from TMDB. Error: {e}",
            )
            raise

    def __get_show_content_ratings(self, show_id: int) -> dict:
        try:
            tv = tmdbsimple.TV(id=show_id)
            return tv.content_ratings()
        except Exception:
            log.warning(f"Failed to fetch content ratings for show {show_id}")
            return {}

    def __get_show_external_ids(self, show_id: int) -> dict:
        try:
            tv = tmdbsimple.TV(id=show_id)
            return tv.external_ids()
        except Exception as e:
            log.exception(f"TMDB API error getting show external IDs for ID {show_id}")
            notification_manager.send_notification(
                title="TMDB API Error",
                message=f"Failed to fetch show external IDs for ID {show_id} from TMDB. Error: {e}",
            )
            raise

    def __get_show_credits(self, show_id: int) -> list[str]:
        try:
            tv = tmdbsimple.TV(id=show_id)
            credits_data = tv.credits()
            cast = credits_data.get("cast", [])
            return [member["name"] for member in cast[:10]]
        except Exception:
            log.warning(f"Failed to fetch credits for show {show_id}")
            return []

    def __get_season_metadata(
        self, show_id: int, season_number: int, language: str | None = None
    ) -> dict:
        if language is None:
            language = self.default_language
        try:
            season = tmdbsimple.TV_Seasons(tv_id=show_id, season_number=season_number)
            return season.info(language=language)
        except Exception as e:
            log.exception(
                f"TMDB API error getting season {season_number} metadata for show ID {show_id}"
            )
            notification_manager.send_notification(
                title="TMDB API Error",
                message=f"Failed to fetch season {season_number} metadata for show ID {show_id} from TMDB. Error: {e}",
            )
            raise

    def __search_tv(self, query: str, page: int) -> dict:
        try:
            search = tmdbsimple.Search()
            return search.tv(query=query, page=page, language=self.default_language)
        except Exception as e:
            log.exception(f"TMDB API error searching TV shows with query '{query}'")
            notification_manager.send_notification(
                title="TMDB API Error",
                message=f"Failed to search TV shows with query '{query}' on TMDB. Error: {e}",
            )
            raise

    def __get_trending_tv(self) -> dict:
        try:
            trending = tmdbsimple.Trending(media_type="tv")
            return trending.info(language=self.default_language)
        except Exception as e:
            log.exception("TMDB API error getting trending TV")
            notification_manager.send_notification(
                title="TMDB API Error",
                message=f"Failed to fetch trending TV shows from TMDB. Error: {e}",
            )
            raise

    def __get_movie_metadata(self, movie_id: int, language: str | None = None) -> dict:
        if language is None:
            language = self.default_language
        try:
            movie = tmdbsimple.Movies(id=movie_id)
            return movie.info(language=language)
        except Exception as e:
            log.exception(f"TMDB API error getting movie metadata for ID {movie_id}")
            notification_manager.send_notification(
                title="TMDB API Error",
                message=f"Failed to fetch movie metadata for ID {movie_id} from TMDB. Error: {e}",
            )
            raise

    def __get_movie_external_ids(self, movie_id: int) -> dict:
        try:
            movie = tmdbsimple.Movies(id=movie_id)
            return movie.external_ids()
        except Exception as e:
            log.exception(
                f"TMDB API error getting movie external IDs for ID {movie_id}"
            )
            notification_manager.send_notification(
                title="TMDB API Error",
                message=f"Failed to fetch movie external IDs for ID {movie_id} from TMDB. Error: {e}",
            )
            raise

    def __get_movie_release_dates(self, movie_id: int) -> dict:
        try:
            movie = tmdbsimple.Movies(id=movie_id)
            return movie.release_dates()
        except Exception:
            log.warning(f"Failed to fetch release dates for movie {movie_id}")
            return {}

    def __get_movie_credits(self, movie_id: int) -> list[str]:
        try:
            movie = tmdbsimple.Movies(id=movie_id)
            credits_data = movie.credits()
            cast = credits_data.get("cast", [])
            return [member["name"] for member in cast[:10]]
        except Exception:
            log.warning(f"Failed to fetch credits for movie {movie_id}")
            return []

    def __search_movie(self, query: str, page: int) -> dict:
        try:
            search = tmdbsimple.Search()
            return search.movie(query=query, page=page, language=self.default_language)
        except Exception as e:
            log.exception(f"TMDB API error searching movies with query '{query}'")
            notification_manager.send_notification(
                title="TMDB API Error",
                message=f"Failed to search movies with query '{query}' on TMDB. Error: {e}",
            )
            raise

    def __get_trending_movies(self) -> dict:
        try:
            trending = tmdbsimple.Trending(media_type="movie")
            return trending.info(language=self.default_language)
        except Exception as e:
            log.exception("TMDB API error getting trending movies")
            notification_manager.send_notification(
                title="TMDB API Error",
                message=f"Failed to fetch trending movies from TMDB. Error: {e}",
            )
            raise

    @override
    def download_show_poster_image(self, show: Show) -> bool:
        # Determine which language to use based on show's original_language
        language = self.__get_language_param(show.original_language)

        # Fetch metadata in the appropriate language to get localized poster
        show_metadata = self.__get_show_metadata(
            int(show.external_id), language=language
        )

        # downloading the poster
        # all pictures from TMDB should already be jpeg, so no need to convert
        if show_metadata["poster_path"] is not None:
            poster_url = (
                "https://image.tmdb.org/t/p/original" + show_metadata["poster_path"]
            )
            if miramedia.metadata.utils.download_poster_image(
                storage_path=self.storage_path, poster_url=poster_url, uuid=show.id
            ):
                log.info("Successfully downloaded poster image for show " + show.name)
            else:
                log.warning(f"download for image of show {show.name} failed")
                return False
        else:
            log.warning(f"image for show {show.name} could not be downloaded")
            return False
        return True

    def __find_by_imdb(self, imdb_id: str) -> dict:
        """Use TMDB's /find endpoint to look up by IMDb ID."""
        try:
            find = tmdbsimple.Find(imdb_id)
            return find.info(external_source="imdb_id")
        except Exception:
            log.warning(f"TMDB find by IMDb ID failed for {imdb_id}")
            return {}

    @override
    @cached("tmdb.get_show_metadata_by_imdb")
    def get_show_metadata_by_imdb(
        self, imdb_id: str, language: str | None = None
    ) -> Show | None:
        """Look up a show by IMDb ID via TMDB's /find endpoint."""
        result = self.__find_by_imdb(imdb_id)
        tv_results = result.get("tv_results", [])
        if tv_results:
            return self.get_show_metadata(str(tv_results[0]["id"]), language=language)
        return None

    @override
    @cached("tmdb.get_movie_metadata_by_imdb")
    def get_movie_metadata_by_imdb(
        self, imdb_id: str, language: str | None = None
    ) -> Movie | None:
        """Look up a movie by IMDb ID via TMDB's /find endpoint."""
        result = self.__find_by_imdb(imdb_id)
        movie_results = result.get("movie_results", [])
        if movie_results:
            return self.get_movie_metadata(
                str(movie_results[0]["id"]), language=language
            )
        return None

    @override
    @cached("tmdb.get_show_metadata")
    def get_show_metadata(self, show_id: str, language: str | None = None) -> Show:
        """
        :param show_id: the external id of the show (TMDB numeric string)
        :param language: optional language code (ISO 639-1) to fetch metadata in
        :return: returns a Show object
        """
        tmdb_id = int(show_id)
        # If language not provided, fetch once to determine original language
        if language is None:
            show_metadata = self.__get_show_metadata(tmdb_id)
            language = show_metadata.get("original_language")

        # Determine which language to use for metadata
        language = self.__get_language_param(language)

        # Fetch show metadata in the appropriate language
        show_metadata = self.__get_show_metadata(tmdb_id, language=language)

        # get imdb id
        external_ids = self.__get_show_external_ids(show_id=tmdb_id)
        imdb_id = external_ids.get("imdb_id")

        # Extract genres from info response
        genres = [g["name"] for g in show_metadata.get("genres", [])]

        # Extract cast
        cast = self.__get_show_credits(tmdb_id)

        season_list = []
        # inserting all the metadata into the objects
        for season in show_metadata["seasons"]:
            season_metadata = self.__get_season_metadata(
                show_id=show_metadata["id"],
                season_number=season["season_number"],
                language=language,
            )
            episode_list = [
                Episode(
                    title=episode["name"],
                    number=EpisodeNumber(episode["episode_number"]),
                    air_date=miramedia.metadata.utils.parse_iso_date(
                        episode.get("air_date")
                    ),
                )
                for episode in season_metadata["episodes"]
            ]

            season_list.append(
                Season(
                    number=SeasonNumber(season_metadata["season_number"]),
                    episodes=episode_list,
                )
            )

        year = miramedia.metadata.utils.get_year_from_date(
            show_metadata["first_air_date"]
        )

        return Show(
            external_id=show_id,
            name=show_metadata["name"],
            overview=show_metadata["overview"],
            year=year,
            seasons=season_list,
            metadata_provider=self.name,
            ended=show_metadata["status"] in ENDED_STATUS,
            original_language=show_metadata.get("original_language"),
            imdb_id=imdb_id,
            vote_average=show_metadata.get("vote_average"),
            content_rating=_extract_show_content_rating(
                self.__get_show_content_ratings(tmdb_id)
            ),
            genres=genres,
            cast=cast,
        )

    @override
    @cached("tmdb.get_show_imdb_id", ttl=60 * 60 * 24)
    def get_show_imdb_id(self, external_id: str) -> str | None:
        """Resolve a show's IMDb id from its TMDB id (search omits external ids).

        One ``/tv/{id}/external_ids`` call, cached a day — the mapping is stable.
        Swallows errors so an enrichment pass can't fail an "Add vs View" check.
        """
        try:
            return self.__get_show_external_ids(int(external_id)).get("imdb_id") or None
        except Exception:
            return None

    @override
    @cached("tmdb.get_movie_imdb_id", ttl=60 * 60 * 24)
    def get_movie_imdb_id(self, external_id: str) -> str | None:
        """Movie counterpart of :meth:`get_show_imdb_id`."""
        try:
            return (
                self.__get_movie_external_ids(int(external_id)).get("imdb_id") or None
            )
        except Exception:
            return None

    @override
    @cached("tmdb.search_show", ttl=60 * 60)
    @miramedia.metadata.utils.reraise_provider_unreachable
    def search_show(
        self, query: str | None = None, skip: int = 0, max_pages: int = 5
    ) -> list[MetaDataProviderSearchResult]:
        """
        Search for shows using TMDB API.
        If no query is provided, it will return the most popular shows.
        """
        results = []
        if query is None:
            results = self.__get_trending_tv()["results"]
        else:
            for page_number in range(1, max_pages + 1):
                result_page = self.__search_tv(query=query, page=page_number)

                if not result_page["results"]:
                    break
                results.extend(result_page["results"])

        formatted_results = []
        for result in results:
            try:
                if result["poster_path"] is not None:
                    poster_url = (
                        "https://image.tmdb.org/t/p/original" + result["poster_path"]
                    )
                else:
                    poster_url = None

                # Determine which name to use based on primary_languages
                original_language = result.get("original_language")
                original_name = result.get("original_name")
                display_name = result["name"]

                overview = result["overview"]
                # Use original name if language is in primary_languages and skip overview
                if original_language and original_language in self.primary_languages:
                    display_name = original_name
                    overview = None

                formatted_results.append(
                    MetaDataProviderSearchResult(
                        poster_path=poster_url,
                        overview=overview,
                        name=display_name,
                        external_id=str(result["id"]),
                        year=miramedia.metadata.utils.get_year_from_date(
                            result["first_air_date"]
                        ),
                        metadata_provider=self.name,
                        added=False,
                        vote_average=result["vote_average"],
                        original_language=original_language,
                    )
                )
            except Exception:
                log.warning("Error processing search result", exc_info=True)
        return formatted_results

    @override
    @cached("tmdb.get_movie_metadata")
    def get_movie_metadata(self, movie_id: str, language: str | None = None) -> Movie:
        """
        Get movie metadata with language-aware fetching.

        :param movie_id: the external id of the movie (TMDB numeric string)
        :param language: optional language code (ISO 639-1) to fetch metadata in
        :return: returns a Movie object
        """
        tmdb_id = int(movie_id)
        # If language not provided, fetch once to determine original language
        if language is None:
            movie_metadata = self.__get_movie_metadata(movie_id=tmdb_id)
            language = movie_metadata.get("original_language")

        # Determine which language to use for metadata
        language = self.__get_language_param(language)

        # Fetch movie metadata in the appropriate language
        movie_metadata = self.__get_movie_metadata(movie_id=tmdb_id, language=language)

        # get imdb id
        external_ids = self.__get_movie_external_ids(movie_id=tmdb_id)
        imdb_id = external_ids.get("imdb_id")

        # Extract genres from info response
        genres = [g["name"] for g in movie_metadata.get("genres", [])]

        # Extract cast
        cast = self.__get_movie_credits(tmdb_id)

        year = miramedia.metadata.utils.get_year_from_date(
            movie_metadata["release_date"]
        )

        return Movie(
            external_id=movie_id,
            name=movie_metadata["title"],
            overview=movie_metadata["overview"],
            year=year,
            release_date=miramedia.metadata.utils.parse_iso_date(
                movie_metadata.get("release_date")
            ),
            metadata_provider=self.name,
            original_language=movie_metadata.get("original_language"),
            imdb_id=imdb_id,
            vote_average=movie_metadata.get("vote_average"),
            content_rating=_extract_movie_content_rating(
                self.__get_movie_release_dates(tmdb_id)
            ),
            runtime=movie_metadata.get("runtime"),
            genres=genres,
            cast=cast,
        )

    @override
    @cached("tmdb.search_movie", ttl=60 * 60)
    @miramedia.metadata.utils.reraise_provider_unreachable
    def search_movie(
        self, query: str | None = None, skip: int = 0, max_pages: int = 5
    ) -> list[MetaDataProviderSearchResult]:
        """
        Search for movies using TMDB API.
        If no query is provided, it will return the most popular movies.
        """
        results = []
        if query is None:
            results = self.__get_trending_movies()["results"]
        else:
            for page_number in range(1, max_pages + 1):
                result_page = self.__search_movie(query=query, page=page_number)

                if not result_page["results"]:
                    break
                results.extend(result_page["results"])

        formatted_results = []
        for result in results:
            try:
                if result["poster_path"] is not None:
                    poster_url = (
                        "https://image.tmdb.org/t/p/original" + result["poster_path"]
                    )
                else:
                    poster_url = None

                # Determine which name to use based on primary_languages
                original_language = result.get("original_language")
                original_title = result.get("original_title")
                display_name = result["title"]

                overview = result["overview"]
                # Use original name if language is in primary_languages and skip overview
                if original_language and original_language in self.primary_languages:
                    display_name = original_title
                    overview = None

                formatted_results.append(
                    MetaDataProviderSearchResult(
                        poster_path=poster_url,
                        overview=overview,
                        name=display_name,
                        external_id=str(result["id"]),
                        year=miramedia.metadata.utils.get_year_from_date(
                            result["release_date"]
                        ),
                        metadata_provider=self.name,
                        added=False,
                        vote_average=result["vote_average"],
                        original_language=original_language,
                    )
                )
            except Exception:
                log.warning("Error processing search result", exc_info=True)
        return formatted_results

    @override
    def download_movie_poster_image(self, movie: Movie) -> bool:
        # Determine which language to use based on movie's original_language
        language = self.__get_language_param(movie.original_language)

        # Fetch metadata in the appropriate language to get localized poster
        movie_metadata = self.__get_movie_metadata(
            movie_id=int(movie.external_id), language=language
        )

        # downloading the poster
        # all pictures from TMDB should already be jpeg, so no need to convert
        if movie_metadata["poster_path"] is not None:
            poster_url = (
                "https://image.tmdb.org/t/p/original" + movie_metadata["poster_path"]
            )
            if miramedia.metadata.utils.download_poster_image(
                storage_path=self.storage_path, poster_url=poster_url, uuid=movie.id
            ):
                log.info("Successfully downloaded poster image for movie " + movie.name)
            else:
                log.warning(f"download for image of movie {movie.name} failed")
                return False
        else:
            log.warning(f"image for movie {movie.name} could not be downloaded")
            return False
        return True
