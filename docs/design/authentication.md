# Design: authentication via an external OIDC identity provider (Authentik)

**Status:** proposed — draft for discussion, nothing implemented yet
**Date:** 2026-09-21
**Related:** ROADMAP "Larger / future → 2. Authentication"; `SECURITY.md` deployment posture;
[MCP authorization spec](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/authorization)
(RFC 9728 protected-resource metadata, RFC 8414 / OIDC discovery).

All hostnames in this document are placeholders (`idp.example.com`, `cmdb.example.com`).

## Problem

HomeLabCMDB has no authentication at all. The web UI (which can mutate inventory), the read-only
JSON API, and the MCP server all trust anyone who can reach them. That is documented as a deliberate
trade-off for a LAN-only deployment, but it blocks two things the project now wants:

1. **Reaching the CMDB from outside the LAN** — a public hostname behind a tunnel, so the
   inventory is usable from anywhere without a VPN.
2. **Cloud AI clients over MCP** — claude.ai custom connectors, ChatGPT connectors, Claude Code's
   remote MCP support, and similar clients speak MCP over HTTP and authorize with OAuth 2.1. They
   cannot spawn the current stdio server, and they need a bearer-token-protected HTTP endpoint that
   advertises its authorization server.

The homelab now runs **Authentik** as its identity provider (IdP). It supplies OAuth2/OIDC —
authorization code with PKCE, device code, `client_credentials`, token exchange, JWKS, and
per-application discovery documents. The CMDB should delegate all authentication to it rather than
grow a password table of its own.

## Current state (facts, from the code)

| Surface | Today | Mutating? |
|---|---|---|
| Web UI (`cmdb/web/`, 12 routers, FastAPI + HTMX) | no auth | yes — tags, notes, fields, imports, collect, cluster/node CRUD, image delete/noisy |
| JSON API (`/api/v1`, GET only) | no auth, documented as "keep LAN-only" | no |
| MCP server (`cmdb/mcp/server.py`, FastMCP, **stdio only**) | no auth; spawned by the client with direct SQLite access | yes — `add_tag`/`remove_tag`/`delete_host`/`import_ansible`/`add_cluster`/… |
| CLI | local process, direct DB | n/a |
| Scan feeds (`scripts/trivy-scan*.sh`, `cmdb_feed=http`) | POST to `/import/upload/trivy` unauthenticated | yes |

Other relevant facts:

- `CMDB_SECRET_KEY` exists in `Settings` and the README but **nothing reads it** — no session
  middleware, no signed cookies. It becomes the session-cookie key in this design.
- The Docker image installs the `collect` dependency group but **not `mcp`**; a remote MCP endpoint
  served from the container needs that group added to the image build.
- The pinned MCP Python SDK (`mcp==1.28.0` in `uv.lock`) ships server-side OAuth *resource server*
  support: a `TokenVerifier` hook, `AuthSettings(issuer_url, resource_server_url, required_scopes)`,
  bearer middleware, a `WWW-Authenticate: Bearer resource_metadata=…` challenge on 401, and an RFC
  9728 document at `/.well-known/oauth-protected-resource/<path>`. It also pulls in `pyjwt[crypto]`,
  so JWT/JWKS validation needs no new dependency beyond making it explicit.
- Tests drive the web app with `TestClient` + `app.dependency_overrides`, and call MCP tool
  functions directly with `get_session` monkeypatched. Both patterns extend cleanly to an auth
  dependency that can be overridden.
- Demo mode (`cmdb demo`, `cmdb-demo` compose profile) must keep working with zero setup.

## Constraints from the IdP (facts, verified on the deployed Authentik 2026.8.x)

These shape the design more than anything in this repo:

- **No anonymous Dynamic Client Registration.** DCR exists but requires a bearer token with a
  dedicated scope, and the `registration_endpoint` is only advertised when a DCR object is
  configured. Cloud AI clients that expect open DCR will not self-register. **Supported path:
  provision OAuth2 clients by hand and paste `client_id`/`client_secret` into the connector.**
