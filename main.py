import datetime
import json
import logging
import os
import pathlib
import time
from typing import Optional

import jwt
import requests
from flask import Flask, jsonify, redirect, request, g, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix

import bloom
import internal_api
import roles

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

JWT_SECRET = os.environ.get("JWT_SIGNING_SECRET", "")
AUTH_SERVICE_URL = "https://auth-service.storesight.org"
LOCAL_DEV = os.environ.get("LOCAL_DEV") == "1"


def _dev_token_path():
    """Return the path to the dev token file."""
    return pathlib.Path.home() / ".storesight" / "dev-token"


@app.before_request
def require_auth():
    if request.path == "/health":
        return

    if LOCAL_DEV:
        # Local development: read identity from dev token file
        token_file = _dev_token_path()
        try:
            token_str = token_file.read_text().strip()
        except FileNotFoundError:
            return (
                "<h1>Dev token not found</h1>"
                "<p>No dev token at ~/.storesight/dev-token. "
                "Run the dev token setup flow to authenticate.</p>"
            ), 401

        if not token_str:
            return (
                "<h1>Dev token not found</h1>"
                "<p>Dev token file is empty. Re-run the setup flow.</p>"
            ), 401

        # Decode without verifying signature (no JWT_SIGNING_SECRET locally).
        # Check expiry manually.
        try:
            payload = jwt.decode(
                token_str, options={"verify_signature": False, "verify_aud": False, "verify_exp": False}
            )
        except jwt.InvalidTokenError:
            return "<h1>Invalid dev token</h1><p>Re-run the setup flow.</p>", 401

        if payload.get("exp", 0) < time.time():
            return (
                "<h1>Dev token expired</h1>"
                "<p>Your dev token has expired. Re-authenticate by running the setup flow.</p>"
            ), 401

        g.user = {"email": payload.get("email", ""), "name": payload.get("name", "")}
        return

    # Production: validate storesight_session cookie
    token = request.cookies.get("storesight_session")
    if not token:
        return redirect(f"{AUTH_SERVICE_URL}/login?return_url={request.url}")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        g.user = {"email": payload["email"], "name": payload.get("name", "")}
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return redirect(f"{AUTH_SERVICE_URL}/login?return_url={request.url}")


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/logout")
def logout():
    return redirect(f"{AUTH_SERVICE_URL}/logout?return_url={request.url_root}")


# ---------- Serve the Next.js static export ----------
#
# The Dockerfile builds `shift-assignments/` to `frontend/` inside the image.
# Flask owns the whole origin — `/api/*` and `/health` are served by specific
# route handlers below; everything else falls through to the static export.
_FRONTEND_DIR = pathlib.Path(__file__).parent / "frontend"


def _serve_static(path: str):
    """Resolve a request path against the Next.js export tree.

    Next.js `output: "export"` writes one of two shapes depending on
    `trailingSlash`. Handle both:
      /          -> index.html
      /foo       -> foo.html        (default)
      /foo       -> foo/index.html  (trailingSlash: true)
      /_next/... -> _next/... (hashed assets — exact match)
    """
    if not _FRONTEND_DIR.is_dir():
        # Image built without the frontend stage — surface clearly instead of 404.
        return (
            "<h1>Frontend not built</h1>"
            "<p>The Next.js static export is missing from this image. "
            "Rebuild with the two-stage Dockerfile.</p>",
            500,
        )
    # Normalize: strip leading/trailing slashes, reject path traversal.
    cleaned = path.strip("/")
    if not cleaned:
        cleaned = "index.html"
    candidate = (_FRONTEND_DIR / cleaned).resolve()
    try:
        candidate.relative_to(_FRONTEND_DIR.resolve())
    except ValueError:
        return "Not found", 404

    if candidate.is_file():
        return send_from_directory(_FRONTEND_DIR, cleaned)
    html_variant = _FRONTEND_DIR / f"{cleaned}.html"
    if html_variant.is_file():
        return send_from_directory(_FRONTEND_DIR, f"{cleaned}.html")
    index_variant = _FRONTEND_DIR / cleaned / "index.html"
    if index_variant.is_file():
        return send_from_directory(_FRONTEND_DIR, f"{cleaned}/index.html")
    # Fall back to Next's 404 page so the user sees the app shell, not a raw error.
    notfound = _FRONTEND_DIR / "404.html"
    if notfound.is_file():
        return send_from_directory(_FRONTEND_DIR, "404.html"), 404
    return "Not found", 404


@app.route("/")
def index():
    return _serve_static("index.html")


@app.route("/<path:path>")
def frontend_catchall(path):
    return _serve_static(path)


# ---------- API: identity + role ----------


@app.route("/api/me")
def api_me():
    email = g.user.get("email", "")
    name = g.user.get("name", "")
    return jsonify({"email": email, "name": name, "role": roles.get_role(email)})


# ---------- API: reviewers / admins ----------


