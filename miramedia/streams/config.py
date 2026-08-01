from pydantic_settings import BaseSettings


class StreamsConfig(BaseSettings):
    hls_cache_max_gb: float = 20.0
    hls_cache_max_age_days: int = 30
