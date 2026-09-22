from pathlib import Path

from pydantic import model_validator
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

    # --- Remote MCP over HTTP (CMDB_MCP_*) ---------------------------------
    #
    # Everything here defaults to off/None, so a fresh clone gets exactly
    # today's behaviour: `cmdb mcp` over stdio, and /mcp returning 404 from the
    # web app. Turning it on is a deployment decision made in a local, never
    # committed, docker-compose.override.yml -- this repo is public and must
    # carry no hostname, issuer or client id.
    #
    # The endpoint is served by `cmdb serve`, not by `cmdb mcp`: the Authentik
    # outpost's internal_host is a single origin, so the MCP routes have to live
    # in the same FastAPI app as the UI. `cmdb mcp` stays stdio-only.
    mcp_remote_enabled: bool = False
    # Authentik's per-provider issuer, trailing slash included, compared to the
    # token's `iss` as an exact string.
    mcp_issuer_url: str | None = None
    mcp_jwks_url: str | None = None
    # Authentik sets `aud` to the provider's client_id. Checking it is OURS to
    # do: the SDK's RequireAuthMiddleware enforces only required_scopes and
    # never looks at `aud` or `resource`, so without this a token minted for
    # ANY application on the same Authentik would verify here.
    mcp_audience: str | None = None
    # The RFC 9728 resource identifier -- the public URL of the endpoint, with
    # no trailing slash. The SDK builds both the metadata document's path and
    # the WWW-Authenticate challenge from this, so it must be the public origin
    # even though the outpost reaches us on 172.17.0.1.
    mcp_resource_url: str | None = None
    # Optional fallback for the groups claim. Authentik's userinfo endpoint is
    # global, not per-provider.
    mcp_userinfo_url: str | None = None
    # Comma-separated, not list[str]: pydantic-settings JSON-decodes complex
    # types from the environment, which would force
    # CMDB_MCP_ALLOWED_HOSTS='["a","b"]' in a compose file. Plain CSV is what a
    # human actually wants to write there.
    mcp_allowed_hosts: str = ""
    mcp_allowed_origins: str = ""
    mcp_required_groups: str = "homelab-admins"
    mcp_clock_skew_seconds: int = 60
    mcp_jwks_cache_seconds: int = 300
    mcp_userinfo_cache_seconds: int = 60
    mcp_http_timeout: float = 5.0

    @staticmethod
    def _csv(raw: str) -> list[str]:
        return [item.strip() for item in raw.split(",") if item.strip()]

    @property
    def mcp_allowed_hosts_list(self) -> list[str]:
        return self._csv(self.mcp_allowed_hosts)

    @property
    def mcp_allowed_origins_list(self) -> list[str]:
        return self._csv(self.mcp_allowed_origins)

    @property
    def mcp_required_groups_set(self) -> frozenset[str]:
        return frozenset(self._csv(self.mcp_required_groups))

    @model_validator(mode="after")
    def _validate_mcp_remote(self) -> "Settings":
        """Refuse to boot rather than serve /mcp with a verifier that cannot verify.

        /mcp is exempt from the Authentik proxy that guards the rest of this
        app, so the token checks in cmdb.mcp.auth are the only thing between
        the fleet's data and the internet. A half-configured verifier must not
        be allowed to degrade into a running app with an open endpoint -- so
        this raises at construction, which fails the import of cmdb.config and
        kills every entrypoint. A loud restart loop is the correct failure.
        """
        if not self.mcp_remote_enabled:
            return self
        missing = [
            name
            for name in (
                "mcp_issuer_url",
                "mcp_jwks_url",
                "mcp_audience",
                "mcp_resource_url",
            )
            if not getattr(self, name)
        ]
        if missing:
            raise ValueError(
                "mcp_remote_enabled is set but "
                + ", ".join(f"CMDB_{name.upper()}" for name in missing)
                + " is unset; refusing to serve /mcp without a working verifier"
            )
        if self.mcp_resource_url.endswith("/"):
            # A trailing slash moves the metadata document to
            # /.well-known/oauth-protected-resource/mcp/ -- a different route,
            # which the outpost carve-out still exempts but no client looks for,
            # and the WWW-Authenticate pointer would lead nowhere.
            raise ValueError("CMDB_MCP_RESOURCE_URL must not end in '/'")
        if not self.mcp_issuer_url.endswith("/"):
            # Authentik's per-provider issuer carries a trailing slash and `iss`
            # is compared as an exact string; dropping it rejects every token.
            raise ValueError("CMDB_MCP_ISSUER_URL must end in '/' (Authentik's per-provider issuer does)")
        if not self.mcp_allowed_hosts_list:
            # DNS-rebinding protection is on, and an empty allowlist rejects
            # every request with a generic error -- a failure that looks like a
            # routing bug and takes a long time to find.
            raise ValueError("CMDB_MCP_ALLOWED_HOSTS must list at least one host")
        return self

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
