"""Bloom (Internal Tool API) proxy for the QC shift feed.

Drives the assignments dashboard from `/api/prioritized-jobs`, which returns
jobs ranked by FA-web's algorithm (jicco, close date, submission age,
reimbursement, P&G store-walk, part-one, plus relative sub-count /
pending-ratio / days-remaining weighting). Each row represents one job with
unreviewed submissions, already prioritized by the API.

Response shape — one Row per job with unreviewed submissions:

    {
      id, jobId, projectId, projectName,
      priority, name,
      unreviewedCount, oldestSubmission,
      groupIds, extras,
    }
"""

import datetime
import logging
import time

import internal_api

# No status filtering needed — /api/prioritized-jobs only returns jobs with
# new submissions. Kept for backward compatibility.
DEFAULT_STATUS = None

# Clients whose jobs must NEVER be assigned to the QC team — they're handled by
# a third party (Cloud Factory) first and can't be approved here until they come
# back. Compared case-insensitively against a job's `client` email.
EXCLUDED_CLIENTS = {"joanna.riney@menasha.com"}


def is_excluded_client(client):
    """True when this job's client is handled by a third party (see above)."""
    return str(client or "").strip().lower() in EXCLUDED_CLIENTS
_CACHE = {"fetched_at": 0.0, "rows": []}
_CACHE_TTL_SECONDS = 60
# Project-name cache: {project_id: name}. Shares the 60s TTL pattern.
_PROJECT_NAME_CACHE = {"fetched_at": 0.0, "names": {}}
# Constants for project name pagination
PAGE_SIZE = 100
MAX_RG_PAGES = 50
# CF-denied-count cache: independent of the main 60s job cache and of any
# force-refresh on it. "Refresh" force-bypasses _CACHE on every click, and
# without its own cache this query would re-hit /api/responsegroups on every
# one of those clicks — on top of the main /api/prioritized-jobs call, all
# competing for the same 60 req/min budget. A few extra requests per refresh
# is fine; the same few requests repeated by every impatient re-click is what
# tips it into 429s, and internal_api's backoff on a 429 is up to 127s per
# request — which is exactly what "a refresh that never finishes" looks like.
# CF-denial data doesn't change fast enough to need finer granularity than a
# few minutes, so this cache is deliberately longer-lived than _CACHE.
_CF_DENIED_CACHE = {"fetched_at": 0.0, "min_date": None, "counts": {}}
_CF_DENIED_CACHE_TTL_SECONDS = 300
MAX_CF_DENIED_PAGES = 10


def _g(d, *keys):
    """Return the first non-empty value from `d` among `keys`, else ''."""
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return ""


def _fetch_prioritized_jobs_raw():
    """Fetch jobs from /api/prioritized-jobs (unpaginated, pre-prioritized).

    Returns list of job records already ranked by FA-web's algorithm.
    """
    resp = internal_api.get("/api/prioritized-jobs")
    return resp.get("data", []) if isinstance(resp, dict) else []


