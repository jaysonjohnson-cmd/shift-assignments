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
**Owner:** micah.mccollum@storesight.com

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

**Rollback:** Use Cloud Run revision traffic splitting in GCP console.

---

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

