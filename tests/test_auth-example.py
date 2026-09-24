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


# --- Model + migration -----------------------------------------------------


def test_user_model_round_trips(db):
    from cmdb.domain.models import User

    db.add(User(username="alice", email="alice@example.test", is_admin=True))
    db.commit()
    row = db.query(User).filter_by(username="alice").one()
    assert row.is_admin is True
    assert row.is_active is True
    assert row.password_hash is None  # an OIDC-only user has none
    assert row.oidc_subject is None
    assert row.last_login_at is None
    assert row.created_at is not None


def test_usernames_are_unique(db):
    from sqlalchemy.exc import IntegrityError

    from cmdb.domain.models import User

    db.add(User(username="alice"))
    db.commit()
    db.add(User(username="alice"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_oidc_subject_is_unique(db):
    """Two local users must never be able to claim one federated identity."""
    from sqlalchemy.exc import IntegrityError

    from cmdb.domain.models import User

    db.add(User(username="alice", oidc_subject="subject-1"))
    db.commit()
    db.add(User(username="bob", oidc_subject="subject-1"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_migration_creates_the_users_table(tmp_path, monkeypatch):
    """Migrations run automatically on web startup and on `cmdb mcp`, so the
    table has to arrive that way and not via create_all."""
    import cmdb.config
    from sqlalchemy import create_engine, inspect

    db_file = tmp_path / "migrated.db"
    monkeypatch.setattr(cmdb.config.settings, "db_path", str(db_file))

    from cmdb.db import run_migrations

    run_migrations()

    engine = create_engine(f"sqlite:///{db_file}")
    inspector = inspect(engine)
    assert "users" in inspector.get_table_names()
    columns = {c["name"]: c for c in inspector.get_columns("users")}
    engine.dispose()
    assert set(columns) == {
        "id",
        "username",
        "email",
        "password_hash",
        "oidc_subject",
        "is_admin",
        "is_active",
        "created_at",
        "last_login_at",
    }
    assert columns["password_hash"]["nullable"] is True
    assert columns["username"]["nullable"] is False


# --- Password hashing ------------------------------------------------------


def test_password_round_trip():
    from cmdb.web.auth.passwords import hash_password, verify_password

    stored = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", stored) is True
    assert verify_password("wrong", stored) is False


def test_password_hash_is_salted_and_self_describing():
    from cmdb.web.auth.passwords import hash_password

    a = hash_password("same")
    b = hash_password("same")
    assert a != b, "per-user random salt, so two hashes of one password differ"
    scheme, n, r, p, salt, digest = a.split("$")
    assert scheme == "scrypt"
    assert (int(n), int(r), int(p)) == (2**15, 8, 1)
    assert salt and digest


def test_verify_rejects_malformed_or_absent_hashes():
    """An OIDC-only user has password_hash=None; that must never verify."""
    from cmdb.web.auth.passwords import verify_password

    for stored in (None, "", "not-a-hash", "scrypt$x$8$1$aa$bb", "bcrypt$1$2$3$4$5"):
        assert verify_password("anything", stored) is False
