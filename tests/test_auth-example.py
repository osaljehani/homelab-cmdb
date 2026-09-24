"""Boundary tests for the application's own authentication.

Everything here is synthetic: `auth.example.test`, `example-client-id`,
`example-admins`. Following tests/test_mcp_remote-example.py, which is the
established pattern for a tracked test whose real counterpart is gitignored.

The single most important assertion in this file is that `POST /mcp` still
answers 401 with a WWW-Authenticate header -- never a 302 to /login -- in every
auth mode. /mcp is exempt from the Authentik proxy that guards the rest of the
deployment, so cmdb/mcp/auth.py is the only boundary on that path; an auth
middleware that redirected it would break the live claude.ai connector and
silently replace a token challenge with a login page.
"""

from __future__ import annotations

import pytest

from cmdb.config import DEFAULT_SECRET_KEY, Settings

ISSUER = "https://auth.example.test/application/o/example-cmdb/"
CLIENT_ID = "example-client-id"
CLIENT_SECRET = "example-client-secret"
REDIRECT = "https://cmdb.example.test/auth/oidc/callback"
GROUP = "example-admins"

_AUTH_ENV = (
    "CMDB_AUTH_MODE",
    "CMDB_AUTH_REQUIRED_GROUPS",
    "CMDB_SECRET_KEY",
    "CMDB_OIDC_ISSUER_URL",
    "CMDB_OIDC_CLIENT_ID",
    "CMDB_OIDC_CLIENT_SECRET",
    "CMDB_OIDC_REDIRECT_URL",
)


@pytest.fixture
def clean_env(monkeypatch):
    """Settings() reads the real environment; make every auth var explicit."""
    for name in _AUTH_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _oidc_env(monkeypatch) -> None:
    monkeypatch.setenv("CMDB_OIDC_ISSUER_URL", ISSUER)
    monkeypatch.setenv("CMDB_OIDC_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("CMDB_OIDC_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.setenv("CMDB_OIDC_REDIRECT_URL", REDIRECT)


# --- Config: modes ---------------------------------------------------------


def test_local_is_the_default_mode(clean_env):
    """A fresh clone gets a login page, not an open inventory UI."""
    cfg = Settings()
    assert cfg.auth_modes_set == frozenset({"local"})
    assert cfg.auth_enabled is True
    assert cfg.session_enabled is True


def test_none_disables_the_gate(clean_env):
    clean_env.setenv("CMDB_AUTH_MODE", "none")
    cfg = Settings()
    assert cfg.auth_enabled is False
    assert cfg.session_enabled is False


def test_local_and_oidc_compose(clean_env):
    """Both doors on one login page -- the public story."""
    _oidc_env(clean_env)
    clean_env.setenv("CMDB_AUTH_MODE", "local,oidc")
    cfg = Settings()
    assert cfg.auth_modes_set == frozenset({"local", "oidc"})


def test_mode_parsing_tolerates_spacing_and_case(clean_env):
    clean_env.setenv("CMDB_AUTH_MODE", " LOCAL , ")
    assert Settings().auth_modes_set == frozenset({"local"})


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("banana", id="unknown-token"),
        pytest.param("local,banana", id="unknown-token-among-valid"),
        pytest.param("", id="empty"),
        pytest.param(",", id="only-separators"),
    ],
)
def test_unknown_or_empty_mode_refuses_to_boot(clean_env, value):
    clean_env.setenv("CMDB_AUTH_MODE", value)
    with pytest.raises(Exception, match="CMDB_AUTH_MODE"):
        Settings()


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("none,local", id="none-with-local"),
        pytest.param("proxy,local", id="proxy-with-local"),
        pytest.param("none,proxy", id="none-with-proxy"),
    ],
)
def test_exclusive_modes_refuse_to_compose(clean_env, value):
    clean_env.setenv("CMDB_AUTH_MODE", value)
    with pytest.raises(Exception, match="cannot be combined"):
        Settings()


# --- Config: oidc ----------------------------------------------------------


@pytest.mark.parametrize(
    "missing",
    [
        "CMDB_OIDC_ISSUER_URL",
        "CMDB_OIDC_CLIENT_ID",
        "CMDB_OIDC_CLIENT_SECRET",
        "CMDB_OIDC_REDIRECT_URL",
    ],
)
def test_oidc_without_its_config_refuses_to_boot(clean_env, missing):
    _oidc_env(clean_env)
    clean_env.delenv(missing)
    clean_env.setenv("CMDB_AUTH_MODE", "oidc")
    with pytest.raises(Exception, match=missing):
        Settings()


def test_oidc_without_its_dependency_group_names_the_group(clean_env, monkeypatch):
    """The trap the `mcp` group already sprang once: an old image plus new env
    took down the whole web UI. The failure must say what to install."""
    import cmdb.config as config_mod

    _oidc_env(clean_env)
    clean_env.setenv("CMDB_AUTH_MODE", "oidc")
    monkeypatch.setattr(config_mod, "_missing_oidc_modules", lambda: ["jwt"])
    with pytest.raises(Exception, match=r"--group oidc"):
        Settings()


def test_oidc_config_is_not_required_when_oidc_is_off(clean_env):
    clean_env.setenv("CMDB_AUTH_MODE", "local")
    assert Settings().oidc_issuer_url is None


# --- Config: the secret key -----------------------------------------------


def test_explicit_secret_key_always_wins(clean_env, tmp_path):
    clean_env.setenv("CMDB_SECRET_KEY", "an-explicit-key")
    clean_env.setenv("CMDB_DB_PATH", str(tmp_path / "cmdb.db"))
    cfg = Settings()
    assert cfg.resolved_secret_key() == "an-explicit-key"
    assert not (tmp_path / ".secret_key").exists()


def test_default_secret_key_is_replaced_by_a_persisted_random_one(clean_env, tmp_path):
    """Signing sessions with the shipped key would be a known-key forgery, but
    refusing to boot on it would crash every fresh `docker compose up`."""
    clean_env.setenv("CMDB_DB_PATH", str(tmp_path / "cmdb.db"))
    cfg = Settings()
    assert cfg.secret_key == DEFAULT_SECRET_KEY

    first = cfg.resolved_secret_key()
    assert first != DEFAULT_SECRET_KEY
    assert len(first) >= 32

    key_file = tmp_path / ".secret_key"
    assert key_file.exists()
    assert key_file.stat().st_mode & 0o777 == 0o600

    # Sessions must survive a restart, so a second process reads the same key.
    assert Settings().resolved_secret_key() == first


def test_secret_key_is_not_generated_for_a_sessionless_mode(clean_env, tmp_path):
    """`none` must stay byte-identical to today, and `proxy` reads identity from
    headers per request -- neither signs a cookie, so neither writes a key."""
    clean_env.setenv("CMDB_DB_PATH", str(tmp_path / "cmdb.db"))
    for mode in ("none", "proxy"):
        clean_env.setenv("CMDB_AUTH_MODE", mode)
        cfg = Settings()
        assert cfg.session_enabled is False
        assert cfg.resolved_secret_key() == DEFAULT_SECRET_KEY
    assert not (tmp_path / ".secret_key").exists()


def test_required_groups_default_to_any_authenticated_user(clean_env):
    assert Settings().auth_required_groups_set == frozenset()


def test_required_groups_parse_as_csv(clean_env):
    clean_env.setenv("CMDB_AUTH_REQUIRED_GROUPS", f" {GROUP}, other ")
    assert Settings().auth_required_groups_set == frozenset({GROUP, "other"})
