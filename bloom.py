"""Bloom (Internal Tool API) proxy for the QC shift feed.

Drives the assignments dashboard from `/api/responsegroups` filtered to
unreviewed status (default `N`). Groups response groups by `job_id`, counts
the unreviewed backlog per job, and fetches job names separately so each
row in the dashboard represents one job with an unreviewed RG count.

Response shape — one Row per *job that has at least one unreviewed RG*:

    {
      id, projectId, jobId, groupIds,
      priority, name,
      unreviewedCount, oldestSubmission,
      extras,
    }

Priority: rank by `unreviewedCount` descending (biggest backlog = 1).
"""

import logging
import time

import internal_api

# Default response-group status treated as "unreviewed". FA admin uses a
# single-letter code; flip via the ?status= query param if it turns out to
# be something else (e.g. 'New', or multiple codes comma-separated).
DEFAULT_STATUS = "N"
# Bloom silently caps per_page at 100 regardless of request.
PAGE_SIZE = 100
# Cap on response-group pagination. 5000 RGs is plenty of headroom for today's
# ~3.7k unreviewed backlog; bump if that grows.
MAX_RG_PAGES = 50
_CACHE = {"fetched_at": 0.0, "status": None, "rows": []}
_CACHE_TTL_SECONDS = 60
# Project-name cache: {project_id: name}. Shares the 60s TTL pattern.
_PROJECT_NAME_CACHE = {"fetched_at": 0.0, "names": {}}


def _g(d, *keys):
    """Return the first non-empty value from `d` among `keys`, else ''."""
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return ""


def _fetch_response_groups(status):
    """Paginate /api/responsegroups filtered to the given status. Returns list."""
    # Accept comma-separated status codes ("N,P") — Bloom takes them as-is and
    # falls back to per-code requests if the API only honors one at a time.
    statuses = [s.strip() for s in (status or "").split(",") if s.strip()]
    if not statuses:
        statuses = [DEFAULT_STATUS]

    all_rgs = []
    for s in statuses:
        for page in range(1, MAX_RG_PAGES + 1):
            params = {"page": page, "per_page": PAGE_SIZE, "status": s}
            resp = internal_api.get("/api/responsegroups", params=params)
            batch = resp.get("data", []) if isinstance(resp, dict) else []
            if not batch:
                break
            all_rgs.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
    return all_rgs


def _group_by_job(rgs):
    """Collapse response-group records into one entry per job_id.

    Returns dict keyed on str(job_id):
      {
        "job_id": str, "project_id": str,
        "count": int, "group_ids": [str, ...],
        "oldest_submission": "YYYY-MM-DD..." or "",
      }
    """
    by_job = {}
    for rg in rgs:
        jid = rg.get("job_id")
        if jid in (None, ""):
            continue
        key = str(jid)
        entry = by_job.get(key)
        if entry is None:
            entry = {
                "job_id": key,
                "project_id": str(rg.get("project_id") or ""),
                "count": 0,
                "group_ids": [],
                "oldest_submission": "",
            }
            by_job[key] = entry
        entry["count"] += 1
        rg_id = rg.get("id")
        if rg_id not in (None, ""):
            entry["group_ids"].append(str(rg_id))
        submitted = _g(rg, "submission_date", "local_submission_date", "create_ts")
        if submitted:
            prev = entry["oldest_submission"]
            if not prev or str(submitted) < str(prev):
                entry["oldest_submission"] = str(submitted)
        # Fill project_id if it was empty on earlier RG but present here.
        if not entry["project_id"] and rg.get("project_id") not in (None, ""):
            entry["project_id"] = str(rg.get("project_id"))
    return by_job


def _row_from_entry(entry, priority, project_names=None):
    project_id = entry["project_id"]
    project_name = ""
    if project_names and project_id:
        project_name = project_names.get(project_id, "")
    return {
        "id": entry["job_id"],
        "projectId": project_id,
        "projectName": project_name,
        "jobId": entry["job_id"],
        "groupIds": entry["group_ids"],
        "priority": priority,
        "name": "",
        "unreviewedCount": entry["count"],
        "oldestSubmission": entry["oldest_submission"],
        "extras": {},
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
                "projectName": r.get("projectName") or "",
                "jidCount": 0,
                "oldestSubmission": "",
            }
            by_pid[pid] = entry
        entry["jidCount"] += 1
        if not entry["projectName"] and r.get("projectName"):
            entry["projectName"] = r["projectName"]
        submitted = r.get("oldestSubmission") or ""
        if submitted:
            prev = entry["oldestSubmission"]
            if not prev or str(submitted) < str(prev):
                entry["oldestSubmission"] = str(submitted)
    return sorted(by_pid.values(), key=lambda e: (-e["jidCount"], e["projectId"]))


def fetch_prioritized_jobs(status=DEFAULT_STATUS, use_cache=True):
    """Return Rows for every job that has ≥1 unreviewed response group.

    One Row per job, sorted by `unreviewedCount` descending (priority 1 = biggest
    backlog). A 60-second in-process cache keeps the Internal-API rate limit
    headroom comfortable.
    """
    now = time.time()
    if (
        use_cache
        and _CACHE["status"] == status
        and _CACHE["rows"]
        and (now - _CACHE["fetched_at"]) < _CACHE_TTL_SECONDS
    ):
        return _CACHE["rows"]

    rgs = _fetch_response_groups(status)
    by_job = _group_by_job(rgs)

    # NOTE: We intentionally do NOT hit /api/jobs — that endpoint's full-list
    # pagination takes 7+ minutes and the only thing it adds is job name +
    # filtering out a handful of inactive jobs (619 → 616 locally). Users
    # chose raw speed; names stay empty and counts land within ~0.5% of
    # Admin. Revisit if a lightweight job-lookup endpoint becomes available.

    # Sort: unreviewedCount desc, then oldest submission asc (older backlog wins ties),
    # then job_id asc for deterministic ordering.
    ordered = sorted(
        by_job.values(),
        key=lambda e: (-e["count"], e["oldest_submission"] or "9999", int(e["job_id"]) if e["job_id"].isdigit() else 0),
    )

    # Resolve project names via the bulk list endpoint (one request per 100
    # projects, typically 6–10 total). Failures are non-fatal — rows just
    # carry an empty projectName and the UI falls back to `Project {id}`.
    project_ids = {e["project_id"] for e in ordered if e["project_id"]}
    try:
        project_names = fetch_project_names(project_ids)
    except Exception:  # noqa: BLE001 — names are a nicety, never block the feed
        project_names = {}
    rows = [_row_from_entry(e, idx, project_names) for idx, e in enumerate(ordered, start=1)]

    _CACHE["fetched_at"] = now
    _CACHE["status"] = status
    _CACHE["rows"] = rows
    logging.info(
        "bloom.fetch_prioritized_jobs status=%s rgs=%d jobs=%d",
        status, len(rgs), len(rows),
    )
    return rows


def clear_cache():
    """Reset the in-process cache. Used by tests and the 'Force refresh' path."""
    _CACHE["fetched_at"] = 0.0
    _CACHE["status"] = None
    _CACHE["rows"] = []
    clear_project_name_cache()
