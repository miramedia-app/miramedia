from pydantic_settings import BaseSettings


class PlaybackConfig(BaseSettings):
    continue_watching: bool = True  # Dashboard Continue Watching row
