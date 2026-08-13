from pydantic_settings import BaseSettings


class QbittorrentConfig(BaseSettings):
    host: str = "localhost"
    port: int = 8080
    username: str = "admin"
    password: str = "admin"  # noqa: S105
    enabled: bool = False

    category_name: str = "MiraMedia"
    category_save_path: str = ""  # e.g."/data/torrents/miramedia", must match the completed download path, but from QBittorrent's container


class TransmissionConfig(BaseSettings):
    path: str = "/transmission/rpc"
    https_enabled: bool = True
    host: str = "localhost"
    port: int = 9091
    username: str = ""
    password: str = ""
    enabled: bool = False


class SabnzbdConfig(BaseSettings):
    host: str = "localhost"
    port: int = 8080
    api_key: str = ""
    enabled: bool = False
    base_path: str = "/api"
    verify_tls: bool = True


class NativeTorrentConfig(BaseSettings):
    enabled: bool = False
    # Must match the host port published in docker-compose (6881 tcp+udp).
    # Dev compose overrides this to 6891 via env to dodge the bundled
    # qbittorrent's 6881 mapping under the external/all profile.
    listen_port_start: int = 6881
    max_download_rate: int = 0  # 0 = unlimited, KB/s
    max_upload_rate: int = 0  # 0 = unlimited, KB/s


class TorrentConfig(BaseSettings):
    qbittorrent: QbittorrentConfig = QbittorrentConfig()
    transmission: TransmissionConfig = TransmissionConfig()
    sabnzbd: SabnzbdConfig = SabnzbdConfig()
    native: NativeTorrentConfig = NativeTorrentConfig()
