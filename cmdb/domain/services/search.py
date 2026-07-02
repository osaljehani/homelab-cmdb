from dataclasses import dataclass

from sqlalchemy import or_
from sqlalchemy.orm import Session

from cmdb.domain.models import Container, Host, Image


@dataclass
class SearchHit:
    kind: str  # "host" | "container" | "image"
    label: str
    sublabel: str
    url: str


def global_search(session: Session, q: str, limit_per_kind: int = 5) -> list[SearchHit]:
    """Search hosts, containers and images by name-ish fields.

    Hosts first, then containers, then images. Queries shorter than two
    characters return nothing (the nav dropdown fires on every keystroke).
    """
    q = (q or "").strip()
    if len(q) < 2:
        return []
    like = f"%{q}%"

    hits: list[SearchHit] = []

    hosts = (
        session.query(Host)
        .filter(
            or_(
                Host.hostname.ilike(like),
                Host.fqdn.ilike(like),
                Host.primary_ipv4.ilike(like),
                Host.tailscale_ipv4.ilike(like),
                Host.tailscale_dns_name.ilike(like),
            )
        )
        .order_by(Host.hostname)
        .limit(limit_per_kind)
        .all()
    )
    for h in hosts:
        parts = [p for p in (h.primary_ipv4, h.os_distribution) if p]
        hits.append(
            SearchHit(
                kind="host",
                label=h.hostname,
                sublabel=" · ".join(parts),
                url=f"/hosts/{h.hostname}",
            )
        )

    containers = (
        session.query(Container)
        .filter(or_(Container.name.ilike(like), Container.image.ilike(like)))
        .order_by(Container.name)
        .limit(limit_per_kind)
        .all()
    )
    for c in containers:
        parts = [p for p in (c.host.hostname if c.host else None, c.state) if p]
        hits.append(
            SearchHit(
                kind="container",
                label=c.name,
                sublabel=" · ".join(parts),
                url=f"/hosts/{c.host.hostname}" if c.host else "/containers",
            )
        )

    images = (
        session.query(Image)
        .filter(Image.ref.ilike(like))
        .order_by(Image.ref)
        .limit(limit_per_kind)
        .all()
    )
    for img in images:
        scanned = (
            f"scanned {img.last_scanned_at:%Y-%m-%d}"
            if img.last_scanned_at
            else "not scanned"
        )
        hits.append(
            SearchHit(kind="image", label=img.ref, sublabel=scanned, url=f"/images/{img.ref}")
        )

    return hits
