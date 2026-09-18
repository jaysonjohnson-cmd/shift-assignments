# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**QC Shift Assignments** is a full-stack tool for generating and managing QC shift assignments. It combines:
- **Backend:** Flask (Python) with role-based access control (admin/reviewer/viewer)
- **Frontend:** Next.js 16 (React 19) with client-side rendering; static export deployed alongside Flask
- **Data:** Storage API for persistence (shift snapshots, completions, reviewers, admins)
- **Auth:** Centralized `auth-service.storesight.org` with JWT cookie (`storesight_session`)

**Live:** https://qc-shift-assignments.storesight.org  
**Cloud Run service:** `qc-shift-assignments` (storesight-internal-tools, us-central1)  
**GitHub:** https://github.com/jaysonjohnson-cmd/shift-assignments  
**Owner:** jayson.johnson@storesight.com

---

## Quick Start

### Prerequisites
- Python 3.10+ (`python3 --version`)
- Node 18+ (`npm --version`)
- `@storesight.com` Google account

### Initial Setup (one-time)
```bash
# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
pip install -r requirements.txt

# Authenticate for local dev (opens browser for OAuth)
python3 get-dev-token.py
# Token saved to ~/.storesight/dev-token (expires in 8 hours)

# Install frontend dependencies
cd shift-assignments
npm install
cd ..
```

### Running Locally
```bash
# Terminal 1: Start Flask backend (required)
TOOL_SLUG=qc-shift-assignments LOCAL_DEV=1 FLASK_DEBUG=1 python3 main.py
# Runs on http://localhost:8080

# Terminal 2: Start Next.js dev server (optional, for HMR)
cd shift-assignments
NEXT_PUBLIC_API_ORIGIN=http://localhost:8080 npm run dev
# Runs on http://localhost:3000
```

Or use the convenience script:
```bash
bash run-local.sh  # Handles venv, dependencies, Flask startup
```

**Important:** `TOOL_SLUG=qc-shift-assignments` is required when accessing the Storage API. Without it, API calls get 403 "Missing X-Tool-Slug header".

---

## Architecture

### Backend (`main.py`, 1238 lines)

**Core responsibilities:**
- JWT authentication (`@app.before_request` validates `storesight_session` cookie)
- Role determination (admin, reviewer, viewer) via `roles.py`
- API routing for assignments, jobs, shifts, completions, and management
- Integration with Bloom for live job data
- Storage API access for persistence

**Key routes:**
- `GET /api/me` — Current user (email, name, role)
- `GET /api/bloom/jobs` — Fetch jobs from Bloom (with optional status filter)
- `POST /api/shifts/publish` — Publish shift assignments
- `GET /api/shifts/overview` — Aggregated progress by reviewer
- `GET /api/shifts/jobs` — Detailed job list by reviewer
- `GET/POST /api/reviewers` — Manage reviewer roster
- `GET/POST /api/admins` — Manage admin roster
- `POST /api/shifts/clear` — Wipe active or completed assignments
- `POST /api/shifts/my/complete` — Mark job as done
- `GET /api/shifts/my` — Reviewer's personal task list

**Data flow:**
1. User authenticates → JWT middleware extracts `g.user` (email, name)
2. `roles.get_role(email)` checks Storage API for admin/reviewer records
3. API routes read from Bloom (via `internal_api`), process, and return
4. Storage API (`/api/storage/qc-shift-assignments`) holds shift snapshots, completions, reviewer/admin rosters

### Frontend (`shift-assignments/`, Next.js 16)

**Structure:**
- `app/` — Page components (layout.tsx, page.tsx, assignments, settings, my-tasks, team-assignments)
- `components/` — Reusable UI (AssignMenu, ShiftComposer, AssignmentsOverview, etc.)
- `lib/` — Utilities (API client, state store, types, hooks)
- `public/` — Static assets (logos, favicons)

