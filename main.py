import datetime
import json
import logging
import os
import pathlib
import re
import threading
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
    if request.path in ("/health", "/version"):
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


@app.route("/version")
def version():
    """Public — returns the git SHA the running instance was built from, so a
    deploy can be confirmed in one check (curl /version) instead of inferring it
    from behavior. Baked in at build time via GIT_SHA (cloudbuild.yaml); falls
    back to 'dev' locally."""
    sha = os.environ.get("GIT_SHA", "") or "dev"
    return jsonify({"sha": sha, "short": sha[:7] if sha != "dev" else "dev"})


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


def _require_admin_or_lead():
    """Return a Flask response if the caller is neither admin nor lead, else None."""
    if not roles.is_admin_or_lead(g.user.get("email", "")):
        return jsonify({"error": "admin or lead only"}), 403
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


def _validate_color(body):
    """Extract an optional hex color (#rgb / #rrggbb) from a payload. Returns None if absent."""
    if not isinstance(body, dict):
        return None
    raw = (body.get("color") or "").strip()
    if not raw:
        return None
    if re.fullmatch(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})", raw):
        return raw.lower()
    return None


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
    body = request.get_json(silent=True)
    name, email, err = _validate_person_body(body)
    if err:
        return err
    color = _validate_color(body)
    existing = {r["email"] for r in roles.list_reviewers()}
    if email in existing:
        return jsonify({"error": "reviewer with that email already exists"}), 409
    doc_id = roles.create_record("reviewer", name, email, color)
    logging.info(
        "POST /api/reviewers by=%s created reviewer=%s", g.user.get("email"), email
    )
    return jsonify({"data": {"id": doc_id, "name": name, "email": email, "color": color}}), 201


@app.route("/api/reviewers/<doc_id>", methods=["PUT"])
def api_reviewers_update(doc_id):
    denied = _require_admin()
    if denied is not None:
        return denied
    body = request.get_json(silent=True)
    name, email, err = _validate_person_body(body)
    if err:
        return err
    color = _validate_color(body)
    try:
        roles.update_record(doc_id, "reviewer", name, email, color)
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 500
        return jsonify({"error": f"storage api returned {status}"}), status
    logging.info(
        "PUT /api/reviewers/%s by=%s email=%s", doc_id, g.user.get("email"), email
    )
    return jsonify({"data": {"id": doc_id, "name": name, "email": email, "color": color}})


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


# ---------- API: leads ----------

@app.route("/api/leads", methods=["GET"])
def api_leads_list():
    return jsonify({"data": roles.list_leads()})


@app.route("/api/leads", methods=["POST"])
def api_leads_create():
    denied = _require_admin()
    if denied is not None:
        return denied
    body = request.get_json(silent=True) or {}
    name, email, err = _validate_person_body(body)
    if err:
        return err
    existing = {l["email"] for l in roles.list_leads()}
    if email in existing:
        return jsonify({"error": "lead with that email already exists"}), 409
    doc_id = roles.create_record("lead", name, email)
    logging.info("POST /api/leads by=%s created lead=%s", g.user.get("email"), email)
    return jsonify({"data": {"id": doc_id, "name": name, "email": email}}), 201


@app.route("/api/leads/<doc_id>", methods=["PUT"])
def api_leads_update(doc_id):
    denied = _require_admin()
    if denied is not None:
        return denied
    body = request.get_json(silent=True) or {}
    name, email, err = _validate_person_body(body)
    if err:
        return err
    try:
        roles.update_record(doc_id, "lead", name, email)
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 500
        return jsonify({"error": f"storage api returned {status}"}), status
    logging.info("PUT /api/leads/%s by=%s email=%s", doc_id, g.user.get("email"), email)
    return jsonify({"data": {"id": doc_id, "name": name, "email": email}})


@app.route("/api/leads/<doc_id>", methods=["DELETE"])
def api_leads_delete(doc_id):
    denied = _require_admin()
    if denied is not None:
        return denied
    try:
        roles.delete_record(doc_id)
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 500
        return jsonify({"error": f"storage api returned {status}"}), status
    logging.info("DELETE /api/leads/%s by=%s", doc_id, g.user.get("email"))
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
    denied = _require_admin_or_lead()
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
    denied = _require_admin_or_lead()
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


_SUB_AGES_CACHE: dict = {"data": {}, "fetched_at": 0.0, "loading": False}
_SUB_AGES_LOCK = threading.Lock()
_SUB_AGES_TTL = 600  # 10 minutes