def _require_admin():
    """Return a Flask response if the caller isn't an admin, else None."""
    if not roles.is_admin(g.user.get("email", "")):
        return jsonify({"error": "admin only"}), 403
    return None


def _validate_person_body(body):
    """Validate an add/update payload. Returns (name, email, error_response)."""
    if not isinstance(body, dict):
        return None, None, (jsonify({"error": "body must be a JSON object"}), 400)
    name = (body.get("name") or "").strip()
    email = (body.get("email") or "").strip().lower()
    if not name:
        return None, None, (jsonify({"error": "name is required"}), 400)
    if not email or "@" not in email:
        return None, None, (jsonify({"error": "a valid email is required"}), 400)
    return name, email, None


@app.route("/api/reviewers", methods=["GET"])
def api_reviewers_list():
    try:
        return jsonify({"data": roles.list_reviewers()})
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 500
        return jsonify({"error": f"storage api returned {status}", "data": []}), status


@app.route("/api/reviewers", methods=["POST"])
def api_reviewers_create():
    denied = _require_admin()
    if denied is not None:
        return denied
    name, email, err = _validate_person_body(request.get_json(silent=True))
    if err:
        return err
    existing = {r["email"] for r in roles.list_reviewers()}
    if email in existing:
        return jsonify({"error": "reviewer with that email already exists"}), 409
    doc_id = roles.create_record("reviewer", name, email)
    logging.info(
        "POST /api/reviewers by=%s created reviewer=%s", g.user.get("email"), email
    )
    return jsonify({"data": {"id": doc_id, "name": name, "email": email}}), 201


@app.route("/api/reviewers/<doc_id>", methods=["PUT"])
def api_reviewers_update(doc_id):
    denied = _require_admin()
    if denied is not None:
        return denied
    name, email, err = _validate_person_body(request.get_json(silent=True))
    if err:
        return err
    try:
        roles.update_record(doc_id, "reviewer", name, email)
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 500
        return jsonify({"error": f"storage api returned {status}"}), status
    logging.info(
        "PUT /api/reviewers/%s by=%s email=%s", doc_id, g.user.get("email"), email
    )
    return jsonify({"data": {"id": doc_id, "name": name, "email": email}})


@app.route("/api/reviewers/<doc_id>", methods=["DELETE"])
def api_reviewers_delete(doc_id):
    denied = _require_admin()
    if denied is not None:
        return denied
    try:
        roles.delete_record(doc_id)
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 500
        return jsonify({"error": f"storage api returned {status}"}), status
    logging.info("DELETE /api/reviewers/%s by=%s", doc_id, g.user.get("email"))
    return jsonify({"data": {"id": doc_id}})


@app.route("/api/admins", methods=["GET"])
def api_admins_list():
    root_email = roles.ROOT_ADMIN_EMAIL.lower()
    root_entry = {"id": "__root__", "name": "Jayson Johnson", "email": root_email}
    try:
        stored = roles.list_admins()
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 500
        # Fall back to just the root admin so the UI can still function.
        return (
            jsonify({"error": f"storage api returned {status}", "data": [root_entry]}),
            status,
        )
    # Surface the root admin in the listing even if they haven't been stored.
    if not any(a["email"] == root_email for a in stored):
        stored = [root_entry, *stored]
    return jsonify({"data": stored})


@app.route("/api/admins", methods=["POST"])
def api_admins_create():
    denied = _require_admin()
    if denied is not None:
        return denied
    name, email, err = _validate_person_body(request.get_json(silent=True))
    if err:
        return err
    if email == roles.ROOT_ADMIN_EMAIL.lower():
        return jsonify({"error": "that email is already the root admin"}), 409
    existing = {a["email"] for a in roles.list_admins()}
    if email in existing:
        return jsonify({"error": "admin with that email already exists"}), 409
    doc_id = roles.create_record("admin", name, email)
    logging.info(
        "POST /api/admins by=%s created admin=%s", g.user.get("email"), email
    )
    return jsonify({"data": {"id": doc_id, "name": name, "email": email}}), 201


@app.route("/api/admins/<doc_id>", methods=["DELETE"])
def api_admins_delete(doc_id):
    denied = _require_admin()
    if denied is not None:
        return denied
    if doc_id == "__root__":
        return jsonify({"error": "the root admin cannot be removed"}), 400
    try:
        roles.delete_record(doc_id)
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 500
        return jsonify({"error": f"storage api returned {status}"}), status
    logging.info("DELETE /api/admins/%s by=%s", doc_id, g.user.get("email"))
    return jsonify({"data": {"id": doc_id}})


# ---------- API: Bloom feed + published shift snapshots ----------


_STORAGE_PATH = "/api/storage/qc-shift-assignments"