- **No root-level discovery document.** Discovery lives only at
  `https://idp.example.com/application/o/<slug>/.well-known/openid-configuration`. The issuer URL
  therefore has a path. The MCP spec requires clients to try, in order: path-inserted OAuth 2.0
  metadata, path-inserted OIDC discovery, then **path-appended OIDC discovery** — which is the
  Authentik form. Spec-compliant clients find it; clients that only probe the origin root do not.
- **`token_endpoint_auth_methods_supported` has no `none`.** Public PKCE-only clients are not
  advertised; confidential client + secret is the path. PKCE is supported (`S256`) and clients
  should send it, but the server cannot be configured to *require* it.
- **`offline_access` must be requested explicitly** or no refresh token is issued.
  `client_credentials` never yields a refresh token regardless (RFC 6749 §4.4.3).
- **An OAuth2 client for MCP already exists** (slug `homelab-mcp`): confidential; grants
  `authorization_code`, `refresh_token`, `client_credentials`, `device_code`; scopes `openid`,
  `profile`, `email`, `offline_access`; strict redirect URIs for the claude.ai and ChatGPT
  connector callbacks; signed with the IdP's self-signed key and published via JWKS.
- **The IdP's MFA stage currently skips users with no enrolled device** (accepted risk, documented
  in the homelab notes with the reasoning). The note says to revisit that *before the first service
  delegates login to Authentik*. This project is that first service — see Open question 8.

## Goals

- Every network-reachable surface (web UI, JSON API, MCP over HTTP) requires a valid identity
  from the IdP when auth is enabled. Local surfaces (CLI, stdio MCP) stay trust-by-process.
- Browser login is standard OIDC Authorization Code + PKCE; no local passwords, ever.
- Machine access (scripts, cloud AI, agents) is OAuth 2.1 bearer tokens issued by the IdP —
  `authorization_code` for user-delegated clients, `client_credentials` for service accounts.
- The MCP server becomes an MCP-spec-compliant **OAuth resource server** reachable over
  Streamable HTTP, so claude.ai / ChatGPT / Claude Code remote MCP can connect with the
  manually-provisioned client.
- Coarse authorization: read vs write, derived from IdP groups and/or scopes, applied uniformly
  across web, API, and MCP tools.
- Opt-in and backwards compatible: `CMDB_AUTH_MODE=off` behaves exactly like today.

## Non-goals

- Acting as an authorization server (issuing tokens, hosting login forms). The IdP does that.
- Per-object or per-host permissions. Two roles (viewer, admin) are enough for a homelab.
- Local users / password fallback. Break-glass is `CMDB_AUTH_MODE=off` on the LAN.
- Replacing the stdio MCP transport. It remains for local Claude Code / Claude Desktop.
- Forward-auth / proxy-provider integration. Native OIDC keeps the API and MCP bearer paths
  first-class; a proxy in front would not give the MCP client the 401 + resource-metadata
  handshake it needs.

## Design

### 1. One auth core, three adapters

```
cmdb/auth/
  settings.py     CMDB_AUTH_* settings (see table below); validated at startup
  oidc.py         discovery-document + JWKS fetch with caching; token verification
  principal.py    Principal(subject, name, email, groups, scopes, via="session"|"bearer")
  roles.py        Role = viewer | admin, resolved from groups/scopes; require_role() helpers
  web.py          FastAPI dependencies: current_user (session cookie) and bearer_principal
  mcp.py          TokenVerifier for FastMCP; per-tool role checks
```

`Principal` is the single object every adapter produces and every authorization check consumes.
The web session, the API bearer path and the MCP bearer path all normalize into it, so role rules
are written once.

**Token verification** validates IdP-issued JWT access tokens locally: signature against the
cached JWKS (refreshed on unknown `kid`), `iss` equals the configured issuer, `exp`/`nbf`, and
`aud` in the configured allowed audiences. Local validation keeps the CMDB usable during the IdP's
nightly backup blip and keeps per-request latency off the IdP. Token introspection is a possible
fallback (Open question 6) but not the default.

