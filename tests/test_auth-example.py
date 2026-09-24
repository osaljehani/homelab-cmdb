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

import time

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


# --- The gate --------------------------------------------------------------


@pytest.fixture
def gate_app(clean_env, db, monkeypatch):
    """Factory building a fresh app for a given CMDB_AUTH_MODE.

    create_app() is a factory for exactly this reason: the middleware stack is
    assembled at build time, so a mode change needs a new app rather than a
    patched setting. The DB is redirected twice over -- the FastAPI dependency
    for routes, and cmdb.web.auth.session.get_session for the middleware, which
    resolves a principal outside the request/dependency cycle.
    """
    from contextlib import contextmanager

    from cmdb.config import settings as cfg
    from cmdb.web import app as web_app
    from cmdb.web.deps import get_db_dep
    import cmdb.web.auth.session as session_mod

    monkeypatch.setattr(web_app, "run_migrations", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(cfg, "secret_key", "test-secret-key-not-a-real-one")

    @contextmanager
    def _fake_get_session():
        yield db

    monkeypatch.setattr(session_mod, "get_session", _fake_get_session)

    def build(mode: str, **overrides):
        monkeypatch.setattr(cfg, "auth_mode", mode)
        for name, value in overrides.items():
            monkeypatch.setattr(cfg, name, value)
        app = web_app.create_app()
        app.dependency_overrides[get_db_dep] = lambda: db
        return app

    return build


def _client(app, **kwargs):
    from fastapi.testclient import TestClient

    kwargs.setdefault("follow_redirects", False)
    return TestClient(app, **kwargs)


PROXY_HEADERS = {
    "X-authentik-username": "someone",
    "X-authentik-email": "someone@example.test",
    "X-authentik-groups": f"{GROUP}|example-users",
}


@pytest.mark.parametrize("mode", ["local", "proxy", "local,oidc"])
def test_mcp_stays_a_401_challenge_under_every_auth_mode(gate_app, monkeypatch, mode):
    """The assertion this whole file exists for.

    /mcp is exempt from the Authentik proxy, so cmdb/mcp/auth.py is the only
    boundary on that path. Starlette middleware wraps every route including the
    absolute ones attach_remote_mcp() appends, so a gate that redirected them
    would turn a token challenge a cloud client can act on into a login page it
    cannot -- exactly the failure the outpost carve-out exists to avoid.
    """
    if "oidc" in mode:
        _oidc_env(monkeypatch)
    app = gate_app(
        mode,
        mcp_remote_enabled=True,
        mcp_issuer_url=ISSUER,
        mcp_jwks_url=ISSUER + "jwks/",
        mcp_audience=CLIENT_ID,
        mcp_resource_url="https://cmdb.example.test/mcp",
        mcp_allowed_hosts="testserver,cmdb.example.test",
        mcp_required_groups=GROUP,
    )
    with _client(app) as client:
        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"accept": "application/json, text/event-stream"},
        )
        assert r.status_code == 401, f"{mode}: got {r.status_code}, a redirect would break the connector"
        assert "www-authenticate" in r.headers

        meta = client.get("/.well-known/oauth-protected-resource/mcp")
        assert meta.status_code == 200
        assert meta.json()["resource"] == "https://cmdb.example.test/mcp"


def test_mcp_exempt_set_is_empty_when_the_remote_endpoint_is_off(gate_app):
    """Derived from the registered routes, so it cannot exempt a path that
    attach_remote_mcp() did not actually create."""
    from cmdb.web.auth.middleware import AuthMiddleware

    app = gate_app("proxy")
    found = [m for m in app.user_middleware if m.cls is AuthMiddleware]
    assert len(found) == 1
    assert found[0].kwargs["mcp_paths"] == frozenset()

    with _client(app) as client:
        # Gated like any other unknown path, which is what proves it is not
        # exempt -- and a 404 behind the gate, which proves nothing is serving
        # it. An exempt /mcp with no endpoint would 404 anonymously instead.
        assert client.post("/mcp", json={}).status_code == 302
        assert client.post("/mcp", json={}, headers=PROXY_HEADERS).status_code == 404


def test_anonymous_ui_request_redirects_to_login(gate_app):
    app = gate_app("local")
    with _client(app) as client:
        r = client.get("/hosts")
    assert r.status_code == 302
    assert r.headers["location"] == "/login?next=%2Fhosts"


def test_anonymous_api_request_gets_401_json_not_a_redirect(gate_app):
    """A 302 to an HTML login page is useless to an API client."""
    app = gate_app("local")
    with _client(app) as client:
        r = client.get("/api/v1/hosts")
    assert r.status_code == 401
    assert r.json()["detail"]


