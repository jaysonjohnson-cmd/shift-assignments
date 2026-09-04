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

JWT_SECRET = os.environ.get("JWT_SIGNING_SECRET", "")
AUTH_SERVICE_URL = "https://auth-service.storesight.org"
LOCAL_DEV = os.environ.get("LOCAL_DEV") == "1"

TEAM_SCHEDULER_URL = (
    "http://localhost:8081" if LOCAL_DEV
    else "https://team-scheduler.storesight.org"
)


def _send_slack_notification(text):
    """Send a message to #todayistheday Slack channel."""
    channel = "GKVDB7HNG"  # #todayistheday

    try:
        internal_api.post("/api/slack/post", json={"channel": channel, "text": text})
        logging.info("Slack notification sent to #todayistheday")
    except Exception as e:
        logging.warning("Failed to send Slack notification: %s", e)


app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Job IDs to exclude from assignment (do not assign to reviewers)
EXCLUDED_JOB_IDS = {
    "1966569",  # Bayer CVS Audit - Cadillac Program (July 2026 - keep for backwards compat)
}

EXCLUDED_JOB_NAMES = {
    "Photos of House from Street",  # OSI
    "Bayer CVS Audit - Cadillac Program",  # Monthly recurring; excludes all month versions
    "Screener",  # Generic screener jobs - exclude all variants containing "Screener"
    "Talking Rain",  # WHITE GLOVE - Sparkling Water; JID/PID changes weekly
}


def _is_excluded_job_name(job_name: str) -> bool:
    """True if this job name matches any excluded pattern (substring, case-insensitive)."""
    job_name_lower = str(job_name or "").lower()
    # Normalize dashes/hyphens/en-dashes to hyphen for matching
    job_name_normalized = job_name_lower.replace("–", "-").replace("—", "-")
    # Check both exact matches and substring matches (case-insensitive for patterns)
    for excluded_name in EXCLUDED_JOB_NAMES:
        excluded_normalized = excluded_name.lower().replace("–", "-").replace("—", "-")
        if excluded_normalized in job_name_normalized:
            return True
    return False


def _is_excluded_job(row):
    """True if this job should never be assignable or visible in the composer."""
    if str(row.get("jobId") or "") in EXCLUDED_JOB_IDS:
        return True
    job_name = str(row.get("name") or "")
    if _is_excluded_job_name(job_name):
        return True
    # Exclude video jobs, but allow non-video jobs through
    job_name_lower = job_name.lower()
    if "video" in job_name_lower and "non-video" not in job_name_lower:
        return True
    return False


def _dev_token_path():
    """Return the path to the dev token file."""
    return pathlib.Path.home() / ".storesight" / "dev-token"


# Paths that a Google service account may call with an OIDC token instead of a
# storesight_session cookie. Deliberately a fixed allowlist of one: this is for
# Cloud Scheduler triggering the shift auto-publish on a timer, and nothing here
# should widen to the rest of the API. Everything else still requires the cookie.
_OIDC_ALLOWED_PATHS = frozenset({"/api/shifts/auto-publish"})

# Service accounts permitted on those paths, comma-separated, and the audience
# their token must be minted for (this service's public URL). BOTH must be set
# or OIDC auth stays off entirely — that way a deployment that hasn't opted in
# can't grow an accidental open door, and an operator can't half-configure it
# into skipping audience verification.
_OIDC_SERVICE_ACCOUNTS = frozenset(
    e.strip().lower()
    for e in (os.environ.get("SCHEDULER_SERVICE_ACCOUNTS") or "").split(",")
    if e.strip()
)
_OIDC_AUDIENCE = (os.environ.get("SCHEDULER_OIDC_AUDIENCE") or "").strip()


def _oidc_service_account_identity():
    """Identity for a valid Cloud Scheduler OIDC bearer token, else None.

    Returns None for anything unverified — a missing/!Bearer header, a token that
    fails Google's signature/expiry/audience checks, or a verified token whose
    service account isn't on the allowlist. Callers must treat None as
    unauthenticated and fall through to the normal cookie flow.
    """
    if not _OIDC_SERVICE_ACCOUNTS or not _OIDC_AUDIENCE:
        return None
    header = request.headers.get("Authorization") or ""
    if not header.startswith("Bearer "):
        return None
    token = header[len("Bearer "):].strip()
    if not token:
        return None
    try:
        from google.auth.transport import requests as ga_requests
        from google.oauth2 import id_token as ga_id_token

        # Verifies signature, expiry and issuer. The audience is the URL the
        # scheduler was configured with, which must match this service's own.
        claims = ga_id_token.verify_oauth2_token(
            token, ga_requests.Request(), audience=_OIDC_AUDIENCE
        )
    except Exception as exc:  # noqa: BLE001 — any failure is just "not authed"
        logging.warning("OIDC token rejected: %s", exc)
        return None
    if claims.get("iss") not in ("https://accounts.google.com", "accounts.google.com"):
        return None
    email = (claims.get("email") or "").strip().lower()
    if not email or email not in _OIDC_SERVICE_ACCOUNTS:
        logging.warning("OIDC token for non-allowlisted account: %s", email or "<none>")
        return None
    if not claims.get("email_verified"):
        return None
    return {"email": email, "name": "auto-scheduler"}



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

    # Cloud Scheduler calls the auto-publish path with an OIDC token rather than
    # a browser cookie. Checked only for _OIDC_ALLOWED_PATHS, and only when
    # SCHEDULER_SERVICE_ACCOUNTS is configured; every other request falls
    # straight through to the cookie flow below, unchanged.
    if request.path in _OIDC_ALLOWED_PATHS:
        machine = _oidc_service_account_identity()
        if machine is not None:
            g.user = machine
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


# ---------- API: Auto-publish shifts ----------

