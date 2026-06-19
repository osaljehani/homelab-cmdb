FROM python:3.12-slim

# openssh-client is needed by `cmdb collect` (Ansible drives ssh for collection).
RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*

RUN pip install uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
# Include the `collect` group so `cmdb collect` (agentless SSH collection) works.
RUN uv sync --no-dev --group collect --compile-bytecode

COPY cmdb/ cmdb/
COPY alembic.ini .

ENV CMDB_DB_PATH=/data/cmdb.db
ENV CMDB_HOST=0.0.0.0
ENV CMDB_PORT=8080

EXPOSE 8080

ENTRYPOINT ["uv", "run", "cmdb", "serve"]