def test_healthz_is_gated_so_session_js_can_detect_a_lapse(gate_app):
    """static/js/session.js reads a same-origin redirect on /healthz as the
    lapse signal. Leaving it open would make the probe answer 200 forever and
    the stale-session banner would silently never appear again."""
    app = gate_app("local")
    with _client(app) as client:
        assert client.get("/healthz").status_code == 302
        client.cookies.clear()
        assert client.get("/healthz", headers=PROXY_HEADERS).status_code == 302

    app = gate_app("proxy")
    with _client(app) as client:
        assert client.get("/healthz").status_code == 302
        assert client.get("/healthz", headers=PROXY_HEADERS).status_code == 200


def test_static_and_login_are_reachable_anonymously(gate_app, local_user):
    app = gate_app("local")
    with _client(app) as client:
        assert client.get("/static/js/session.js").status_code == 200
        assert client.get("/login").status_code == 200


def test_none_mode_leaves_every_path_open(gate_app):
    """Byte-identical to the app before any of this existed."""
    app = gate_app("none")
    with _client(app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/v1/hosts").status_code == 200


def test_proxy_headers_authenticate_in_proxy_mode(gate_app):
    app = gate_app("proxy")
    with _client(app) as client:
        r = client.get("/", headers=PROXY_HEADERS)
    assert r.status_code == 200
    assert "someone" in r.text


def test_proxy_headers_are_ignored_outside_proxy_mode(gate_app):
    """Header trust is safe only because :8080 is closed to the LAN. Honouring
    these in local mode would let anyone who reaches the port assert any
    identity, with no reverse proxy involved at all."""
    app = gate_app("local")
    with _client(app) as client:
        r = client.get("/", headers=PROXY_HEADERS)
    assert r.status_code == 302
    assert r.headers["location"].startswith("/login")


def test_required_groups_deny_a_principal_outside_the_group(gate_app):
    app = gate_app("proxy", auth_required_groups="homelab-admins")
    with _client(app) as client:
        assert client.get("/", headers=PROXY_HEADERS).status_code == 403
        allowed = dict(PROXY_HEADERS, **{"X-authentik-groups": "homelab-admins"})
        assert client.get("/", headers=allowed).status_code == 200


def test_proxy_mode_needs_the_username_header(gate_app):
    app = gate_app("proxy")
    with _client(app) as client:
        no_user = {k: v for k, v in PROXY_HEADERS.items() if "username" not in k}
        assert client.get("/", headers=no_user).status_code == 302


@pytest.mark.parametrize(
    "target",
    ["https://evil.example/", "//evil.example/", "/\\evil.example", "not-a-path"],
)
def test_next_is_restricted_to_a_local_path(gate_app, target):
    """An open redirect on the login page would let a phishing link bounce a
    freshly-authenticated user off-site."""
    from cmdb.web.auth.middleware import safe_next

    assert safe_next(target) == "/"
    assert safe_next("/hosts?q=1") == "/hosts?q=1"


def test_cross_origin_post_is_rejected(gate_app):
    """A session cookie is new CSRF surface: before this there were no cookies
    at all. SameSite=Lax already blocks the cross-site form POST; the Origin
    check is the belt-and-braces half."""
    app = gate_app("proxy")
    with _client(app) as client:
        hostile = dict(PROXY_HEADERS, Origin="https://evil.example")
        assert client.post("/collect/run", headers=hostile).status_code == 403
        friendly = dict(PROXY_HEADERS, Origin="http://testserver")
        assert client.post("/collect/run", headers=friendly).status_code != 403


def test_session_cookie_is_hardened(gate_app):
    """HttpOnly, SameSite=Lax and Secure are what make the CSRF story hold."""
    from cmdb.web.auth.session import session_cookie_kwargs

    kwargs = session_cookie_kwargs()
    assert kwargs["https_only"] is True
    assert kwargs["same_site"] == "lax"
    assert kwargs["max_age"] == 43200


def test_session_middleware_is_absent_in_sessionless_modes(gate_app):
    """proxy and none sign nothing, so they mount no cookie machinery."""
    from starlette.middleware.sessions import SessionMiddleware

    for mode in ("none", "proxy"):
        app = gate_app(mode)
        assert not [m for m in app.user_middleware if m.cls is SessionMiddleware]

    app = gate_app("local")
    assert [m for m in app.user_middleware if m.cls is SessionMiddleware]


def test_session_middleware_wraps_the_gate(gate_app):
    """Ordering is load-bearing and counter-intuitive: add_middleware inserts at
    index 0 and the stack is built from reversed(user_middleware), so the LAST
    middleware added is the OUTERMOST. The gate reads request.session, so
    SessionMiddleware has to be added after it."""
    from starlette.middleware.sessions import SessionMiddleware

    from cmdb.web.auth.middleware import AuthMiddleware

    app = gate_app("local")
    classes = [m.cls for m in app.user_middleware]
    assert classes.index(SessionMiddleware) < classes.index(AuthMiddleware)


# --- Local login -----------------------------------------------------------

PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def _reset_throttle():
    from cmdb.web.auth import throttle

    throttle.reset()
    yield
    throttle.reset()


@pytest.fixture
def local_user(db):
    from cmdb.domain.models import User
    from cmdb.web.auth.passwords import hash_password

    user = User(
        username="admin",
        email="admin@example.test",
        password_hash=hash_password(PASSWORD),
        is_admin=True,
        is_active=True,
    )
    db.add(user)
    db.commit()
    return user


def _tls_client(app):
    """https base_url, because the session cookie carries Secure by default --
    an http client would accept it and then never send it back."""
    return _client(app, base_url="https://testserver")


def test_local_login_starts_a_session(gate_app, local_user):
    app = gate_app("local")
    with _tls_client(app) as client:
        r = client.post("/login", data={"username": "admin", "password": PASSWORD})
        assert r.status_code == 302
        assert r.headers["location"] == "/"

        page = client.get("/")
        assert page.status_code == 200
        assert "admin" in page.text
    assert local_user.last_login_at is not None


def test_logout_ends_the_session(gate_app, local_user):
    app = gate_app("local")
    with _tls_client(app) as client:
        client.post("/login", data={"username": "admin", "password": PASSWORD})
        assert client.get("/").status_code == 200

        out = client.post("/logout")
        assert out.status_code == 302
        assert out.headers["location"] == "/login"
        assert client.get("/").status_code == 302


@pytest.mark.parametrize(
    "username,password",
    [("admin", "wrong"), ("nobody", PASSWORD), ("nobody", "wrong")],
)
def test_bad_credentials_are_indistinguishable(gate_app, local_user, username, password):
    """A wrong password and a non-existent account must look the same, or the
    login form becomes a user-enumeration oracle."""
    app = gate_app("local")
    with _tls_client(app) as client:
        r = client.post("/login", data={"username": username, "password": password})
    assert r.status_code == 401
    assert "Incorrect username or password." in r.text
    assert "set-cookie" not in r.headers


def test_an_inactive_user_cannot_log_in(gate_app, local_user, db):
    local_user.is_active = False
    db.commit()
    app = gate_app("local")
    with _tls_client(app) as client:
        r = client.post("/login", data={"username": "admin", "password": PASSWORD})
    assert r.status_code == 401


def test_deactivating_a_user_ends_their_session_immediately(gate_app, local_user, db):
    """The principal is resolved against the DB on every request, so `is_active`
    is a kill switch rather than something that waits for the cookie to expire."""
    app = gate_app("local")
    with _tls_client(app) as client:
        client.post("/login", data={"username": "admin", "password": PASSWORD})
        assert client.get("/").status_code == 200

        local_user.is_active = False
        db.commit()
        assert client.get("/").status_code == 302


def test_repeated_failures_lock_the_account_out(gate_app, local_user):
    from cmdb.web.auth.throttle import MAX_FAILURES

    app = gate_app("local")
    with _tls_client(app) as client:
        for _ in range(MAX_FAILURES):
            assert (
                client.post(
                    "/login", data={"username": "admin", "password": "wrong"}
                ).status_code
                == 401
            )
        locked = client.post("/login", data={"username": "admin", "password": PASSWORD})
    assert locked.status_code == 429
    assert "Too many failed attempts" in locked.text


def test_login_honours_a_local_next_and_drops_an_external_one(gate_app, local_user):
    app = gate_app("local")
    with _tls_client(app) as client:
        good = client.post(
            "/login",
            data={"username": "admin", "password": PASSWORD, "next": "/images"},
        )
        assert good.headers["location"] == "/images"

    with _tls_client(app) as client:
        bad = client.post(
            "/login",
            data={
                "username": "admin",
                "password": PASSWORD,
                "next": "https://evil.example/",
            },
        )
        assert bad.headers["location"] == "/"


def test_required_groups_do_not_lock_out_a_local_account(gate_app, local_user):
    """The group requirement applies to identities that carry groups -- proxy and
    oidc -- not to local rows, which have none and would otherwise be denied
    unconditionally. There is no self-registration, so every local row was
    created deliberately; this is what lets `local,oidc` mean "group-gated SSO
    plus a break-glass password"."""
    app = gate_app("local", auth_required_groups="example-admins")
    with _tls_client(app) as client:
        client.post("/login", data={"username": "admin", "password": PASSWORD})
        assert client.get("/").status_code == 200


def test_login_page_redirects_away_in_proxy_mode(gate_app):
    """There is nothing for it to offer: the outpost owns the login experience."""
    app = gate_app("proxy")
    with _client(app) as client:
        r = client.get("/login")
    assert r.status_code == 302
    assert r.headers["location"] == "/"


def test_login_page_offers_both_doors_when_both_are_enabled(
    gate_app, local_user, monkeypatch
):
    _oidc_env(monkeypatch)
    app = gate_app("local,oidc", oidc_display_name="Example SSO")
    with _client(app) as client:
        body = client.get("/login").text
    assert 'name="password"' in body
    assert "Sign in with Example SSO" in body


# --- The users service -----------------------------------------------------


def test_create_user_hashes_the_password(db):
    from cmdb.domain.services.users import create_user
    from cmdb.web.auth.passwords import verify_password

    user = create_user(db, "alice", password="s3cret-passphrase", email="a@example.test")
    assert user.password_hash != "s3cret-passphrase"
    assert verify_password("s3cret-passphrase", user.password_hash)
    assert user.is_active is True
    assert user.is_admin is False


def test_create_user_rejects_a_duplicate_username(db):
    from cmdb.domain.services.users import create_user

    create_user(db, "alice", password="s3cret-passphrase")
    with pytest.raises(ValueError, match="already exists"):
        create_user(db, "alice", password="other-passphrase")


def test_create_user_rejects_a_short_password(db):
    from cmdb.domain.services.users import MIN_PASSWORD_LENGTH, create_user

    with pytest.raises(ValueError, match="at least"):
        create_user(db, "alice", password="x" * (MIN_PASSWORD_LENGTH - 1))


def test_a_federated_user_needs_no_password(db):
    from cmdb.domain.services.users import create_user

    user = create_user(db, "bob", oidc_subject="subject-abc")
    assert user.password_hash is None
    assert user.oidc_subject == "subject-abc"


def test_authenticate_checks_the_password_and_the_active_flag(db):
    from cmdb.domain.services.users import authenticate, create_user

    create_user(db, "alice", password="s3cret-passphrase")
    assert authenticate(db, "alice", "s3cret-passphrase") is not None
    assert authenticate(db, "alice", "wrong") is None
    assert authenticate(db, "nobody", "s3cret-passphrase") is None

    user = authenticate(db, "alice", "s3cret-passphrase")
    user.is_active = False
    db.commit()
    assert authenticate(db, "alice", "s3cret-passphrase") is None


def test_a_password_only_user_cannot_be_authenticated_by_an_empty_password(db):
    """A federated row has password_hash NULL; an empty form field must not
    verify against it."""
    from cmdb.domain.services.users import authenticate, create_user

    create_user(db, "bob", oidc_subject="subject-abc")
    assert authenticate(db, "bob", "") is None


def test_set_password_and_delete(db):
    from cmdb.domain.services.users import (
        authenticate,
        create_user,
        delete_user,
        set_password,
    )

    create_user(db, "alice", password="s3cret-passphrase")
    set_password(db, "alice", "a-different-passphrase")
    assert authenticate(db, "alice", "s3cret-passphrase") is None
    assert authenticate(db, "alice", "a-different-passphrase") is not None

    assert delete_user(db, "alice") is True
    assert delete_user(db, "alice") is False


def test_the_last_active_admin_cannot_be_removed_or_demoted(db):
    """Locking yourself out of your own instance should take more than one
    command. Recovery would mean hand-editing SQLite."""
    from cmdb.domain.services.users import (
        create_user,
        delete_user,
        set_admin,
    )

    create_user(db, "root", password="s3cret-passphrase", is_admin=True)
    create_user(db, "alice", password="s3cret-passphrase")

    with pytest.raises(ValueError, match="last active admin"):
        delete_user(db, "root")
    with pytest.raises(ValueError, match="last active admin"):
        set_admin(db, "root", False)

    # A second admin makes the first one removable.
    set_admin(db, "alice", True)
    assert delete_user(db, "root") is True


def test_count_users(db):
    from cmdb.domain.services.users import count_users, create_user

    assert count_users(db) == 0
    create_user(db, "alice", password="s3cret-passphrase")
    assert count_users(db) == 1


# --- First-run setup -------------------------------------------------------


def test_login_sends_a_fresh_install_to_setup(gate_app):
    """With no users there is nothing to log in as, so the login page would be a
    dead end."""
    app = gate_app("local")
    with _client(app) as client:
        r = client.get("/login")
    assert r.status_code == 302
    assert r.headers["location"] == "/setup"


def test_setup_creates_the_first_admin_and_signs_them_in(gate_app, db):
    from cmdb.domain.services.users import get_user_by_username

    app = gate_app("local")
    with _tls_client(app) as client:
        r = client.post(
            "/setup",
            data={
                "username": "root",
                "password": "a-long-enough-passphrase",
                "confirm": "a-long-enough-passphrase",
            },
        )
        assert r.status_code == 302
        assert r.headers["location"] == "/"
        assert client.get("/").status_code == 200

    user = get_user_by_username(db, "root")
    assert user is not None and user.is_admin is True
    assert user.last_login_at is not None


def test_setup_requires_the_password_twice(gate_app, db):
    from cmdb.domain.services.users import count_users

    app = gate_app("local")
    with _tls_client(app) as client:
        r = client.post(
            "/setup",
            data={
                "username": "root",
                "password": "a-long-enough-passphrase",
                "confirm": "something-else-entirely",
            },
        )
    assert r.status_code == 400
    assert "do not match" in r.text
    assert count_users(db) == 0


def test_setup_closes_itself_once_a_user_exists(gate_app, local_user):
    """It must not become a permanent account-creation hole."""
    app = gate_app("local")
    with _client(app) as client:
        assert client.get("/setup").status_code == 404
        assert (
            client.post(
                "/setup",
                data={"username": "x", "password": "y" * 16, "confirm": "y" * 16},
            ).status_code
            == 404
        )


def test_setup_is_absent_when_local_login_is_off(gate_app):
    app = gate_app("proxy")
    with _client(app) as client:
        assert client.get("/setup").status_code == 404


# --- The users CLI ---------------------------------------------------------


def _patch_cli_session(db, monkeypatch):
    from contextlib import contextmanager

    @contextmanager
    def _fake_session():
        yield db

    monkeypatch.setattr("cmdb.cli.users.get_session", _fake_session)


def test_cli_users_add_list_passwd_rm(db, monkeypatch):
    from typer.testing import CliRunner

    from cmdb.cli.main import app as cli
    from cmdb.domain.services.users import authenticate, get_user_by_username

    _patch_cli_session(db, monkeypatch)
    runner = CliRunner()

    r = runner.invoke(
        cli,
        ["users", "add", "alice", "--email", "a@example.test"],
        input="a-long-enough-passphrase\na-long-enough-passphrase\n",
    )
    assert r.exit_code == 0, r.output
    assert authenticate(db, "alice", "a-long-enough-passphrase") is not None

    assert runner.invoke(cli, ["users", "promote", "alice"]).exit_code == 0
    assert get_user_by_username(db, "alice").is_admin is True
    # Demoting the only admin is refused for the same reason deleting one is.
    assert runner.invoke(cli, ["users", "demote", "alice"]).exit_code == 1

    assert runner.invoke(cli, ["users", "disable", "alice"]).exit_code == 1
    db.refresh(get_user_by_username(db, "alice"))

    r = runner.invoke(cli, ["users", "list"])
    assert r.exit_code == 0
    assert "alice" in r.output
    assert "a@example.test" in r.output

    r = runner.invoke(
        cli,
        ["users", "passwd", "alice"],
        input="a-brand-new-passphrase\na-brand-new-passphrase\n",
    )
    assert r.exit_code == 0, r.output
    assert authenticate(db, "alice", "a-brand-new-passphrase") is not None

    # Demote via the service so the guard has a second admin to fall back on,
    # then the delete is allowed.
    from cmdb.domain.services.users import create_user

    create_user(db, "root", password="a-long-enough-passphrase", is_admin=True)
    r = runner.invoke(cli, ["users", "rm", "alice", "--yes"])
    assert r.exit_code == 0, r.output
    assert authenticate(db, "alice", "a-brand-new-passphrase") is None


def test_cli_users_add_reports_a_duplicate_without_a_traceback(db, monkeypatch):
    from typer.testing import CliRunner

    from cmdb.cli.main import app as cli
    from cmdb.domain.services.users import create_user

    _patch_cli_session(db, monkeypatch)
    create_user(db, "alice", password="a-long-enough-passphrase")

    r = CliRunner().invoke(
        cli,
        ["users", "add", "alice"],
        input="a-long-enough-passphrase\na-long-enough-passphrase\n",
    )
    assert r.exit_code == 1
    assert "already exists" in r.output
    assert "Traceback" not in r.output


def test_cli_users_rm_refuses_the_last_admin(db, monkeypatch):
    from typer.testing import CliRunner

    from cmdb.cli.main import app as cli
    from cmdb.domain.services.users import create_user

    _patch_cli_session(db, monkeypatch)
    create_user(db, "root", password="a-long-enough-passphrase", is_admin=True)

    r = CliRunner().invoke(cli, ["users", "rm", "root", "--yes"])
    assert r.exit_code == 1
    assert "last active admin" in r.output


# --- Federated (OIDC) login ------------------------------------------------

AUTHORIZE_URL = ISSUER + "authorize/"
TOKEN_URL = ISSUER + "token/"
JWKS_URL = ISSUER + "jwks/"
USERINFO_URL = "https://auth.example.test/application/o/userinfo/"
OIDC_SUBJECT = "subject-abcdef"
OIDC_KID = "oidc-test-key"


@pytest.fixture(scope="module")
def oidc_key():
    from cryptography.hazmat.primitives.asymmetric import rsa

    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def idp(oidc_key, monkeypatch):
    """A mock identity provider on httpx.MockTransport.

    Returns a handle whose `token_response` the test sets before driving the
    callback -- the nonce is generated inside /auth/oidc/login, so the id_token
    can only be minted once the test has read it back off the redirect.
    """
    import json as _json

    import httpx as _httpx
    import jwt as _jwt

    from cmdb.web.auth import oidc as oidc_mod

    oidc_mod.reset_caches()

    pub = _jwt.algorithms.RSAAlgorithm.to_jwk(oidc_key.public_key(), as_dict=True)
    pub.update({"kid": OIDC_KID, "use": "sig", "alg": "RS256"})

    discovery = {
        "issuer": ISSUER,
        "authorization_endpoint": AUTHORIZE_URL,
        "token_endpoint": TOKEN_URL,
        "jwks_uri": JWKS_URL,
        "userinfo_endpoint": USERINFO_URL,
    }

    class Handle:
        token_response = None
        token_status = 200
        userinfo = {}
        calls = {"token": 0, "jwks": 0, "userinfo": 0}

        def id_token(self, *, nonce, **overrides):
            now = int(time.time())
            claims = {
                "iss": ISSUER,
                "aud": CLIENT_ID,
                "sub": OIDC_SUBJECT,
                "iat": now,
                "exp": now + 300,
                "nonce": nonce,
                "preferred_username": "someone",
                "email": "someone@example.test",
                "groups": [GROUP],
            }
            claims.update(overrides)
            return _jwt.encode(
                claims, oidc_key, algorithm="RS256", headers={"kid": OIDC_KID}
            )

        def tokens_for(self, *, nonce, **overrides):
            self.token_response = {
                "access_token": "an-access-token",
                "token_type": "Bearer",
                "id_token": self.id_token(nonce=nonce, **overrides),
            }

    handle = Handle()

    def responder(request: _httpx.Request) -> _httpx.Response:
        url = str(request.url)
        if url.endswith(".well-known/openid-configuration"):
            return _httpx.Response(200, json=discovery)
        if url == JWKS_URL:
            handle.calls["jwks"] += 1
            return _httpx.Response(200, json={"keys": [pub]})
        if url == TOKEN_URL:
            handle.calls["token"] += 1
            if handle.token_status != 200:
                return _httpx.Response(handle.token_status, text="nope")
            return _httpx.Response(200, content=_json.dumps(handle.token_response))
        if url == USERINFO_URL:
            handle.calls["userinfo"] += 1
            return _httpx.Response(200, json=handle.userinfo)
        return _httpx.Response(404)

    real = _httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = _httpx.MockTransport(responder)
        return real(*args, **kwargs)

    monkeypatch.setattr(oidc_mod.httpx, "AsyncClient", patched)
    yield handle
    oidc_mod.reset_caches()


@pytest.fixture
def oidc_app(gate_app, monkeypatch, idp):
    """The routes read the `settings` singleton, which was built at import time --
    so setting the environment (as the config tests do) is not enough here; the
    singleton's attributes have to be patched."""
    _oidc_env(monkeypatch)

    def build(mode="oidc", **overrides):
        return gate_app(
            mode,
            oidc_issuer_url=ISSUER,
            oidc_client_id=CLIENT_ID,
            oidc_client_secret=CLIENT_SECRET,
            oidc_redirect_url=REDIRECT,
            **overrides,
        )

    return build


def _begin_oidc(client):
    """Drive /auth/oidc/login and return (state, nonce) from the redirect."""
    from urllib.parse import parse_qs, urlparse

    r = client.get("/auth/oidc/login")
    assert r.status_code == 302, r.text
    query = parse_qs(urlparse(r.headers["location"]).query)
    return r, query


def test_oidc_login_redirects_with_pkce_and_a_nonce(oidc_app):

    app = oidc_app()
    with _tls_client(app) as client:
        r, query = _begin_oidc(client)

    assert r.headers["location"].startswith(AUTHORIZE_URL)
    assert query["client_id"] == [CLIENT_ID]
    assert query["redirect_uri"] == [REDIRECT]
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["scope"] == ["openid profile email"]
    assert len(query["state"][0]) > 20
    assert len(query["nonce"][0]) > 20
    # A base64url SHA-256, unpadded (RFC 7636 4.2). The verifier itself never
    # leaves the server, so its value cannot be asserted from out here -- but its
    # length and alphabet are fixed, and a verifier sent by mistake would fail
    # both.
    challenge = query["code_challenge"][0]
    assert len(challenge) == 43
    assert set(challenge) <= set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    )