def _get_todays_scheduled_reviewers(shift_time=None):
    """Fetch reviewers scheduled to work today from Team Scheduler.

    Args:
        shift_time: Optional shift time filter (e.g., "8:30 AM", "1:00 PM").
                   If provided, only returns reviewers with shifts matching that time.

    Returns a list of reviewer emails who have shifts today (optionally filtered by time).
    Falls back to all active reviewers if Team Scheduler is unavailable or time doesn't match.
    """
    try:
        import datetime as dt

        # Get today's date in YYYY-MM-DD format (Monday of the week)
        today = dt.date.today()
        monday = today - dt.timedelta(days=today.weekday())
        week_key = monday.isoformat()

        # Fetch this week's schedule from Team Scheduler
        url = f"{TEAM_SCHEDULER_URL}/api/week/{week_key}?team=default"
        headers = {}

        # In LOCAL_DEV, read dev token from file and pass as Bearer token
        if LOCAL_DEV:
            try:
                import pathlib
                token_file = pathlib.Path.home() / ".storesight" / "dev-token"
                dev_token = token_file.read_text().strip()
                headers = {"Authorization": f"Bearer {dev_token}"}
            except Exception:
                pass  # Continue without auth if token unavailable
        else:
            # Production: pass session cookie
            token = request.cookies.get("storesight_session")
            if token:
                headers = {"Cookie": f"storesight_session={token}"}

        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()

        week_data = resp.json()
        members = week_data.get("members", [])
        schedule = week_data.get("schedule", {})
        shift_types = week_data.get("shiftTypes", [])

        # Find which members have shifts today (optionally filtered by shift time)
        day_of_week = today.weekday()  # 0=Mon, 6=Sun
        day_key = f"day{day_of_week}"

        scheduled_emails = []
        for member in members:
            member_id = member.get("id")
            if member_id in schedule:
                shifts = schedule[member_id].get(day_key, [])
                if shifts:  # Has a shift today
                    # If shift_time filter provided, check if any shift matches
                    if shift_time:
                        matched = False
                        for shift in shifts:
                            shift_id = shift.get("shift")
                            if shift_id:
                                # Find shift type and check its label
                                shift_type = next(
                                    (st for st in shift_types if st.get("id") == shift_id),
                                    None
                                )
                                if shift_type and shift_type.get("label") == shift_time:
                                    matched = True
                                    break
                        if not matched:
                            continue

                    email = member.get("email", "").strip().lower()
                    if email:
                        scheduled_emails.append(email)

        return scheduled_emails if scheduled_emails else None

    except Exception as e:
        logging.warning("Failed to get today's scheduled reviewers: %s", e)
        return None


def _distribute_evenly(rows, reviewers):
    """Distribute rows evenly across reviewers.

    Returns {reviewer_email: [rows]} with jobs split as evenly as possible.
    Jobs are distributed round-robin to spread load fairly.
    """
    if not reviewers or not rows:
        return {r: [] for r in reviewers}

    assignments = {r: [] for r in reviewers}
    for i, row in enumerate(rows):
        reviewer = reviewers[i % len(reviewers)]
        assignments[reviewer].append(row)

    return assignments


@app.route("/api/shifts/auto-publish", methods=["POST"])
def api_shifts_auto_publish():
    """Automatically create and publish a shift.

    Fetches jobs from Bloom, distributes to today's scheduled reviewers from
    Team Scheduler, and publishes the shift. Can be called on a schedule via
    Cloud Scheduler.

    Query parameters:
        shift_time: Optional shift time filter (e.g., "8:30 AM", "1:00 PM").
                   Only assigns to reviewers with shifts at that time.

    Authenticated either by a signed-in user's storesight_session cookie or, for
    the Cloud Scheduler timer, by an allowlisted service account's OIDC token
    (see _oidc_service_account_identity). Any signed-in user may trigger it —
    it only distributes work to reviewers who are already on the roster.
    """
    # Auth is required by the @app.before_request middleware
    try:
        if not _get_auto_publish_enabled():
            logging.info("Auto-publish skipped: disabled in settings")
            return jsonify({"skipped": True, "reason": "auto-publish is disabled"}), 200

        # Get shift_time filter from query params
        shift_time = request.args.get("shift_time") or None

        # Fetch jobs from Bloom
        rows = bloom.fetch_prioritized_jobs(status=bloom.DEFAULT_STATUS)
        if not rows:
            return jsonify({"error": "no jobs available to publish"}), 400

        # Get today's scheduled reviewers from Team Scheduler (optionally filtered by shift time)
        scheduled = _get_todays_scheduled_reviewers(shift_time=shift_time)
        if scheduled:
            assigned_reviewers = scheduled
            time_label = f" at {shift_time}" if shift_time else ""
            logging.info("Auto-publish: using %d reviewers scheduled today%s", len(assigned_reviewers), time_label)
        else:
            # Fallback: use all active reviewers if Team Scheduler unavailable
            assigned_reviewers = [r["email"] for r in roles.list_reviewers()]
            time_label = f" at {shift_time}" if shift_time else ""
            logging.info("Auto-publish: Team Scheduler unavailable, using all %d active reviewers%s", len(assigned_reviewers), time_label)

        if not assigned_reviewers:
            return jsonify({"error": "no reviewers scheduled today"}), 400

        # Distribute evenly to scheduled reviewers
        assignments = _distribute_evenly(rows, assigned_reviewers)

        # Publish using the existing publish logic
        # Simulate the request body
        normalized = {}
        for email, job_rows in assignments.items():
            key = (email or "").strip().lower()
            if not key:
                continue
            valid_rows = []
            for r in job_rows:
                if _is_excluded_job(r):
                    continue
                unreviewable = int(r.get("unreviewedCount") or 0)
                total_new = int((r.get("extras") or {}).get("newCount") or 0)
                auto_rejected = max(0, total_new - unreviewable)
                total_responses = unreviewable + auto_rejected
                if total_responses > 0:
                    valid_rows.append(_compact_row(r))
            if valid_rows:
                normalized[key] = valid_rows

        if not normalized:
            return jsonify({"error": "no valid jobs to assign"}), 400

        # Dedup across reviewers and cap at 20 jobs per reviewer
        MAX_JOBS_PER_REVIEWER = 20
        seen_job_keys = set()
        reviewer_emails = sorted(normalized.keys())
        for email in reviewer_emails:
            deduped = []
            for r in normalized[email]:
                jk = str(r.get("jobId") or r.get("id") or "")
                if not jk or jk not in seen_job_keys:
                    deduped.append(r)
                    if jk:
                        seen_job_keys.add(jk)
            # Cap at 20 jobs per reviewer
            normalized[email] = deduped[:MAX_JOBS_PER_REVIEWER]

        published_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        published_by = "auto-scheduler"

        try:
            existing_snap_id, existing_snap_data = _latest_snapshot()
        except requests.exceptions.HTTPError:
            existing_snap_id, existing_snap_data = None, None

        if existing_snap_id:
            try:
                all_shift_docs = roles.list_docs_by_kind("reviewer_shift")
            except requests.exceptions.HTTPError:
                all_shift_docs = []

            retained_keys = set()
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

            if retained_keys:
                for email in reviewer_emails:
                    normalized[email] = [
                        r for r in normalized[email]
                        if str(r.get("jobId") or r.get("id") or "") not in retained_keys
                    ]

        snapshot_data = {
            "kind": "shift_snapshot",
            "published_at": published_at,
            "published_by": published_by,
            "reviewer_emails": list(normalized.keys()),
        }

        snap_resp = internal_api.post(_STORAGE_PATH, json={"data": snapshot_data})
        snapshot_id = snap_resp["data"]["id"]

        # Write reviewer_shift docs
        for email, rows in normalized.items():
            chunks = _chunk_rows_for_storage(rows)
            for part_num, chunk in enumerate(chunks):
                doc = {
                    "kind": "reviewer_shift",
                    "shift_snapshot_id": snapshot_id,
                    "reviewer_email": email,
                    "part": part_num,
                    "total_parts": len(chunks),
                    "rows": chunk,
                }
                try:
                    internal_api.post(_STORAGE_PATH, json={"data": doc})
                except requests.exceptions.HTTPError as e:
                    roles.invalidate_doc_cache()
                    return _http_error_response(e)

        roles.invalidate_doc_cache()

        synced_count = sum(len(rows) for rows in normalized.values())
        logging.info(
            "POST /api/shifts/auto-publish published=%s assigned=%d",
            snapshot_id, synced_count,
        )

        # Send Slack notification
        shift_time_label = f" at {request.args.get('shift_time')}" if request.args.get("shift_time") else ""
        slack_msg = f"📋 *Auto-published shift{shift_time_label}* — {synced_count} job{'' if synced_count == 1 else 's'} assigned to {len(normalized)} reviewer{'' if len(normalized) == 1 else 's'}"
        _send_slack_notification(slack_msg)

        return jsonify({
            "snapshot_id": snapshot_id,
            "published_at": published_at,
            "assigned_jobs": synced_count,
            "reviewer_count": len(normalized),
        })

    except requests.exceptions.HTTPError as e:
        return _http_error_response(e)
    except Exception as e:
        logging.error("Auto-publish failed: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/shifts/auto-publish/settings", methods=["GET"])