def _refresh_sub_ages_bg():
    """Background: fetch oldest unreviewed submission for all aged jobs via responsegroups.

    Submissions are never older than 20 days, so per-job responsegroups calls are fast
    (0.15s each). Paced at 1 call/sec to stay under the 60 req/min rate limit.
    All aged jobs processed; results cached for 10 minutes.
    """
    with _SUB_AGES_LOCK:
        if _SUB_AGES_CACHE["loading"]:
            return
        _SUB_AGES_CACHE["loading"] = True

    try:
        rows = bloom.fetch_prioritized_jobs()
        aged = sorted(
            [r for r in rows if (r.get("extras") or {}).get("old_sub", 0) > 0],
            key=lambda r: int(r.get("priority") or 9999),
        )

        for row in aged:
            job_id = row.get("jobId") or row.get("id") or ""
            if not job_id:
                continue
            try:
                resp = internal_api.get(
                    "/api/responsegroups",
                    params={"job_id": job_id, "status": "N", "sort": "submission_date", "per_page": 1},
                )
                rg_rows = resp.get("data", []) if isinstance(resp, dict) else []
                if rg_rows:
                    sub_date = rg_rows[0].get("submission_date", "")
                    if sub_date:
                        parsed = datetime.datetime.strptime(sub_date, "%a, %d %b %Y %H:%M:%S %Z")
                        with _SUB_AGES_LOCK:
                            _SUB_AGES_CACHE["data"][str(job_id)] = parsed.strftime("%Y-%m-%d")
            except Exception as exc:
                logging.debug("submission-ages: job %s failed: %s", job_id, exc)
            time.sleep(1.1)  # ~54 calls/min — safely under 60 req/min limit

        with _SUB_AGES_LOCK:
            _SUB_AGES_CACHE["fetched_at"] = time.time()
        logging.info("submission-ages: cached %d aged jobs", len(_SUB_AGES_CACHE["data"]))
    except Exception as exc:
        logging.warning("submission-ages background refresh failed: %s", exc)
    finally:
        with _SUB_AGES_LOCK:
            _SUB_AGES_CACHE["loading"] = False


@app.route("/api/bloom/submission-ages", methods=["GET"])
def api_bloom_submission_ages():
    """Return oldest unreviewed submission date per job_id, served from cache.

    Returns: {data: {"<job_id>": "YYYY-MM-DD", ...}, loading: bool}
    """
    denied = _require_admin_or_lead()
    if denied is not None:
        return denied

    now = time.time()
    with _SUB_AGES_LOCK:
        fetched_at = _SUB_AGES_CACHE["fetched_at"]
        data = dict(_SUB_AGES_CACHE["data"])
        loading = _SUB_AGES_CACHE["loading"]

    if (now - fetched_at) > _SUB_AGES_TTL and not loading:
        t = threading.Thread(target=_refresh_sub_ages_bg, daemon=True, name="sub-ages-refresh")
        t.start()
        loading = True

    return jsonify({"data": data, "loading": loading})


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
    denied = _require_admin_or_lead()
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

    # Cross-reviewer dedup: if the same job somehow appears in multiple reviewers'
    # lists, keep it only for the first reviewer (alphabetical). This is a safety
    # net — the frontend already deduplicates, but belt-and-suspenders here.
    seen_job_keys: set = set()
    reviewer_emails = sorted(normalized.keys())
    for email in reviewer_emails:
        deduped = []
        for r in normalized[email]:
            jk = str(r.get("jobId") or r.get("id") or "")
            if not jk or jk not in seen_job_keys:
                deduped.append(r)
                if jk:
                    seen_job_keys.add(jk)
        normalized[email] = deduped

    published_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    published_by = g.user.get("email", "")

    # Merge-into-existing-snapshot: if an active shift is already live, add or
    # replace only the reviewers being published — everyone else keeps their rows.
    # Only create a brand-new snapshot when there is no active shift at all.
    try:
        existing_snap_id, existing_snap_data = _latest_snapshot()
    except requests.exceptions.HTTPError:
        existing_snap_id, existing_snap_data = None, None

    if existing_snap_id:
        # Single pass over the live shift's docs: delete the docs for reviewers
        # being replaced, and collect the job keys held by reviewers we are
        # KEEPING. Those retained jobs must not be handed to anyone in this
        # publish, or the same job would sit on two reviewers at once. The
        # assignment pool comes from the live Bloom feed, which doesn't know a
        # job is already assigned — so this server-side guard is what actually
        # prevents overlap, regardless of which compose options were used.
        try:
            all_shift_docs = roles.list_docs_by_kind("reviewer_shift")
        except requests.exceptions.HTTPError as e:
            return _http_error_response(e)
        retained_keys: set = set()
        for doc in all_shift_docs:
            doc_data = doc.get("data") or {}
            if doc_data.get("shift_snapshot_id") != existing_snap_id:
                continue
            if (doc_data.get("reviewer_email") or "").strip().lower() in normalized:
                _try_delete(doc.get("id"))
            else:
                for r in doc_data.get("rows") or []:
                    jk = str(r.get("jobId") or r.get("id") or "")
                    if jk:
                        retained_keys.add(jk)

        # Drop incoming rows that collide with a retained reviewer's jobs.
        if retained_keys:
            for email in reviewer_emails:
                normalized[email] = [
                    r for r in normalized[email]
                    if str(r.get("jobId") or r.get("id") or "") not in retained_keys
                ]

        # Write new reviewer_shift docs under the existing snapshot.
        snapshot_id = existing_snap_id
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
                    "batch_size": len(normalized[email]),
                }
                try:
                    r = internal_api.post(_STORAGE_PATH, json={"data": doc})
                except requests.exceptions.HTTPError as e:
                    for did in written:
                        _try_delete(did)
                    return _http_error_response(e)
                written.append((r.get("data") or {}).get("id"))

        # Update the snapshot's reviewer_emails to include the new reviewers.
        existing_emails = set(existing_snap_data.get("reviewer_emails") or [])
        merged_emails = sorted(existing_emails | set(reviewer_emails))
        updated_snap = {
            **existing_snap_data,
            "reviewer_emails": merged_emails,
            "last_updated_at": published_at,
            "last_updated_by": published_by,
        }
        try:
            internal_api.put(f"{_STORAGE_PATH}/{snapshot_id}", json={"data": updated_snap})
        except requests.exceptions.HTTPError:
            pass  # Non-fatal: reviewer_emails list is informational only

        roles.invalidate_doc_cache("shift_snapshot", "reviewer_shift")
        logging.info(
            "POST /api/shifts/publish (merge) by=%s snapshot_id=%s reviewers=%d",
            published_by, snapshot_id, len(reviewer_emails),
        )
        return jsonify({"data": {"id": snapshot_id, "published_at": published_at}}), 201

    # No active snapshot — create a fresh one.
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
                "batch_size": len(normalized[email]),
            }
            try:
                r = internal_api.post(_STORAGE_PATH, json={"data": doc})
            except requests.exceptions.HTTPError as e:
                for did in written:
                    _try_delete(did)
                _try_delete(snapshot_id)
                return _http_error_response(e)
            written.append((r.get("data") or {}).get("id"))

    roles.invalidate_doc_cache("shift_snapshot", "reviewer_shift")
    logging.info(
        "POST /api/shifts/publish by=%s snapshot_id=%s reviewers=%d",
        published_by, snapshot_id, len(reviewer_emails),
    )
    return jsonify({"data": {"id": snapshot_id, "published_at": published_at}}), 201


