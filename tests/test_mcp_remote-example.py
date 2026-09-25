"""Boundary tests for the remote (streamable-HTTP) MCP endpoint.

/mcp is exempt from the SSO proxy that guards the rest of the app, so these
tests are the regression net for the only boundary that remains. The existing
tests/test_mcp.py calls undecorated tool functions directly and exercises no
transport at all; this file drives the real ASGI stack through TestClient with
tokens signed by a locally generated RSA key.

Two things are asserted here that a future "simplification" of the verifier
would quietly remove, so they are the point of the file:

* a token with a valid signature but the WRONG `aud` must be rejected -- the
  MCP SDK never checks `aud`, so only cmdb/mcp/auth.py stands between this
  endpoint and a token minted for a different application on the same IdP;
* a token whose principal is in no group must be rejected -- that is what
  structurally excludes the client_credentials service account.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from cmdb.domain.services.ansible import import_host

ISSUER = "https://auth.example.test/application/o/example-mcp/"
JWKS_URL = "https://auth.example.test/application/o/example-mcp/jwks/"
USERINFO_URL = "https://auth.example.test/application/o/userinfo/"
AUDIENCE = "example-client-id"
RESOURCE = "https://cmdb.example.test/mcp"
GROUP = "example-admins"
KID = "test-key-1"

DESTRUCTIVE_TOOLS = {
    "add_tag",
    "remove_tag",
    "delete_host",
    "add_cluster",
    "delete_cluster",
    "add_node",
    "remove_node",
    "import_ansible",
    "set_image_noisy",
    "delete_image",
}


@pytest.fixture(scope="module")
def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def jwks_doc(rsa_key) -> dict:
    pub = jwt.algorithms.RSAAlgorithm.to_jwk(rsa_key.public_key(), as_dict=True)
    pub.update({"kid": KID, "use": "sig", "alg": "RS256"})
    return {"keys": [pub]}


def make_token(rsa_key, **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-subject-hash",
        "iat": now,
        "exp": now + 3600,
        "azp": AUDIENCE,
        "scope": "openid email profile",
        "groups": [GROUP],
    }
    claims.update(overrides)
    return jwt.encode(claims, rsa_key, algorithm="RS256", headers={"kid": KID})


@pytest.fixture
def remote_app(monkeypatch, db, host_facts, jwks_doc):
    """A fresh app with the remote endpoint enabled and JWKS served locally.

    A factory is required rather than importing the module-level `app`: the MCP
    session manager's run() is single-shot, so two TestClients over one app
    instance raise RuntimeError.
    """
    import_host(db, host_facts)

    from cmdb.config import settings as cfg
    from cmdb.mcp import server as mcp_server
    from cmdb.web import app as web_app

    for name, value in {
        "mcp_remote_enabled": True,
        "mcp_issuer_url": ISSUER,
        "mcp_jwks_url": JWKS_URL,
        "mcp_audience": AUDIENCE,
        "mcp_resource_url": RESOURCE,
        "mcp_userinfo_url": USERINFO_URL,
        "mcp_allowed_hosts": "testserver,cmdb.example.test",
        "mcp_required_groups": GROUP,
    }.items():
        monkeypatch.setattr(cfg, name, value, raising=False)

    @contextmanager
    def _fake_get_session():
        yield db

    monkeypatch.setattr(mcp_server, "get_session", _fake_get_session)
    monkeypatch.setattr(web_app, "run_migrations", lambda *a, **k: None, raising=False)

    calls: dict[str, int] = {"jwks": 0, "userinfo": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == JWKS_URL:
            calls["jwks"] += 1
            if jwks_unreachable["value"]:
                return httpx.Response(500)
            return httpx.Response(200, json=jwks_doc)
        if str(request.url) == USERINFO_URL:
            calls["userinfo"] += 1
            return httpx.Response(200, json={"groups": userinfo_groups["value"]})
        return httpx.Response(404)

    jwks_unreachable = {"value": False}
    userinfo_groups = {"value": [GROUP]}

    import cmdb.mcp.auth as auth_mod

    real_client = httpx.AsyncClient

    def _client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(auth_mod.httpx, "AsyncClient", _client)

    app = web_app.create_app()
    app.state.calls = calls
    app.state.jwks_unreachable = jwks_unreachable
    app.state.userinfo_groups = userinfo_groups
    return app


def _client(app):
    from fastapi.testclient import TestClient

    return TestClient(app)


def _rpc(client, body: dict, token: str | None = None):
    headers = {
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
    }
    if token:
        headers["authorization"] = f"Bearer {token}"
    return client.post("/mcp", content=json.dumps(body), headers=headers)


def _tools_list(client, token: str):
    """initialize then tools/list. stateless_http makes each POST standalone."""
    _rpc(
        client,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        },
        token,
    )
    return _rpc(client, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, token)


# --- Disabled by default ---------------------------------------------------


def test_disabled_by_default_serves_no_mcp_route():
    """A fresh clone must get today's behaviour: /mcp simply does not exist."""
    from cmdb.web import app as web_app

    with _client(web_app.create_app()) as client:
        assert client.get("/mcp").status_code == 404
        assert client.get("/.well-known/oauth-protected-resource/mcp").status_code == 404


