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

import logging
import time

import internal_api

# No status filtering needed — /api/prioritized-jobs only returns jobs with
# new submissions. Kept for backward compatibility.
DEFAULT_STATUS = None
_CACHE = {"fetched_at": 0.0, "rows": []}
_CACHE_TTL_SECONDS = 60
# Project-name cache: {project_id: name}. Shares the 60s TTL pattern.
_PROJECT_NAME_CACHE = {"fetched_at": 0.0, "names": {}}
# Constants for project name pagination
PAGE_SIZE = 100
MAX_RG_PAGES = 50


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


def _row_from_api(job):
    """Map a job from /api/prioritized-jobs to the Row shape the UI expects.

    /api/prioritized-jobs already includes:
      - id (job_id), name, priority, project_id
      - new (unreviewed count)
      - All other metadata (activeReviewers, subsPerDay, etc.)
    """
    project_id = str(job.get("project_id") or "")
    project_name = ""  # Will be populated separately if needed
    return {
        "id": str(job.get("id") or ""),
        "projectId": project_id,
        "projectName": project_name,
        "jobId": str(job.get("id") or ""),
        "groupIds": [],  # Not provided by this API; can be fetched separately if needed
        "priority": int(job.get("priority") or 0),
        "name": str(job.get("name") or ""),
        "unreviewedCount": int(job.get("new") or 0),
        "oldestSubmission": "",
        "extras": {
            "old_sub": int((job.get("priority_details") or {}).get("old_sub") or 0),
            "startDate": str(job.get("startDate") or ""),
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


def fetch_prioritized_jobs(status=DEFAULT_STATUS, use_cache=True):
    """Return Rows for every job with unreviewed submissions, pre-prioritized by FA-web.

    Calls /api/prioritized-jobs which returns jobs ranked by jicco, close date,
    submission age, reimbursement, P&G store-walk, part-one, plus relative
    sub-count / pending-ratio / days-remaining weighting.

    A 60-second in-process cache keeps the Internal-API rate limit headroom
    comfortable. The `status` parameter is kept for backward compatibility but
    unused (the API only returns jobs with new submissions).
    """
    now = time.time()
    if use_cache and _CACHE["rows"] and (now - _CACHE["fetched_at"]) < _CACHE_TTL_SECONDS:
        return _CACHE["rows"]

    jobs = _fetch_prioritized_jobs_raw()
    # Defensive: skip malformed records with no job id — they can't be assigned
    # or completed, and would render as blank rows in the UI.
    rows = [
        _row_from_api(job)
        for job in jobs
        if isinstance(job, dict) and job.get("id") not in (None, "")
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