**Key pages:**
- `/` — Home menu (Bloom task list refresh, assignment workflows, settings)
- `/assignments` — Create and publish shift assignments (admin only)
- `/team-assignments` — View current shift progress by reviewer
- `/my-tasks` — Reviewer's assigned jobs and completion tracking
- `/settings` — Manage reviewers and admins (admin only)

**State management:**
- `lib/useUser.tsx` — User context (email, role, loading state)
- `lib/useTheme.tsx` — Dark/light theme toggling
- `lib/store.ts` — Zustand store for shift data (rows, reviewers, draft slots)

**API integration:**
- `lib/api.ts` — Fetch wrapper (no auth needed; cookies sent automatically)
- All requests to `/api/*` are relative (proxied by Next.js dev server to Flask on port 8080, or same origin in production)

**UI Library:**
- Tailwind CSS for styling
- Custom Storesight design tokens (colors, spacing, typography)
- No component library — pure HTML + Tailwind

---

## Development Workflow

### Making Changes to the Backend

1. Edit `main.py` or `roles.py`
2. Flask auto-reloads (FLASK_DEBUG=1)
3. Test the endpoint: `curl -H "Authorization: Bearer $(cat ~/.storesight/dev-token)" http://localhost:8080/api/me`
4. Verify in the browser at http://localhost:8080 (or http://localhost:3000 if Next.js is running)

### Making Changes to the Frontend

1. Edit files in `shift-assignments/app/` or `shift-assignments/lib/`
2. If running `npm run dev`, hot reload happens automatically
3. If not running Next.js, refresh `http://localhost:8080` in the browser (Flask serves static exports)

### Important Patterns

**API calls from the frontend:**
```typescript
import { getMe } from "@/lib/api";

const me = await getMe();  // Calls GET /api/me (auto-authenticated via cookies)
```

**Backend API calls:**
```python
from internal_api import get, post

# Fetch data from Internal API
jobs = get("/api/bloom/jobs", params={"force": True, "status": "N"})

# Check the spec first
spec = get("/api/internal-schema-ref")  # See available endpoints and parameters
```

**Role-based access:**
```python
from roles import is_admin

@app.route("/api/assignments/publish", methods=["POST"])
def publish_shift():
    email = g.user["email"]
    if not is_admin(email):
        return {"error": "Admin only"}, 403
    # ... publish logic
```

**Storage API (for persistent data):**
```python
from internal_api import post, get, put

# Create shift snapshot
result = post("/api/storage/qc-shift-assignments", json={
    "data": {
        "kind": "shift_snapshot",
        "published_at": iso_now,
        "assignments": assignments_dict
    }
})
snapshot_id = result["data"]["id"]
```

---

## Testing

```bash
# Run all tests
JWT_SIGNING_SECRET=test-secret pytest tests/

# Run single test file
JWT_SIGNING_SECRET=test-secret pytest tests/test_local_dev_auth.py

# Run with verbose output
JWT_SIGNING_SECRET=test-secret pytest -v tests/
```

`JWT_SIGNING_SECRET` is required (use any value for tests).

---

## Deployment

**Trigger:** Push to `main` on GitHub → Cloud Build auto-deploys  
**Service:** Cloud Run, storesight-internal-tools project  
**Build config:** `cloudbuild.yaml` (Docker build, push to GCR, deploy with OIDC auth)

**Environment variables (set by Cloud Run):**
- `JWT_SIGNING_SECRET` — from Secret Manager
- `TOOL_SLUG=qc-shift-assignments` — sent in X-Tool-Slug header for rate limiting
- `INTERNAL_API_BASE=https://internal-tool-api.storesight.org`
- `SCHEDULER_SERVICE_ACCOUNTS` / `SCHEDULER_OIDC_AUDIENCE` — allow Cloud Scheduler
  to call the auto-publish path with an OIDC token. Both must be set or that path
  stays cookie-only (see Automatic shift assignment below).
