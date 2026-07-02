# Vendored assets

All third-party web assets are committed so the UI works with no internet
access. Versions are pinned in `scripts/vendor-assets.sh`; run it to re-fetch
(set `UPDATE_SUMS=1` when upgrading versions to refresh `../SHA256SUMS`).

| Asset | Version | License | Source |
|---|---|---|---|
| htmx (`js/htmx.min.js`) | 2.0.3 | 0BSD (`htmx-LICENSE`) | https://www.npmjs.com/package/htmx.org |
| Cytoscape.js (`js/cytoscape.min.js`) | 3.34.0 | MIT (`cytoscape-LICENSE`) | https://www.npmjs.com/package/cytoscape |
| Geist Sans (`fonts/geist-sans-*.woff2`) | Fontsource 5.2.5 | SIL OFL 1.1 (`geist-OFL.txt`) | https://www.npmjs.com/package/@fontsource/geist-sans |
| Geist Mono (`fonts/geist-mono-*.woff2`) | Fontsource 5.2.8 | SIL OFL 1.1 (`geist-OFL.txt`) | https://www.npmjs.com/package/@fontsource/geist-mono |