def test_oidc_callback_links_an_existing_account(oidc_app, idp, db):
    """First federated login for an operator-created row binds the subject to it.
    Auto-provisioning is off, so this is the only way in."""
    from cmdb.domain.services.users import create_user, get_user_by_username

    create_user(db, "someone", password="a-long-enough-passphrase")

    app = oidc_app()
    with _tls_client(app) as client:
        _, query = _begin_oidc(client)
        idp.tokens_for(nonce=query["nonce"][0])
        r = client.get(f"/auth/oidc/callback?code=xyz&state={query['state'][0]}")
        assert r.status_code == 302, r.text
        assert r.headers["location"] == "/"
        assert client.get("/").status_code == 200

    user = get_user_by_username(db, "someone")
    assert user.oidc_subject == OIDC_SUBJECT
    assert user.last_login_at is not None


def test_oidc_refuses_an_unprovisioned_identity(oidc_app, idp, db):
    """The default. An IdP that provisions an account for whoever logs in is how
    'add a federated source' silently becomes 'anyone there is a user here'."""
    from cmdb.domain.services.users import count_users

    app = oidc_app()
    with _tls_client(app) as client:
        _, query = _begin_oidc(client)
        idp.tokens_for(nonce=query["nonce"][0])
        r = client.get(f"/auth/oidc/callback?code=xyz&state={query['state'][0]}")

    assert r.status_code == 403
    assert "Ask an administrator" in r.text
    assert count_users(db) == 0


