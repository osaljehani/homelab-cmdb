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

# Re-download pinned vendored web assets (fonts, htmx, cytoscape).
# UPDATE_SUMS=1 refreshes cmdb/web/static/SHA256SUMS after a version bump.
vendor-assets:
    ./scripts/vendor-assets.sh

fmt:
    uv run ruff format cmdb tests

lint:
    uv run ruff check cmdb tests