# Fields kept in the published shift_snapshot per row. Storage API limits
# documents to ~50 KB, so everything My Tasks doesn't use is dropped before
# write. Anything here must stay in sync with the Row fields read by
# shift-assignments/app/my-tasks/page.tsx.
_PUBLISHED_ROW_FIELDS = (
    "id",
    "projectId",
    "jobId",
    "priority",
    "name",
    "unreviewedCount",
    "oldestSubmission",
)


def _compact_row(row):
    """Project a Bloom Row down to the fields My Tasks actually needs."""
    if not isinstance(row, dict):
        return row
    return {k: row[k] for k in _PUBLISHED_ROW_FIELDS if k in row}


# Storage API caps each doc at ~50 KB. We target a smaller budget so the
# doc envelope (kind, shift_snapshot_id, reviewer_email, part metadata) has
# comfortable headroom once serialized.
_REVIEWER_CHUNK_BUDGET_BYTES = 40000


def _chunk_rows_for_storage(rows):
    """Split a reviewer's row list into chunks that fit in Storage's 50KB cap.

    Returns a list of lists. Always returns at least one chunk (possibly
    empty) so callers can index deterministically.
    """
    if not rows:
        return [[]]
    chunks = []
    current = []
    current_size = 2  # `[]`
    for row in rows:
        encoded = len(json.dumps(row, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        # +1 for the comma between items
        projected = current_size + encoded + (1 if current else 0)
        if current and projected > _REVIEWER_CHUNK_BUDGET_BYTES:
            chunks.append(current)
            current = [row]
            current_size = 2 + encoded
        else:
            current.append(row)
            current_size = projected
    chunks.append(current)
    return chunks


def _compact_assignments(assignments):
    """Apply `_compact_row` to every row in an {email: Row[]} mapping."""
    out = {}
    for email, rows in assignments.items():
        if not isinstance(rows, list):
            out[email] = rows
            continue
        out[email] = [_compact_row(r) for r in rows]
    return out


def _http_error_response(exc, source="storage api"):
    """Surface the real upstream response body (if any) instead of just the status.

    Storage API returns JSON error bodies like `{"error": "document too large"}`.
    Older callers only saw `"storage api returned 400"`, which made large-snapshot
    failures impossible to diagnose. Now we unwrap the JSON `error` field, then
    fall back to the raw body, then to the status-only message.
    """
    resp = exc.response
    status = resp.status_code if resp is not None else 500
    upstream = None
    if resp is not None:
        try:
            payload = resp.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            upstream = payload.get("error") or payload.get("message")
        if not upstream:
            text = (resp.text or "").strip()
            if text:
                upstream = text[:500]
    message = (
        f"{source} returned {status}: {upstream}"
        if upstream
        else f"{source} returned {status}"
    )
    return jsonify({"error": message}), status


@app.route("/api/bloom/jobs", methods=["GET"])
def api_bloom_jobs():
    """Return prioritized Rows pulled live from /api/jobs. Admin-only."""
    denied = _require_admin()
    if denied is not None:
        return denied
    status = request.args.get("status") or bloom.DEFAULT_STATUS
    force = request.args.get("force") == "1"
    if force:
        bloom.clear_cache()
    try:
        rows = bloom.fetch_prioritized_jobs(status=status)
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e, source="bloom api")
    logging.info(
        "GET /api/bloom/jobs by=%s status=%s count=%d",
        g.user.get("email"), status, len(rows),
    )
    return jsonify({"data": rows})


@app.route("/api/bloom/projects", methods=["GET"])
def api_bloom_projects():
    """Return project-level summaries derived from the cached Bloom rows.

    One entry per unique projectId: {projectId, projectName, jidCount,
    oldestSubmission}. Shares the 60s cache with /api/bloom/jobs — calling
    force=1 on the jobs endpoint is enough to refresh both.
    """
    denied = _require_admin()
    if denied is not None:
        return denied
    status = request.args.get("status") or bloom.DEFAULT_STATUS
    try:
        rows = bloom.fetch_prioritized_jobs(status=status)
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e, source="bloom api")
    summaries = bloom.project_summaries(rows)
    logging.info(
        "GET /api/bloom/projects by=%s status=%s count=%d",
        g.user.get("email"), status, len(summaries),
    )
    return jsonify({"data": summaries})


@app.route("/api/shifts/latest", methods=["GET"])
def api_shifts_latest():
    """Return the most recent shift_snapshot doc, or {data: null}."""
    try:
        snaps = roles.list_docs_by_kind("shift_snapshot")
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e)
    if not snaps:
        return jsonify({"data": None})
    # list_docs_by_kind paginates newest-first (Storage API default).
    latest = snaps[0]
    return jsonify({"data": {"id": latest.get("id"), **(latest.get("data") or {})}})


def _try_delete(doc_id):
    """Best-effort delete used for rollback — never raises."""
    if not doc_id:
        return
    try:
        internal_api.delete(f"{_STORAGE_PATH}/{doc_id}")
    except Exception:  # noqa: BLE001 — rollback is best-effort
        logging.warning("rollback delete failed for doc_id=%s", doc_id)


