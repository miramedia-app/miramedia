from pydantic_settings import BaseSettings


class StreamsConfig(BaseSettings):
    enabled: bool = True  # Master switch for in-app playback/streaming
    downloads: bool = True  # Allow downloading media files from the player
    hls_cache_max_gb: float = 20.0
    hls_cache_max_age_days: int = 30
