# Design: image UI — stale badge + targeted delete

**Status:** delivered (2026-07-02)
**Date:** 2026-07-02
**Related:** builds on `cmdb images rm` CLI + `delete_image` MCP tool (commits `eb27c7e`, `04cdacd`).
**Superseded in part** by `vuln-trend-snapshots.md` (2026-07-20): deleting an image no longer
erases its past dashboard-trend points — daily rollups are snapshotted at import time.

## Problem

Decommissioned images linger in the CMDB forever. The `Image → ImageScan → Vulnerability`
tables are **append-only history** (re-import appends, never overwrites), and — unlike the
`containers` table, which is replace-on-import and self-heals — nothing prunes an image once its
container stops running. So a removed service (e.g. Semaphore) keeps showing in `/images`, the
dashboard vuln panel, and topology until its rows are deleted by hand.

The `cmdb images rm` CLI and `delete_image` MCP tool already solve deletion at the command layer.
This design brings the capability into the **web UI**, without introducing a footgun.

## Decision

Ship a **hybrid: a non-destructive "stale" badge (find) + a targeted, confirmed delete (act)**.
Explicitly **reject auto-deletion**.

### Why not auto-remove images that "no longer exist"

- Fights the deliberate **append-only history** design — silently discards the audit trail.
- Containers legitimately stop/start. `custom-tool:local` is **on-demand** (`restart: "no"`,
  runs only during a pentest session); auto-purge would wipe its history on every stop and
  rescan from zero. Same hazard for any briefly-stopped service.
- "No longer exists" is ambiguous across **two scan sources** (runtime Docker scan on testhost +
  the Zot registry Trivy feed). An image absent from the Docker scan may still live in the
  registry. Cross-source reasoning makes auto-delete fragile.
- Silent + destructive, against the confirm-first discipline (`~/.claude/rules/homelabcmdb.md`).

### Why not a separate "decommissioned" flag (for now — YAGNI)

It would be a second flag beside `expected_noisy` with overlapping meaning (noisy = "ignore its
vulns"; decommissioned = "hide but keep"). It doesn't satisfy the "actually clear it out" need.
Add it later only if a real "keep history but hide" case appears.

## What to build

### 1. Targeted delete (core)

- A **Remove** action on the image **detail** page (and optionally a per-row action on `/images`).
- Opens a **confirm modal** stating exactly what will be deleted: `ref`, N scans, M vulnerabilities
  (the modal is the UI-layer equivalent of the CLI `--yes` prompt / MCP `confirm=True` guard).
- On confirm, the web route calls the **existing** `images.delete_image(session, ref)` — do **not**
  reimplement delete logic. The service already cascades scans + vulnerabilities and returns counts.
- Reflect the result with a flash/toast ("Removed `<ref>` — N scans, M vulnerabilities") and drop
  the row / redirect to `/images`.
- Follow the existing **HTMX** pattern (POST that removes the row in place), matching current pages.

### 2. Stale badge (discovery, non-destructive)

- Compute a **"not seen in last scan"** indicator and show it as a badge on `/images` (optionally a
  "show stale only" filter). This does the *discovery* job that motivated auto-delete — "which
  images are probably gone?" — while deleting nothing.
- Definition: an image is stale when its `last_scanned_at` predates the most recent scan **run**
  (i.e. it was not in the newest scan envelope). Cheap from existing timestamps
  (`ImageScan.scanned_at` / `ImportLog`), no schema change.
- **Open question — multi-source scoping:** the runtime Docker scan covers *running* images only,
  while the Zot feed covers registry/k8s images (disjoint sets). A registry-only image will always
  look "stale" against the Docker run and vice-versa. Decide during implementation whether to scope
  staleness **per source** (needs a source marker per scan/image — the CMDB does not persist `host`
  today, images are keyed on `ref` only) or to keep a single simple "older than newest run overall"
  rule and accept some false positives. Simplest first cut: single rule; revisit if noisy.

## Interfaces / boundaries

- **Reused, unchanged:** `images.delete_image(session, ref)` — single source of truth for deletion.
- **New service helper:** a small `stale_images(session)` / per-image `is_stale` computed in the
  images service (pure function of scan timestamps), unit-tested independently.
- **Web layer:** one new POST route (delete) that wraps the service + confirm modal; list template
  gains a badge column and the delete affordance. No new tables, no migration.

## Testing (TDD)

- Service: `delete_image` already covered; add tests for the stale helper (image in newest run →
  not stale; image whose latest scan predates newest run → stale; single-image / empty cases).
- Web: delete route removes the image and flashes counts; stale badge renders for a stale image and
  not for a current one. Follow `tests/test_web_images.py` patterns.

## Out of scope

- Auto-deletion of any kind.
- A decommissioned/soft-hide flag.
- Bulk delete (could follow later; start with single-image targeted delete).