@app.route("/api/shifts/publish", methods=["POST"])
def api_shifts_publish():
    """Admin publishes a shift.

    Storage API caps documents at ~50 KB, so we never store the full
    {email: rows} map in one doc. Instead we write:
      • one slim `shift_snapshot` index doc (metadata + reviewer emails)
      • one `reviewer_shift` doc per reviewer with their rows

    `/api/shifts/my` joins back by `shift_snapshot_id + reviewer_email`.
    If any per-reviewer write fails, everything written so far is rolled
    back so we never leave an orphan index pointing at missing rows.
    """
    denied = _require_admin()
    if denied is not None:
        return denied
    body = request.get_json(silent=True) or {}
    assignments = body.get("assignments")
    if not isinstance(assignments, dict):
        return jsonify({"error": "assignments must be an object"}), 400

    normalized = {}
    for email, rows in assignments.items():
        key = (email or "").strip().lower()
        if not key:
            continue
        if not isinstance(rows, list):
            continue
        normalized[key] = [_compact_row(r) for r in rows]

    if not normalized:
        return jsonify({"error": "assignments cannot be empty — assign at least one reviewer before publishing"}), 400

    published_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    published_by = g.user.get("email", "")
    reviewer_emails = sorted(normalized.keys())

    index_doc = {
        "kind": "shift_snapshot",
        "published_at": published_at,
        "published_by": published_by,
        "reviewer_emails": reviewer_emails,
    }
    try:
        resp = internal_api.post(_STORAGE_PATH, json={"data": index_doc})
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e)
    snapshot_id = (resp.get("data") or {}).get("id")

    written = []
    for email in reviewer_emails:
        chunks = _chunk_rows_for_storage(normalized[email])
        for idx, chunk in enumerate(chunks):
            doc = {
                "kind": "reviewer_shift",
                "shift_snapshot_id": snapshot_id,
                "reviewer_email": email,
                "rows": chunk,
                "part": idx,
                "part_count": len(chunks),
            }
            try:
                r = internal_api.post(_STORAGE_PATH, json={"data": doc})
            except requests.exceptions.HTTPError as e:
                for did in written:
                    _try_delete(did)
                _try_delete(snapshot_id)
                return _http_error_response(e)
            written.append((r.get("data") or {}).get("id"))

    logging.info(
        "POST /api/shifts/publish by=%s snapshot_id=%s reviewers=%d",
        published_by, snapshot_id, len(reviewer_emails),
    )
    return jsonify({"data": {"id": snapshot_id, "published_at": published_at}}), 201


def _latest_snapshot():
    """Helper — return the latest snapshot's {id, data} or (None, None).

    Skips empty snapshots — those where all reviewer_shift docs have zero rows
    (e.g. published with prioritizeAged on before the fix, or a mid-publish
    failure). Uses the most recent snapshot that actually has assigned jobs.
    """
    snaps = roles.list_docs_by_kind("shift_snapshot")
    if not snaps:
        return None, None

    # Count total rows per snapshot across all reviewer_shift docs.
    snap_row_counts: dict[str, int] = {}
    for d in roles.list_docs_by_kind("reviewer_shift"):
        data = d.get("data") or {}
        sid = data.get("shift_snapshot_id")
        if sid:
            snap_row_counts[sid] = snap_row_counts.get(sid, 0) + len(data.get("rows") or [])

    # Return the most recent snapshot that has reviewer_emails AND rows assigned.
    for snap in snaps:
        data = snap.get("data") or {}
        if data.get("reviewer_emails") and snap_row_counts.get(snap.get("id"), 0) > 0:
            return snap.get("id"), data

    # Fallback: first snapshot with reviewer_emails (edge case: first-ever publish)
    for snap in snaps:
        data = snap.get("data") or {}
        if data.get("reviewer_emails"):
            return snap.get("id"), data

    return None, None


def _rows_for_reviewer(snapshot_id, email):
    """Return the per-reviewer rows stored under the given snapshot.

    Assembles all chunk docs (see `_chunk_rows_for_storage`) for this
    reviewer in `part` order. Docs written before chunking (no `part`
    field) are treated as a single chunk at position 0.
    """
    norm = (email or "").strip().lower()
    matches = []
    for doc in roles.list_docs_by_kind("reviewer_shift"):
        data = doc.get("data") or {}
        if data.get("shift_snapshot_id") != snapshot_id:
            continue
        if (data.get("reviewer_email") or "").strip().lower() != norm:
            continue
        matches.append(data)
    if not matches:
        return None
    matches.sort(key=lambda d: int(d.get("part") or 0))
    out = []
    for data in matches:
        out.extend(data.get("rows") or [])
    return out