def api_auto_publish_settings_get():
    return jsonify({"data": {"enabled": _get_auto_publish_enabled()}})


@app.route("/api/shifts/auto-publish/settings", methods=["POST"])
def api_auto_publish_settings_set():
    denied = _require_admin()
    if denied is not None:
        return denied
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("enabled"), bool):
        return jsonify({"error": "enabled (boolean) is required"}), 400
    enabled = body["enabled"]
    try:
        _set_auto_publish_enabled(enabled)
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e)
    logging.info(
        "POST /api/shifts/auto-publish/settings by=%s enabled=%s",
        g.user.get("email"), enabled,
    )
    return jsonify({"data": {"enabled": enabled}})


# ---------- API: Sync Team Scheduler ----------

@app.route("/api/sync/team-scheduler", methods=["POST"])
def api_sync_team_scheduler():
    denied = _require_admin()
    if denied is not None:
        return denied
    try:
        url = f"{TEAM_SCHEDULER_URL}/api/members/export?team=default"
        token = request.cookies.get("storesight_session")
        headers = {"Cookie": f"storesight_session={token}"} if token else {}
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()

        members = resp.json().get("members", [])
        existing = {r["email"] for r in roles.list_reviewers()}
        synced = 0
        for member in members:
            email = (member.get("email") or "").strip().lower()
            name = member.get("name", "").strip()
            if email and email not in existing:
                roles.create_record("reviewer", name, email)
                synced += 1
        logging.info("POST /api/sync/team-scheduler by=%s synced=%d", g.user.get("email"), synced)
        return jsonify({"synced": synced, "total": len(members)})
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 500
        return jsonify({"error": f"team scheduler api returned {status}"}), status
    except Exception as e:
        logging.error("Sync failed: %s", e)
        return jsonify({"error": str(e)}), 500


# ---------- API: Bloom feed + published shift snapshots ----------


_STORAGE_PATH = "/api/storage/qc-shift-assignments"


_AUTO_PUBLISH_KEY = "auto_publish_enabled"


def _find_tool_config_doc(key, force=False):
    """Return the tool_config doc for `key`, or None."""
    for doc in roles.list_docs_by_kind("tool_config", force=force):
        if (doc.get("data") or {}).get("key") == key:
            return doc
    return None


def _get_auto_publish_enabled(force=False):
    """True if scheduled auto-publish is enabled. Defaults to False."""
    doc = _find_tool_config_doc(_AUTO_PUBLISH_KEY, force=force)
    if doc is None:
        return False
    return bool((doc.get("data") or {}).get("value"))


def _set_auto_publish_enabled(enabled):
    """Persist the auto-publish toggle to Storage API."""
    data = {"kind": "tool_config", "key": _AUTO_PUBLISH_KEY, "value": bool(enabled)}
    existing = _find_tool_config_doc(_AUTO_PUBLISH_KEY, force=True)
    if existing:
        internal_api.put(f"{_STORAGE_PATH}/{existing['id']}", json={"data": data})
    else:
        internal_api.post(_STORAGE_PATH, json={"data": data})
    roles.invalidate_doc_cache("tool_config")


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
    include_aged = request.args.get("aged") == "1"
    if force:
        bloom.clear_cache()
    try:
        rows = bloom.fetch_prioritized_jobs(status=status, include_aged=include_aged)
    except requests.exceptions.HTTPError as e:
        return _http_error_response(e, source="bloom api")

    # Filter out: (1) excluded jobs, (2) jobs with zero reviewable responses
    filtered = []
    for r in rows:
        if _is_excluded_job(r):
            continue
        unreviewable = int(r.get("unreviewedCount") or 0)
        if unreviewable <= 0:
            continue
        filtered.append(r)

    logging.info(
        "GET /api/bloom/jobs by=%s status=%s count=%d (filtered from %d)",
        g.user.get("email"), status, len(filtered), len(rows),
    )
    return jsonify({"data": filtered})


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
    rows = [r for r in rows if not _is_excluded_job(r)]
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
                        # Bloom returns GMT (UTC) timestamps. Keep the full instant —
                        # truncating to a bare date here and re-anchoring it to
                        # midnight on the frontend was making "days old" drift by
                        # up to a day depending on what time it is when you look.
                        parsed = datetime.datetime.strptime(
                            sub_date, "%a, %d %b %Y %H:%M:%S %Z"
                        ).replace(tzinfo=datetime.timezone.utc)
                        with _SUB_AGES_LOCK:
                            _SUB_AGES_CACHE["data"][str(job_id)] = parsed.isoformat()
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


