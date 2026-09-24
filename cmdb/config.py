import importlib.util
import os
import secrets
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root (cmdb/config.py -> repo). Used to anchor relative paths so startup
# is independent of the current working directory (e.g. the MCP server launched
# via `uv run --project <repo> cmdb mcp` from an arbitrary cwd).
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The placeholder this repo ships. Never used to sign anything: see
# Settings.resolved_secret_key().
DEFAULT_SECRET_KEY = "change-me-in-production"

# CMDB_AUTH_MODE is a *set*, so a login page can offer more than one door.
# `none` and `proxy` are whole-app postures and compose with nothing; `local`
# and `oidc` are login-page options and compose with each other.
AUTH_MODES = frozenset({"none", "local", "oidc", "proxy"})
EXCLUSIVE_AUTH_MODES = frozenset({"none", "proxy"})

# Modes that authenticate once and then remember it in a signed cookie. `proxy`
# is absent deliberately: it re-reads identity from the reverse proxy's headers
# on every request, so it needs neither a cookie nor a signing key.
SESSION_AUTH_MODES = frozenset({"local", "oidc"})

# Imported lazily by cmdb/web/auth/oidc.py, so a clone that never enables OIDC
# never needs them. Checked at Settings construction when `oidc` IS enabled --
# see _validate_auth for why that failure has to name the group.
OIDC_MODULES = ("httpx", "jwt", "cryptography")


