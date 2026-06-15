from pydantic import BaseModel

from miramedia.movies.schemas import MovieId
from miramedia.shows.schemas import ShowId


class MetaDataProviderSearchResult(BaseModel):
    poster_path: str | None
    overview: str | None
    name: str
    external_id: str
    imdb_id: str | None = None
    year: int | None
    metadata_provider: str
    added: bool
    vote_average: float | None = None
    original_language: str | None = None
    id: MovieId | ShowId | None = None  # Internal ID if already added
    genres: list[str] | None = None
    cast: list[str] | None = None
