"""Custom subtitle providers for MiraMedia.

Registers extra providers not bundled with subliminal (yifysubtitles, subdl, subsource).
"""

from subliminal.extensions import provider_manager

provider_manager.register(
    "yifysubtitles = miramedia.subtitles.providers.yifysubtitles:YifySubtitlesProvider"
)
provider_manager.register("subdl = miramedia.subtitles.providers.subdl:SubDLProvider")
provider_manager.register(
    "subsource = miramedia.subtitles.providers.subsource:SubsourceProvider"
)