def test_enabled_without_config_refuses_to_boot(monkeypatch):
    """The fail-closed guarantee, enforced at construction, not per request."""
    from cmdb.config import Settings

    monkeypatch.setenv("CMDB_MCP_REMOTE_ENABLED", "true")
    monkeypatch.delenv("CMDB_MCP_ISSUER_URL", raising=False)
    with pytest.raises(Exception, match="refusing to serve"):
        Settings()


# --- Rejections ------------------------------------------------------------


def test_no_token_is_401_with_origin_level_resource_metadata(remote_app):
    with _client(remote_app) as client:
        response = _rpc(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert response.status_code == 401
    # Built from resource_server_url, not from the inbound request -- so it must
    # name the public origin even when the reverse proxy reaches this app on some
    # unrelated private address.
    assert (
        'resource_metadata="https://cmdb.example.test/.well-known/oauth-protected-resource/mcp"'
        in response.headers["www-authenticate"]
    )


def test_garbage_token_is_401(remote_app):
    with _client(remote_app) as client:
        assert _rpc(client, {}, token="not-a-token").status_code == 401


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"iss": "https://auth.example.test/application/o/other/"}, id="wrong-issuer"),
        pytest.param({"aud": "some-other-application"}, id="wrong-audience"),
        pytest.param({"exp": int(time.time()) - 120}, id="expired"),
        pytest.param({"groups": ["example-users"]}, id="wrong-group"),
        pytest.param({"groups": []}, id="no-group-service-account"),
        pytest.param({"sub": None}, id="missing-sub"),
    ],
)
def test_bad_claims_are_401(remote_app, rsa_key, overrides):
    if overrides.get("sub", "x") is None:
        overrides = {k: v for k, v in overrides.items() if k != "sub"}
        token = jwt.encode(
            {
                "iss": ISSUER,
                "aud": AUDIENCE,
                "iat": int(time.time()),
                "exp": int(time.time()) + 3600,
                "groups": [GROUP],
            },
            rsa_key,
            algorithm="RS256",
            headers={"kid": KID},
        )
    else:
        token = make_token(rsa_key, **overrides)
    with _client(remote_app) as client:
        assert _rpc(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token).status_code == 401


def test_alg_none_is_401(remote_app):
    token = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": "s", "iat": 0, "exp": 9999999999, "groups": [GROUP]},
        key="",
        algorithm="none",
        headers={"kid": KID},
    )
    with _client(remote_app) as client:
        assert _rpc(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token).status_code == 401


def test_signed_by_a_foreign_key_is_401(remote_app):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = make_token(other)
    with _client(remote_app) as client:
        assert _rpc(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token).status_code == 401


def test_unreachable_jwks_fails_closed(remote_app, rsa_key):
    """An IdP outage must 401, never open the door."""
    remote_app.state.jwks_unreachable["value"] = True
    token = make_token(rsa_key)
    with _client(remote_app) as client:
        assert _rpc(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token).status_code == 401


