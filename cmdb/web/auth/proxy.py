"""Identity asserted by a trusted reverse proxy.

This is how the homelab deployment works: an Authentik proxy provider on the
embedded outpost authenticates the request (SSO, MFA, group binding) and adds
``X-authentik-username`` / ``-email`` / ``-groups``, and the app reads them.

**The trust is in the network path, not in the headers.** Anything that can
reach the port can assert any username here -- there is no signature to check.
That is only acceptable because the container publishes on ``172.17.0.1:8080``
(docker0) and is closed to the LAN, so the sole route in is through the outpost.
Publishing this port more widely, in any mode where these headers are honoured,
hands out unauthenticated admin. Two consequences:

* the headers are read **only** when ``proxy`` is in CMDB_AUTH_MODE, never as a
  fallback in ``local``/``oidc`` mode, where they carry no weight at all;
* the header names are configurable, so oauth2-proxy, Traefik forward-auth and
  Cloudflare Access work without a code change.
"""

from __future__ import annotations

from cmdb.config import settings
from cmdb.web.auth.principal import Principal


def principal_from_headers(headers) -> Principal | None:
    """Build a principal from the configured headers, or None if unauthenticated."""
    username = (headers.get(settings.auth_proxy_user_header) or "").strip()
    if not username:
        return None

    raw_groups = headers.get(settings.auth_proxy_groups_header) or ""
    separator = settings.auth_proxy_groups_separator
    # Authentik joins with "|". Splitting on the empty string would raise, so a
    # misconfigured separator degrades to "one group" rather than a 500.
    parts = raw_groups.split(separator) if separator else [raw_groups]
    groups = frozenset(part.strip() for part in parts if part.strip())

    return Principal(
        username=username,
        email=(headers.get(settings.auth_proxy_email_header) or "").strip() or None,
        groups=groups,
        source="proxy",
    )