def _delete_docs_bg(doc_ids):
    """Delete a batch of Storage docs off the request path.

    Used for cleanup that's already reflected in the warm cache (so reads are
    correct immediately) and only needs to happen in Storage eventually —
    e.g. stale completions cleared on republish. Runs sequentially so it
    shares the 60 req/min Storage limit gracefully instead of bursting it.
    """
    for doc_id in doc_ids:
        _try_delete(doc_id)


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
    flags = body.get("flags") or {}

    normalized = {}
    for email, rows in assignments.items():
        key = (email or "").strip().lower()
        if not key:
            continue
        if not isinstance(rows, list):
            continue
        # Filter out excluded jobs and empty jobs (no responses to review).
        # Jobs with responses stuck in CF (0 new, > 0 massReview) are already filtered at the Bloom level.
        valid_rows = []
        filtered_jobs = []
        for r in rows:
            job_id = str(r.get("jobId") or r.get("id") or "")
            if job_id in EXCLUDED_JOB_IDS:
                filtered_jobs.append((job_id, "excluded_id"))
                continue
            job_name = str(r.get("name") or "")
            if _is_excluded_job_name(job_name):
                filtered_jobs.append((job_id, "excluded_name"))
                continue
            # Exclude video jobs, but allow non-video jobs through
            if "video" in job_name.lower() and "non-video" not in job_name.lower():
                filtered_jobs.append((job_id, "excluded_video"))
                continue
            unreviewable = int(r.get("unreviewedCount") or 0)
            # Only assign jobs with actual reviewable responses. Jobs with only
            # auto-rejected responses (from CF) have nothing for the reviewer to do.
            if unreviewable > 0:
                valid_rows.append(_compact_row(r))
            else:
                filtered_jobs.append((job_id, f"zero_reviewable(count={unreviewable})"))
        if filtered_jobs:
            logging.warning(
                "publish: filtered out %d jobs for %s (reasons: %s)",
                len(filtered_jobs), key, filtered_jobs[:5],
            )
        if valid_rows:
            logging.warning(
                "publish: assigned %d jobs to %s (after filtering %d)",
                len(valid_rows), key, len(filtered_jobs),
            )
            normalized[key] = valid_rows
        else:
            logging.warning(
                "publish: NO jobs assigned to %s after filtering (filtered %d with 0 work)",
                key, len(filtered_jobs),
            )

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
        # When adding to a published shift, merge new jobs with existing ones.
        # Collect existing jobs per reviewer to preserve them.
        # Only prevent assigning jobs to multiple reviewers (cross-reviewer dedup).
        try:
            all_shift_docs = roles.list_docs_by_kind("reviewer_shift")
        except requests.exceptions.HTTPError as e:
            return _http_error_response(e)
        existing_jobs_by_email: dict = {}
        other_reviewers_keys: set = set()
        for doc in all_shift_docs:
            doc_data = doc.get("data") or {}
            if doc_data.get("shift_snapshot_id") != existing_snap_id:
                continue
            reviewer_email = (doc_data.get("reviewer_email") or "").strip().lower()
            rows = doc_data.get("rows") or []
            if not existing_jobs_by_email.get(reviewer_email):
                existing_jobs_by_email[reviewer_email] = []
            existing_jobs_by_email[reviewer_email].extend(rows)

        # Build set of keys for ALL existing assignments (to prevent cross-reviewer and self-duplication)
        all_existing_keys: set = set()
        for email, jobs in existing_jobs_by_email.items():
            for r in jobs:
                jk = str(r.get("jobId") or r.get("id") or "")
                if jk:
                    all_existing_keys.add(jk)

        # Get all completed jobs so we can exclude them from the retained assignments
        # (prevents completed jobs from accumulating in the queue as we add more work).
        # IMPORTANT: only exclude "normally" completed jobs (not override-completed).
        # Override-completed jobs must stay in the queue to show as completed on My Tasks.
        completed_keys: set = set()
        try:
            all_completions = _list_completions_for_snapshot(existing_snap_id)
            completed_keys = {
                _completion_job_key(c) for c in all_completions
                if _completion_job_key(c) and not c.get("overridden")
            }
        except requests.exceptions.HTTPError:
            pass  # Best-effort; if completion lookup fails, keep all existing jobs

        # For reviewers in the new publish: keep incomplete existing + add truly NEW jobs
        for email in reviewer_emails:
            existing = existing_jobs_by_email.get(email, [])
            # Re-filter existing jobs: exclude excluded jobs, completed jobs, and jobs with zero reviewable work
            existing_filtered = [
                r for r in existing
                if not _is_excluded_job(r)
                and str(r.get("jobId") or r.get("id") or "") not in completed_keys
                and int(r.get("unreviewedCount") or 0) > 0
            ]
            # Filter new jobs to exclude anything already assigned (to any reviewer, including this one)
            new_jobs = [
                r for r in normalized[email]
                if str(r.get("jobId") or r.get("id") or "") not in all_existing_keys
            ]
            # Append new jobs to incomplete existing ones
            normalized[email] = existing_filtered + new_jobs

        # Write new reviewer_shift docs under the existing snapshot.
        snapshot_id = existing_snap_id
        # Clear ORPHANED completions only — ones whose job is no longer in the
        # reviewer's merged row set (e.g. dropped by the excluded-job re-filter
        # above). Since this path now merges new jobs into the reviewer's
        # existing ones instead of replacing them, a retained job's completion
        # must survive the publish — deleting it unconditionally (the old
        # behavior, from when publish fully replaced a reviewer's rows) made
        # already-finished jobs reappear as incomplete every time an admin
        # added more work to an active shift. Drop orphans from the warm cache
        # immediately (in-memory, no network call) so every read reflects it
        # right away, then delete the actual Storage docs in the background —
        # this can be dozens of individual DELETE calls sharing the 60 req/min
        # Storage limit, and doing that synchronously here was what made
        # publish take minutes when a reviewer had a long completion history.
        stale_completion_ids = []
        for email in reviewer_emails:
            retained_keys = {
                str(r.get("jobId") or r.get("id") or "") for r in normalized[email]
            }
            try:
                existing_completions = _list_completions_for_snapshot(
                    snapshot_id, reviewer_email=email
                )
                for c in existing_completions:
                    if _completion_job_key(c) in retained_keys:
                        continue  # still assigned — keep the completion
                    roles.cache_remove_doc("completion", c.get("id"))
                    if c.get("id"):
                        stale_completion_ids.append(c["id"])
            except requests.exceptions.HTTPError:
                pass  # Best-effort cleanup — don't block publish on completion lookup
        if stale_completion_ids:
            threading.Thread(
                target=_delete_docs_bg, args=(stale_completion_ids,),
                daemon=True, name="publish-completion-cleanup",
            ).start()
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
            "prioritization_flags": flags,
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
        # Send Slack notification
        job_count = sum(len(rows) for rows in normalized.values())
        reviewer_count = len(normalized)
        slack_msg = f"📋 *Shift assignments published* — {job_count} job{'' if job_count == 1 else 's'} assigned to {reviewer_count} reviewer{'' if reviewer_count == 1 else 's'}"
        _send_slack_notification(slack_msg)

        return jsonify({"data": {"id": snapshot_id, "published_at": published_at}}), 201

    # No active snapshot — create a fresh one.
    # Clear any old completions for all reviewers so they start with a clean slate.
    # (This prevents completions from the previous shift from applying to new job IDs.)
    for email in reviewer_emails:
        try:
            # Get completions from ANY snapshot (not limited to existing_snap_id).
            all_completions = roles.list_docs_by_kind("completion")
            for c in all_completions:
                c_data = c.get("data") or {}
                if (c_data.get("reviewer_email") or "").strip().lower() == email.strip().lower():
                    try:
                        internal_api.delete(f"{_STORAGE_PATH}/{c.get('id')}")
                    except requests.exceptions.HTTPError:
                        pass
                    roles.cache_remove_doc("completion", c.get("id"))
        except Exception:
            pass  # Best-effort cleanup — don't block publish on completion deletion

    index_doc = {
        "kind": "shift_snapshot",
        "published_at": published_at,
        "published_by": published_by,
        "reviewer_emails": reviewer_emails,
        "prioritization_flags": flags,
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
    # Send Slack notification (the merge-publish branch above sends its own).
    job_count = sum(len(rows) for rows in normalized.values())
    reviewer_count = len(normalized)
    slack_msg = f"📋 *Shift assignments published* — {job_count} job{'' if job_count == 1 else 's'} assigned to {reviewer_count} reviewer{'' if reviewer_count == 1 else 's'}"
    _send_slack_notification(slack_msg)

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


def _live_counts_by_job():
    """{jobId: {"reviewable", "new"}} from the live feed, or None if unavailable.

    None is meaningful and must be propagated: it means "no live data", and a
    caller has to fall back to the stored snapshot counts. Treating an empty
    feed as real data would mark every assigned job as fully reviewed.
    """
    try:
        feed = bloom.fetch_prioritized_jobs()
    except Exception:  # noqa: BLE001 — the live overlay is best-effort
        return None
    live = {
        str(j.get("jobId")): {
            "reviewable": int(j.get("unreviewedCount") or 0),
            "new": int((j.get("extras") or {}).get("newCount") or 0),
        }
        for j in feed
        if j.get("jobId")
    }
    # Empty feed → treat as no data (don't auto-mark every job reviewed).
    return live or None


def _is_unactionable_row(row, live_by_job):
    """True if this job still has responses but none the reviewer can action.

    Everything left on it was auto-rejected (distance, etc.), which is cleared
    on the Responses page rather than in My Tasks. /api/shifts/my hides these
    rows, so the completion finish-check MUST discount them the same way: a
    reviewer holding one otherwise never reaches is_complete, so their queue is
    never topped up and they sit idle with an apparently empty task list. Both
    callers share this predicate so the two views can't drift apart again.
    """
    if live_by_job is None:
        return False
    jid = str(row.get("jobId") or "")
    if not jid:
        return False
    live = live_by_job.get(jid)
    if live is None:
        return False
    return live["reviewable"] == 0 and live["new"] > 0


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
    # Per job: reviewable count (massReview) and the raw New count, so we can
    # show both "what's left to review" and "what's stuck as auto-rejected".
    live_by_job = _live_counts_by_job()

    enriched = []
    for row in rows:
        # Filter out excluded jobs (including video jobs) that shouldn't appear in My Tasks
        if _is_excluded_job(row):
            continue
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
            if live is not None:
                # Job is in the live feed — use current data.
                reviewable = live["reviewable"]
                new = live["new"]
                item["unreviewedCount"] = reviewable
                # Responses left that aren't reviewable (auto-rejected for distance,
                # etc.) — the reviewer clears these on the Responses page, not here.
                item["autoRejected"] = max(0, new - reviewable)
                # Skip jobs with zero reviewable responses (all auto-rejected).
                # These have no actionable work and shouldn't appear on My Tasks.
                if _is_unactionable_row(row, live_by_job):
                    continue
            elif completion:
                # Job is not in the live feed AND has been marked completed.
                # Treat as fully reviewed (0). Don't modify item — let it show as done.
                item["unreviewedCount"] = 0
                item["autoRejected"] = 0
            # else: Job is not in feed and not completed. Keep stored counts to
            # preserve newly assigned jobs or jobs temporarily absent from the feed.
        enriched.append(item)

    # Self-heal refill. The completion POST finish-check is the primary trigger,
    # but it only fires on the incomplete→complete *edge* — and when that edge is
    # missed the reviewer has nothing left to click, so nothing can ever re-trigger
    # it and they sit idle for the rest of the shift. Known ways to miss it: a job
    # that's hidden from this list (all responses auto-rejected) still counts as
    # assigned-and-not-done in the finish-check, so is_complete never goes true;
    # an excluded-title straggler leaves was_complete already true on the last
    # click; or the refill itself failed best-effort and returned nothing.
    #
    # An earlier GET-time refill was removed because running two triggers handed a
    # finished reviewer two batches at once (each missing the other's write). That
    # race is now held off by the storage-level refill_lock inside
    # _auto_refill_reviewer, which didn't exist then — the second caller sees the
    # lock and backs off. The cooldown covers the other direction: a polling page
    # re-attempting on every load when the pool is genuinely empty.
    pending = [it for it in enriched if not it.get("completedAt")]
    if rows and not pending and _self_heal_cooldown_ok(snap_id, email):
        logging.warning(
            "self-heal: %s has 0 pending of %d visible jobs; attempting refill",
            email, len(enriched),
        )
        try:
            added = _auto_refill_reviewer(snap_id, email, len(enriched) or len(rows))
        except Exception as exc:  # noqa: BLE001 — a read must never fail on refill
            logging.warning("self-heal refill failed for %s: %s", email, exc)
            added = []
        if added:
            logging.warning("self-heal: refilled %d jobs for %s", len(added), email)
            for r in added:
                item = {**r, "completedAt": None}
                jid = str(r.get("jobId") or "")
                live = live_by_job.get(jid) if (live_by_job is not None and jid) else None
                if live is not None:
                    item["unreviewedCount"] = live["reviewable"]
                    item["autoRejected"] = max(0, live["new"] - live["reviewable"])
                enriched.append(item)
        else:
            logging.warning("self-heal: no eligible jobs to refill for %s", email)
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


def _cleanup_orphaned_refill_locks(max_age_seconds=600):
    """Clean up refill_lock documents older than max_age_seconds (default 10 min).

    Defensive measure against orphaned locks accumulating if exceptions occur
    that somehow bypass lock cleanup. Runs best-effort and never blocks refill.
    """
    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        cutoff = now - datetime.timedelta(seconds=max_age_seconds)

        lock_docs = roles.list_docs_by_kind("refill_lock", force=True)
        for doc in lock_docs:
            data = doc.get("data") or {}
            locked_at_str = data.get("locked_at")
            if not locked_at_str:
                continue
            try:
                locked_at = datetime.datetime.fromisoformat(
                    locked_at_str.replace("Z", "+00:00")
                )
                if locked_at < cutoff:
                    # Lock is stale; delete it
                    doc_id = doc.get("id")
                    if doc_id:
                        _try_delete(doc_id)
                        logging.warning(
                            "Cleaned up orphaned refill_lock for %s (locked at %s)",
                            data.get("reviewer_email"),
                            locked_at_str,
                        )
            except (ValueError, TypeError):
                # Malformed timestamp; delete the broken lock
                doc_id = doc.get("id")
                if doc_id:
                    _try_delete(doc_id)
                    logging.warning("Cleaned up malformed refill_lock (id=%s)", doc_id)
    except Exception as exc:  # noqa: BLE001 — cleanup is best-effort
        logging.warning("Failed to clean up orphaned refill locks: %s", exc)


# Oldest Bloom feed a refill will build a batch from. Comfortably above the
# warmer's 45s interval so a warm cache always satisfies it, low enough that a
# stalled warmer forces a real fetch rather than assigning from a stale ranking.
_REFILL_MAX_FEED_AGE = 120

_SELF_HEAL_COOLDOWN_SECONDS = 120
_SELF_HEAL_MAX_TRACKED = 500
_self_heal_attempts = {}  # (snap_id, email) -> time.monotonic() of last attempt
_self_heal_lock = threading.Lock()


def _mark_refill_attempted(snap_id, email):
    """Stamp a refill attempt against the GET-time self-heal cooldown.

    Called from both refill triggers. The POST finish-check stamps too, so the
    page load that immediately follows a finish doesn't redo the same uncached
    Bloom fetch the POST just did.
    """
    key = (snap_id, (email or "").strip().lower())
    now = time.monotonic()
    with _self_heal_lock:
        # Keep the dict from growing without bound across a long-lived instance.
        if len(_self_heal_attempts) >= _SELF_HEAL_MAX_TRACKED:
            cutoff = now - _SELF_HEAL_COOLDOWN_SECONDS
            for k in [k for k, v in _self_heal_attempts.items() if v < cutoff]:
                del _self_heal_attempts[k]
        _self_heal_attempts[key] = now


def _self_heal_cooldown_ok(snap_id, email):
    """True if it's been long enough to retry a self-heal refill for this reviewer.

    Records the attempt as a side effect, so a caller that gets True should go
    ahead and try. In-memory and per-instance on purpose: the cross-instance
    guard against two refills running at once is the storage-level refill_lock
    inside _auto_refill_reviewer. All this does is stop a polling My Tasks page
    from re-fetching the uncached Bloom feed on every load when the pool is
    legitimately empty.
    """
    key = (snap_id, (email or "").strip().lower())
    now = time.monotonic()
    with _self_heal_lock:
        last = _self_heal_attempts.get(key)
        if last is not None and now - last < _SELF_HEAL_COOLDOWN_SECONDS:
            return False
    _mark_refill_attempted(snap_id, email)
    return True


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

    Reapplies the prioritization flags from the snapshot (prioritizeAged,
    prioritizeUrgency, etc.) so refills maintain consistent prioritization.

    Concurrent refill safety: uses a "refill_lock" marker in storage to prevent
    duplicate jobs when multiple refill requests run in parallel. Only one
    refill per reviewer per snapshot is allowed at a time.
    """
    # Defensive cleanup: remove any orphaned locks older than 10 minutes
    _cleanup_orphaned_refill_locks()

    norm = (email or "").strip().lower()
    lock_id = None
    logging.warning("auto-refill: starting for %s (snap=%s, fallback_count=%d)", email, snap_id, fallback_count)
    try:
        # Guard against concurrent refills: check if another refill is in progress.
        # If so, skip this one to avoid distributing the same jobs twice.
        try:
            lock_docs = roles.list_docs_by_kind("refill_lock", force=True)
            for doc in lock_docs:
                data = doc.get("data") or {}
                if (data.get("shift_snapshot_id") == snap_id and
                    (data.get("reviewer_email") or "").strip().lower() == norm):
                    logging.info("auto-refill skipped for %s (refill already in progress)", email)
                    return []  # Another refill is running; skip to avoid duplicates
        except Exception:
            pass  # Best-effort; continue without the lock if it fails

        # Write a lock marker to prevent concurrent refills
        lock_doc = {
            "kind": "refill_lock",
            "shift_snapshot_id": snap_id,
            "reviewer_email": norm,
            "locked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        try:
            lock_resp = internal_api.post(_STORAGE_PATH, json={"data": lock_doc})
            lock_id = (lock_resp.get("data") or {}).get("id")
        except Exception as exc:
            logging.warning("auto-refill: failed to create lock for %s: %s", email, exc)
            return []  # Can't proceed safely without lock

        touched_keys = set()
        max_part = -1
        batch_size = None
        prioritization_flags = {}

        # Read snapshot data to get prioritization flags for consistent refilling
        try:
            snaps = roles.list_docs_by_kind("shift_snapshot", force=True)
            for snap in snaps:
                if snap.get("id") == snap_id:
                    snap_data = snap.get("data") or {}
                    prioritization_flags = snap_data.get("prioritization_flags") or {}
                    break
        except Exception as exc:  # noqa: BLE001 — flags are best-effort
            logging.warning("auto-refill: failed to read snapshot flags for %s: %s", email, exc)

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
                    touched_keys.add(k)
            if (data.get("reviewer_email") or "").strip().lower() == norm:
                max_part = max(max_part, int(data.get("part") or 0))
                bs = data.get("batch_size")
                if bs:
                    batch_size = bs if batch_size is None else min(batch_size, bs)

        # A job is only off-limits while it's actively sitting in someone's queue —
        # once completed, drop it from the exclusion set so fresh unreviewed
        # responses that land on it later (this feed gets continuous new
        # submissions all day) are reachable again. Without this, every job ever
        # touched during the shift was excluded forever, so the "fresh" pool only
        # ever shrank — completed jobs that later racked up brand-new unreviewed
        # responses became permanently unassignable to anyone.
        try:
            completions = _list_completions_for_snapshot(snap_id, force=True)
        except Exception as exc:  # noqa: BLE001 — refill is best-effort
            logging.warning("auto-refill: failed to list completions for %s: %s", email, exc)
            completions = []
        completed_keys = {_completion_job_key(c) for c in completions if _completion_job_key(c)}
        assigned_keys = touched_keys - completed_keys
        logging.warning("auto-refill KEYS for %s: touched=%d, completed=%d, assigned=%d (keys to exclude from pool)",
                       email, len(touched_keys), len(completed_keys), len(assigned_keys))
        # Log sample keys to verify they match expected format
        if touched_keys or completed_keys or assigned_keys:
            logging.warning("auto-refill SAMPLE: touched_sample=%s completed_sample=%s assigned_sample=%s",
                           sorted(list(touched_keys))[:3], sorted(list(completed_keys))[:3], sorted(list(assigned_keys))[:3])

        # Refill the original allotment, not the (possibly grown) current queue.
        count = batch_size if batch_size else fallback_count
        if count <= 0:
            return []

        try:
            # Read the background-warmed cache rather than forcing a fresh fetch.
            # The upstream ranks ~500 jobs and takes 10-12s to answer, and this
            # runs on the reviewer's critical path (the GET self-heal), so a hard
            # bypass meant a 10-12 second page load. _REFILL_MAX_FEED_AGE bounds
            # how stale a pool we'll accept and refetches beyond that, so a dead
            # warmer degrades to the old behaviour instead of assigning from a
            # long-stale feed.
            #
            # The staleness this admits is a job whose last responses were
            # cleared in the last minute. Such a job is hidden from My Tasks by
            # the live overlay in /api/shifts/my and is discounted by the same
            # rule in the finish-check, so it costs the reviewer a slot in the
            # batch, not a stuck queue.
            pool = bloom.fetch_prioritized_jobs(max_age=_REFILL_MAX_FEED_AGE)
        except Exception as exc:  # noqa: BLE001 — refill is best-effort
            logging.warning("auto-refill: failed to fetch jobs for %s: %s", email, exc)
            return []

        # Reapply prioritization flags to sort pool consistently with initial assignment
        if prioritization_flags:
            prioritize_aged = prioritization_flags.get("prioritizeAged", False)
            prioritize_urgency = prioritization_flags.get("prioritizeUrgency", False)
            prioritize_new = prioritization_flags.get("prioritizeNew", False)

            if prioritize_aged:
                # Separate aged jobs (old_sub > 0) and prioritize them
                aged_jobs = []
                regular_jobs = []
                for r in pool:
                    if int((r.get("extras") or {}).get("old_sub") or 0) > 0:
                        aged_jobs.append(r)
                    else:
                        regular_jobs.append(r)
                pool = aged_jobs + regular_jobs
                logging.warning("auto-refill for %s: prioritizeAged=True, separated %d aged + %d regular jobs",
                               email, len(aged_jobs), len(regular_jobs))

            if prioritize_urgency:
                # Sort by urgency score (higher = more urgent)
                def urgency_score(row):
                    # Simplified urgency: days left (deadline) + days waiting (age)
                    # This is a simplified version; full version would match assign.ts logic
                    extras = row.get("extras") or {}
                    end_date_str = str(extras.get("endDate") or "")
                    oldest_sub_str = str(row.get("oldestSubmission") or "")

                    # Days until deadline (-100 if no/invalid endDate)
                    close_score = 15
                    if end_date_str:
                        try:
                            end_date = datetime.datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
                            days_left = (end_date.timestamp() - datetime.datetime.now(datetime.timezone.utc).timestamp()) / 86400
                            if days_left < 0:
                                close_score = 100
                            elif days_left <= 1.5:
                                close_score = 100
                            elif days_left <= 3:
                                close_score = 90
                            elif days_left <= 7:
                                close_score = 70
                            elif days_left <= 14:
                                close_score = 50
                            elif days_left <= 30:
                                close_score = 30
                            else:
                                close_score = 10
                        except (ValueError, AttributeError):
                            pass

                    # Response age urgency
                    wait_score = 0
                    if oldest_sub_str:
                        try:
                            old_date = datetime.datetime.fromisoformat(oldest_sub_str.replace("Z", "+00:00"))
                            days_old = (datetime.datetime.now(datetime.timezone.utc).timestamp() - old_date.timestamp()) / 86400
                            if days_old >= 30:
                                wait_score = 100
                            elif days_old >= 14:
                                wait_score = 80
                            elif days_old >= 7:
                                wait_score = 60
                            elif days_old >= 3:
                                wait_score = 40
                            elif days_old >= 1:
                                wait_score = 20
                        except (ValueError, AttributeError):
                            pass

                    return int(0.6 * close_score + 0.4 * wait_score)

                pool.sort(key=urgency_score, reverse=True)
                logging.warning("auto-refill for %s: prioritizeUrgency=True, sorted by urgency", email)

        fresh = []
        skipped_reasons = {"no_key": 0, "already_assigned": 0, "excluded_id": 0, "excluded_name": 0, "excluded_client": 0, "no_unreviewed": 0}
        for r in pool:
            k = _job_key(r)
            if not k or k in assigned_keys:
                if not k:
                    skipped_reasons["no_key"] += 1
                else:
                    skipped_reasons["already_assigned"] += 1
                continue
            # Skip excluded jobs (jobs with responses stuck in CF are already filtered at the Bloom level).
            if str(r.get("jobId") or "") in EXCLUDED_JOB_IDS:
                skipped_reasons["excluded_id"] += 1
                continue
            if _is_excluded_job_name(str(r.get("name") or "")):
                skipped_reasons["excluded_name"] += 1
                continue
            if "video" in str(r.get("name") or "").lower() and "non-video" not in str(r.get("name") or "").lower():
                skipped_reasons["excluded_client"] += 1
                continue
            # Skip jobs from excluded clients (e.g., Menasha handled by Cloud Factory).
            if bloom.is_excluded_client((r.get("extras") or {}).get("client")):
                skipped_reasons["excluded_client"] += 1
                continue
            # Skip jobs with no reviewable work (unreviewedCount == 0). These have
            # only auto-rejected responses, which must be cleared on the Responses page
            # (not in My Tasks), so assigning them to a reviewer would show them as
            # blocked when trying to mark done. Let the reviewer encounter them via the
            # completion block, not via auto-refill. (Checked first so we skip even if
            # auto-rejected > 0, preventing auto-refill from assigning jobs that will
            # immediately be hidden from My Tasks.)
            unreviewable = int(r.get("unreviewedCount") or 0)
            if unreviewable <= 0:
                skipped_reasons["no_unreviewed"] += 1
                continue
            total_new = int((r.get("extras") or {}).get("newCount") or 0)
            auto_rejected = max(0, total_new - unreviewable)
            total_responses = unreviewable + auto_rejected
            if total_responses <= 0:
                continue
            fresh.append(_compact_row(r))
            assigned_keys.add(k)  # guard against dupes within the same feed
            if len(fresh) >= count:
                break
        logging.warning("auto-refill for %s: pool=%d, assigned_keys=%d, skipped: %s, fresh=%d",
                       email, len(pool), len(assigned_keys), skipped_reasons, len(fresh))
        if not fresh:
            logging.warning("auto-refill: no new jobs left for %s (reasons: %s)", email, skipped_reasons)
            return []

        try:
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
        except Exception as exc:  # noqa: BLE001 — refill is best-effort
            logging.warning("auto-refill: failed during storage write for %s: %s", email, exc)
            return []
    finally:
        # Always clean up the refill lock marker to allow future refills,
        # even if an exception occurred or an early return was taken.
        if lock_id:
            _try_delete(lock_id)


def _notify_reviewer_finished(email, total_jobs, added_jobs, snap_id=None):
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
    logging.warning(
        "POST /api/shifts/my/complete by=%s job_id=%s override=%s snapshot_id=%s",
        email, job_id, doc.get("overridden"), snap_id,
    )
    # If this completion cleared the reviewer's whole queue, ping the admin so
    # they can hand out more work. Best-effort — never block the response.
    #
    # Always read authoritatively (force=True) here rather than trusting the
    # warm cache. cache_upsert_doc only mutates an already-populated cache —
    # a burst of reviewers finishing around the same time (many concurrent
    # writes to the "completion" kind) is exactly when a stale warm-cache
    # read is most likely, and this is the ONLY place a finished reviewer's
    # queue gets topped up. A false "not finished yet" here means they
    # silently sit idle for the rest of the shift with nothing to
    # re-trigger the check — there's no job left for them to complete.
    try:
        assigned = _rows_for_reviewer(snap_id, email, force=True) or []
        # Count only what the reviewer can actually see and act on in My Tasks —
        # the same two filters that endpoint applies. Excluded titles/video jobs,
        # and jobs whose remaining responses are all auto-rejected (invisible
        # there, so impossible to check off). Counting either as outstanding work
        # means is_complete never goes true and the queue is never topped up.
        live_by_job = _live_counts_by_job()
        assigned = [
            r for r in assigned
            if not _is_excluded_job(r) and not _is_unactionable_row(r, live_by_job)
        ]
        assigned_keys = {_row_job_key(r) for r in assigned}
        done = _list_completions_for_snapshot(snap_id, reviewer_email=email, force=True)

        # Include override-completed jobs (forced completions that bypassed unreviewed checks).
        # These count as "done" even though they may have unreviewed responses, because
        # the reviewer explicitly confirmed they should be marked complete.
        done_keys = {_completion_job_key(c) for c in done}
        override_keys = {_completion_job_key(c) for c in done if c.get("overridden")}
        all_done_keys = done_keys | override_keys  # Union of normal and override completions

        # The completion for this job was already written to storage above, so
        # done_keys includes it. To detect the transition from incomplete→complete,
        # we check the state before and after, excluding the current job from "before".
        # Must convert job_id to string since all_done_keys contains strings.
        done_keys_before = all_done_keys - {str(job_id)}
        done_keys_after = all_done_keys

        # was_complete = all assigned jobs were done before this completion
        # is_complete = all assigned jobs are done after this completion
        # Only trigger refill if: not all were done before, but all are done now
        was_complete = len(assigned_keys) > 0 and assigned_keys <= done_keys_before
        is_complete = len(assigned_keys) > 0 and assigned_keys <= done_keys_after

        logging.warning(
            "finish-check for %s: assigned=%d, done=%d, override=%d, was_complete=%s, is_complete=%s",
            email, len(assigned_keys), len(done_keys), len(override_keys), was_complete, is_complete,
        )
        # Debug: show actual keys for comparison
        logging.warning(
            "finish-check DEBUG: assigned_keys=%s, done_keys=%s, override_keys=%s, all_done_keys=%s",
            sorted(list(assigned_keys)), sorted(list(done_keys)), sorted(list(override_keys)), sorted(list(all_done_keys)),
        )
        # Debug: show raw completions to verify overridden flag
        logging.warning(
            "finish-check DEBUG raw completions: %s",
            [(c.get("job_id"), c.get("overridden")) for c in done][:5],
        )
        # Debug: show which jobs are still incomplete (if any)
        incomplete = assigned_keys - all_done_keys
        if incomplete:
            logging.warning(
                "finish-check: %s has %d incomplete jobs: %s",
                email, len(incomplete), list(incomplete)[:3],
            )
        if not was_complete and is_complete:
            # This job pushed us from incomplete→complete. Refill a fresh fixed-size batch.
            batch_size = len(assigned)
            logging.warning("finish-check: triggering auto-refill for %s (batch_size=%d, assigned=%d, done=%d, override=%d)",
                           email, batch_size, len(assigned), len(done_keys), len(override_keys))
            _mark_refill_attempted(snap_id, email)
            added = _auto_refill_reviewer(snap_id, email, batch_size)
            logging.warning("finish-check: auto-refill for %s returned %d new jobs", email, len(added))
            # Only send notification if refill actually found jobs. With high concurrency,
            # multiple requests may all call refill before any writes to storage, potentially
            # sending duplicate notifications. This is acceptable since notifications are
            # best-effort and duplicates won't break the system.
            if added:
                logging.warning("finish-check: notifying reviewer %s of %d new assignments", email, len(added))
                _notify_reviewer_finished(email, batch_size, len(added), snap_id)
            else:
                logging.warning("finish-check: auto-refill found no new jobs for %s (pool exhausted?)", email)
        else:
            logging.warning("finish-check: no refill triggered for %s (was_complete=%s, is_complete=%s, assigned=%d, done=%d)",
                        email, was_complete, is_complete, len(assigned_keys), len(all_done_keys))
    except Exception as exc:  # noqa: BLE001 — refill/ping must not break completion
        logging.error("finish-check failed for %s: %s", email, exc, exc_info=True)

    # Include refill debugging info in response so user can see why refill succeeded/failed
    refill_debug = {}
    if 'is_complete' in locals() and is_complete:
        refill_debug = {
            "refill_triggered": True,
            "refill_found_jobs": len(added) if 'added' in locals() else 0,
            "debug_info": f"assigned={len(assigned_keys) if 'assigned_keys' in locals() else 0}, touched_keys tracked"
        }

    response_data = {"id": doc_id, **doc}
    if refill_debug:
        response_data["_refill_debug"] = refill_debug
    return jsonify({"data": response_data}), 201


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
            # Hide jobs with no work left (0 live unreviewed), unless they're explicitly marked done.
            # This keeps the admin view in sync with what reviewers see.
            if live is not None and live <= 0 and not completed:
                continue
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


@app.route("/api/shifts/remove-job", methods=["POST"])
def api_remove_job():
    """Remove a specific job from a reviewer's assignment. Admin or lead only."""
    denied = _require_admin_or_lead()
    if denied is not None:
        return denied
    body = request.get_json(silent=True) or {}
    reviewer_email = (body.get("reviewer_email") or "").strip().lower()
    job_id = str(body.get("job_id") or "").strip()

    if not reviewer_email or not job_id:
        return jsonify({"error": "reviewer_email and job_id are required"}), 400

    try:
        all_shift_docs = roles.list_docs_by_kind("reviewer_shift")
        removed = False

        for doc in all_shift_docs:
            doc_data = doc.get("data") or {}
            if (doc_data.get("reviewer_email") or "").strip().lower() != reviewer_email:
                continue

            rows = doc_data.get("rows") or []
            new_rows = [r for r in rows if str(r.get("jobId") or r.get("id") or "") != job_id]

            if len(new_rows) < len(rows):
                # Job was removed
                doc_data["rows"] = new_rows
                doc_id = doc.get("id")
                try:
                    internal_api.put(f"{_STORAGE_PATH}/{doc_id}", json={"data": doc_data})
                    removed = True
                except requests.exceptions.HTTPError as e:
                    return _http_error_response(e)

        roles.invalidate_doc_cache()

        if removed:
            logging.info(
                "POST /api/shifts/remove-job by=%s job=%s reviewer=%s",
                g.user.get("email"), job_id, reviewer_email,
            )
            return jsonify({"ok": True})
        else:
            return jsonify({"error": "job not found for this reviewer"}), 404

    except requests.exceptions.HTTPError as e:
        return _http_error_response(e)
    except Exception as e:
        logging.error("Remove job failed: %s", e)
        return jsonify({"error": str(e)}), 500


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


# Start the Bloom cache warmer at import, not on first request. gunicorn loads
# main:app during container startup, so the ~11s upstream fetch is already in
# flight before the first user request arrives on a new revision. Without this
# the first reviewer to load a page after a deploy pays for it. Set
# BLOOM_WARMER=0 to opt out (the test suite does).
bloom._ensure_warmer_started()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    # threaded=True so a slow upstream call (e.g. the Bloom project-name
    # pagination) doesn't block a concurrent publish request — without it,
    # the browser's publish fetch queues behind the slow request and times
    # out as "Failed to fetch".
    app.run(host="0.0.0.0", port=port, threaded=True)
