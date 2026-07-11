from sqlalchemy.orm import Session

from cmdb.domain.models import Host, Tag


def list_hosts(session: Session, tag: str | None = None, os_family: str | None = None) -> list[Host]:
    q = session.query(Host)
    if tag:
        q = q.filter(Host.tags.any(Tag.name == tag.lower().strip()))
    if os_family:
        q = q.filter(Host.os_family.ilike(f"%{os_family}%"))
    return q.order_by(Host.hostname).all()


def get_host(session: Session, hostname: str) -> Host | None:
    return session.query(Host).filter_by(hostname=hostname).first()


def add_tag(session: Session, hostname: str, tag_name: str) -> Host:
    host = get_host(session, hostname)
    if not host:
        raise ValueError(f"Host '{hostname}' not found")
    name = tag_name.lower().strip()
    tag = session.query(Tag).filter_by(name=name).first()
    if not tag:
        tag = Tag(name=name)
        session.add(tag)
        session.flush()
    if tag not in host.tags:
        host.tags.append(tag)
    return host


def remove_tag(session: Session, hostname: str, tag_name: str) -> Host:
    host = get_host(session, hostname)
    if not host:
        raise ValueError(f"Host '{hostname}' not found")
    name = tag_name.lower().strip()
    host.tags = [t for t in host.tags if t.name != name]
    return host


def _require_host(session: Session, hostname: str) -> Host:
    host = get_host(session, hostname)
    if not host:
        raise ValueError(f"Host '{hostname}' not found")
    return host


def set_notes(session: Session, hostname: str, notes: str | None) -> Host:
    host = _require_host(session, hostname)
    host.notes = notes or None
    session.flush()
    return host


def set_custom_field(session: Session, hostname: str, key: str, value: str) -> Host:
    host = _require_host(session, hostname)
    # Assign a fresh dict: SQLAlchemy does not track in-place JSON mutation.
    host.custom_fields = {**(host.custom_fields or {}), key.strip(): value}
    session.flush()
    return host


def remove_custom_field(session: Session, hostname: str, key: str) -> Host:
    host = _require_host(session, hostname)
    fields = dict(host.custom_fields or {})
    fields.pop(key.strip(), None)
    host.custom_fields = fields
    session.flush()
    return host


def delete_host(session: Session, hostname: str) -> bool:
    host = get_host(session, hostname)
    if not host:
        return False
    session.delete(host)
    return True
