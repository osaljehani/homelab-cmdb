default:
    just --list

install:
    uv sync --all-groups

test:
    uv run pytest -v

test-one filter:
    uv run pytest -v -k "{{filter}}"

serve:
    uv run cmdb serve

db-upgrade:
    uv run cmdb db upgrade

# Refresh the local CLI/MCP database (./cmdb.db) from the Docker volume.
# Uses SQLite's online backup, so the copy is consistent even while the
# container is writing. Override paths: `just sync-db data/cmdb.db cmdb.db`.
sync-db src="data/cmdb.db" dst="cmdb.db":
    sqlite3 "{{src}}" ".backup '{{dst}}'"
    @echo "Refreshed {{dst}} from {{src}}"

fmt:
    uv run ruff format cmdb tests

lint:
    uv run ruff check cmdb tests