def _list_completions_for_snapshot(snapshot_id, reviewer_email=None):
    """Return completion docs for a snapshot, optionally filtered by reviewer."""
    docs = roles.list_docs_by_kind("completion")
    out = []
    norm_email = (reviewer_email or "").strip().lower()
    for doc in docs:
        data = doc.get("data") or {}
        if data.get("shift_snapshot_id") != snapshot_id:
            continue
        if norm_email and (data.get("reviewer_email") or "").lower() != norm_email:
            continue
        out.append({"id": doc.get("id"), **data})
    return out


@app.route("/api/shifts/my", methods=["GET"])
def api_shifts_my():
    """Return this reviewer's rows from the latest snapshot + completion state."""
    email = (g.user.get("email") or "").strip().lower()
    try:
        snap_id, snap_data = _latest_snapshot()
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e)
    if not snap_id:
        return jsonify({"data": {"snapshot_id": None, "rows": []}})
    try:
        rows = _rows_for_reviewer(snap_id, email)
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e)
    if rows is None:
        # Legacy snapshot shape — fall back to in-snapshot assignments map.
        rows = (snap_data.get("assignments") or {}).get(email, []) or []
    try:
        completions = _list_completions_for_snapshot(snap_id, reviewer_email=email)
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e)
    done_by_pid = {c["project_id"]: c for c in completions if c.get("project_id")}
    enriched = []
    for row in rows:
        pid = str(row.get("projectId") or row.get("id") or "")
        completion = done_by_pid.get(pid)
        enriched.append({
            **row,
            "completedAt": completion.get("completed_at") if completion else None,
        })
    return jsonify({
        "data": {
            "snapshot_id": snap_id,
            "published_at": snap_data.get("published_at"),
            "rows": enriched,
        }
    })


def _job_key(row):
    """Stable per-job identity used to keep a job from being assigned twice."""
    return str((row or {}).get("jobId") or (row or {}).get("id") or "")


def _auto_refill_reviewer(snap_id, email, count):
    """Top up a finished reviewer's queue with up to `count` fresh jobs.

    Pulls from the live prioritized feed, skipping any job already assigned to
    anyone in the current shift (preserving the no-overlap guarantee), and
    appends the new rows as an additional reviewer_shift chunk. Returns the
    rows added (compacted). Best-effort: returns [] on any failure or when the
    feed has nothing new left.
    """
    norm = (email or "").strip().lower()
    assigned_keys = set()
    max_part = -1
    try:
        docs = roles.list_docs_by_kind("reviewer_shift")
    except Exception as exc:  # noqa: BLE001 — refill is best-effort
        logging.warning("auto-refill: failed to list shifts for %s: %s", email, exc)
        return []
    for doc in docs:
        data = doc.get("data") or {}
        if data.get("shift_snapshot_id") != snap_id:
            continue
        for r in data.get("rows") or []:
            k = _job_key(r)
            if k:
                assigned_keys.add(k)
        if (data.get("reviewer_email") or "").strip().lower() == norm:
            max_part = max(max_part, int(data.get("part") or 0))

    try:
        pool = bloom.fetch_prioritized_jobs()
    except Exception as exc:  # noqa: BLE001 — refill is best-effort
        logging.warning("auto-refill: failed to fetch jobs for %s: %s", email, exc)
        return []

    fresh = []
    for r in pool:
        k = _job_key(r)
        if not k or k in assigned_keys:
            continue
        fresh.append(_compact_row(r))
        assigned_keys.add(k)  # guard against dupes within the same feed
        if len(fresh) >= count:
            break
    if not fresh:
        logging.info("auto-refill: no new jobs left for %s", email)
        return []

    next_part = max_part + 1
    chunks = _chunk_rows_for_storage(fresh)
    for idx, chunk in enumerate(chunks):
        doc = {
            "kind": "reviewer_shift",
            "shift_snapshot_id": snap_id,
            "reviewer_email": norm,
            "rows": chunk,
            "part": next_part + idx,
            "part_count": next_part + len(chunks),
        }
        try:
            internal_api.post(_STORAGE_PATH, json={"data": doc})
        except Exception as exc:  # noqa: BLE001 — refill is best-effort
            logging.warning("auto-refill: failed to store chunk for %s: %s", email, exc)
            break
    logging.info("auto-refilled %d jobs for %s", len(fresh), email)
    return fresh


