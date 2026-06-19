"""On-demand (agentless) collection.

Instead of running export scripts on each device and uploading the output, this
module drives the `ansible` binary from the CMDB host (the controller) over SSH and
feeds the results straight into the existing import pipeline. No new parsing lives
here   facts go through ``ansible.import_from_path`` and containers through
``docker_import.import_containers``.

Requires the ``ansible`` binary on PATH (install the ``collect`` dependency group:
``uv sync --group collect``) and an Ansible inventory describing the hosts.
"""

import json
import subprocess
import tempfile
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy.orm import Session

from cmdb.config import settings
from cmdb.domain.models import ImportLog, ImportSource
from cmdb.domain.services import ansible as ansible_svc
from cmdb.domain.services.docker_import import import_containers
from cmdb.domain.services.generate import _get_hosts, generate_inventory_yaml

# Same command scripts/docker-export.sh runs; one JSON object per line.
_DOCKER_PS_CMD = "docker ps --all --no-trunc --format '{{json .}}'"

# Give a slow homelab room to answer, but don't hang a web request forever.
_TIMEOUT_SECONDS = 300


class CollectError(Exception):
    """Raised when collection cannot start at all (no inventory, ansible missing)."""


def resolve_inventory(explicit: str | None = None) -> str | None:
    """Return an explicit/env inventory path, or None to generate one from the DB."""
    return explicit or settings.ansible_inventory


@contextmanager
def inventory_for(session: Session, explicit: str | None = None) -> Iterator[str]:
    """Yield an Ansible inventory path to run against.

    An explicit path (CLI ``-i`` or an uploaded file) or ``CMDB_ANSIBLE_INVENTORY`` is
    used as-is. Otherwise a self-contained inventory is generated from the database
    (hosts + SSH vars) into a temp file that is removed when the context exits.
    """
    path = resolve_inventory(explicit)
    if path:
        yield path
        return

    if not _get_hosts(session):
        raise CollectError(
            "No hosts in database and no inventory provided. Add hosts or pass an "
            "inventory (--inventory / upload / CMDB_ANSIBLE_INVENTORY)."
        )

    tmp = tempfile.NamedTemporaryFile(
        "w", suffix=".yml", prefix="cmdb-inventory-", delete=False
    )
    try:
        tmp.write(generate_inventory_yaml(session, include_ssh_vars=True))
        tmp.close()
        yield tmp.name
    finally:
        Path(tmp.name).unlink(missing_ok=True)


def _ansible_cmd(inventory: str, limit: str | None, module: str, args: str | None,
                 tree: str) -> list[str]:
    cmd = ["ansible", "-i", inventory, limit or "all", "-m", module]
    if args:
        cmd += ["-a", args]
    if settings.ansible_user:
        cmd += ["--user", settings.ansible_user]
    if settings.ssh_private_key:
        cmd += ["--private-key", settings.ssh_private_key]
    cmd += ["--tree", tree]
    return cmd


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS
        )
    except FileNotFoundError as exc:
        raise CollectError(
            "`ansible` not found. Install it with `uv sync --group collect`."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise CollectError(f"ansible timed out after {_TIMEOUT_SECONDS}s") from exc


def _tree_files(tree: str) -> list[Path]:
    return [f for f in Path(tree).iterdir() if f.is_file()]


def _inventory_label(explicit: str | None, resolved: str) -> str:
    """A human-friendly inventory name for import logs.

    A generated temp inventory has an opaque path, so label it ``generated from DB``
    rather than leaking the tempfile name into the log.
    """
    return resolved if resolve_inventory(explicit) else "generated from DB"


def collect_facts(
    session: Session,
    inventory: str | None = None,
    limit: str | None = None,
    source: ImportSource = ImportSource.COLLECT,
) -> ImportLog:
    """Gather Ansible facts live and import them via the existing fact pipeline."""
    with (
        inventory_for(session, inventory) as inv,
        tempfile.TemporaryDirectory() as tree,
    ):
        result = _run(_ansible_cmd(inv, limit, "setup", None, tree))
        # ansible writes one JSON file per attempted host (success or failure) into
        # the tree; import_from_path turns each into a host (failures land in notes).
        log = ansible_svc.import_from_path(session, tree, source)
        inv_label = _inventory_label(inventory, inv)

    target = limit or "all"
    log.filename = f"collect setup ({inv_label} :: {target})"
    if result.returncode != 0 and result.stderr.strip():
        note = result.stderr.strip()
        log.notes = f"{log.notes}\n{note}" if log.notes else note
    session.flush()
    return log


def collect_docker(
    session: Session,
    inventory: str | None = None,
    limit: str | None = None,
    source: ImportSource = ImportSource.COLLECT,
) -> ImportLog:
    """Gather `docker ps` live and import containers via the existing pipeline."""
    with (
        inventory_for(session, inventory) as inv,
        tempfile.TemporaryDirectory() as tree,
    ):
        result = _run(_ansible_cmd(inv, limit, "shell", _DOCKER_PS_CMD, tree))
        inv_label = _inventory_label(inventory, inv)

        upserted = 0
        errors: list[str] = []
        for f in _tree_files(tree):
            inv_host = f.name
            try:
                data = json.loads(f.read_text())
            except Exception as exc:
                errors.append(f"{inv_host}: could not read result: {exc}")
                continue

            # Skip unreachable/failed hosts   importing an empty set would wipe the
            # host's existing containers (import_containers is replace-on-import).
            if data.get("unreachable") or data.get("failed") or data.get("rc", 0) != 0:
                reason = data.get("msg") or data.get("stderr") or "collection failed"
                errors.append(f"{inv_host}: {reason.strip()}")
                continue

            containers = []
            for line in (data.get("stdout") or "").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    containers.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

            try:
                counts = import_containers(
                    session, {"host": inv_host, "containers": containers}
                )
                upserted += counts["containers"]
            except Exception as exc:
                errors.append(f"{inv_host}: {exc}")

    if result.returncode != 0 and result.stderr.strip():
        errors.append(result.stderr.strip())

    target = limit or "all"
    log = ImportLog(
        source=source,
        filename=f"collect docker ({inv_label} :: {target})",
        hosts_upserted=0,
        hosts_failed=0,
        containers_upserted=upserted,
        notes="\n".join(errors) or None,
    )
    session.add(log)
    session.flush()
    return log