def _latest_snapshot(reviewer_shift_docs=None):
    """Helper — return (snapshot_id, snapshot_data) or (None, None).

    Skips snapshots where all reviewer_shift docs have zero rows (empty publish).
    Pass pre-fetched reviewer_shift_docs to avoid a duplicate storage scan when
    the caller already has them.
    """
    snaps = roles.list_docs_by_kind("shift_snapshot")
    if not snaps:
        return None, None

    if reviewer_shift_docs is None:
        reviewer_shift_docs = roles.list_docs_by_kind("reviewer_shift")

    snap_row_counts: dict = {}
    for d in reviewer_shift_docs:
        data = d.get("data") or {}
        sid = data.get("shift_snapshot_id")
        if sid:
            snap_row_counts[sid] = snap_row_counts.get(sid, 0) + len(data.get("rows") or [])

    for snap in snaps:
        data = snap.get("data") or {}
        row_count = snap_row_counts.get(snap.get("id"), 0)
        # Accept snapshots that have rows — whether or not reviewer_emails is set
        # (older snapshots published before merge-publish don't have that field).
        if row_count > 0:
            return snap.get("id"), data

    return None, None


def _rows_for_reviewer(snapshot_id, email, force=False):
    """Return the per-reviewer rows stored under the given snapshot.

    Assembles all chunk docs (see `_chunk_rows_for_storage`) for this
    reviewer in `part` order. Docs written before chunking (no `part`
    field) are treated as a single chunk at position 0. Pass force=True on
    the refill finish-check so a just-written refill part is seen — otherwise
    a stale warm-cache read can let a second completion refill again.
    """
    norm = (email or "").strip().lower()
    matches = []
    for doc in roles.list_docs_by_kind("reviewer_shift", force=force):
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
    return _dedup_rows(out)


def _dedup_rows(rows):
    """Drop rows that repeat a job key (first occurrence wins, order preserved).

    Belt-and-suspenders against a refill race writing the same batch twice: even
    if a duplicate part lands in storage, no reviewer or count ever sees a dupe
    because every read path runs the rows through here. The dedupe script still
    cleans the stored bloat, but correctness never depends on it."""
    seen, out = set(), []
    for r in rows or []:
        k = _job_key(r)
        if k and k in seen:
            continue
        if k:
            seen.add(k)
        out.append(r)
    return out


