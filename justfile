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

fmt:
    uv run ruff format cmdb tests

lint:
    uv run ruff check cmdb tests