### 2. Web UI: OIDC Authorization Code + PKCE, server-side session

- Add Starlette `SessionMiddleware` keyed by `CMDB_SECRET_KEY` (finally used; startup refuses
  the default value when auth is on). Cookie: `HttpOnly`, `SameSite=Lax`, `Secure` when the
  public URL is https.
- New router `cmdb/web/routes/auth.py`: `GET /auth/login` (redirect to the IdP with `state`,
  `nonce`, PKCE `S256`), `GET /auth/callback` (exchange code, validate ID token, store a compact
  `Principal` in the session), `POST /auth/logout` (clear session, then RP-initiated logout at
  the IdP's `end_session_endpoint`).
- A `current_user` dependency attached at the `include_router` level for every page router. Not
  protected: `/static`, `/auth/*`, `/healthz` (new, also useful for the fleet startup verify).
- **HTMX:** a partial request that hits an expired session must not get a login page injected
  into a table cell. Unauthenticated `HX-Request` calls return `401` with `HX-Redirect: /auth/login`.
- Nav shows the display name and a Logout button; mutating controls are hidden for viewers, and
  the routes enforce the role regardless (the template is convenience, the dependency is the
  boundary).
- Reverse-proxy awareness: `cmdb serve` passes `proxy_headers=True` and a configurable
  `forwarded_allow_ips` to uvicorn so the callback `redirect_uri` is built with the public
  https origin, not the container's `http://0.0.0.0:8080`.
- CSRF: the session cookie is `SameSite=Lax` and the OIDC `state` is bound to the session. All
  mutating web routes are POST/DELETE reached from same-origin forms/HTMX; a same-site cookie is
  sufficient for a homelab. No additional token layer proposed.
- Library: **Authlib** (Starlette integration) for the browser flow — it handles discovery, PKCE,
  `state`/`nonce`, ID-token validation and the token exchange. Alternative is ~200 lines of
  `httpx` + `pyjwt` by hand. See Open question 7.

### 3. JSON API: bearer tokens, and an authenticated ingest endpoint

- `/api/v1` gains a `bearer_principal` dependency: `Authorization: Bearer <IdP access token>`,
  validated as in §1. A missing/invalid token gets `401` with `WWW-Authenticate: Bearer`.
- The read endpoints require role `viewer`.
- **New:** `POST /api/v1/import/trivy` (role `admin`, or a dedicated `cmdb:ingest` scope) as the
  machine-facing replacement for `POST /import/upload/trivy`, which becomes a web-session-only
  form endpoint. `scripts/trivy-scan.sh` (`cmdb_feed=http`) and `docs/image-scanning.md` switch to
  the new endpoint and take a `cmdb_token` (or `client_id`/`client_secret` to fetch one via
  `client_credentials`). The `docker exec` feed used on the CMDB host is unaffected.
- Optionally the browser session is also accepted on `/api/v1` so `/docs` stays usable from a
  logged-in browser; bearer remains the documented contract.

### 4. MCP: Streamable HTTP as an OAuth resource server

- `cmdb mcp` gains `--transport stdio|streamable-http` (stdio stays the default, unchanged, no
  auth — it is a local process with direct DB access, same trust level as the CLI).
- For HTTP, the FastMCP instance is built with a `TokenVerifier` (§1) and
  `AuthSettings(issuer_url=<IdP issuer for the CMDB provider>, resource_server_url=<public
  CMDB URL>/mcp, required_scopes=[...])`. The SDK then:
  - challenges unauthenticated calls with `401` + `WWW-Authenticate: Bearer
    resource_metadata="https://cmdb.example.com/.well-known/oauth-protected-resource/mcp"`;
  - serves that RFC 9728 document with `authorization_servers: [<issuer>]`, from which a
    spec-compliant client derives the IdP's discovery URL (path-appended OIDC form, see
    Constraints) and runs Authorization Code + PKCE with the pasted `client_id`/`client_secret`.