def _list_completions_for_snapshot(snapshot_id, reviewer_email=None, force=False):
    """Return completion docs for a snapshot, optionally filtered by reviewer.

    Pass ``force=True`` on correctness-critical paths (the finish check) to read
    authoritatively from Storage rather than the warm cache.
    """
    docs = roles.list_docs_by_kind("completion", force=force)
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
    done_by_jid = {_completion_job_key(c): c for c in completions if _completion_job_key(c)}

    # Overlay LIVE unreviewed counts from the prioritized feed so the list shows
    # reality, not the publish-time snapshot. A job that has dropped out of the
    # feed has no unreviewed responses left (fully reviewed) → count 0, which the
    # UI treats as already-done. Best-effort: if the feed is unavailable we keep
    # the stored counts rather than blanking the page.
    try:
        feed = bloom.fetch_prioritized_jobs()
        # Per job: reviewable count (massReview) and the raw New count, so we can
        # show both "what's left to review" and "what's stuck as auto-rejected".
        live_by_job = {
            str(j.get("jobId")): {
                "reviewable": int(j.get("unreviewedCount") or 0),
                "new": int((j.get("extras") or {}).get("newCount") or 0),
            }
            for j in feed
            if j.get("jobId")
        }
    except Exception:  # noqa: BLE001 — live overlay is best-effort
        live_by_job = None
    # Empty feed → treat as no data (don't auto-mark every job reviewed).
    if not live_by_job:
        live_by_job = None

    enriched = []
    for row in rows:
        completion = done_by_jid.get(_row_job_key(row))
        item = {
            **row,
            "completedAt": completion.get("completed_at") if completion else None,
        }
        # Only override jobs we can identify in the live feed by jobId; rows
        # without a jobId (legacy) keep their stored count untouched.
        jid = str(row.get("jobId") or "")
        if live_by_job is not None and jid:
            live = live_by_job.get(jid)
            reviewable = live["reviewable"] if live else 0
            new = live["new"] if live else 0
            item["unreviewedCount"] = reviewable
            # Responses left that aren't reviewable (auto-rejected for distance,
            # etc.) — the reviewer clears these on the Responses page, not here.
            item["autoRejected"] = max(0, new - reviewable)
        enriched.append(item)

    # NOTE: refilling happens ONLY on the completion POST finish-check, not here.
    # A GET-time self-heal used to also refill, but with two triggers a finished
    # reviewer got two batches at once (each missing the other's write → 40 rows,
    # 20 duplicated). One trigger = no race. With checkmark-only the POST trigger
    # is reliable, so this read stays a pure read.
    try:
        color = next(
            (r.get("color") for r in roles.list_reviewers() if r["email"] == email),
            None,
        )
    except Exception:  # noqa: BLE001 — color lookup is best-effort
        color = None
    return jsonify({
        "data": {
            "snapshot_id": snap_id,
            "published_at": snap_data.get("published_at"),
            "color": color,
            "rows": enriched,
        }
    })


def _job_key(row):
    """Stable per-job identity used to keep a job from being assigned twice."""
    return str((row or {}).get("jobId") or (row or {}).get("id") or "")


def _auto_refill_reviewer(snap_id, email, fallback_count):
    """Top up a finished reviewer's queue with a fresh fixed-size batch.

    The batch is the reviewer's original allotment (`batch_size`, stamped on
    their reviewer_shift docs at publish) — NOT their accumulated queue size, so
    finishing a queue of 20 yields 20 new jobs every cycle instead of doubling.
    `fallback_count` is used only for legacy snapshots published before
    batch_size was recorded.

    Pulls from the live prioritized feed, skipping any job already assigned to
    anyone in the current shift (preserving the no-overlap guarantee), and
    appends the new rows as an additional reviewer_shift chunk. Returns the
    rows added (compacted). Best-effort: returns [] on any failure or when the
    feed has nothing new left.
    """
    norm = (email or "").strip().lower()
    assigned_keys = set()
    max_part = -1
    batch_size = None
    try:
        # Authoritative read: compute assigned_keys and next_part from current
        # storage so a concurrent refill's just-written part is seen — otherwise
        # two refills pick the same next_part and the same jobs (duplicate batch).
        docs = roles.list_docs_by_kind("reviewer_shift", force=True)
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
            bs = data.get("batch_size")
            if bs:
                batch_size = bs if batch_size is None else min(batch_size, bs)

    # Refill the original allotment, not the (possibly grown) current queue.
    count = batch_size if batch_size else fallback_count
    if count <= 0:
        return []

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
        # Skip jobs handled by a third party (Cloud Factory) — they can't be
        # approved here until they come back, so never refill them.
        if bloom.is_excluded_client((r.get("extras") or {}).get("client")):
            continue
        # Skip jobs with no unreviewed work left — assigning one would just
        # auto-clear on the reviewer's screen and immediately re-trigger refill.
        if int(r.get("unreviewedCount") or 0) <= 0:
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
            "batch_size": count,
        }
        try:
            r = internal_api.post(_STORAGE_PATH, json={"data": doc})
            new_id = (r.get("data") or {}).get("id")
            if new_id:
                roles.cache_upsert_doc("reviewer_shift", {"id": new_id, "data": doc})
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


def _live_unreviewed_count(job_id, force=False):
    """Unreviewed-response count for a job from Bloom's prioritized feed.

    A fully-reviewed job drops out of the feed entirely (or shows 0), so:
      • >0  → still has unreviewed responses
      •  0  → reviewed / not in the feed
      • None → couldn't reach Bloom (caller should fail open, not block)
    """
    try:
        feed = bloom.fetch_prioritized_jobs(use_cache=not force)
    except Exception as exc:  # noqa: BLE001 — never block completion on a Bloom hiccup
        logging.warning("unreviewed-count lookup failed for job %s: %s", job_id, exc)
        return None
    target = str(job_id)
    for j in feed:
        if str(j.get("jobId") or j.get("id") or "") == target:
            return int(j.get("unreviewedCount") or 0)
    return 0


def _iso_week_key(dt):
    """Return an ISO year-week key like '2026-W26' for grouping tallies."""
    iso = dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _week_start_utc(dt):
    """Monday 00:00:00 UTC of the week containing dt."""
    monday = (dt - datetime.timedelta(days=dt.weekday())).date()
    return datetime.datetime(monday.year, monday.month, monday.day, tzinfo=datetime.timezone.utc)