- `BLOOM_WARMER=0` — opt out of the background Bloom cache warmer (tests set this)
- `SLACK_ADMIN_USER_ID` — Slack *user* id (`U...`) DM'd when a reviewer closes
  out their shift. Unset = close-out still works, it just doesn't notify.

---

## Automatic shift assignment

Shifts can publish themselves on a timer instead of being composed by hand.

**The switches.** Both admin-only, in Settings, stored as `tool_config` docs so
they survive restarts. While one is off its endpoint returns `{"skipped": true}`
and writes nothing, so the Cloud Scheduler jobs can safely exist before you turn
them on.

- "Automatic shift assignment" → `POST /api/shifts/auto-publish`
- "Clear shifts at end of day" → `POST /api/shifts/auto-clear`, 5:30 PM Central
  daily. Runs every day, not weekdays only: a Friday shift would otherwise sit
  until Monday. Note this is a *hard* clear at 5:30 PM; the day-boundary expiry
  below is a separate, always-on safety net.

**The schedule.** `bash setup-scheduler.sh` creates one Cloud Scheduler job per
shift time, and is idempotent — re-run it after changing a time. Shift times live
in the `JOBS` array and must match the `shift_time` values the UI sends verbatim
(see `AssignMenu.tsx`), en-dash included, since they're compared against Team
Scheduler's labels. Times are US Central (`America/Chicago`) — the QC team is in
Arkansas. `DRY_RUN=1` prints the commands without applying them.

Two shifts starting at the same clock time are staggered a couple of minutes
apart on purpose: auto-publish writes a new snapshot and rewrites the
`reviewer_shift` docs for everyone in its batch, so simultaneous runs would race
over that shared state.

**The auth exception.** Production auth is normally the `storesight_session`
cookie and nothing else. Exactly two paths additionally accept a Google OIDC
token from an allowlisted service account, enforced by `_OIDC_ALLOWED_PATHS` in
`main.py`:

- `/api/shifts/auto-publish` — the shift timer
- `/api/shifts/auto-clear` — the end-of-day clear

Both are purpose-built for Cloud Scheduler: they take no destructive
parameters and each is gated behind its own Settings switch. That shape is the
whole point. `/api/shifts/auto-clear` exists *instead of* pointing the scheduler
at `/api/shifts/clear`, which takes a `mode` — a scheduled caller must never be
able to reach `mode="reset"` (deletes every shift across all time) or scope a
clear to a single reviewer.

Don't add a third path unless it has those same properties, and never widen this
to a prefix match. `tests/test_scheduler_oidc_auth.py` pins the set's exact
contents — an earlier change added `/api/shifts/clear` here and the whole suite
still passed, because nothing asserted what was in it. A valid scheduler token
must never unlock the rest of the API.

**The Bloom feed is slow.** `/api/prioritized-jobs` is a single unpaginated
request, but the upstream ranks ~500 jobs and takes 10-12 seconds. A background
warmer keeps the 60s cache hot so no user-facing request pays that, concurrent
misses are single-flighted into one call, and auto-refill reads the warm cache
via `max_age` rather than forcing a fetch. If you add a caller, prefer
`fetch_prioritized_jobs()` or a `max_age=` bound; reserve `use_cache=False` for
an explicit user-driven Refresh.

**How big a top-up is.** Auto-refill is bounded by *responses*, not job count:
each reviewer's docs carry `batch_responses` (their original batch's total),
scaled by `_REFILL_BUDGET_MULTIPLE`, and the refill stops once that's met.
Bounding by response count is deliberate — stopping at a job count made 20
two-response jobs a "full batch". The reviewer's original `batch_size` stays on
as a hard ceiling on the job count, so a top-up can't exceed their allotment
however the budget lands.

Only the unscaled base is stamped onto a refill chunk, so `batch_responses`
keeps meaning "the original batch's total" rather than drifting upward.