- **Hosting:** mount the MCP ASGI app at `/mcp` inside the existing FastAPI app (one container,
  one port, one public hostname), joining the two lifespans. The `.well-known` route must be
  registered at the **origin root** of the FastAPI app, not under the `/mcp` mount, or clients
  never find it. Alternative: a second port/process — see Open question 5.
- **Per-tool authorization:** tools are tagged read or write; the write set (`add_tag`,
  `remove_tag`, `delete_host`, `add_cluster`, `delete_cluster`, `add_node`, `remove_node`,
  `import_ansible`, `set_image_noisy`, `delete_image`) requires role `admin`. The check reads the
  `Principal` from the request context the SDK exposes (`get_access_token()`), so the tool bodies
  themselves stay untouched. Alternatively expose only read tools over HTTP (Open question 3).
- `import_ansible(path)` reads a **server-side** path. Over HTTP that is a remote-file-read
  primitive gated only by role. It should be removed from the HTTP tool set (or restricted to a
  configured import directory) as part of this work.
- The Docker image adds the `mcp` dependency group; `docker-compose.yml` gains the new env vars.

### 5. Roles and claims

Two roles, resolved in this order, first match wins:

1. `client_credentials` service accounts: role from scopes — `cmdb:write` ⇒ admin, `cmdb:read`
   ⇒ viewer. (Custom scope mappings on the IdP provider; the service account user is auto-created
   by the IdP on first use.)
2. Users: role from the `groups` claim — membership in `CMDB_AUTH_ADMIN_GROUPS` ⇒ admin,
   `CMDB_AUTH_VIEWER_GROUPS` ⇒ viewer. The IdP's default `profile` scope mapping emits group
   names, so no custom mapping is needed for this.
3. Otherwise: **403**, even with a valid token. Being a valid IdP user is not enough; the
   application must be assigned. (Also bind the IdP application to those groups so unassigned
   users are refused at the IdP before reaching the CMDB.)

### 6. Configuration surface