def _missing_oidc_modules() -> list[str]:
    """Which of the `oidc` dependency group's modules are absent.

    A module-level function rather than a method so tests can substitute it
    without installing or uninstalling anything.
    """
    return [name for name in OIDC_MODULES if importlib.util.find_spec(name) is None]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CMDB_")

    db_path: str = "./cmdb.db"
    host: str = "0.0.0.0"
    port: int = 8080
    secret_key: str = DEFAULT_SECRET_KEY

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

    # --- Authentication (CMDB_AUTH_*, CMDB_OIDC_*, CMDB_SESSION_*) ---------
    #
    # Defaults are the public-safe ones: a fresh `docker compose up` gets a real
    # login page, not an open inventory UI. Comma-separated so one login page can
    # offer local passwords AND a federated button -- see AUTH_MODES.
    auth_mode: str = "local"

    # Signed-cookie session. 12h matches the Authentik session this deployment
    # already runs, so a lapse looks the same to the user either way.
    session_cookie_name: str = "cmdb_session"
    session_max_age: int = 43200
    # Set false ONLY for a plain-http localhost run. True is unambiguously right
    # on the live origin: HSTS went zone-wide on 2026-09-25 (max-age=2592000), so
    # that host is HTTPS-only in any browser that has ever seen it.
    session_cookie_secure: bool = True

    # Empty means "any authenticated user". A non-empty CSV means the principal's
    # groups must intersect it -- the app-side equivalent of the outpost's
    # group binding, which keeps working when the app is reached directly.
    auth_required_groups: str = ""

    # --- proxy mode --------------------------------------------------------
    # Header names are configurable so the same code serves oauth2-proxy,
    # Traefik forward-auth and Cloudflare Access. Defaults are Authentik's.
    auth_proxy_user_header: str = "X-authentik-username"
    auth_proxy_email_header: str = "X-authentik-email"
    auth_proxy_groups_header: str = "X-authentik-groups"
    # Authentik joins group names with a pipe, not a comma -- a comma would split
    # any group whose name contains one.
    auth_proxy_groups_separator: str = "|"

    # --- local mode: brute-force backoff -----------------------------------
    # In-memory, per username, no new table. Mirrors the intent of the Authentik
    # ReputationPolicy on the live path without pretending to replace it.
    auth_max_login_failures: int = 5
    auth_lockout_seconds: int = 300

    # --- oidc mode ---------------------------------------------------------
    oidc_issuer_url: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_redirect_url: str | None = None
    oidc_scopes: str = "openid profile email"
    oidc_groups_claim: str = "groups"
    # What the login button says: "Sign in with <this>".
    oidc_display_name: str = "SSO"

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

    # --- Authentication helpers -------------------------------------------

    @property
    def auth_modes_set(self) -> frozenset[str]:
        return frozenset(mode.lower() for mode in self._csv(self.auth_mode))

    @property
    def auth_enabled(self) -> bool:
        """False only in `none` mode, where the app behaves exactly as it did
        before authentication existed."""
        return "none" not in self.auth_modes_set

    @property
    def session_enabled(self) -> bool:
        """Whether a signed session cookie is in play at all."""
        return bool(self.auth_modes_set & SESSION_AUTH_MODES)

    @property
    def auth_required_groups_set(self) -> frozenset[str]:
        return frozenset(self._csv(self.auth_required_groups))

    @property
    def oidc_scopes_list(self) -> list[str]:
        return [s for s in self.oidc_scopes.split() if s]

    @property
    def secret_key_path(self) -> Path:
        """Beside the database, which is already the one writable, persistent
        place every deployment has (the ./data volume in compose)."""
        return self.db_file.parent / ".secret_key"

    def resolved_secret_key(self) -> str:
        """The key used to sign sessions -- never the one this repo ships.

        Signing with DEFAULT_SECRET_KEY would be a known-key forgery: anyone
        could mint a session cookie for any user. But `local` is the default
        mode now, so *refusing to boot* on the default value would crash every
        fresh `docker compose up`, which is the opposite of a usable default.

        So: generate 32 random bytes on first use and persist them 0600 beside
        the DB. Zero-config, never a shipped key, and sessions survive a
        restart. An explicit CMDB_SECRET_KEY always wins, and a mode that signs
        nothing (`none`, `proxy`) writes no file at all.
        """
        if self.secret_key != DEFAULT_SECRET_KEY:
            return self.secret_key
        if not self.session_enabled:
            return self.secret_key

        path = self.secret_key_path
        existing = self._read_secret_key(path)
        if existing:
            return existing

        path.parent.mkdir(parents=True, exist_ok=True)
        generated = secrets.token_urlsafe(32)
        try:
            # O_EXCL + mode at creation: a chmod after the write would leave a
            # window where the key is world-readable, and O_EXCL is what makes
            # two workers racing at startup converge on one key instead of
            # each overwriting the other's.
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return self._read_secret_key(path) or generated
        with os.fdopen(fd, "w") as fh:
            fh.write(generated + "\n")
        return generated

    @staticmethod
    def _read_secret_key(path: Path) -> str | None:
        try:
            return path.read_text().strip() or None
        except OSError:
            return None

    @model_validator(mode="after")
    def _validate_auth(self) -> "Settings":
        """Reject a half-configured gate at construction, like _validate_mcp_remote.

        Every failure here is a restart loop, which is the correct outcome: the
        alternative is an app that boots and serves the fleet's inventory with a
        gate that silently is not one.
        """
        modes = self.auth_modes_set
        unknown = sorted(modes - AUTH_MODES)
        if unknown:
            raise ValueError(
                f"CMDB_AUTH_MODE has unknown mode(s) {', '.join(unknown)}; "
                f"valid modes are {', '.join(sorted(AUTH_MODES))}"
            )
        if not modes:
            raise ValueError(
                "CMDB_AUTH_MODE is empty; name at least one of "
                f"{', '.join(sorted(AUTH_MODES))}"
            )
        exclusive = modes & EXCLUSIVE_AUTH_MODES
        if exclusive and len(modes) > 1:
            raise ValueError(
                f"CMDB_AUTH_MODE: {', '.join(sorted(exclusive))} cannot be combined "
                "with another mode -- it describes the whole app, not a login option"
            )

        if "oidc" not in modes:
            return self

        missing = [
            name
            for name in (
                "oidc_issuer_url",
                "oidc_client_id",
                "oidc_client_secret",
                "oidc_redirect_url",
            )
            if not getattr(self, name)
        ]
        if missing:
            raise ValueError(
                "CMDB_AUTH_MODE includes 'oidc' but "
                + ", ".join(f"CMDB_{name.upper()}" for name in missing)
                + " is unset; refusing to offer a sign-in button that cannot work"
            )
        absent = _missing_oidc_modules()
        if absent:
            # Naming the group is the whole point. The `mcp` group already sprang
            # this trap once: an old image plus new env took down the entire web
            # UI, because cmdb.web imports the feature's module unconditionally.
            # A message that only said "No module named jwt" cost real time.
            raise ValueError(
                "CMDB_AUTH_MODE includes 'oidc' but its dependencies are missing ("
                + ", ".join(absent)
                + "); install the optional group: `uv sync --group oidc` "
                "(or rebuild the image, whose Dockerfile passes --group oidc)"
            )
        return self

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
    def db_file(self) -> Path:
        # Resolve a relative db_path against the repo root, not the cwd, so the
        # DB lands in the same place regardless of where the process is launched.
        # An absolute CMDB_DB_PATH is honored unchanged.
        path = Path(self.db_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_file}"


settings = Settings()
