import logging
from typing import override

import tvdb_v4_official

import miramedia.metadata.utils
from miramedia.config import MiraMediaConfig
from miramedia.metadata.backends.generic import AbstractMetadataProvider
from miramedia.metadata.cache import cached
from miramedia.metadata.schemas import MetaDataProviderSearchResult
from miramedia.movies.schemas import Movie
from miramedia.shows.schemas import Episode, Season, SeasonNumber, Show

log = logging.getLogger(__name__)


class TvdbMetadataProvider(AbstractMetadataProvider):
    name = "tvdb"

    def __init__(self) -> None:
        config = MiraMediaConfig().metadata.tvdb
        try:
            self.client = tvdb_v4_official.TVDB(config.api_key)
        except Exception:
            log.exception("Failed to initialize TVDB client — check your API key")
            raise

    def __get_show(self, show_id: int) -> dict:
        try:
            return self.client.get_series_extended(show_id)
        except Exception:
            log.exception(f"TVDB API error getting show metadata for ID {show_id}")
            raise

    def __get_season(self, season_id: int) -> dict:
        try:
            return self.client.get_season_extended(season_id)
        except Exception:
            log.exception(f"TVDB API error getting season metadata for ID {season_id}")
            raise

    def __search(self, query: str) -> list[dict]:
        try:
            return self.client.search(query)
        except Exception:
            log.exception(f"TVDB API error searching with query '{query}'")
            raise

    def __get_movie(self, movie_id: int) -> dict:
        try:
            return self.client.get_movie_extended(movie_id)
        except Exception:
            log.exception(f"TVDB API error getting movie metadata for ID {movie_id}")
            raise

    def __get_trending_tv(self) -> list[dict]:
        try:
            results = self.client.get_all_series()
            return results[:20] if results else []
        except Exception:
            log.exception("TVDB API error getting trending TV shows")
            raise

    def __get_trending_movies(self) -> list[dict]:
        try:
            results = self.client.get_all_movies()
            return results[:20] if results else []
        except Exception:
            log.exception("TVDB API error getting trending movies")
            raise

    @staticmethod
    def __extract_imdb_id(data: dict) -> str | None:
        """Extract IMDb ID from remoteIds where type == 2."""
        remote_ids = data.get("remoteIds")
        if remote_ids:
            for remote_id in remote_ids:
                if remote_id.get("type") == 2:
                    return remote_id.get("id")
        return None

    @staticmethod
    def __extract_genres(data: dict) -> list[str]:
        """Extract genre names from extended data."""
        genres = data.get("genres")
        if genres:
            return [g["name"] for g in genres if "name" in g]
        return []

    @staticmethod
    def __extract_cast(data: dict) -> list[str]:
        """Extract character/actor names from extended data."""
        characters = data.get("characters")
        if characters:
            return [c["name"] for c in characters if "name" in c]
        return []

    @override
    def download_show_poster_image(self, show: Show) -> bool:
        show_metadata = self.__get_show(show_id=int(show.external_id))

        if show_metadata.get("image") is not None:
            miramedia.metadata.utils.download_poster_image(
                storage_path=self.storage_path,
                poster_url=show_metadata["image"],
                uuid=show.id,
            )
            log.debug("Successfully downloaded poster image for show " + show.name)
            return True
        log.warning(f"image for show {show.name} could not be downloaded")
        return False

    @override
    @cached("tvdb.get_show_metadata_by_imdb")
    def get_show_metadata_by_imdb(
        self, imdb_id: str, language: str | None = None
    ) -> Show | None:
        """Look up a show by IMDb ID via TVDB search."""
        try:
            results = self.client.search(imdb_id, type="series")
            if results:
                tvdb_id = results[0].get("tvdb_id") or results[0].get("id")
                if tvdb_id:
                    return self.get_show_metadata(str(tvdb_id), language=language)
        except Exception:
            log.warning(f"TVDB IMDb lookup failed for show {imdb_id}")
        return None

    @override
    @cached("tvdb.get_movie_metadata_by_imdb")
    def get_movie_metadata_by_imdb(
        self, imdb_id: str, language: str | None = None
    ) -> Movie | None:
        """Look up a movie by IMDb ID via TVDB search."""
        try:
            results = self.client.search(imdb_id, type="movie")
            if results:
                tvdb_id = results[0].get("tvdb_id") or results[0].get("id")
                if tvdb_id:
                    return self.get_movie_metadata(str(tvdb_id), language=language)
        except Exception:
            log.warning(f"TVDB IMDb lookup failed for movie {imdb_id}")
        return None

    @override
    @cached("tvdb.get_show_metadata")
    def get_show_metadata(self, show_id: str, language: str | None = None) -> Show:
        """
        :param show_id: The external id of the show (TVDB numeric string)
        :param language: does nothing, TVDB does not support multiple languages
        """
        tvdb_id = int(show_id)
        series = self.__get_show(tvdb_id)
        seasons = []
        seasons_ids = [season["id"] for season in series["seasons"]]

        # get imdb id from remote ids
        imdb_id = self.__extract_imdb_id(series)

        # Extract genres and cast from extended data
        genres = self.__extract_genres(series)
        cast = self.__extract_cast(series)

        for season_id in seasons_ids:
            s = self.__get_season(season_id=season_id)
            # the seasons need to be filtered to a certain type,
            # otherwise the same season will be imported in aired and dvd order,
            # which causes duplicate season number + show ids which in turn violates a unique constraint of the season table
            if s["type"]["id"] != 1:
                log.info(
                    f"Season {s['type']['id']} will not be downloaded because it is not a 'aired order' season"
                )
                continue

            episodes = [
                Episode(
                    number=episode["number"],
                    title=episode["name"],
                    air_date=miramedia.metadata.utils.parse_iso_date(
                        episode.get("aired")
                    ),
                )
                for episode in s["episodes"]
            ]
            seasons.append(
                Season(
                    number=SeasonNumber(s["number"]),
                    episodes=episodes,
                )
            )

        return Show(
            name=series["name"],
            overview=series["overview"],
            year=series.get("year"),
            external_id=str(series["id"]),
            metadata_provider=self.name,
            seasons=seasons,
            ended=False,
            imdb_id=imdb_id,
            genres=genres,
            cast=cast,
        )

    @override
    @cached("tvdb.search_show", ttl=60 * 60)
    @miramedia.metadata.utils.reraise_provider_unreachable
    def search_show(
        self, query: str | None = None, skip: int = 0
    ) -> list[MetaDataProviderSearchResult]:
        if query:
            results = self.__search(query=query)
            formatted_results = []
            for result in results:
                try:
                    if result["type"] == "series":
                        try:
                            year = result["year"]
                        except KeyError:
                            year = None

                        formatted_results.append(
                            MetaDataProviderSearchResult(
                                poster_path=result.get("image_url"),
                                overview=result.get("overview"),
                                name=result["name"],
                                external_id=str(result["tvdb_id"]),
                                year=year,
                                metadata_provider=self.name,
                                added=False,
                                vote_average=None,
                            )
                        )
                except Exception:
                    log.warning("Error processing search result", exc_info=True)
            return formatted_results
        results = self.__get_trending_tv()
        formatted_results = []
        for result in results:
            try:
                if result.get("type") == "series":
                    try:
                        year = result["year"]
                    except KeyError:
                        year = None

                    formatted_results.append(
                        MetaDataProviderSearchResult(
                            poster_path="https://artworks.thetvdb.com"
                            + result.get("image")
                            if result.get("image")
                            else None,
                            overview=result.get("overview"),
                            name=result["name"],
                            external_id=str(result["id"]),
                            year=year,
                            metadata_provider=self.name,
                            added=False,
                            vote_average=None,
                        )
                    )
            except Exception:
                log.warning("Error processing search result", exc_info=True)
        return formatted_results

    @override
    @cached("tvdb.search_movie", ttl=60 * 60)
    @miramedia.metadata.utils.reraise_provider_unreachable
    def search_movie(
        self, query: str | None = None, skip: int = 0
    ) -> list[MetaDataProviderSearchResult]:
        if query:
            results = self.__search(query=query)
            results = results[:20]
            log.debug("got %d results from TVDB search", len(results))
            formatted_results = []
            for result in results:
                if result["type"] != "movie":
                    continue

                movie_data = self.__get_movie(result["tvdb_id"])

                try:
                    try:
                        year = movie_data["year"]
                    except KeyError:
                        year = None

                    formatted_results.append(
                        MetaDataProviderSearchResult(
                            poster_path=movie_data.get("image_url"),
                            overview=movie_data.get("overview"),
                            name=movie_data["name"],
                            external_id=str(
                                movie_data.get("tvdb_id", movie_data.get("id"))
                            ),
                            year=year,
                            metadata_provider=self.name,
                            added=False,
                            vote_average=None,
                        )
                    )
                except Exception:
                    log.warning("Error processing search result", exc_info=True)
            return formatted_results
        results = self.__get_trending_movies()
        log.debug("got %d results from TVDB trending", len(results))
        formatted_results = []
        for result in results:
            movie_data = self.__get_movie(result["id"])
            try:
                try:
                    year = movie_data["year"]
                except KeyError:
                    year = None

                if movie_data.get("image"):
                    poster_path = "https://artworks.thetvdb.com" + str(
                        movie_data.get("image")
                    )
                else:
                    poster_path = None

                formatted_results.append(
                    MetaDataProviderSearchResult(
                        poster_path=poster_path if movie_data.get("image") else None,
                        overview=movie_data.get("overview"),
                        name=movie_data["name"],
                        external_id=str(movie_data["id"]),
                        year=year,
                        metadata_provider=self.name,
                        added=False,
                        vote_average=None,
                    )
                )
            except Exception:
                log.warning("Error processing search result", exc_info=True)
        return formatted_results

    @override
    def download_movie_poster_image(self, movie: Movie) -> bool:
        movie_metadata = self.__get_movie(int(movie.external_id))

        if movie_metadata.get("image") is not None:
            miramedia.metadata.utils.download_poster_image(
                storage_path=self.storage_path,
                poster_url=movie_metadata["image"],
                uuid=movie.id,
            )
            log.info("Successfully downloaded poster image for movie " + movie.name)
            return True
        log.warning(f"image for movie {movie.name} could not be downloaded")
        return False

    @override
    @cached("tvdb.get_movie_metadata")
    def get_movie_metadata(self, movie_id: str, language: str | None = None) -> Movie:
        """
        :param movie_id: the external id of the movie (TVDB numeric string)
        :param language: does nothing, TVDB does not support multiple languages
        :return: returns a Movie object
        """
        tvdb_id = int(movie_id)
        movie = self.__get_movie(movie_id=tvdb_id)

        # get imdb id from remote ids
        imdb_id = self.__extract_imdb_id(movie)

        # Extract genres and cast from extended data
        genres = self.__extract_genres(movie)
        cast = self.__extract_cast(movie)

        # TVDB exposes a list of release dates with type metadata. Grab the
        # first one if present; the field is purely advisory.
        release_iso: str | None = None
        releases = movie.get("releases") or []
        if releases:
            release_iso = releases[0].get("date")

        return Movie(
            name=movie["name"],
            overview="Overviews are not supported with TVDB",
            year=movie.get("year"),
            release_date=miramedia.metadata.utils.parse_iso_date(release_iso),
            external_id=str(movie["id"]),
            metadata_provider=self.name,
            imdb_id=imdb_id,
            genres=genres,
            cast=cast,
        )