def test_oidc_auto_create_provisions_when_explicitly_enabled(oidc_app, idp, db):
    from cmdb.domain.services.users import get_user_by_username

    app = oidc_app(oidc_auto_create_users=True)
    with _tls_client(app) as client:
        _, query = _begin_oidc(client)
        idp.tokens_for(nonce=query["nonce"][0])
        r = client.get(f"/auth/oidc/callback?code=xyz&state={query['state'][0]}")
        assert r.status_code == 302, r.text

    user = get_user_by_username(db, "someone")
    assert user is not None
    assert user.oidc_subject == OIDC_SUBJECT
    assert user.password_hash is None
    assert user.is_admin is False


def test_oidc_never_rebinds_a_username_owned_by_another_subject(oidc_app, idp, db):
    """Two federated identities must not be able to contend for one username."""
    from cmdb.domain.services.users import create_user

    create_user(db, "someone", oidc_subject="a-different-subject")

    app = oidc_app(oidc_auto_create_users=True)
    with _tls_client(app) as client:
        _, query = _begin_oidc(client)
        idp.tokens_for(nonce=query["nonce"][0])
        r = client.get(f"/auth/oidc/callback?code=xyz&state={query['state'][0]}")
    assert r.status_code == 403


def test_oidc_rejects_a_mismatched_state(oidc_app, idp):
    """state is the callback's CSRF defence."""
    app = oidc_app()
    with _tls_client(app) as client:
        _, query = _begin_oidc(client)
        idp.tokens_for(nonce=query["nonce"][0])
        r = client.get("/auth/oidc/callback?code=xyz&state=not-the-issued-state")
    assert r.status_code == 401
    assert "did not come from this browser" in r.text
    assert idp.calls["token"] == 0, "must refuse before redeeming the code"