def _notify_reviewer_finished(email, total_jobs, added_jobs):
    """Best-effort Slack ping when a reviewer finishes their whole queue.

    Posts to the channel in the SLACK_NOTIFY_CHANNEL env var. No-ops (with a
    log line) when no channel is configured, and never raises — a failed ping
    must never break the reviewer's completion.
    """
    channel = (os.environ.get("SLACK_NOTIFY_CHANNEL") or "").strip()
    if not channel:
        logging.info(
            "reviewer %s finished all jobs; SLACK_NOTIFY_CHANNEL unset, no ping", email
        )
        return
    name = email
    try:
        for r in roles.list_reviewers():
            if r.get("email") == email:
                name = r.get("name") or email
                break
    except Exception as exc:  # noqa: BLE001 — name lookup is best-effort
        logging.warning("finish-ping name lookup failed for %s: %s", email, exc)
    plural = "s" if total_jobs != 1 else ""
    if added_jobs > 0:
        added_plural = "s" if added_jobs != 1 else ""
        tail = f"auto-assigned {added_jobs} more job{added_plural}."
    else:
        tail = "no more jobs left in the queue to assign."
    text = (
        f":white_check_mark: *{name}* just finished all {total_jobs} "
        f"assignment{plural} — {tail}"
    )
    try:
        internal_api.post("/api/slack/post", json={"channel": channel, "text": text})
        logging.info("sent finish ping for %s to channel %s", email, channel)
    except Exception as exc:  # noqa: BLE001 — ping is best-effort
        logging.warning("failed to send finish ping for %s: %s", email, exc)


@app.route("/api/shifts/my/complete", methods=["POST"])
def api_shifts_my_complete():
    """Mark a row done for the signed-in reviewer. Idempotent."""
    body = request.get_json(silent=True) or {}
    project_id = str(body.get("project_id") or "").strip()
    if not project_id:
        return jsonify({"error": "project_id is required"}), 400
    email = (g.user.get("email") or "").strip().lower()
    try:
        snap_id, _ = _latest_snapshot()
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e)
    if not snap_id:
        return jsonify({"error": "no shift has been published yet"}), 409
    # Idempotency: skip create if one already exists.
    try:
        existing = _list_completions_for_snapshot(snap_id, reviewer_email=email)
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e)
    for c in existing:
        if str(c.get("project_id")) == project_id:
            return jsonify({"data": c})
    completed_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    doc = {
        "kind": "completion",
        "reviewer_email": email,
        "project_id": project_id,
        "shift_snapshot_id": snap_id,
        "completed_at": completed_at,
        "note": (body.get("note") or "").strip(),
    }
    try:
        resp = internal_api.post(_STORAGE_PATH, json={"data": doc})
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e)
    doc_id = (resp.get("data") or {}).get("id")
    logging.info(
        "POST /api/shifts/my/complete by=%s project_id=%s snapshot_id=%s",
        email, project_id, snap_id,
    )
    # If this completion cleared the reviewer's whole queue, ping the admin so
    # they can hand out more work. Best-effort — never block the response.
    try:
        assigned = _rows_for_reviewer(snap_id, email) or []
        assigned_keys = {_row_project_key(r) for r in assigned}
        done_keys = {str(c.get("project_id")) for c in existing}
        done_keys.add(project_id)
        if assigned_keys and assigned_keys <= done_keys:
            # Auto-assign the same number of fresh jobs, then ping the admin.
            added = _auto_refill_reviewer(snap_id, email, len(assigned))
            _notify_reviewer_finished(email, len(assigned), len(added))
    except Exception as exc:  # noqa: BLE001 — refill/ping must not break completion
        logging.warning("finish-check failed for %s: %s", email, exc)
    return jsonify({"data": {"id": doc_id, **doc}}), 201


@app.route("/api/shifts/my/complete/<project_id>", methods=["DELETE"])
def api_shifts_my_uncomplete(project_id):
    """Un-complete a row (reviewer misclicked)."""
    email = (g.user.get("email") or "").strip().lower()
    try:
        snap_id, _ = _latest_snapshot()
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e)
    if not snap_id:
        return jsonify({"data": {"project_id": project_id}})
    try:
        existing = _list_completions_for_snapshot(snap_id, reviewer_email=email)
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e)
    for c in existing:
        if str(c.get("project_id")) == str(project_id):
            try:
                internal_api.delete(f"{_STORAGE_PATH}/{c['id']}")
            except requests.exceptions.HTTPError as e:
                return _http_error_response(e)
            logging.info(
                "DELETE /api/shifts/my/complete/%s by=%s", project_id, email,
            )
            break
    return jsonify({"data": {"project_id": str(project_id)}})


@app.route("/api/shifts/completions", methods=["GET"])
def api_shifts_list_completions():
    """Admin: return all completion docs for the latest snapshot."""
    denied = _require_admin()
    if denied is not None:
        return denied
    try:
        snap_id, _ = _latest_snapshot()
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e)
    if not snap_id:
        return jsonify({"data": {"snapshot_id": None, "completions": []}})
    try:
        completions = _list_completions_for_snapshot(snap_id)
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e)
    return jsonify(
        {"data": {"snapshot_id": snap_id, "completions": completions}}
    )