def test_groups_absent_falls_back_to_userinfo(remote_app, rsa_key):
    token = make_token(rsa_key, groups=None)
    with _client(remote_app) as client:
        ok = _tools_list(client, token)
        assert ok.status_code == 200
        remote_app.state.userinfo_groups["value"] = []
        denied = _rpc(
            client,
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
            make_token(rsa_key, groups=None, jti="second"),
        )
        assert denied.status_code == 401


# --- The one accepted case -------------------------------------------------


def test_valid_token_exposes_exactly_the_read_only_tools(remote_app, rsa_key):
    with _client(remote_app) as client:
        response = _tools_list(client, make_token(rsa_key))
    assert response.status_code == 200
    names = {tool["name"] for tool in response.json()["result"]["tools"]}
    assert len(names) == 13, sorted(names)
    assert not (names & DESTRUCTIVE_TOOLS), sorted(names & DESTRUCTIVE_TOOLS)
    assert "list_hosts" in names


def test_tool_call_succeeds_over_the_transport(remote_app, rsa_key):
    """A real tools/call, not just tools/list -- where the threadpool shim sits.

    build_remote_mcp() registers ``_in_threadpool(fn)`` instead of ``fn``. A
    wrapper that lost the original signature or return annotation still lists
    fine; it only fails when something actually calls it.
    """
    with _client(remote_app) as client:
        token = make_token(rsa_key)
        _tools_list(client, token)
        response = _rpc(
            client,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "list_hosts", "arguments": {}},
            },
            token,
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "error" not in body, body
    assert body["result"].get("isError") is not True, body
    assert "testhost" in json.dumps(body["result"])


def test_remote_tools_run_off_the_event_loop(remote_app):
    """The remote instance awaits its tools; the module-level names stay sync.

    Every tool is blocking SQLAlchemy, and the remote endpoint serves
    concurrent HTTP requests, so registering them raw would let one query --
    vuln_summary over 175k rows -- hold the event loop. Rebinding the
    module-level names instead would break READ_ONLY_TOOLS and
    tests/test_mcp.py, which call them directly.
    """
    import inspect

    from cmdb.mcp import server

    remote = server.build_remote_mcp()
    registered = remote._tool_manager.get_tool("vuln_summary")
    assert registered is not None
    assert registered.is_async is True
    assert registered.fn.__wrapped__ is server.vuln_summary
    assert not inspect.iscoroutinefunction(server.vuln_summary)
    # func_metadata follows __wrapped__, so the schema is the original's.
    assert inspect.signature(registered.fn) == inspect.signature(server.vuln_summary)


def test_stdio_server_is_unchanged_and_carries_all_tools():
    """The remote subset must not have been carved out of the stdio server."""
    import asyncio

    from cmdb.mcp import server

    assert server.mcp.settings.auth is None
    assert server.mcp._token_verifier is None
    tools = asyncio.run(server.mcp.list_tools())
    assert len(tools) == 23
    assert DESTRUCTIVE_TOOLS.issubset({tool.name for tool in tools})


# --- Routing -----------------------------------------------------------------


def test_metadata_document_serves_at_the_origin(remote_app):
    with _client(remote_app) as client:
        response = client.get("/.well-known/oauth-protected-resource/mcp")
        assert response.status_code == 200
        assert response.json()["resource"] == RESOURCE
        assert response.json()["authorization_servers"] == [ISSUER]
        # The bare RFC 8414-style path is NOT where the SDK puts it; asserting
        # the 404 is what proves the path-inserted form is in use, which is the
        # form the outpost carve-out exempts.
        assert client.get("/.well-known/oauth-protected-resource").status_code == 404


def test_mcp_is_not_double_mounted_and_ui_is_untouched(remote_app):
    with _client(remote_app) as client:
        assert client.get("/mcp/mcp").status_code == 404
        assert client.get("/hosts").status_code == 200
        # FastAPI's own 404, not the sub-app's PlainTextResponse -- proves the
        # delegating routes did not swallow unmatched paths.
        missing = client.get("/no-such-page")
        assert missing.status_code == 404
        assert missing.json() == {"detail": "Not Found"}
