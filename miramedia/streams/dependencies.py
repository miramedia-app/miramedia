from fastapi import HTTPException, status

from miramedia.config import MiraMediaConfig


def require_streaming_enabled() -> None:
    """Gate all streaming endpoints on the ``streams.enabled`` config flag.

    The router is mounted unconditionally so its schemas always appear in
    the generated OpenAPI spec; this dependency enforces that the feature
    is actually active before any request hits a handler.
    """
    if not MiraMediaConfig().streams.enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Streaming feature is disabled",
        )


def require_stream_or_download_access() -> None:
    """Gate subtitle endpoints when neither streaming nor downloads is enabled."""
    config = MiraMediaConfig()
    if not config.streams.enabled and not config.streams.downloads:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Streaming and downloads features are disabled",
        )


def require_stream_or_download_enabled(download: bool) -> None:
    """Gate the direct-file endpoints on the flag matching the request kind.

    Streaming and downloads are independent switches sharing these endpoints:
    ``?download=true`` requests need ``streams.downloads``, plain streaming
    requests need ``streams.enabled``.
    """
    if download:
        if not MiraMediaConfig().streams.downloads:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Downloads feature is disabled",
            )
    else:
        require_streaming_enabled()
