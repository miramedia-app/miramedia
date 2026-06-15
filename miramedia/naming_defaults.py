"""Default media naming templates.

These defaults preserve MiraMedia's historical on-disk layout while making
the formats configurable through system settings.
"""

DEFAULT_MOVIE_FOLDER_FORMAT = "{title} ({year}) {provider_tag}"
DEFAULT_SHOW_FOLDER_FORMAT = "{title} ({year}) {provider_tag}"
DEFAULT_SEASON_FOLDER_FORMAT = "Season {season_number}"
DEFAULT_MOVIE_FILE_FORMAT = "{title} ({year}){suffix}"
DEFAULT_EPISODE_FILE_FORMAT = (
    "{show_title} S{season_number:02d}E{episode_number:02d}{suffix}"
)