**Keeping the heavy end spread.** Big jobs are capped two ways, and the smaller
wins: `_REFILL_MAX_LARGE_JOBS` as an absolute ceiling, and
`_REFILL_MAX_LARGE_SHARE` as a fraction of the large jobs actually on offer
("large" being 3x the pool median, floored). The share is the one that matters —
a fixed cap alone silently stops protecting anything once the pool holds fewer
large jobs than the cap, which is the common case here (a few hundred tiny jobs
and only a handful of heavy ones). With the share, the first reviewer to finish
can never take the whole heavy end regardless of queue shape. It always lets at
least one through, or a lone large job would never be assigned to anyone.

**Rollback:** Use Cloud Run revision traffic splitting in GCP console.

---

## Shifts expire at the day boundary

A shift published on an earlier *local* day is finished, and the read paths hide
it — so yesterday's assignments never appear in today's My Tasks and nobody has
to remember to clear them.

Day boundaries are US Central, not UTC (`_SHIFT_TZ`), because the QC team is in
Arkansas: a shift published at 4 PM CDT is stored as 21:00Z and must still count
as that day rather than rolling over at 7 PM local. Every shift runs inside one
working day (8:00 AM earliest, 6:00 PM latest), so a previous-day snapshot is
never mid-flight.

`_latest_snapshot()` skips stale snapshots. Callers that need to act on a
finished shift pass `include_stale=True` — `/api/shifts/clear` does, so an admin
can still tidy up after the fact. A snapshot whose timestamp won't parse counts
as current: hiding a live shift is worse than showing a stale one.

Hiding alone would let docs accumulate against the 10k-per-namespace Storage
cap, so both publish paths call `_purge_stale_shift_docs()` first to delete the
previous day's snapshot, `reviewer_shift` and `completion` docs. It's
best-effort — a failed delete is logged and skipped rather than failing the
publish.

**Don't hardcode `published_at` in a shift fixture.** A fixed date silently turns
an "active shift" fixture into a finished one the day after it's written; three
tests broke this way. Use a helper that returns today (see `_today_published_at`
in `tests/test_bloom_routes.py`).

---

## Closing out a shift

A reviewer ends their own shift from My Tasks ("Done for the shift" →
`POST /api/shifts/my/close`). It takes no parameters and only ever touches the
caller's own data.

**Deleting their `reviewer_shift` docs is the whole mechanism.** It's not just
cleanup — `_auto_refill_reviewer` builds its exclusion set from the rows held in
*every* `reviewer_shift` doc (`assigned_keys = touched_keys - completed_keys`),
so a job no doc is holding becomes assignable to the rest of the team again.
That same deletion also stops both refill triggers for the person leaving: the
completion finish-check has no job left to fire on, and the self-heal path in
`/api/shifts/my` guards on the reviewer having rows.

**Completion docs are deliberately kept.** The Progress Tracker and the weekly
leaderboard both read them; deleting them would erase credit for work that was
actually done. Only the unfinished rows go back.

A `shift_closeout` marker doc records the state, surfaces as `closed_out` on
`/api/shifts/my`, and makes the endpoint idempotent. The marker is keyed to the
snapshot, so the next published shift reopens the reviewer automatically — and
a clear (`mode="all"`) drops it so an admin can reopen someone mid-shift.

There is intentionally **no confirmation prompt** and no block on unfinished
work: ending the shift is always allowed, and whatever isn't checked off is
released rather than lost.

Each close-out sends **one DM per reviewer** to `SLACK_ADMIN_USER_ID` — a
deliberate choice over batching them into an end-of-day digest, so the admin
sees people finish in real time. Nothing is posted to a shared channel, and the
reviewer closing out isn't messaged. Note the Slack proxy (`/api/slack/post`)
is channel-only by spec; passing a *user* id works because Slack routes it to
the bot's IM with that person, which is the only DM a bot can send.

## Key Files and Their Roles

