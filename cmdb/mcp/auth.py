"""Bearer-token verification for the remote MCP endpoint.

``/mcp`` is deliberately exempt from the Authentik proxy provider that guards
the rest of this app: a proxy answers an unauthenticated request with a 302 to
an interactive login page, and a cloud AI client cannot complete one. So the
checks in this module are not defence in depth -- they are the *only* boundary
between the fleet's inventory and the internet.

Three things carry that weight, and all three are easy to leave out:

* **``aud``.** The MCP SDK's ``RequireAuthMiddleware`` enforces ``required_scopes``
  and nothing else -- it never looks at ``aud`` or ``resource``. Authentik sets
  ``aud`` to the provider's client_id, so without this check a token minted for
  *any* application on the same Authentik would be accepted here.
* **Group membership.** The ``homelab-mcp`` client_credentials service account
  is a real, validly-signed principal that belongs to no group. Requiring
  ``homelab-admins`` excludes every machine credential structurally, rather than
  relying on them never being pointed at this endpoint.
* **Failing closed.** Every path out of :meth:`AuthentikTokenVerifier.verify_token`
  that is not a fully verified, in-group token returns ``None``, which the SDK
  turns into a 401. An unreachable JWKS, a rotated key, a malformed token, an
  unexpected bug -- all 401. Nothing here may raise: an exception escaping into
  the middleware becomes a 500, which is not a refusal a client can act on.

JWKS is fetched with httpx rather than PyJWT's own ``PyJWKClient``. That is not
a style preference. ``PyJWKClient`` fetches with ``urllib``, whose default
User-Agent Cloudflare blocks -- measured 2026-09-22 against this deployment:
``Python-urllib/3.x`` gets 403, httpx gets 200. Using it would have produced an
endpoint that 401s every token in production while every local test passed.
httpx is also async, so the fetch does not need a thread hop off the event loop.
"""

from __future__ import annotations

import hashlib
import logging
import time

import anyio
import httpx
import jwt
from mcp.server.auth.provider import AccessToken

logger = logging.getLogger(__name__)

# One forced JWKS refetch per this many seconds when a token presents an
# unknown `kid`. Without the floor, a flood of junk tokens carrying random kids
# would turn this endpoint into an amplifier pointed at the IdP.
_FORCED_REFETCH_INTERVAL = 30.0


class _JwksCache:
    """TTL'd JWKS with single-flight refresh and a rate-limited kid-miss refetch."""

    def __init__(self, url: str, ttl: float, timeout: float) -> None:
        self._url = url
        self._ttl = ttl
        self._timeout = timeout
        self._keys: jwt.PyJWKSet | None = None
        self._expires_at = 0.0
        self._last_forced = 0.0
        self._lock = anyio.Lock()

    async def _fetch(self) -> None:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(self._url)
        response.raise_for_status()
        # Raises on a malformed document, which is what we want: a bad JWKS
        # must not quietly replace a good one.
        self._keys = jwt.PyJWKSet.from_dict(response.json())
        self._expires_at = time.monotonic() + self._ttl

    async def signing_key(self, kid: str) -> jwt.PyJWK:
        async with self._lock:
            if self._keys is None or time.monotonic() >= self._expires_at:
                await self._fetch()
            try:
                return self._keys[kid]
            except KeyError:
                now = time.monotonic()
                if now - self._last_forced < _FORCED_REFETCH_INTERVAL:
                    raise
                self._last_forced = now
                await self._fetch()
                return self._keys[kid]


class AuthentikTokenVerifier:
    """Verify an Authentik-issued RS256 access token for the remote MCP endpoint.

    Implements the SDK's ``TokenVerifier`` protocol, which is a single method.
    """

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str,
        userinfo_url: str | None = None,
        required_groups: frozenset[str] = frozenset(),
        algorithms: tuple[str, ...] = ("RS256",),
        leeway: int = 60,
        jwks_cache_seconds: int = 300,
        userinfo_cache_seconds: int = 60,
        timeout: float = 5.0,
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._algorithms = list(algorithms)
        self._jwks = _JwksCache(jwks_url, jwks_cache_seconds, timeout)
        self._userinfo_url = userinfo_url
        self._required_groups = required_groups
        self._leeway = leeway
        self._userinfo_ttl = userinfo_cache_seconds
        self._timeout = timeout
        self._groups_cache: dict[str, tuple[float, frozenset[str]]] = {}

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            return await self._verify(token)
        except Exception as exc:
            # The single fail-closed funnel. Log the shape of the failure so a
            # misconfiguration is diagnosable, but never the token itself.
            logger.warning("MCP token rejected: %s: %s", type(exc).__name__, exc)
            return None

    async def _verify(self, token: str) -> AccessToken | None:
        header = jwt.get_unverified_header(token)
        # Pin the algorithm before touching the key. Reading `alg` from the
        # header and trusting it is how `alg: none` and
        # HS256-signed-with-the-RSA-public-key get in.
        if header.get("alg") not in self._algorithms:
            raise ValueError(f"unacceptable alg {header.get('alg')!r}")
        kid = header.get("kid")
        if not kid:
            raise ValueError("token has no kid")
        key = await self._jwks.signing_key(kid)

        claims = jwt.decode(
            token,
            key.key,
            algorithms=self._algorithms,
            issuer=self._issuer,
            audience=self._audience,
            leeway=self._leeway,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )

        if self._required_groups:
            groups = await self._groups_for(token, claims)
            if not self._required_groups.issubset(groups):
                raise ValueError("token principal is not in the required group(s)")

        return AccessToken(
            token=token,
            client_id=claims.get("azp") or self._audience,
            scopes=(claims.get("scope") or "").split(),
            expires_at=int(claims["exp"]),
            subject=claims.get("sub"),
            claims=claims,
        )

    async def _groups_for(self, token: str, claims: dict) -> frozenset[str]:
        """Groups from the access token, falling back to /userinfo.

        The claim is normally present: Authentik's stock `profile` scope mapping
        emits it (there is no separate `groups` scope), and the provider sets
        include_claims_in_id_token, which is what puts scope-mapping claims into
        the access token. The fallback is a safety net for a client that did not
        request `profile` -- not the primary path.

        An unresolvable group set is an empty one, i.e. denied.
        """
        claimed = claims.get("groups")
        if isinstance(claimed, list):
            return frozenset(str(group) for group in claimed)
        if not self._userinfo_url:
            return frozenset()

        # Keyed on jti where available, else a digest -- never the raw token,
        # which would put credentials in a long-lived dict.
        cache_key = claims.get("jti") or hashlib.sha256(token.encode()).hexdigest()
        now = time.monotonic()
        cached = self._groups_cache.get(cache_key)
        if cached and cached[0] > now:
            return cached[1]

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(
                self._userinfo_url, headers={"Authorization": f"Bearer {token}"}
            )
        if response.status_code != 200:
            return frozenset()
        groups = frozenset(str(group) for group in (response.json().get("groups") or ()))

        if len(self._groups_cache) > 256:
            self._groups_cache.clear()
        self._groups_cache[cache_key] = (now + self._userinfo_ttl, groups)
        return groups
