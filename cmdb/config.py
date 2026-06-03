from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CMDB_")

    db_path: str = "./cmdb.db"
    host: str = "0.0.0.0"
    port: int = 8080
    secret_key: str = "change-me-in-production"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"


settings = Settings()