| File | Purpose |
|------|---------|
| `main.py` | Flask app, routes, auth middleware |
| `roles.py` | Role resolution (admin/reviewer/viewer) via Storage API |
| `bloom.py` | Bloom API client for job fetching |
| `internal_api.py` | Internal API client with automatic auth and retry logic |
| `shift-assignments/app/assignments/page.tsx` | Shift creation and publishing UI |
| `shift-assignments/app/team-assignments/page.tsx` | Shift progress overview |
| `shift-assignments/lib/store.ts` | Zustand store for assignment state |
| `shift-assignments/lib/api.ts` | Frontend HTTP client |
| `requirements.txt` | Python dependencies |
| `shift-assignments/package.json` | Frontend dependencies and scripts |
| `cloudbuild.yaml` | Cloud Build deployment pipeline |
| `setup-scheduler.sh` | Creates/updates the auto-publish Cloud Scheduler jobs |

---

## Common Tasks

### Add a New Admin/Reviewer Endpoint
1. Edit `main.py` (add route)
2. Use `roles.list_admins()` / `roles.list_reviewers()` to read from Storage API
3. Use `roles.create_record()` or `roles.update_record()` to write
4. Test with dev token: `curl -H "Authorization: Bearer $(cat ~/.storesight/dev-token)" http://localhost:8080/api/admins`

### Modify Shift Assignment Logic
1. Edit `lib/assign.ts` (assignment algorithm)
2. Test in the browser via `/assignments` → compose a shift → click "Publish"
3. Check the API response in DevTools Network tab

### Change UI for a Role
1. Check `lib/useUser.tsx` for current user's role
2. Conditionally render in components: `{isAdmin && <AdminFeature />}`
3. Test by logging in as different roles (Settings page)

### Debug Storage API 403 Errors
- **Symptom:** "Missing X-Tool-Slug header" or 403 on Storage API calls
- **Cause:** `TOOL_SLUG` environment variable not set
- **Fix:** `TOOL_SLUG=qc-shift-assignments python3 main.py`

### Check Bloom Job Data
```bash
curl -H "Authorization: Bearer $(cat ~/.storesight/dev-token)" \
  "http://localhost:8080/api/bloom/jobs?force=1" | jq .
```

---

## Rate Limits and Quotas

**Internal API:**
- 60 requests/minute per tool (keyed on X-Tool-Slug header)
- 429 response triggers automatic retry with exponential backoff (1s, 2s, 4s)
- Don't fire many requests in parallel; fetch sequentially or use pagination

**Storage API:**
- 10,000 documents per tool namespace
- 50 KB per document
- No rate limit, but accessed through Internal API (so respects 60 req/min global limit)

---

## What NOT to Do

- ❌ Hardcode credentials, tokens, API keys, or secrets
- ❌ Modify `@app.before_request` auth middleware or remove `/health` / `/logout` routes
  — the one sanctioned exception is the Cloud Scheduler OIDC check for
  `/api/shifts/auto-publish`; don't extend it to other paths
- ❌ Call databases (Postgres, etc.) directly — use Internal API + Storage API only
- ❌ Build a custom login page — centralized auth handles it
- ❌ Use raw `requests` library for Internal API — use `internal_api` helper
- ❌ Store sensitive data (passwords, PII, tokens) in Storage API without CTO approval
- ❌ Deploy to GCP projects other than `storesight-internal-tools`
- ❌ Call Slack, Google AI, or external APIs directly — use Internal API proxies

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "Dev token not found" on startup | Run `python3 get-dev-token.py` in terminal |
| Dev token expired (8 hours) | Run `python3 get-dev-token.py` again |
| Storage API returns 403 | Check that `TOOL_SLUG=qc-shift-assignments` is set |
| Next.js dev server shows 404 on `/api/*` | Make sure Flask is running on port 8080 |
| Python import errors | Run `pip install -r requirements.txt` in activated venv |
| `python3: command not found` | Install Python: `brew install python` (Mac) or `sudo apt install python3` (Linux) |