def test_oidc_rejects_a_replayed_nonce(oidc_app, idp, db):
    """The nonce binds the id_token to this login attempt, so a token minted for
    a different one must not be accepted."""
    from cmdb.domain.services.users import create_user

    create_user(db, "someone", password="a-long-enough-passphrase")
    app = oidc_app()
    with _tls_client(app) as client:
        _, query = _begin_oidc(client)
        idp.tokens_for(nonce="a-nonce-from-some-other-attempt")
        r = client.get(f"/auth/oidc/callback?code=xyz&state={query['state'][0]}")
    assert r.status_code == 401
    assert client.get("/").status_code == 302


def test_oidc_rejects_a_token_minted_for_another_application(oidc_app, idp, db):
    """Same IdP, different client_id. Without the aud check every application on
    the IdP could mint a login for this one."""
    from cmdb.domain.services.users import create_user

    create_user(db, "someone", password="a-long-enough-passphrase")
    app = oidc_app()
    with _tls_client(app) as client:
        _, query = _begin_oidc(client)
        idp.tokens_for(nonce=query["nonce"][0], aud="some-other-client")
        r = client.get(f"/auth/oidc/callback?code=xyz&state={query['state'][0]}")
    assert r.status_code == 401


def test_oidc_rejects_a_foreign_issuer(oidc_app, idp, db):
    from cmdb.domain.services.users import create_user

    create_user(db, "someone", password="a-long-enough-passphrase")
    app = oidc_app()
    with _tls_client(app) as client:
        _, query = _begin_oidc(client)
        idp.tokens_for(
            nonce=query["nonce"][0], iss="https://auth.evil.test/application/o/x/"
        )
        r = client.get(f"/auth/oidc/callback?code=xyz&state={query['state'][0]}")
    assert r.status_code == 401