def _record_review_event(email, completed_at_iso, responses=0):
    """Increment the reviewer's per-week leaderboard tally (one doc per
    reviewer+ISO-week). Tracks both jobs completed (`days`/`total`) and the
    number of responses cleared (`resp_days`/`resp_total`) so the leaderboard
    can show response volume, not just job count. Bounded growth (reviewers ×
    weeks) keeps us well under the namespace cap, and it survives shift clears
    because it isn't a completion doc."""
    try:
        dt = datetime.datetime.fromisoformat(completed_at_iso)
    except (ValueError, TypeError):
        dt = datetime.datetime.now(datetime.timezone.utc)
    week = _iso_week_key(dt)
    day = dt.date().isoformat()
    norm = (email or "").strip().lower()
    responses = max(0, int(responses or 0))

    def _find(force=False):
        for d in roles.list_docs_by_kind("review_tally", force=force):
            data = d.get("data") or {}
            if data.get("week") == week and (data.get("reviewer_email") or "").lower() == norm:
                return d
        return None

    existing = _find()
    # Before creating a fresh doc, confirm one didn't just get written by a
    # concurrent completion (stale warm cache) — avoids duplicate tally docs.
    if not existing:
        existing = _find(force=True)

    if existing:
        data = {**(existing.get("data") or {})}
        days = {**(data.get("days") or {})}
        days[day] = int(days.get(day, 0)) + 1
        resp_days = {**(data.get("resp_days") or {})}
        resp_days[day] = int(resp_days.get(day, 0)) + responses
        data["days"] = days
        data["total"] = int(data.get("total", 0)) + 1
        data["resp_days"] = resp_days
        data["resp_total"] = int(data.get("resp_total", 0)) + responses
        internal_api.put(f"{_STORAGE_PATH}/{existing['id']}", json={"data": data})
        roles.cache_upsert_doc("review_tally", {"id": existing["id"], "data": data})
    else:
        data = {
            "kind": "review_tally",
            "reviewer_email": norm,
            "week": week,
            "days": {day: 1},
            "total": 1,
            "resp_days": {day: responses},
            "resp_total": responses,
        }
        resp = internal_api.post(_STORAGE_PATH, json={"data": data})
        new_id = (resp.get("data") or {}).get("id")
        if new_id:
            roles.cache_upsert_doc("review_tally", {"id": new_id, "data": data})


@app.route("/api/shifts/leaderboard", methods=["GET"])
def api_shifts_leaderboard():
    """Weekly reviewer leaderboard: jobs completed per reviewer for the current
    ISO week, with a Mon–Sun daily breakdown. Visible to admins and leads."""
    denied = _require_admin_or_lead()
    if denied is not None:
        return denied

    now = datetime.datetime.now(datetime.timezone.utc)
    week = _iso_week_key(now)
    week_start = _week_start_utc(now)
    day_keys = [(week_start + datetime.timedelta(days=i)).date().isoformat() for i in range(7)]

    try:
        tallies = [
            d for d in roles.list_docs_by_kind("review_tally")
            if (d.get("data") or {}).get("week") == week
        ]
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e)

    try:
        roster = {
            r["email"].lower(): {"name": r.get("name") or "", "color": r.get("color")}
            for r in roles.list_reviewers()
        }
    except Exception:  # noqa: BLE001 — name/color lookup is best-effort
        roster = {}

    # Merge by reviewer: a race can leave more than one tally doc for the same
    # reviewer+week. Summing them on read means a reviewer always appears ONCE
    # with combined numbers, regardless of duplicate docs in storage.
    merged = {}
    for d in tallies:
        data = d.get("data") or {}
        email = (data.get("reviewer_email") or "").lower()
        if not email:
            continue
        days = data.get("days") or {}
        resp_days = data.get("resp_days") or {}
        m = merged.setdefault(email, {"total": 0, "responses": 0,
                                      "days": [0] * 7, "resp_days": [0] * 7})
        m["total"] += int(data.get("total", 0))
        m["responses"] += int(data.get("resp_total", 0))
        for i, k in enumerate(day_keys):
            m["days"][i] += int(days.get(k, 0))
            m["resp_days"][i] += int(resp_days.get(k, 0))

    reviewers = []
    for email, m in merged.items():
        info = roster.get(email) or {}
        reviewers.append({
            "email": email,
            "name": info.get("name") or email.split("@")[0],
            "color": info.get("color"),
            "total": m["total"],
            "days": m["days"],
            "responses": m["responses"],
            "resp_days": m["resp_days"],
        })
    reviewers.sort(key=lambda r: (-r["total"], r["name"]))

    totals_by_day = [sum(r["days"][i] for r in reviewers) for i in range(7)]
    return jsonify({"data": {
        "week": week,
        "week_start": week_start.date().isoformat(),
        "day_labels": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "reviewers": reviewers,
        "team_total": sum(r["total"] for r in reviewers),
        "team_responses": sum(r["responses"] for r in reviewers),
        "totals_by_day": totals_by_day,
        "best_day": max(range(7), key=lambda i: totals_by_day[i]) if any(totals_by_day) else None,
    }})


