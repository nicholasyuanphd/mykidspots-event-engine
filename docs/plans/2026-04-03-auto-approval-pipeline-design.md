# Auto-Approval Pipeline Design

**Date:** 2026-04-03
**Status:** Approved
**Author:** Brainstorming session with founder + Head of Engineering review

---

## Problem

CVB tourism sources (`trust_level: "new"`) land all events as `status=pending` in the DB. The `/admin/pipeline` curator console was the manual review UI for these — but it has been sunset. There is currently no path to approve the ~300+ pending tourism events without manual SQL or a new admin UI.

## Solution

A two-phase automated system: **source graduation** (one-time) + **random spot-check** (weekly). Cost: pennies per year.

---

## Phase 1 — Source Graduation Audit (One-Time Per Source)

After a new CVB source has been scraped for the first time, a CLI command:

1. Queries 10 random `status=pending` events from that source
2. Runs them through the existing `AIClassifier` (Haiku, same prompt as ingestion)
3. If ≥8/10 pass → graduates the source:
   - Writes `trust_level='verified'` to `source_trust_overrides` table
   - Bulk-updates all `status=pending` events for that source → `status='active'`
4. If <8/10 pass → logs a warning; source stays pending; requires investigation

**Usage:**
```bash
uv run python -m event_engine.cli audit-source visitraleigh-events
uv run python -m event_engine.cli audit-source wilmington-beaches-events
```

**Why bulk-activation is safe:** `verdict == "no"` returns `None` (event never inserted). Every event that reaches `status=pending` in the DB has already passed the AI kid-relevance filter. Graduating a source and bulk-activating its pending events will not surface any AI-rejected events.

---

## Phase 2 — Weekly Random Spot-Check (Ongoing)

A scheduled job runs every Sunday at 9 AM ET:

1. Loads all sources with `trust_level='verified'` in `source_trust_overrides`
2. For each verified CVB source: samples **5 random** `status=active` events
3. Runs each through `AIClassifier`
4. Logs every result (source, event ID, title, verdict) to structured log for trend visibility
5. If fail rate > 40% for any source (≥3/5 events fail):
   - Writes `trust_level='new'` back to `source_trust_overrides`
   - Future ingestion runs will land new events as `pending` again
   - Logs a `source_downgraded` warning (visible in GitHub Actions run log)

**Important:** Downgrade is forward-only. Already-active events from a downgraded source are not retracted — the events themselves were vetted at ingestion time. Only future scrape runs are affected.

**Sample size rationale:** 5 events minimum (not 3) to reduce false-positive downgrades from small-sample variance. At 5 events/source × 6 CVB sources = 30 Haiku API calls/week ≈ $0.015/week ($0.78/year).

---

## Architecture

### New Module: `event_engine/spot_check/`

```
src/event_engine/spot_check/
  __init__.py
  auditor.py        # Phase 1: one-time graduation audit
  spot_checker.py   # Phase 2: weekly random spot-check
```

### Reused Infrastructure (no changes needed)

| Component | Where | Reused as-is |
|-----------|-------|-------------|
| `AIClassifier` | `event_engine/classify/ai_classifier.py` | ✅ Same class, same Haiku model |
| `source_trust_overrides` table | Supabase DB | ✅ Already read by `trust_overrides.py` — write path is new |
| asyncpg connection pool | `event_engine/db/connection.py` | ✅ Same pool setup |
| `Settings` (API keys, DB URL) | `event_engine/config.py` | ✅ No new env vars needed |

### CLI Extension

Add subcommand to `cli.py`:
```
python -m event_engine audit-source <source-id>
```

Existing `python -m event_engine` (no subcommand) continues to run the full scrape pipeline unchanged.

### Scheduler: New GitHub Actions Workflow

New file: `.github/workflows/spot-check.yml`
Pattern: mirrors existing `scrape.yml` exactly (uv, Python 3.12, same secrets).
Schedule: `cron: "0 14 * * 0"` (Sundays 9 AM ET / 14:00 UTC)
`workflow_dispatch` input for manual runs.

---

## Data Model

No new DB tables. Existing tables used:

| Table | Operation | When |
|-------|-----------|------|
| `events` | `SELECT` (random sample by source_url domain) | Both phases |
| `events` | `UPDATE SET status='active' WHERE ...` | Phase 1 graduation |
| `source_trust_overrides` | `INSERT ... ON CONFLICT DO UPDATE` | Both phases (graduate / downgrade) |

---

## What You Do

**For new CVB sources after first scrape:** Run `audit-source <source-id>` once. That's it. No manual review queue.

**Ongoing:** Nothing. GitHub Actions runs every Sunday. Check the workflow run log if you want to see results.

**If a source gets auto-downgraded:** GitHub Actions log shows `source_downgraded` warning. Investigate by reading the spot-check log lines above it (which 3+ events failed and why), then decide whether to re-graduate or leave as pending.

---

## Out of Scope

- No new Supabase tables or migrations
- No Next.js UI (the curator console is sunset)
- Does not affect `trust_level: "verified"` sources in YAML (library sources — they stay verified as configured)
- Does not affect non-CVB sources
