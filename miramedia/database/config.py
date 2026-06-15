from pydantic_settings import BaseSettings


class DbConfig(BaseSettings):
    host: str = "localhost"
    port: int = 5432
    user: str = "miramedia"
    password: str = "miramedia"  # noqa: S105
    dbname: str = "miramedia"
