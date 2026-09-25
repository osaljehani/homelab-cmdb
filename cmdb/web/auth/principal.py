"""The authenticated identity, however it was established."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Principal:
    """Who is making this request.

    Deliberately not the ``User`` row: a ``proxy`` principal has no row at all,
    and an ``oidc`` one may be linked to a row that carries none of the groups
    the IdP just asserted. ``source`` records which door was used, because the
    group check treats them differently -- see
    :func:`cmdb.web.auth.middleware.authorized`.
    """

    username: str
    email: str | None = None
    groups: frozenset[str] = field(default_factory=frozenset)
    is_admin: bool = False
    source: str = "local"
