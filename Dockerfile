FROM python:3.12-slim

RUN pip install uv

WORKDIR /app
COPY pyproject.toml .
RUN uv sync --no-dev --compile-bytecode

COPY cmdb/ cmdb/
COPY alembic.ini .

ENV CMDB_DB_PATH=/data/cmdb.db
ENV CMDB_HOST=0.0.0.0
ENV CMDB_PORT=8080

EXPOSE 8080

ENTRYPOINT ["uv", "run", "cmdb", "serve"]
