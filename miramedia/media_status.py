from enum import StrEnum


class MediaStatus(StrEnum):
    skipped = "skipped"
    wanted = "wanted"
    downloaded = "downloaded"
