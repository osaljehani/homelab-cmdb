# Design: immutable daily vuln snapshots for the dashboard trend

**Status:** shipped 2026-07-20
**Related:** supersedes part of `images-stale-badge-and-delete.md` (its "deleting rewrites the
trend" caveat no longer applies).

## Problem

The dashboard's 30-day vulnerability trend was computed live at render time: join `image_scans`
against the *current* `images` table, filtered to the *current* running set. History was a
function of today's state, which broke it two ways:

1. **Remediate + delete the old image** — `delete_image()` cascade-deletes the image's scans, so
   its contribution vanished from *every past point*. Instead of showing 1058 → 858, the chart
   claimed the fleet "always was" at 858. The one thing a trend exists to show — the drop — was
   the one thing it couldn't.
2. **Running-set drift** — any image leaving the running set (tag upgrade, stopped container)
   silently dropped its history out of the trend, even with no deletion involved.

## Decision

Record an **immutable daily snapshot per scanned image** at import time (`vuln_snapshots`):
severity rollup from the latest scan plus frozen `was_running`/`was_noisy` flags. The trend reads
snapshots only. Key properties:

- `image_ref` is a **plain string, not an FK** — rows survive image deletion by construction.
- Writers **replace the whole day** (delete + insert), the codebase's replace-on-import idiom:
  same-day re-imports stay duplicate-free, and a deleted image drops out of today's point
  because the rewrite simply no longer sees it.
- Snapshot classification comes from `image_overview()` — the same placement logic as
  `vuln_summary` — so the trend and the severity card can't disagree about what "running" means.
- Write points: trivy `import_from_path` (single choke point for CLI + web upload); a
  today-only refresh in `delete_image()` and `set_noisy()`; and (added same day, see below)
  Docker/K8s inventory imports and `/collect` runs, so a stopped or replaced container moves
  today's point at the next collection instead of waiting for the nightly scan.
- The introducing migration **backfills** from existing `image_scans` history (raw SQL — no ORM
  imports in a mid-chain migration; fresh-DB replay must not depend on future columns), using
  current placement flags as the best available approximation of history. The service-layer
  `backfill_snapshots()` intentionally duplicates ~35 lines of this logic in ORM form for the
  demo seed and the `cmdb db backfill-vuln-snapshots` escape hatch.

## Accepted tradeoff

Deleting an image still loses its per-CVE `Vulnerability` rows; snapshots preserve daily
severity rollups only. That matches the goal (trend integrity), not full forensic history —
the scan JSON on disk remains the forensic record.

## Rejected alternatives

- **FK with `ON DELETE SET NULL`** — keeps rows but orphans them namelessly; a ref string is
  more useful for debugging and equally immutable.
- **Aggregate-only daily rows** (one fleet-wide row/day) — cheaper, but can't retro-filter by
  noisy/running or explain which image moved a point.
- ~~**Hooking Docker/K8s inventory imports**~~ — initially rejected (inventory imports run far
  more often than scans, and the flags self-correct at the next daily trivy import). **Reversed
  the same day**: the actual remediation loop is *remediate → /collect → look at the chart*, and
  a day of lag there defeats the point. The refresh is a today-only rewrite over a homelab-sized
  image set, so the churn concern was theoretical. Hooked: `docker_import.import_from_path`,
  `k8s_import.import_from_path`, `collect.collect_docker`, `collect.collect_k8s`.
- **Soft-delete flag on images** — keeps `/images` clean only by adding a second notion of
  deleted; the earlier stale-badge design already rejected it, and it still wouldn't freeze
  history (flags mutate).
