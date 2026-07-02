#!/usr/bin/env bash
# Download the pinned versions of all vendored web assets into cmdb/web/static/.
# The binaries are committed; run this only to (re)fetch or upgrade them, then
# refresh SHA256SUMS and the licenses in cmdb/web/static/vendor-licenses/.
set -euo pipefail

HTMX_VERSION="2.0.3"
CYTOSCAPE_VERSION="3.34.0"
GEIST_SANS_VERSION="5.2.5"
GEIST_MONO_VERSION="5.2.8"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATIC="$ROOT/cmdb/web/static"
CDN="https://cdn.jsdelivr.net/npm"

mkdir -p "$STATIC/js" "$STATIC/fonts" "$STATIC/vendor-licenses"

fetch() { # fetch <url> <dest>
  echo "  $2"
  curl -fsSL "$1" -o "$2"
}

echo "js:"
fetch "$CDN/htmx.org@$HTMX_VERSION/dist/htmx.min.js" "$STATIC/js/htmx.min.js"
fetch "$CDN/cytoscape@$CYTOSCAPE_VERSION/dist/cytoscape.min.js" "$STATIC/js/cytoscape.min.js"

echo "fonts:"
for w in 400 500 600 700; do
  fetch "$CDN/@fontsource/geist-sans@$GEIST_SANS_VERSION/files/geist-sans-latin-$w-normal.woff2" \
    "$STATIC/fonts/geist-sans-latin-$w-normal.woff2"
done
for w in 400 500; do
  fetch "$CDN/@fontsource/geist-mono@$GEIST_MONO_VERSION/files/geist-mono-latin-$w-normal.woff2" \
    "$STATIC/fonts/geist-mono-latin-$w-normal.woff2"
done

echo "licenses:"
fetch "$CDN/htmx.org@$HTMX_VERSION/LICENSE" "$STATIC/vendor-licenses/htmx-LICENSE"
fetch "$CDN/cytoscape@$CYTOSCAPE_VERSION/LICENSE" "$STATIC/vendor-licenses/cytoscape-LICENSE"
fetch "$CDN/@fontsource/geist-sans@$GEIST_SANS_VERSION/LICENSE" "$STATIC/vendor-licenses/geist-OFL.txt"

echo "checksums:"
SUMS="$STATIC/SHA256SUMS"
if [[ "${UPDATE_SUMS:-}" == "1" ]]; then
  (cd "$STATIC" && find js fonts -type f | sort | xargs sha256sum) > "$SUMS"
  echo "  wrote $SUMS"
else
  (cd "$STATIC" && sha256sum -c SHA256SUMS)
fi

echo "done."