@app.route("/api/shifts/my/complete", methods=["POST"])
def api_shifts_my_complete():
    """Mark a row done for the signed-in reviewer. Idempotent."""
    body = request.get_json(silent=True) or {}
    job_id = str(body.get("job_id") or "").strip()
    if not job_id:
        return jsonify({"error": "job_id is required"}), 400
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
        if _completion_job_key(c) == job_id:
            return jsonify({"data": c})

    # Guard against marking a job done while it still has unreviewed responses —
    # that usually means the reviewer hasn't actually cleared it. The cached feed
    # can lag, so only block after confirming against a fresh pull. The reviewer
    # can override (e.g. when responses are unreviewable due to the FieldAgent
    # alt-picture bug) by re-submitting with override=true.
    if not body.get("override"):
        remaining = _live_unreviewed_count(job_id)
        if remaining:
            remaining = _live_unreviewed_count(job_id, force=True)
        if remaining:
            return jsonify({
                "error": (
                    f"This job still has {remaining} unreviewed "
                    f"response{'s' if remaining != 1 else ''} — finish reviewing it "
                    "before marking it done."
                ),
                "unreviewed": remaining,
            }), 409

    completed_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    doc = {
        "kind": "completion",
        "reviewer_email": email,
        "job_id": job_id,
        "shift_snapshot_id": snap_id,
        "completed_at": completed_at,
        "note": (body.get("note") or "").strip(),
        # True when the reviewer confirmed past the unreviewed-responses warning.
        "overridden": bool(body.get("override")),
    }
    try:
        resp = internal_api.post(_STORAGE_PATH, json={"data": doc})
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e)
    doc_id = (resp.get("data") or {}).get("id")
    # Reflect this write in the warm cache immediately so the finish check (and
    # any read-after-write) sees it without waiting for the next background scan.
    roles.cache_upsert_doc("completion", {"id": doc_id, "data": doc})
    # Tally this review into the reviewer's weekly leaderboard total. Stored
    # separately from completions (kind "review_tally") so clearing/republishing
    # a shift never erases the week's standings. Best-effort. `responses` is the
    # job's assigned response count (stored on the row) so the board can show
    # response volume, not just job count.
    try:
        responses = 0
        for r in (_rows_for_reviewer(snap_id, email) or []):
            if _row_job_key(r) == job_id:
                responses = int(r.get("unreviewedCount") or 0)
                break
        _record_review_event(email, completed_at, responses)
    except Exception as exc:  # noqa: BLE001 — leaderboard must not break completion
        logging.warning("review tally failed for %s: %s", email, exc)
    logging.info(
        "POST /api/shifts/my/complete by=%s job_id=%s snapshot_id=%s",
        email, job_id, snap_id,
    )
    # If this completion cleared the reviewer's whole queue, ping the admin so
    # they can hand out more work. Best-effort — never block the response.
    try:
        assigned = _rows_for_reviewer(snap_id, email) or []
        assigned_keys = {_row_job_key(r) for r in assigned}
        # Cheap pass against the warm cache. The completion we just wrote was
        # upserted above, and every prior completion was upserted on its own
        # request, so on a warm instance this already reflects the full set —
        # which is what fixes the fast-finisher miss (no more wipe-on-write).
        done = _list_completions_for_snapshot(snap_id, reviewer_email=email)
        done_keys = {_completion_job_key(c) for c in done}
        done_keys.add(job_id)
        if assigned_keys and assigned_keys <= done_keys:
            # Looks finished — confirm with authoritative reads before refilling.
            # Re-read ASSIGNED with force too: if a near-simultaneous completion
            # already triggered a refill, the fresh read includes that new batch
            # (not yet done) so assigned_keys is no longer a subset of done — and
            # we skip a second refill. That's what prevents duplicate batches.
            assigned = _rows_for_reviewer(snap_id, email, force=True) or assigned
            assigned_keys = {_row_job_key(r) for r in assigned}
            confirmed = _list_completions_for_snapshot(
                snap_id, reviewer_email=email, force=True
            )
            confirmed_keys = {_completion_job_key(c) for c in confirmed}
            confirmed_keys.add(job_id)
            if assigned_keys <= confirmed_keys:
                # Refill a fresh fixed-size batch (the original allotment), then
                # ping the admin. len(assigned) is only a fallback for legacy
                # snapshots that predate the stored batch_size.
                added = _auto_refill_reviewer(snap_id, email, len(assigned))
                _notify_reviewer_finished(email, len(assigned), len(added))
    except Exception as exc:  # noqa: BLE001 — refill/ping must not break completion
        logging.warning("finish-check failed for %s: %s", email, exc)
    return jsonify({"data": {"id": doc_id, **doc}}), 201


