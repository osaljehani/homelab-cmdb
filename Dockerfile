# syntax=docker/dockerfile:1

# ─── Builder ─────────────────────────────────────────────────────────────────
# glibc base + uv + CPython 3.13. uv installs a *self-contained* standalone
# Python and builds the venv against it; both are relocatable into the distroless
# runtime below. 3.13 (not Chainguard's bleeding-edge 3.14) keeps ansible-core —
# the engine behind `cmdb collect` — on a supported controller interpreter.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_PYTHON=3.13 \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_PYTHON_DOWNLOADS=automatic \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

# A standalone 3.13 we can copy wholesale into distroless, then resolve deps
# against it. The `collect` group pulls ansible-core + paramiko (pure-Python SSH)
# so the runtime needs no openssh-client. --no-dev drops the test/lint tooling.
RUN uv python install 3.13
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --group collect --no-install-project --frozen

COPY cmdb/ cmdb/
COPY alembic.ini ./
RUN uv sync --no-dev --group collect --frozen

# ─── Runtime ─────────────────────────────────────────────────────────────────
# distroless/cc = glibc + libgcc, nothing else. No perl-base, no openssh-client →
# the four "no fix" Debian CRITs that rode in on python:3.12-slim are gone, while
# the collection path keeps working over paramiko.
FROM gcr.io/distroless/cc-debian12 AS runtime

# uv drives both the entrypoint and the scanner's `docker exec … uv run cmdb
# import trivy`, so it must live in the runtime image (statically linked → runs
# on distroless as-is).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Keep the standalone interpreter and venv at their build-time paths so the
# venv's interpreter symlink still resolves.
COPY --from=builder /opt/python /opt/python
COPY --from=builder /app /app

# glibc's C.UTF-8 locale (a directory under /usr/lib/locale on Debian) — without
# it ansible-core aborts with "unsupported locale setting" on distroless.
COPY --from=builder /usr/lib/locale /usr/lib/locale

WORKDIR /app

ENV PATH=/app/.venv/bin:/usr/local/bin:/usr/bin:/bin \
    UV_NO_SYNC=1 \
    UV_PYTHON_DOWNLOADS=never \
    # distroless carries no locale; ansible-core aborts unless the interpreter
    # reports a UTF-8 encoding. glibc's built-in C.UTF-8 needs no locale files.
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUTF8=1 \
    CMDB_DB_PATH=/data/cmdb.db \
    CMDB_HOST=0.0.0.0 \
    CMDB_PORT=8080 \
    # Distroless ships no ssh binary: drive Ansible collection over the pure-Python
    # paramiko connection plugin (transport=paramiko) so no openssh-client is
    # needed. The DB-generated inventory's `-o StrictHostKeyChecking=no` is an
    # openssh-only arg paramiko ignores, so host-key checking is disabled here to
    # preserve the previous behaviour.
    ANSIBLE_TRANSPORT=paramiko \
    ANSIBLE_HOST_KEY_CHECKING=False

EXPOSE 8080
ENTRYPOINT ["uv", "run", "cmdb", "serve"]