def _safe_int(value):
    """Coerce a feed value to int, or None when it's missing/blank/non-numeric.
    The feed sends some counts as "" (empty string), which int() chokes on."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _row_from_api(job, cf_denied_count=0):
    """Map a job from /api/prioritized-jobs to the Row shape the UI expects.

    /api/prioritized-jobs already includes:
      - id (job_id), name, priority, project_id
      - new (unreviewed count)
      - All other metadata (activeReviewers, subsPerDay, etc.)

    `cf_denied_count` is fetched separately (see `_fetch_cf_denied_counts`) but
    no longer surfaced in assignments — jobs with new==0 are filtered out
    entirely, so cf_denied responses (already auto-approved by FieldAgent)
    are not actionable work and don't reach the queue.
    """
    project_id = str(job.get("project_id") or "")
    project_name = ""  # Will be populated separately if needed
    # Use the REVIEWABLE count ("Mass Review"), not raw "New". "New" includes
    # responses the reviewer can't act on — e.g. auto-rejected for distance —
    # which would otherwise block completion and the finish ping forever. The
    # gap (new - massReview) is exactly those un-reviewable responses. Fall
    # back to "new" when massReview is missing/blank (some feed rows send "").
    #
    # Capped at "new": massReview can be LARGER than "new" when most of a
    # job's mass-review backlog is checked out to a third party (Cloud
    # Factory) and hasn't been released back into FieldAgent's queue yet —
    # e.g. massReview=100, new=1 means only 1 is actually sitting here
    # ready to review, not 100. Reviewers can never act on more than "new".
    base_unreviewed = (
        min(_safe_int(job.get("massReview")), _safe_int(job.get("new")) or 0)
        if _safe_int(job.get("massReview")) is not None
        else (_safe_int(job.get("new")) or 0)
    )
    return {
        "id": str(job.get("id") or ""),
        "projectId": project_id,
        "projectName": project_name,
        "jobId": str(job.get("id") or ""),
        "groupIds": [],  # Not provided by this API; can be fetched separately if needed
        "priority": int(job.get("priority") or 0),
        "name": str(job.get("name") or ""),
        "unreviewedCount": base_unreviewed + cf_denied_count,
        "oldestSubmission": "",
        "extras": {
            "old_sub": int((job.get("priority_details") or {}).get("old_sub") or 0),
            "startDate": str(job.get("startDate") or ""),
            # Deadline + backlog signals used by the Old Submissions triage view.
            "endDate": str(job.get("endDate") or ""),
            "pendingRatio": float((job.get("priority_details") or {}).get("pending_ratio") or 0),
            "numSubs": float((job.get("priority_details") or {}).get("num_subs") or 0),
            # Raw "New" count (incl. un-reviewable/auto-rejected) for reference.
            "newCount": _safe_int(job.get("new")) or 0,
            # Client owner email — used to scope to Storesight / Retail Pipeline jobs.
            "client": str(job.get("client") or ""),
            # Cloud-Factory-denied responses auto-approved before human re-review.
            "cfDeniedCount": cf_denied_count,
        },
    }


def fetch_project_names(project_ids):
    """Return {projectId: projectName} for the given ids, with a 60s cache.

    Best-effort: pages through `/api/projects` in bulk (per_page=100) — one
    request per 100 projects rather than one per id, to stay under the 60
    req/min rate limit. Any ids not resolved fall back to ''. Results are
    cached in-process.
    """
    ids = {str(pid) for pid in project_ids if pid not in (None, "")}
    if not ids:
        return {}
    now = time.time()
    cache = _PROJECT_NAME_CACHE
    fresh = (now - cache["fetched_at"]) < _CACHE_TTL_SECONDS
    if fresh and ids.issubset(cache["names"].keys()):
        return {pid: cache["names"].get(pid, "") for pid in ids}

    # Rebuild the whole map from a bulk paginated list — cheaper than per-id
    # requests once we need more than a handful.
    names = {}
    try:
        for page in range(1, MAX_RG_PAGES + 1):
            resp = internal_api.get(
                "/api/projects",
                params={"page": page, "per_page": PAGE_SIZE},
            )
            batch = resp.get("data", []) if isinstance(resp, dict) else []
            if not batch:
                break
            for item in batch:
                if not isinstance(item, dict):
                    continue
                pid = str(item.get("id") or item.get("project_id") or "")
                if not pid:
                    continue
                names[pid] = str(_g(item, "name", "project_name", "title") or "")
            if len(batch) < PAGE_SIZE:
                break
    except Exception:  # noqa: BLE001 — names are best-effort
        pass

    # Record misses as "" so the next call sees them as cached and doesn't
    # retrigger a full paginated sweep when a few projects aren't listed.
    for pid in ids:
        names.setdefault(pid, "")
    cache["names"] = names
    cache["fetched_at"] = now
    return {pid: names.get(pid, "") for pid in ids}


def clear_project_name_cache():
    _PROJECT_NAME_CACHE["fetched_at"] = 0.0
    _PROJECT_NAME_CACHE["names"] = {}


def project_summaries(rows=None):
    """Return one summary per unique projectId from the given rows (or cache).

    Shape: [{projectId, projectName, jidCount, oldestSubmission}], sorted by
    jidCount desc.
    """
    if rows is None:
        rows = _CACHE["rows"] or []
    by_pid = {}
    for r in rows:
        pid = r.get("projectId") or ""
        if not pid:
            continue
        entry = by_pid.get(pid)
        if entry is None:
            entry = {
                "projectId": pid,
                "projectName": r.get("projectName") or r.get("name") or "",
                "jidCount": 0,
                "oldestSubmission": "",
            }
            by_pid[pid] = entry
        entry["jidCount"] += 1
        if not entry["projectName"]:
            entry["projectName"] = r.get("projectName") or r.get("name") or ""
        submitted = r.get("oldestSubmission") or ""
        if submitted:
            prev = entry["oldestSubmission"]
            if not prev or str(submitted) < str(prev):
                entry["oldestSubmission"] = str(submitted)
    return sorted(by_pid.values(), key=lambda e: (-e["jidCount"], e["projectId"]))


def _earliest_start_date_iso(jobs):
    """Earliest job startDate (feed sends "MM/DD/YYYY") across `jobs`, as
    "YYYY-MM-DD" — used to scope the CF-denied query (see below) to jobs
    still active in the current feed instead of scanning all history.
    Returns None if no job has a parseable startDate.
    """
    earliest = None
    for job in jobs:
        raw = str(job.get("startDate") or "")
        try:
            dt = datetime.datetime.strptime(raw, "%m/%d/%Y")
        except ValueError:
            continue
        if earliest is None or dt < earliest:
            earliest = dt
    return earliest.strftime("%Y-%m-%d") if earliest else None


def _fetch_cf_denied_counts(min_submission_date):
    """Return {job_id_str: count} of responses Cloud Factory denied that
    FieldAgent's own automation auto-approved anyway (status "A") before a
    human ever re-reviewed them.

    These never show up in a job's "new" count — as far as Bloom is
    concerned the response is already approved — so a job can report 0
    unreviewed while genuinely wrong, CF-flagged work sits unassigned and
    invisible. Scoped to `min_submission_date` (the oldest still-active
    job's start date) so this only ever covers jobs currently in the feed,
    keeping it to a couple of paginated calls instead of scanning all
    history (unscoped, this endpoint has 48k+ matching rows going back
    years).

    Cached independently of the caller's own cache/force flag — see
    `_CF_DENIED_CACHE` — so repeated force-refreshes of the main job list
    don't repeatedly re-hit this endpoint too.
    """
    now = time.time()
    cache = _CF_DENIED_CACHE
    fresh = (now - cache["fetched_at"]) < _CF_DENIED_CACHE_TTL_SECONDS
    if fresh and cache["min_date"] == min_submission_date:
        return cache["counts"]

    counts = {}
    page = 1
    try:
        while page <= MAX_CF_DENIED_PAGES:
            resp = internal_api.get(
                "/api/responsegroups",
                params={
                    "tp_review_status": "R",
                    "status": "A",
                    "submission_date_from": min_submission_date,
                    "page": page,
                    "per_page": PAGE_SIZE,
                },
            )
            batch = resp.get("data", []) if isinstance(resp, dict) else []
            if not batch:
                break
            for rg in batch:
                jid = str(rg.get("job_id") or "")
                if jid:
                    counts[jid] = counts.get(jid, 0) + 1
            if len(batch) < PAGE_SIZE:
                break
            page += 1
    except Exception as exc:  # noqa: BLE001 — best-effort, never block the feed
        logging.warning("CF-denied count fetch failed: %s", exc)
        # Serve stale counts rather than none if we have them — a request
        # spike/429 shouldn't make previously-visible CF-denied jobs vanish.
        if cache["counts"]:
            return cache["counts"]
        return counts

    cache["fetched_at"] = now
    cache["min_date"] = min_submission_date
    cache["counts"] = counts
    return counts


def fetch_prioritized_jobs(status=DEFAULT_STATUS, use_cache=True, include_aged=False):
    """Return Rows for every job with unreviewed submissions, pre-prioritized by FA-web.

    Calls /api/prioritized-jobs which returns jobs ranked by jicco, close date,
    submission age, reimbursement, P&G store-walk, part-one, plus relative
    sub-count / pending-ratio / days-remaining weighting.

    A 60-second in-process cache keeps the Internal-API rate limit headroom
    comfortable. The `status` parameter is kept for backward compatibility but
    unused (the API only returns jobs with new submissions).

    When include_aged=True, includes jobs with old unreviewed submissions even if
    they have zero new responses. Used by the Old Submissions page.

    Excludes jobs from clients handled by a third party (Cloud Factory).
    """
    now = time.time()
    if use_cache and _CACHE["rows"] and (now - _CACHE["fetched_at"]) < _CACHE_TTL_SECONDS:
        return _CACHE["rows"]

    jobs = _fetch_prioritized_jobs_raw()

    min_date = _earliest_start_date_iso(jobs)
    cf_denied_counts = _fetch_cf_denied_counts(min_date) if min_date else {}

    # Defensive: skip malformed records with no job id — they can't be assigned
    # or completed, and would render as blank rows in the UI.
    # Skip jobs with no "new" responses in FieldAgent's queue unless include_aged is True.
    # Even if CF-denied responses exist, they're either already auto-approved or parked at a third
    # party (e.g. Cloud Factory) with nothing actionable here. Only include jobs
    # where reviewers have actual queue entries to work on. Uses _safe_int (not
    # bare int()) since the feed sends "" for some counts, which int() would raise on.
    rows = [
        _row_from_api(job, cf_denied_counts.get(str(job.get("id") or ""), 0))
        for job in jobs
        if isinstance(job, dict) and job.get("id") not in (None, "")
        and ((_safe_int(job.get("new")) or 0) > 0 or (include_aged and (_safe_int(job.get("old_sub")) or 0) > 0))
    ]

    # Skip project name fetching on cache misses to reduce rate limit pressure.
    # Project names are nice-to-have; the UI can fall back to "Project {id}" if needed.
    # Only fetch if we have explicit cache miss AND project names are empty.
    # This keeps us under the 60 req/min Internal API rate limit.

    _CACHE["fetched_at"] = now
    _CACHE["rows"] = rows
    logging.info(
        "bloom.fetch_prioritized_jobs jobs=%d",
        len(rows),
    )
    return rows


def clear_cache():
    """Reset the in-process cache. Used by tests and the 'Force refresh' path."""
    _CACHE["fetched_at"] = 0.0
    _CACHE["rows"] = []
    clear_project_name_cache()