def test_oidc_surfaces_a_provider_refusal(oidc_app, idp):
    app = oidc_app()
    with _tls_client(app) as client:
        _, query = _begin_oidc(client)
        r = client.get(
            f"/auth/oidc/callback?error=access_denied&state={query['state'][0]}"
        )
    assert r.status_code == 401
    assert "refused the login" in r.text


def test_oidc_surfaces_a_token_endpoint_failure(oidc_app, idp, db):
    app = oidc_app()
    idp.token_status = 400
    with _tls_client(app) as client:
        _, query = _begin_oidc(client)
        r = client.get(f"/auth/oidc/callback?code=xyz&state={query['state'][0]}")
    assert r.status_code == 401
    # The token endpoint's body can echo the code back; it must not be shown.
    assert "xyz" not in r.text


def test_oidc_groups_feed_the_required_group_gate(oidc_app, idp, db):
    """The IdP's groups ride in the signed session, because there is no way to
    re-ask it on every request."""
    from cmdb.domain.services.users import create_user

    create_user(db, "someone", password="a-long-enough-passphrase")

    app = oidc_app(auth_required_groups=GROUP)
    with _tls_client(app) as client:
        _, query = _begin_oidc(client)
        idp.tokens_for(nonce=query["nonce"][0])
        assert (
            client.get(
                f"/auth/oidc/callback?code=xyz&state={query['state'][0]}"
            ).status_code
            == 302
        )
        assert client.get("/").status_code == 200

    app = oidc_app(auth_required_groups="a-group-they-are-not-in")
    with _tls_client(app) as client:
        _, query = _begin_oidc(client)
        idp.tokens_for(nonce=query["nonce"][0])
        client.get(f"/auth/oidc/callback?code=xyz&state={query['state'][0]}")
        assert client.get("/").status_code == 403


def test_oidc_falls_back_to_userinfo_for_groups(oidc_app, idp, db):
    """Authentik has no `groups` scope -- the claim rides on the stock `profile`
    mapping -- so the fallback is for providers that only expose it here."""
    from cmdb.domain.services.users import create_user

    create_user(db, "someone", password="a-long-enough-passphrase")
    idp.userinfo = {"groups": [GROUP]}

    app = oidc_app(auth_required_groups=GROUP)
    with _tls_client(app) as client:
        _, query = _begin_oidc(client)
        idp.tokens_for(nonce=query["nonce"][0], groups=None)
        assert (
            client.get(
                f"/auth/oidc/callback?code=xyz&state={query['state'][0]}"
            ).status_code
            == 302
        )
        assert client.get("/").status_code == 200
    assert idp.calls["userinfo"] == 1


def test_oidc_routes_are_absent_when_the_mode_is_off(gate_app, local_user):
    app = gate_app("local")
    with _client(app) as client:
        assert client.get("/auth/oidc/login").status_code == 404
        assert client.get("/auth/oidc/callback?code=x&state=y").status_code == 404