@app.route("/api/shifts/completions", methods=["DELETE"])
def api_shifts_reset_completions():
    """Admin: clear all completion docs for the current snapshot."""
    denied = _require_admin()
    if denied is not None:
        return denied
    try:
        snap_id, _ = _latest_snapshot()
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e)
    if not snap_id:
        return jsonify({"data": {"deleted": 0}})
    try:
        completions = _list_completions_for_snapshot(snap_id)
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e)
    deleted = 0
    for c in completions:
        try:
            internal_api.delete(f"{_STORAGE_PATH}/{c['id']}")
            deleted += 1
        except requests.exceptions.HTTPError:
            # skip, continue
            pass
    logging.info(
        "DELETE /api/shifts/completions by=%s snapshot_id=%s count=%d",
        g.user.get("email"), snap_id, deleted,
    )
    return jsonify({"data": {"deleted": deleted}})


def _row_project_key(row):
    """Match completion docs to rows (completion.project_id vs row.projectId|id)."""
    return str(row.get("projectId") or row.get("id") or "")


@app.route("/api/shifts/overview", methods=["GET"])
def api_shifts_overview():
    """Admin: live check-in view of the current shift.

    Returns per-reviewer totals so admins can see who's keeping up without
    fetching every row into the browser.
    """
    denied = _require_admin()
    if denied is not None:
        return denied
    try:
        snap_id, snap_data = _latest_snapshot()
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e)
    if not snap_id:
        return jsonify({"data": {"snapshot_id": None, "reviewers": []}})

    try:
        reviewer_docs = [
            d for d in roles.list_docs_by_kind("reviewer_shift")
            if (d.get("data") or {}).get("shift_snapshot_id") == snap_id
        ]
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e)
    try:
        completions = _list_completions_for_snapshot(snap_id)
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e)

    try:
        roster = {r["email"].lower(): r.get("name") or "" for r in roles.list_reviewers()}
    except Exception:  # noqa: BLE001 — name lookup is best-effort
        roster = {}

    done_by_reviewer = {}  # email -> {project_id, ...}
    for c in completions:
        email = (c.get("reviewer_email") or "").lower()
        pid = str(c.get("project_id") or "")
        if not email or not pid:
            continue
        done_by_reviewer.setdefault(email, set()).add(pid)

    rows_by_email = {}
    for doc in reviewer_docs:
        data = doc.get("data") or {}
        email = (data.get("reviewer_email") or "").lower()
        rows_by_email.setdefault(email, []).extend(data.get("rows") or [])

    reviewers_out = []
    for email, rows in rows_by_email.items():
        total = len(rows)
        if total == 0:
            continue
        done_set = done_by_reviewer.get(email, set())
        completed = sum(1 for r in rows if _row_project_key(r) in done_set)
        priorities = [r.get("priority") for r in rows if isinstance(r.get("priority"), int)]
        reviewers_out.append({
            "email": email,
            "name": roster.get(email, ""),
            "total": total,
            "completed": completed,
            "pending": total - completed,
            "first_priority": min(priorities) if priorities else None,
            "last_priority": max(priorities) if priorities else None,
        })

    # Largest workloads first, stable tiebreak by name/email.
    reviewers_out.sort(key=lambda r: (-r["total"], r["name"] or r["email"]))

    return jsonify({
        "data": {
            "snapshot_id": snap_id,
            "published_at": snap_data.get("published_at"),
            "reviewers": reviewers_out,
        }
    })


@app.route("/api/shifts/jobs", methods=["GET"])
def api_shifts_jobs():
    """Admin: detailed view of all jobs assigned in the current shift."""
    denied = _require_admin()
    if denied is not None:
        return denied
    try:
        snap_id, snap_data = _latest_snapshot()
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e)
    if not snap_id:
        return jsonify({"data": {"snapshot_id": None, "jobs_by_reviewer": []}})

    try:
        reviewer_docs = [
            d for d in roles.list_docs_by_kind("reviewer_shift")
            if (d.get("data") or {}).get("shift_snapshot_id") == snap_id
        ]
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e)
    try:
        completions = _list_completions_for_snapshot(snap_id)
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e)

    try:
        roster = {r["email"].lower(): r.get("name") or "" for r in roles.list_reviewers()}
    except Exception:  # noqa: BLE001 — name lookup is best-effort
        roster = {}

    done_by_reviewer = {}
    for c in completions:
        email = (c.get("reviewer_email") or "").lower()
        pid = str(c.get("project_id") or "")
        if not email or not pid:
            continue
        done_by_reviewer.setdefault(email, set()).add(pid)

    rows_by_email = {}
    for doc in reviewer_docs:
        data = doc.get("data") or {}
        email = (data.get("reviewer_email") or "").lower()
        rows_by_email.setdefault(email, []).extend(data.get("rows") or [])

    jobs_by_reviewer = []
    for email, rows in rows_by_email.items():
        done_set = done_by_reviewer.get(email, set())
        jobs = []
        for r in rows:
            completed = _row_project_key(r) in done_set
            jobs.append({
                "id": r.get("id", ""),
                "projectId": r.get("projectId", ""),
                "jobId": r.get("jobId", ""),
                "priority": r.get("priority"),
                "unreviewedCount": r.get("unreviewedCount", 0),
                "name": r.get("name", ""),
                "completed": completed,
                "oldestSubmission": r.get("oldestSubmission", ""),
                "groupIds": r.get("groupIds", []),
            })
        jobs_by_reviewer.append({
            "email": email,
            "name": roster.get(email, ""),
            "jobs": jobs,
        })

    jobs_by_reviewer.sort(key=lambda r: (r["name"] or r["email"]))

    return jsonify({
        "data": {
            "snapshot_id": snap_id,
            "published_at": snap_data.get("published_at"),
            "jobs_by_reviewer": jobs_by_reviewer,
        }
    })


