from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CMDB_")

    db_path: str = "./cmdb.db"
    host: str = "0.0.0.0"
    port: int = 8080
    secret_key: str = "change-me-in-production"

    # On-demand collection (agentless, via the `ansible` binary over SSH).
    # SSH user / key may also be set as inventory vars; these are convenience overrides.
    ansible_inventory: str | None = None
    ansible_user: str | None = None
    ssh_private_key: str | None = None
    # ansible_ssh_common_args for DB-generated inventories. None -> a sane default is
    # applied at generation time; an explicit empty string disables it.
    ansible_ssh_args: str | None = None

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"


settings = Settings()