@app.route("/api/shifts/my/complete/<job_id>", methods=["DELETE"])
def api_shifts_my_uncomplete(job_id):
    """Un-complete a row (reviewer misclicked)."""
    email = (g.user.get("email") or "").strip().lower()
    try:
        snap_id, _ = _latest_snapshot()
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e)
    if not snap_id:
        return jsonify({"data": {"job_id": job_id}})
    try:
        existing = _list_completions_for_snapshot(snap_id, reviewer_email=email)
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e)
    for c in existing:
        if _completion_job_key(c) == str(job_id):
            try:
                internal_api.delete(f"{_STORAGE_PATH}/{c['id']}")
            except requests.exceptions.HTTPError as e:
                return _http_error_response(e)
            roles.cache_remove_doc("completion", c["id"])
            logging.info(
                "DELETE /api/shifts/my/complete/%s by=%s", job_id, email,
            )
            break
    return jsonify({"data": {"job_id": str(job_id)}})


@app.route("/api/shifts/completions", methods=["GET"])
def api_shifts_list_completions():
    """Admin: return all completion docs for the latest snapshot."""
    denied = _require_admin_or_lead()
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
            roles.cache_remove_doc("completion", c["id"])
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


def _row_job_key(row):
    """Per-job identity for completion tracking (jobId preferred over id)."""
    return str(row.get("jobId") or row.get("id") or "")


def _completion_job_key(c):
    """Extract the job key from a completion doc (job_id preferred, falls back to project_id for legacy docs)."""
    return str(c.get("job_id") or c.get("project_id") or "")