@app.route("/api/shifts/clear", methods=["POST"])
def api_shifts_clear():
    """Admin: mass-clear tasks and/or completion marks on the current shift.

    Body: `{mode: "active" | "completed" | "all"}`
      • active    — wipe only rows the reviewer has NOT marked done
      • completed — wipe only rows the reviewer HAS marked done (and their completion docs)
      • all       — wipe everything (rows + completions) for the current snapshot
    """
    denied = _require_admin()
    if denied is not None:
        return denied
    body = request.get_json(silent=True) or {}
    mode = body.get("mode")
    if mode not in ("active", "completed", "all"):
        return jsonify({"error": "mode must be 'active', 'completed', or 'all'"}), 400

    try:
        snap_id, _ = _latest_snapshot()
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e)
    if not snap_id:
        return jsonify({"data": {"mode": mode, "cleared_rows": 0, "cleared_completions": 0}})

    try:
        reviewer_docs = [
            d for d in roles.list_docs_by_kind("reviewer_shift")
            if (d.get("data") or {}).get("shift_snapshot_id") == snap_id
        ]
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e)
    try:
        completions = _list_completions_for_snapshot(snap_id)
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e)

    done_set = {
        ((c.get("reviewer_email") or "").lower(), str(c.get("project_id") or ""))
        for c in completions
    }

    cleared_rows = 0
    cleared_completions = 0

    def _rewrite_or_delete(doc, kept_rows):
        path = f"{_STORAGE_PATH}/{doc['id']}"
        data = doc.get("data") or {}
        if kept_rows:
            new_data = {**data, "rows": kept_rows}
            internal_api.put(path, json={"data": new_data})
        else:
            internal_api.delete(path)

    if mode == "all":
        for d in reviewer_docs:
            rows = (d.get("data") or {}).get("rows") or []
            try:
                internal_api.delete(f"{_STORAGE_PATH}/{d['id']}")
                cleared_rows += len(rows)
            except requests.exceptions.HTTPError:
                pass
        for c in completions:
            try:
                internal_api.delete(f"{_STORAGE_PATH}/{c['id']}")
                cleared_completions += 1
            except requests.exceptions.HTTPError:
                pass
    else:
        # Surgical modes rewrite each reviewer_shift doc to filter rows.
        keep_done = mode == "active"  # active: keep done rows, drop pending
        for d in reviewer_docs:
            data = d.get("data") or {}
            email = (data.get("reviewer_email") or "").lower()
            rows = data.get("rows") or []
            kept = []
            for r in rows:
                is_done = (email, _row_project_key(r)) in done_set
                if keep_done == is_done:
                    kept.append(r)
            cleared_rows += len(rows) - len(kept)
            if len(kept) == len(rows):
                continue
            try:
                _rewrite_or_delete(d, kept)
            except requests.exceptions.HTTPError as e:
                return _http_error_response(e)
        if mode == "completed":
            # Also delete the completion docs — the rows they pointed at are gone.
            for c in completions:
                try:
                    internal_api.delete(f"{_STORAGE_PATH}/{c['id']}")
                    cleared_completions += 1
                except requests.exceptions.HTTPError:
                    pass

    logging.info(
        "POST /api/shifts/clear by=%s mode=%s snapshot_id=%s rows=%d completions=%d",
        g.user.get("email"), mode, snap_id, cleared_rows, cleared_completions,
    )
    return jsonify({
        "data": {
            "mode": mode,
            "cleared_rows": cleared_rows,
            "cleared_completions": cleared_completions,
        }
    })


@app.route("/api/bloom/_probe", methods=["GET"])
def api_bloom_probe():
    """Admin debug: proxy arbitrary Internal API GETs to discover endpoint shapes."""
    denied = _require_admin()
    if denied is not None:
        return denied
    path = request.args.get("path") or "/api/jobs"
    params = {}
    for k, v in request.args.items():
        if k == "path":
            continue
        params[k] = v
    try:
        resp = internal_api.get(path, params=params or None)
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e, source="bloom api")
    return jsonify({"data": resp})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    # threaded=True so a slow upstream call (e.g. the Bloom project-name
    # pagination) doesn't block a concurrent publish request — without it,
    # the browser's publish fetch queues behind the slow request and times
    # out as "Failed to fetch".
    app.run(host="0.0.0.0", port=port, threaded=True)
