"""Authorization code + PKCE against any OIDC provider.

WHY THIS DOES NOT SHARE CODE WITH cmdb/mcp/auth.py

Both verify a JWT from the same issuer, and the resemblance is a trap. That
module verifies a bearer token handed to it by an arbitrary internet caller, and
it is the only boundary on an endpoint deliberately exempted from the reverse
proxy -- so it is under an explicit do-not-refactor note, and every path out of
it must fail closed. This module verifies an id_token *we* just fetched from the
token endpoint over TLS, authenticated with our own client secret. The threat
models differ, the failure modes differ, and merging them would put the MCP
boundary's invariants at the mercy of a login-page change.

WHAT IS CHECKED, AND WHY EACH ONE MATTERS

* ``iss`` -- exact string match against the configured issuer. Authentik's
  per-provider issuer carries a trailing slash and is compared verbatim.
* ``aud`` -- must be our client_id. Without it, an id_token minted for any other
  application on the same IdP would be accepted here.
* ``nonce`` -- must match the one this browser session generated, which is what
  binds the token to this login attempt rather than a replayed earlier one.
* ``state`` -- checked by the route, not here; it binds the redirect back to this
  session and is the CSRF defence for the callback.
* PKCE ``S256`` -- the code cannot be redeemed by anyone who intercepts it
  without the verifier, which never leaves this server.

JWKS is fetched with httpx, not PyJWT's PyJWKClient. That is not a style
preference: PyJWKClient fetches with urllib, whose default User-Agent Cloudflare
blocks -- measured against this deployment, urllib gets 403 and httpx gets 200.
Using it would produce a login that fails only in production.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx
import jwt

from cmdb.config import settings

# Discovery and JWKS are both effectively static; a short TTL keeps a key
# rotation from needing a restart without hammering the IdP on every login.
_DISCOVERY_TTL = 300.0
_JWKS_TTL = 300.0

_discovery: tuple[float, dict] | None = None
_jwks: tuple[float, jwt.PyJWKSet] | None = None


class OidcError(Exception):
    """A login that cannot be completed. The message is shown to the user, so it
    must never carry a token, a code or a client secret."""


@dataclass(frozen=True)
class OidcIdentity:
    subject: str
    username: str
    email: str | None = None
    groups: frozenset[str] = field(default_factory=frozenset)


def new_verifier() -> str:
    """A PKCE code verifier: 43-128 chars of unreserved characters (RFC 7636)."""
    return secrets.token_urlsafe(64)


def challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _issuer() -> str:
    issuer = settings.oidc_issuer_url or ""
    return issuer if issuer.endswith("/") else issuer + "/"


async def _get_json(url: str, **kwargs) -> dict:
    async with httpx.AsyncClient(timeout=settings.mcp_http_timeout) as client:
        response = await client.get(url, **kwargs)
    if response.status_code != 200:
        raise OidcError(f"identity provider returned {response.status_code} for {url}")
    return response.json()


async def discover() -> dict:
    """The provider's OIDC discovery document.

    Appended to the issuer rather than derived from the origin: Authentik serves
    only the per-provider document at ``{issuer}/.well-known/openid-configuration``
    and has nothing at the root.
    """
    global _discovery
    if _discovery and _discovery[0] > time.monotonic():
        return _discovery[1]
    document = await _get_json(_issuer() + ".well-known/openid-configuration")
    for required in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not document.get(required):
            raise OidcError(f"discovery document has no {required}")
    _discovery = (time.monotonic() + _DISCOVERY_TTL, document)
    return document


async def _signing_key(kid: str) -> jwt.PyJWK:
    global _jwks
    if not _jwks or _jwks[0] <= time.monotonic():
        document = await discover()
        _jwks = (
            time.monotonic() + _JWKS_TTL,
            jwt.PyJWKSet.from_dict(await _get_json(document["jwks_uri"])),
        )
    try:
        return _jwks[1][kid]
    except KeyError:
        # A rotation mid-session: refetch once rather than failing the login.
        document = await discover()
        _jwks = (
            time.monotonic() + _JWKS_TTL,
            jwt.PyJWKSet.from_dict(await _get_json(document["jwks_uri"])),
        )
        return _jwks[1][kid]


async def authorization_url(*, state: str, nonce: str, challenge: str) -> str:
    document = await discover()
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.oidc_client_id,
            "redirect_uri": settings.oidc_redirect_url,
            "scope": " ".join(settings.oidc_scopes_list),
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    separator = "&" if "?" in document["authorization_endpoint"] else "?"
    return f"{document['authorization_endpoint']}{separator}{query}"


async def exchange_code(code: str, verifier: str) -> dict:
    """Redeem the authorization code.

    ``client_secret_post`` because that is what Authentik advertises (alongside
    basic); it does not advertise ``none``, so a public PKCE-only client is not
    an option there.
    """
    document = await discover()
    async with httpx.AsyncClient(timeout=settings.mcp_http_timeout) as client:
        response = await client.post(
            document["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.oidc_redirect_url,
                "client_id": settings.oidc_client_id,
                "client_secret": settings.oidc_client_secret,
                "code_verifier": verifier,
            },
        )
    if response.status_code != 200:
        # The body can echo the code back; keep it out of the message.
        raise OidcError(f"token endpoint returned {response.status_code}")
    tokens = response.json()
    if not tokens.get("id_token"):
        raise OidcError("token response carried no id_token")
    return tokens


async def _claims(id_token: str, nonce: str) -> dict:
    header = jwt.get_unverified_header(id_token)
    # Pin the algorithm before touching the key: trusting the header's `alg` is
    # how `alg: none` and HS256-signed-with-the-RSA-public-key get in.
    if header.get("alg") != "RS256":
        raise OidcError(f"unacceptable id_token alg {header.get('alg')!r}")
    kid = header.get("kid")
    if not kid:
        raise OidcError("id_token has no kid")

    key = await _signing_key(kid)
    claims = jwt.decode(
        id_token,
        key.key,
        algorithms=["RS256"],
        issuer=_issuer(),
        audience=settings.oidc_client_id,
        leeway=settings.mcp_clock_skew_seconds,
        options={"require": ["exp", "iat", "iss", "aud", "sub"]},
    )
    if claims.get("nonce") != nonce:
        raise OidcError("id_token nonce does not match this login attempt")
    return claims


async def _groups(claims: dict, access_token: str | None) -> frozenset[str]:
    """Groups from the id_token, falling back to /userinfo.

    Authentik has no ``groups`` scope: the claim rides on the stock ``profile``
    scope mapping. Requesting a scope by that name gets nothing, so the fallback
    exists for providers that only expose it at the userinfo endpoint.
    """
    claimed = claims.get(settings.oidc_groups_claim)
    if isinstance(claimed, list):
        return frozenset(str(group) for group in claimed)

    document = await discover()
    endpoint = document.get("userinfo_endpoint")
    if not endpoint or not access_token:
        return frozenset()
    try:
        info = await _get_json(
            endpoint, headers={"Authorization": f"Bearer {access_token}"}
        )
    except OidcError:
        return frozenset()
    found = info.get(settings.oidc_groups_claim)
    return frozenset(str(group) for group in found) if isinstance(found, list) else frozenset()


async def identity_from_tokens(tokens: dict, nonce: str) -> OidcIdentity:
    claims = await _claims(tokens["id_token"], nonce)
    username = (
        claims.get("preferred_username")
        or (claims.get("email") or "").split("@")[0]
        or claims["sub"]
    )
    return OidcIdentity(
        subject=str(claims["sub"]),
        username=str(username),
        email=claims.get("email"),
        groups=await _groups(claims, tokens.get("access_token")),
    )


def reset_caches() -> None:
    """Test hook, and a way to pick up a rotated key without a restart."""
    global _discovery, _jwks
    _discovery = None
    _jwks = None
