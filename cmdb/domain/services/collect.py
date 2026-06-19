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
from cmdb.domain.services.k8s_import import import_cluster, parse_kubectl_json

# One JSON object per line. We use `--format json` (Docker >= 23.0) rather than the
# `{{json .}}` Go-template form: Ansible runs the shell module's args through Jinja2,
# which parses `{{...}}` as an expression and fails before docker ever runs.
#
# Guard on `command -v docker` first: a host without docker (e.g. a pure k3s node)
# would otherwise fail with rc 127 and surface a misleading generic error. Instead it
# prints the `status=no-docker` sentinel and exits 0, and is skipped quietly   mirroring
# how the k8s probe emits `status=no-k8s` for non-control-plane hosts.
_DOCKER_PS_CMD = (
    "if ! command -v docker >/dev/null 2>&1; then echo status=no-docker; exit 0; fi\n"
    "docker ps --all --no-trunc --format json"
)
_NO_DOCKER_SENTINEL = "status=no-docker"

# Probe a host for a usable control-plane kubectl and, if found, emit a
# marker-delimited blob of raw `kubectl -o json` (transformed in Python   the remote
# may lack jq). A host with no control-plane kubectl prints `status=no-k8s` and is
# skipped. No `{{ }}`: Ansible's shell module runs args through Jinja2 (see _DOCKER_PS_CMD).
_K8S_PROBE_CMD = """\
KUBECTL=""
for c in "kubectl" "k3s kubectl" "sudo -n kubectl" "sudo -n k3s kubectl"; do
  if $c get nodes >/dev/null 2>&1; then KUBECTL="$c"; break; fi
done
if [ -z "$KUBECTL" ]; then echo "status=no-k8s"; exit 0; fi
echo "__CMDB_K8S__"
echo "status=ok"
echo "context=$($KUBECTL config current-context 2>/dev/null)"
echo "__NODES__"
$KUBECTL get nodes -o json
echo "__NAMESPACES__"
$KUBECTL get namespaces -o json
"""

# kubeconfig contexts too generic to use as a CMDB cluster name; fall back to the
# control-plane host's inventory name instead.
_GENERIC_K8S_CONTEXTS = {"", "default", "kubernetes"}

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


def _strip_ansible_warnings(text: str) -> str:
    """Drop ansible's cosmetic ``[WARNING]`` / ``[DEPRECATION WARNING]`` lines.

    These (e.g. Python interpreter-discovery notices) are emitted to stderr on every
    run and only add noise to the import-log notes.
    """
    kept = [
        ln for ln in text.splitlines()
        if not ln.lstrip().startswith(("[WARNING]", "[DEPRECATION WARNING]"))
    ]
    return "\n".join(kept).strip()


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
    if result.returncode != 0:
        note = _strip_ansible_warnings(result.stderr)
        if note:
            log.notes = f"{log.notes}\n{note}" if log.notes else note
    session.flush()
    return log


def _parse_k8s_blob(stdout: str) -> dict[str, str] | None:
    """Split the probe's marker-delimited stdout into its parts.

    Returns ``{context, nodes_raw, ns_raw}`` for a control-plane host, or ``None`` when
    the host is not a control-plane (no ``__NODES__``/``__NAMESPACES__`` markers).
    """
    if "__NODES__" not in stdout or "__NAMESPACES__" not in stdout:
        return None
    head, _, rest = stdout.partition("__NODES__")
    nodes_raw, _, ns_raw = rest.partition("__NAMESPACES__")
    context = ""
    for line in head.splitlines():
        if line.startswith("context="):
            context = line[len("context="):].strip()
    return {"context": context, "nodes_raw": nodes_raw.strip(), "ns_raw": ns_raw.strip()}


def collect_k8s(
    session: Session,
    inventory: str | None = None,
    limit: str | None = None,
    source: ImportSource = ImportSource.COLLECT,
) -> ImportLog:
    """Discover K8s clusters live (kubectl over SSH) and import nodes + namespaces.

    Each control-plane host enumerates its whole cluster in one pass, so workers are
    discovered without being reached directly. Hosts that aren't control-planes are
    skipped silently (``import_cluster`` is additive   skipping never wipes data).
    """
    with (
        inventory_for(session, inventory) as inv,
        tempfile.TemporaryDirectory() as tree,
    ):
        result = _run(_ansible_cmd(inv, limit, "shell", _K8S_PROBE_CMD, tree))
        inv_label = _inventory_label(inventory, inv)

        clusters = nodes = namespaces = 0
        errors: list[str] = []
        tree_files = _tree_files(tree)
        for f in tree_files:
            inv_host = f.name
            try:
                data = json.loads(f.read_text())
            except Exception as exc:
                errors.append(f"{inv_host}: could not read result: {exc}")
                continue

            if data.get("unreachable") or data.get("failed") or data.get("rc", 0) != 0:
                reason = data.get("msg") or data.get("stderr") or "collection failed"
                errors.append(f"{inv_host}: {reason.strip()}")
                continue

            blob = _parse_k8s_blob(data.get("stdout") or "")
            if blob is None:
                # Not a control-plane (no usable kubectl)   expected for workers and
                # non-k8s hosts; skip quietly rather than logging noise.
                continue

            context = blob["context"]
            cluster_name = (
                inv_host if context.lower() in _GENERIC_K8S_CONTEXTS else context
            )
            try:
                cluster_data = parse_kubectl_json(
                    blob["nodes_raw"], blob["ns_raw"], cluster_name
                )
                counts = import_cluster(session, cluster_data)
                clusters += counts["clusters"]
                nodes += counts["nodes"]
                namespaces += counts["namespaces"]
                errors.extend(f"{inv_host}: {e}" for e in counts["errors"])
            except Exception as exc:
                errors.append(f"{inv_host}: {exc}")

    if result.returncode != 0:
        # See collect_docker: a failure before any per-host result lands on stdout.
        detail = _strip_ansible_warnings(result.stderr)
        if not detail and not tree_files:
            detail = _strip_ansible_warnings(result.stdout)
        if detail:
            errors.append(detail)

    target = limit or "all"
    log = ImportLog(
        source=source,
        filename=f"collect k8s ({inv_label} :: {target})",
        hosts_upserted=0,
        hosts_failed=0,
        k8s_clusters_upserted=clusters,
        k8s_nodes_upserted=nodes,
        k8s_namespaces_upserted=namespaces,
        notes="\n".join(errors) or None,
    )
    session.add(log)
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
        tree_files = _tree_files(tree)
        for f in tree_files:
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

            if (data.get("stdout") or "").strip() == _NO_DOCKER_SENTINEL:
                # Host has no docker installed   expected (e.g. a k3s node); skip
                # quietly. Crucially, don't fall through to import_containers with an
                # empty set, which would wipe any previously-collected containers.
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

    if result.returncode != 0:
        # Prefer stderr, but when ansible fails before producing any per-host result
        # (e.g. bad inventory or an arg-templating error) the message lands on stdout
        # with an empty stderr   surface it instead of silently reporting zero.
        detail = _strip_ansible_warnings(result.stderr)
        if not detail and not tree_files:
            detail = _strip_ansible_warnings(result.stdout)
        if detail:
            errors.append(detail)

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
