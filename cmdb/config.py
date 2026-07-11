from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root (cmdb/config.py -> repo). Used to anchor relative paths so startup
# is independent of the current working directory (e.g. the MCP server launched
# via `uv run --project <repo> cmdb mcp` from an arbitrary cwd).
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CMDB_")

    db_path: str = "./cmdb.db"
    host: str = "0.0.0.0"
    port: int = 8080
    secret_key: str = "change-me-in-production"

    # A host whose facts are at least this old counts as stale on the dashboard.
    stale_days: int = 7

    # A mount at or above this used-space percentage appears in the dashboard
    # storage warnings.
    storage_warn_pct: int = 85

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
        # Resolve a relative db_path against the repo root, not the cwd, so the
        # DB lands in the same place regardless of where the process is launched.
        # An absolute CMDB_DB_PATH is honored unchanged.
        path = Path(self.db_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return f"sqlite:///{path}"


settings = Settings()