| Variable | Default | Purpose |
|---|---|---|
| `CMDB_AUTH_MODE` | `off` | `off` (today's behaviour) or `oidc` |
| `CMDB_PUBLIC_URL` | _(unset)_ | External origin, e.g. `https://cmdb.example.com`; builds redirect URIs and the MCP resource URL. Required when `oidc` |
| `CMDB_OIDC_ISSUER` | _(unset)_ | IdP issuer for the web-login provider, e.g. `https://idp.example.com/application/o/homelab-cmdb/` |
| `CMDB_OIDC_CLIENT_ID` / `CMDB_OIDC_CLIENT_SECRET` | _(unset)_ | Web-login client credentials (secret via env or `_FILE`) |
| `CMDB_OIDC_SCOPES` | `openid profile email` | Scopes requested at login |
| `CMDB_AUTH_AUDIENCES` | web client id | Comma-separated `aud` values accepted on bearer tokens (add the MCP client id here) |
| `CMDB_AUTH_ADMIN_GROUPS` | `cmdb-admins` | IdP groups mapped to admin |
| `CMDB_AUTH_VIEWER_GROUPS` | `cmdb-viewers` | IdP groups mapped to viewer |
| `CMDB_MCP_ISSUER` | `CMDB_OIDC_ISSUER` | Issuer advertised in protected-resource metadata (differs if MCP uses its own provider) |
| `CMDB_FORWARDED_ALLOW_IPS` | `127.0.0.1` | Proxies whose `X-Forwarded-*` uvicorn trusts (the tunnel container's address) |
| `CMDB_SECRET_KEY` | `change-me-in-production` | Session-cookie signing key; refused when `oidc` and left at default |

Startup validates the combination (issuer reachable, discovery document parseable, secret key
set) and fails fast with a clear message rather than serving a half-configured login.

### 7. IdP-side provisioning (documented, not automated by this repo)

- Provider + application `homelab-cmdb` for browser login: confidential, `authorization_code` +
  `refresh_token`, redirect URI `https://cmdb.example.com/auth/callback`, scopes `openid profile
  email`, signing key published via JWKS, access-token JWTs.
- Reuse the existing `homelab-mcp` provider for MCP and machine clients (or fold both into one,
  Open question 2). Add custom scope mappings `cmdb:read` / `cmdb:write` and enable them on the
  provider.
- Groups `cmdb-admins`, `cmdb-viewers`; bind both applications' access policy to them.
- Before go-live: the MFA `not_configured_action` decision (Open question 8).

### 8. Deployment sketch

```
browser / claude.ai / ChatGPT / Claude Code
        │  https://cmdb.example.com          (tunnel or reverse proxy, TLS at the edge)
        ▼
  cmdb container :8080  ── /            web UI, session cookie
                        ── /api/v1      bearer
                        ── /mcp         bearer, Streamable HTTP
                        ── /.well-known/oauth-protected-resource/mcp
        │  validates tokens offline (JWKS cached); fetches discovery + JWKS from
        ▼
  https://idp.example.com/application/o/<slug>/   (Authentik)
```

The IdP's nightly restart only affects new logins and token refreshes; existing sessions and
cached JWKS keep working.

## Phases

Each phase is a separate PR, mergeable on its own, with `CMDB_AUTH_MODE=off` unchanged throughout.

| # | Phase | Deliverable | Depends on |
|---|---|---|---|
| 0 | Design (this doc) | Decisions on the open questions | — |
| 1 | Auth core | `cmdb/auth/` settings, discovery/JWKS cache, JWT verification, `Principal`, roles; unit tests with a locally generated RSA key and a stubbed discovery document | 0 |
| 2 | Web login | Session middleware, `/auth/*` routes, `current_user` dependency on all page routers, HTMX 401 handling, nav user/logout, `/healthz`, proxy-header settings | 1 |
| 3 | API bearer + ingest | Bearer dependency on `/api/v1`, `POST /api/v1/import/trivy`, scan scripts + `docs/image-scanning.md` updated | 1 |
| 4 | MCP over HTTP | `--transport streamable-http`, `TokenVerifier`, `AuthSettings`, root-level `.well-known`, per-tool roles, `import_ansible` restriction, `mcp` group in the image | 1, 2 (shared app) |
| 5 | Docs + release | README env table and "Authentication" section, `SECURITY.md` posture rewrite, `CHANGELOG`, ROADMAP item closed, connector setup walkthrough for claude.ai / Claude Code | 2–4 |

Estimated size: phases 1–2 are the bulk (~600–900 lines incl. tests); 3 and 4 are each a few
hundred; 5 is docs only.

## Test strategy

- **Unit (auth core):** generate an RSA keypair in the test, serve a fake JWKS + discovery
  document from an in-process `httpx` mock transport, mint tokens with controlled `iss`/`aud`/
  `exp`/`groups`/`scope`, assert accept/reject and role resolution. No network.
- **Web:** `TestClient` with `CMDB_AUTH_MODE=oidc` and the discovery mock: unauthenticated page →
  302 to `/auth/login`; unauthenticated HTMX → 401 + `HX-Redirect`; callback with a valid
  `state`/code → session set; viewer POSTing to a mutating route → 403; `CMDB_AUTH_MODE=off` →
  every existing web test passes unchanged.
- **API:** no bearer → 401 with `WWW-Authenticate`; viewer token → 200 on GETs, 403 on ingest;
  service-account token with `cmdb:write` → 200 on ingest.
- **MCP over HTTP:** start the ASGI app in-process; `GET /.well-known/oauth-protected-resource/mcp`
  matches the configured issuer; `POST /mcp` without a token → 401 with `resource_metadata`; with
  a viewer token, `list_hosts` works and `delete_host` is refused.
- All fixtures use fictional data per `CONTRIBUTING.md`.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Lock-out (IdP down, misconfigured group) | `CMDB_AUTH_MODE=off` on the LAN is the break-glass; startup validation catches config errors before they hide the UI |
| Clock skew breaks `exp`/`nbf` | Small leeway (60 s); IdP and CMDB host are the same machine today |
| Cloud client cannot discover the IdP (no root discovery doc) | Protected-resource metadata points at the exact issuer; document the path-appended form; verify claude.ai and Claude Code against it before calling phase 4 done |
| Public exposure widens the attack surface of a formerly LAN-only app | Auth on every route by construction (router-level dependency, tested); `import_ansible` path read removed from HTTP; rate limiting left to the edge |
| Token replay against the wrong service | `aud` check against an explicit allow-list; MCP and web can use distinct client ids |
| Session fixation / CSRF | `state` + `nonce`, `SameSite=Lax`, session regenerated at login |
| Secret in compose env | Support `CMDB_OIDC_CLIENT_SECRET_FILE`; never log tokens or the secret |
| New dependency surface (Authlib) | Pin in `uv.lock`; it is widely used with Starlette/FastAPI and covers the fiddly parts (PKCE, nonce, ID-token validation) that are easy to get subtly wrong by hand |

## Open questions (decisions needed before phase 1)

1. **Public reachability.** Cloud AI clients need `https://cmdb.example.com` reachable from the
   internet, which means a tunnel hostname. Dedicated tunnel for the CMDB, or share the IdP's?
   If the CMDB stays LAN/VPN-only instead, phase 4 still serves Claude Code / Claude Desktop over
   HTTP on the private network, but claude.ai and ChatGPT cannot reach it. *Recommendation:* a
   dedicated tunnel hostname, same reasoning that gave the IdP its own.
2. **One IdP provider or two?** Reuse `homelab-mcp` for MCP and add `homelab-cmdb` for browser
   login (distinct redirect URIs, lifetimes, audiences), or a single provider for everything.
   *Recommendation:* two; `CMDB_AUTH_AUDIENCES` accepts both.
3. **Write tools over HTTP.** Expose the full 23-tool set with role gating, or a read-only subset
   for remote clients (writes stay on stdio/CLI)? *Recommendation:* full set gated by role,
   except `import_ansible`, which is dropped from HTTP.
4. **Default posture for the public project.** Keep `CMDB_AUTH_MODE=off` as the default so
   existing users are unaffected, with a startup warning when the bind address is not loopback?
   *Recommendation:* yes, off by default, loudly documented.
5. **MCP hosting.** Mount at `/mcp` in the same app and port (one hostname, one container), or a
   separate `cmdb mcp --transport streamable-http --port 8081` process? *Recommendation:* mount;
   it keeps the `.well-known` document and the resource URL on one origin.
6. **Token validation.** Local JWT/JWKS validation (offline, fast) as proposed, or IdP
   introspection per request (instant revocation, IdP dependency on every call)? *Recommendation:*
   JWT with a short IdP access-token lifetime (e.g. 10–15 min); revocation then bounds at that.
7. **Dependencies.** OK to add `authlib` (browser flow) and `itsdangerous` (Starlette sessions),
   and to promote `pyjwt[crypto]` to a core dependency? Alternative is a hand-rolled flow with
   `httpx` + `pyjwt` only.
8. **IdP MFA posture.** The homelab notes flag the MFA `skip` setting for revisit before the first
   consumer goes live. Should the switch to `deny` be a go-live prerequisite for phase 2, or
   tracked separately? (Out of this repo's scope, but it gates whether "SSO" here is actually
   stronger than no auth on a LAN.)
9. **Session lifetime and refresh.** Browser sessions: fixed lifetime (e.g. 12 h) with re-login,
   or silent refresh using a stored refresh token (`offline_access`)? *Recommendation:* fixed
   lifetime, no refresh tokens in the web session — simpler and nothing to leak.
10. **Homelab documentation.** After the decisions land, should the vault's CMDB and IdP notes
    and the IdP deployment's own docs (new client, groups, scope mappings) be updated as part of
    the same effort, in their own repos?