@app.route("/api/shifts/overview", methods=["GET"])
def api_shifts_overview():
    """Admin: live check-in view of the current shift.

    Returns per-reviewer totals so admins can see who's keeping up without
    fetching every row into the browser.
    """
    denied = _require_admin_or_lead()
    if denied is not None:
        return denied
    # Fetch reviewer_shift docs once and reuse for both snapshot selection and
    # overview rendering — avoids a duplicate full storage scan.
    try:
        all_reviewer_shift_docs = roles.list_docs_by_kind("reviewer_shift")
        snap_id, snap_data = _latest_snapshot(reviewer_shift_docs=all_reviewer_shift_docs)
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e)
    if not snap_id:
        return jsonify({"data": {"snapshot_id": None, "reviewers": []}})

    reviewer_docs = [
        d for d in all_reviewer_shift_docs
        if (d.get("data") or {}).get("shift_snapshot_id") == snap_id
    ]
    try:
        completions = _list_completions_for_snapshot(snap_id)
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e)

    try:
        roster = {
            r["email"].lower(): {"name": r.get("name") or "", "color": r.get("color")}
            for r in roles.list_reviewers()
        }
    except Exception:  # noqa: BLE001 — name lookup is best-effort
        roster = {}

    done_by_reviewer = {}  # email -> {job_key, ...}
    for c in completions:
        email = (c.get("reviewer_email") or "").lower()
        jkey = _completion_job_key(c)
        if not email or not jkey:
            continue
        done_by_reviewer.setdefault(email, set()).add(jkey)

    rows_by_email = {}
    for doc in reviewer_docs:
        data = doc.get("data") or {}
        email = (data.get("reviewer_email") or "").lower()
        rows_by_email.setdefault(email, []).extend(data.get("rows") or [])

    reviewers_out = []
    for email, rows in rows_by_email.items():
        rows = _dedup_rows(rows)  # never count a duplicated row (refill-race guard)
        total = len(rows)
        if total == 0:
            continue
        done_set = done_by_reviewer.get(email, set())
        # Done = checked off. The checkmark is the single source of truth shared
        # with My Tasks and the Leaderboard.
        completed = sum(1 for r in rows if _row_job_key(r) in done_set)
        priorities = [r.get("priority") for r in rows if isinstance(r.get("priority"), int)]
        reviewers_out.append({
            "email": email,
            "name": (roster.get(email) or {}).get("name", ""),
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
    denied = _require_admin_or_lead()
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
        roster = {
            r["email"].lower(): {"name": r.get("name") or "", "color": r.get("color")}
            for r in roles.list_reviewers()
        }
    except Exception:  # noqa: BLE001 — name lookup is best-effort
        roster = {}

    done_by_reviewer = {}
    for c in completions:
        email = (c.get("reviewer_email") or "").lower()
        jkey = _completion_job_key(c)
        if not email or not jkey:
            continue
        done_by_reviewer.setdefault(email, set()).add(jkey)

    # Live overlay (same as overview / My Tasks): a job is done when explicitly
    # completed OR its live unreviewed count is 0. Keeps this detailed view in
    # step with what reviewers actually see.
    try:
        feed = bloom.fetch_prioritized_jobs()
        live_by_job = {
            str(j.get("jobId")): int(j.get("unreviewedCount") or 0)
            for j in feed if j.get("jobId")
        }
    except Exception:  # noqa: BLE001 — overlay is best-effort
        live_by_job = None
    # An empty feed (transient upstream blip) must NOT mark every job done —
    # treat "no data" as no overlay rather than "everything reviewed".
    if not live_by_job:
        live_by_job = None

    rows_by_email = {}
    for doc in reviewer_docs:
        data = doc.get("data") or {}
        email = (data.get("reviewer_email") or "").lower()
        rows_by_email.setdefault(email, []).extend(data.get("rows") or [])

    jobs_by_reviewer = []
    for email, rows in rows_by_email.items():
        rows = _dedup_rows(rows)  # never list a duplicated row (refill-race guard)
        done_set = done_by_reviewer.get(email, set())
        jobs = []
        for r in rows:
            jid = str(r.get("jobId") or "")
            live = live_by_job.get(jid, 0) if (live_by_job is not None and jid) else None
            # Done = checked off only (live count is shown for context, not used
            # to mark done — keeps this view in step with the checkmark).
            completed = _row_job_key(r) in done_set
            jobs.append({
                "id": r.get("id", ""),
                "projectId": r.get("projectId", ""),
                "jobId": r.get("jobId", ""),
                "priority": r.get("priority"),
                "unreviewedCount": live if live is not None else r.get("unreviewedCount", 0),
                "name": r.get("name", ""),
                "completed": completed,
                "oldestSubmission": r.get("oldestSubmission", ""),
                "groupIds": r.get("groupIds", []),
            })
        info = roster.get(email) or {}
        jobs_by_reviewer.append({
            "email": email,
            "name": info.get("name", ""),
            "color": info.get("color"),
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

    Body: `{mode: "active" | "completed" | "all", reviewer_email?: string}`
      • active    — wipe only rows the reviewer has NOT marked done
      • completed — wipe only rows the reviewer HAS marked done (and their completion docs)
      • all       — wipe everything (rows + completions) for the current snapshot

    When `reviewer_email` is provided, the clear is scoped to just that reviewer
    and the shift stays live for everyone else (the snapshot is never deleted).
    """
    denied = _require_admin_or_lead()
    if denied is not None:
        return denied
    body = request.get_json(silent=True) or {}
    mode = body.get("mode")
    if mode not in ("active", "completed", "all", "reset"):
        return jsonify({"error": "mode must be 'active', 'completed', 'all', or 'reset'"}), 400
    reviewer_email = (body.get("reviewer_email") or "").strip().lower() or None

    # "reset" nukes every snapshot + reviewer_shift + completion across all time.
    if mode == "reset":
        cleared_rows = 0
        cleared_completions = 0
        cleared_snapshots = 0
        for doc in roles.list_docs_by_kind("reviewer_shift"):
            rows = (doc.get("data") or {}).get("rows") or []
            _try_delete(doc.get("id"))
            cleared_rows += len(rows)
        for doc in roles.list_docs_by_kind("completion"):
            _try_delete(doc.get("id"))
            cleared_completions += 1
        for doc in roles.list_docs_by_kind("shift_snapshot"):
            _try_delete(doc.get("id"))
            cleared_snapshots += 1
        roles.invalidate_doc_cache("shift_snapshot", "reviewer_shift", "completion")
        logging.info(
            "POST /api/shifts/clear mode=reset by=%s snapshots=%d rows=%d completions=%d",
            g.user.get("email"), cleared_snapshots, cleared_rows, cleared_completions,
        )
        return jsonify({"data": {"mode": "reset", "cleared_rows": cleared_rows, "cleared_completions": cleared_completions, "cleared_snapshots": cleared_snapshots}})

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
            and (
                reviewer_email is None
                or (d.get("data") or {}).get("reviewer_email", "").lower() == reviewer_email
            )
        ]
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e)
    try:
        completions = _list_completions_for_snapshot(snap_id, reviewer_email=reviewer_email)
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e)

    done_set = {
        ((c.get("reviewer_email") or "").lower(), _completion_job_key(c))
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
        # Only end the whole shift on a global clear. A per-reviewer clear must
        # leave the snapshot intact so the shift stays live for everyone else.
        if reviewer_email is None:
            _try_delete(snap_id)
    else:
        # Surgical modes rewrite each reviewer_shift doc to filter rows.
        keep_done = mode == "active"  # active: keep done rows, drop pending
        for d in reviewer_docs:
            data = d.get("data") or {}
            email = (data.get("reviewer_email") or "").lower()
            rows = data.get("rows") or []
            kept = []
            for r in rows:
                is_done = (email, _row_job_key(r)) in done_set
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

    # A global surgical clear can empty every reviewer's rows (e.g. "clear
    # completed" on a fully-finished shift, or "clear pending" before anyone
    # started). That leaves a snapshot with zero assignments — a zombie that
    # _latest_snapshot rejects, so the app shows "no active shift" instead of a
    # clean slate. End the shift in that case. (Per-reviewer clears never touch
    # the snapshot; "all" already deletes it above.)
    if mode != "all" and reviewer_email is None:
        try:
            remaining = [
                d for d in roles.list_docs_by_kind("reviewer_shift", force=True)
                if (d.get("data") or {}).get("shift_snapshot_id") == snap_id
                and (d.get("data") or {}).get("rows")
            ]
            if not remaining:
                _try_delete(snap_id)
        except requests.exceptions.HTTPError:
            pass  # best-effort cleanup; the zombie is harmless to a re-publish

    roles.invalidate_doc_cache("shift_snapshot", "reviewer_shift", "completion")
    logging.info(
        "POST /api/shifts/clear by=%s mode=%s reviewer=%s snapshot_id=%s rows=%d completions=%d",
        g.user.get("email"), mode, reviewer_email or "*", snap_id, cleared_rows, cleared_completions,
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
