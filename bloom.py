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
import os
import threading
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

# /api/prioritized-jobs is a single unpaginated request, but the upstream ranks
# ~500 jobs per call and takes 10-12 seconds to answer. Request count is not the
# problem here; latency is. Two things keep that off any user-facing request:
#
#   * a background warmer refreshes _CACHE on a timer, so the 60s TTL is
#     effectively never cold and nobody pays the 10-12s on a page load;
#   * _FETCH_LOCK single-flights the fetch, so N concurrent cache misses cost
#     one upstream call instead of N of them (a burst of reviewers finishing
#     around the same time used to mean a 10-12s call each).
_WARMER_INTERVAL_SECONDS = 45   # < _CACHE_TTL_SECONDS so the cache never expires
_FETCH_LOCK = threading.Lock()
_WARMER_STARTED = False
_WARMER_LOCK = threading.Lock()


def _warmer_loop():
    """Daemon thread: keep _CACHE warm so no request pays the upstream latency."""
    retry_delay = 15
    while True:
        try:
            fetch_prioritized_jobs(use_cache=False)
            retry_delay = 15
            time.sleep(_WARMER_INTERVAL_SECONDS)
        except Exception as exc:  # noqa: BLE001 — warmer must never die
            logging.warning("bloom warmer refresh failed: %s", exc)
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 120)


def _ensure_warmer_started():
    """Start the warmer once, on first use. Set BLOOM_WARMER=0 to opt out."""
    global _WARMER_STARTED
    if _WARMER_STARTED or os.environ.get("BLOOM_WARMER") == "0":
        return
    with _WARMER_LOCK:
        if _WARMER_STARTED:
            return
        threading.Thread(
            target=_warmer_loop, daemon=True, name="bloom-cache-warmer"
        ).start()
        _WARMER_STARTED = True
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

# "Old submission" threshold, in days. Matches what FA-web's own `old_sub`
# priority component claims to measure ("submissions older than 3 days"), so
# the Old Submissions view keeps meaning the same thing it always advertised.
AGED_SUBMISSION_DAYS = 3

_AGED_CACHE = {"fetched_at": 0.0, "min_days": None, "by_job": {}}
_AGED_CACHE_TTL_SECONDS = 300
MAX_AGED_PAGES = 10


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


def fetch_job_pending_count(job_id):
    """Live count of one job's pending submissions that are ours to review.

    Two small /api/responsegroups reads (~0.3s total) instead of refetching the
    whole prioritized feed (10-12s): everything still status N, minus what is
    checked out to a third party right now (tp_review_status N), which isn't
    reviewable here. Returns None if either read fails, so the caller can fall
    back to the feed.
    """
    try:
        pending = internal_api.get("/api/responsegroups", params={
            "job_id": job_id, "status": "N", "per_page": 1})
        at_third_party = internal_api.get("/api/responsegroups", params={
            "job_id": job_id, "status": "N", "tp_review_status": "N", "per_page": 1})
    except Exception as exc:  # noqa: BLE001 — caller falls back to the feed
        logging.warning("pending-count lookup failed for job %s: %s", job_id, exc)
        return None
    total = _safe_int(((pending or {}).get("pagination") or {}).get("total"))
    parked = _safe_int(((at_third_party or {}).get("pagination") or {}).get("total"))
    if total is None or parked is None:
        return None
    return max(0, total - parked)


def _safe_int(value):
    """Coerce a feed value to int, or None when it's missing/blank/non-numeric.
    The feed sends some counts as "" (empty string), which int() chokes on."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _row_from_api(job, cf_denied_count=0, aged=None):
    """Map a job from /api/prioritized-jobs to the Row shape the UI expects.

    /api/prioritized-jobs already includes:
      - id (job_id), name, priority, project_id
      - new (unreviewed count)
      - All other metadata (activeReviewers, subsPerDay, etc.)

    `cf_denied_count` is fetched separately (see `_fetch_cf_denied_counts`) but
    no longer surfaced in assignments — jobs with new==0 are filtered out
    entirely, so cf_denied responses (already auto-approved by FieldAgent)
    are not actionable work and don't reach the queue.

    `aged` is this job's entry from `fetch_aged_submissions()`, or None. It
    becomes `extras.agedCount` / `extras.oldestAged`, which is what every
    aged-work path keys off. `extras.old_sub` is still passed through, but
    only as a raw echo of the upstream field — see the note on it below.
    """
    project_id = str(job.get("project_id") or "")
    project_name = ""  # Will be populated separately if needed
    # Always use raw "New" — the number of unreviewed submissions FieldAgent
    # has sitting in the queue right now. This used to be capped at
    # min(massReview, new) on the theory that "Mass Review" was the
    # trustworthy reviewable subset and the gap was un-reviewable noise
    # (auto-rejected for distance, etc). Disproved 2026-09-23: cross-checked
    # against FA-web's own Prioritized Jobs admin page and found massReview
    # reading 0 on jobs where every single "new" response was confirmed
    # individually approvable there ("# to Approve" matched "new" exactly) —
    # including the #1 priority job in the whole queue (new=16, massReview=0).
    # That cap was silently zeroing 20 jobs / 55 real submissions out of the
    # entire tool. A job with responses waiting must never disappear, so
    # "new" — the ceiling on what a reviewer can ever act on — is the count.
    base_unreviewed = _safe_int(job.get("new")) or 0
    # Informational only — never subtracted from unreviewedCount above, so it
    # can no longer hide a job or shrink its count. "massReview" is officially
    # documented (FA-web's own OpenAPI spec) as just "Mass-review count, or
    # empty string when not a mass-review job" — it is NOT documented as a
    # reviewable/auto-rejected split, and using it that way is what caused the
    # 2026-09-23 bug where real work vanished from the tool. Kept anyway as a
    # "may need clearing in Response Search rather than reviewing" nudge on
    # jayson's explicit call: verified live against job 1971209 (new=16,
    # massReview=0) where the gap matched what turned out to actually need
    # rejecting once a human looked. Unverified for the general case — treat
    # it as a hint that's worth a look, not a trustworthy count.
    _mr = _safe_int(job.get("massReview"))
    possible_reject = max(0, base_unreviewed - _mr) if _mr is not None else 0
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
            # Raw echo of FA-web's priority component. NOT a count of aged
            # submissions and NOT safe to branch on: `priority_details` is a
            # point breakdown (its parts sum to `total`), `old_sub` is
            # "points for submissions older than 3 days", and it reads 0 on
            # every row the upstream returns even when the job demonstrably
            # has aged work. Use `agedCount` instead.
            "old_sub": int((job.get("priority_details") or {}).get("old_sub") or 0),
            # Aged pending submissions for this job, measured from
            # /api/responsegroups rather than trusted from the feed.
            "agedCount": int((aged or {}).get("count") or 0),
            "oldestAged": str((aged or {}).get("oldest") or ""),
            "startDate": str(job.get("startDate") or ""),
            # Deadline + backlog signals used by the Old Submissions triage view.
            "endDate": str(job.get("endDate") or ""),
            "pendingRatio": float((job.get("priority_details") or {}).get("pending_ratio") or 0),
            "numSubs": float((job.get("priority_details") or {}).get("num_subs") or 0),
            # Raw "New" count (incl. un-reviewable/auto-rejected) for reference.
            "newCount": _safe_int(job.get("new")) or 0,
            # Informational "may need clearing, not reviewing" hint — see the
            # note on `possible_reject` above. Never affects unreviewedCount.
            "possibleRejectCount": possible_reject,
            # Client owner email — used to scope to Storesight / Retail Pipeline jobs.
            "client": str(job.get("client") or ""),
            # Cloud-Factory-denied responses auto-approved before human re-review.
            "cfDeniedCount": cf_denied_count,
            # FA-web's own P&G store-walk flag, from the priority breakdown.
            # Unlike `old_sub` this one is genuinely populated: measured against
            # the live feed it matched the 9 jobs named "P&G Display Store Walk"
            # exactly, with no misses either way, and is binary in practice
            # (300 or 0). Preferred over matching the name so the filter
            # survives a rename.
            "pngStoreWalk": float(
                (job.get("priority_details") or {}).get("png_store_walk") or 0
            ) > 0,
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


def _parse_submission_date(raw):
    """Parse a responsegroup `submission_date` into an aware UTC datetime.

    The feed sends RFC-1123-ish strings ("Fri, 18 Sep 2026 19:50:36 GMT").
    Returns None when it won't parse, so one bad row can't sink the batch.
    """
    try:
        return datetime.datetime.strptime(
            str(raw), "%a, %d %b %Y %H:%M:%S %Z"
        ).replace(tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return None


def fetch_aged_submissions(min_days=AGED_SUBMISSION_DAYS):
    """Return {job_id_str: {"count": int, "oldest": iso}} for aged pending work.

    One date-bounded query over `/api/responsegroups` replaces what used to be
    a per-job scan: `submission_date_to` bounds the result to submissions at
    least `min_days` old, and every row carries its own `job_id`, so the whole
    backlog arrives in a page or two instead of one call per job in the feed.

    Rows currently checked out to a third party (`tp_review_status == "N"`)
    are excluded — that work isn't ours to review until it comes back, and
    surfacing it would put jobs in reviewers' queues with nothing actionable
    in them. Rows the third party already handed back are kept, including the
    CF-flagged ones (`tp_review_status == "X"`, e.g. "CF: [Q4] invalid
    product"), which are still `status="N"` precisely because they need a
    human.

    Do NOT derive this from `priority_details.old_sub` or the
    `*_subs_count` buckets: all four are zero on every row the upstream
    returns, including jobs whose true aged count is nonzero.

    Best-effort and independently cached, like `_fetch_cf_denied_counts` — the
    background warmer refreshes the job feed every 45s and this must not ride
    along on every one of those.
    """
    now = time.time()
    cache = _AGED_CACHE
    fresh = (now - cache["fetched_at"]) < _AGED_CACHE_TTL_SECONDS
    if fresh and cache["min_days"] == min_days:
        return cache["by_job"]

    cutoff = (
        datetime.datetime.now(datetime.timezone.utc).date()
        - datetime.timedelta(days=min_days)
    ).isoformat()

    by_job = {}
    page = 1
    try:
        while page <= MAX_AGED_PAGES:
            resp = internal_api.get(
                "/api/responsegroups",
                params={
                    "status": "N",
                    "submission_date_to": cutoff,
                    "sort": "submission_date",
                    "page": page,
                    "per_page": PAGE_SIZE,
                },
            )
            batch = resp.get("data", []) if isinstance(resp, dict) else []
            if not batch:
                break
            for rg in batch:
                if str(rg.get("tp_review_status") or "").strip().upper() == "N":
                    continue
                jid = str(rg.get("job_id") or "")
                if not jid:
                    continue
                parsed = _parse_submission_date(rg.get("submission_date"))
                entry = by_job.setdefault(jid, {"count": 0, "oldest": ""})
                entry["count"] += 1
                if parsed is not None:
                    iso = parsed.isoformat()
                    if not entry["oldest"] or iso < entry["oldest"]:
                        entry["oldest"] = iso
            if len(batch) < PAGE_SIZE:
                break
            page += 1
    except Exception as exc:  # noqa: BLE001 — best-effort, never block the feed
        logging.warning("aged-submission fetch failed: %s", exc)
        # Serve stale data rather than none, so a 429 doesn't make the whole
        # Old Submissions view read as "no aged work".
        if cache["by_job"]:
            return cache["by_job"]
        return by_job

    cache["fetched_at"] = now
    cache["min_days"] = min_days
    cache["by_job"] = by_job
    logging.info(
        "bloom.fetch_aged_submissions cutoff=%s jobs=%d rows=%d",
        cutoff, len(by_job), sum(e["count"] for e in by_job.values()),
    )
    return by_job


def fetch_prioritized_jobs(status=DEFAULT_STATUS, use_cache=True, include_aged=False,
                           max_age=None):
    """Return Rows for every job with unreviewed submissions, pre-prioritized by FA-web.

    Calls /api/prioritized-jobs which returns jobs ranked by jicco, close date,
    submission age, reimbursement, P&G store-walk, part-one, plus relative
    sub-count / pending-ratio / days-remaining weighting.

    A 60-second in-process cache keeps the Internal-API rate limit headroom
    comfortable, and a background warmer keeps that cache hot — the upstream
    call takes 10-12 seconds for ~500 jobs, so a cold read is something no
    user-facing request should ever pay. The `status` parameter is kept for
    backward compatibility but unused (the API only returns jobs with new
    submissions).

    `max_age` accepts cached rows up to that many seconds old, overriding the
    default TTL, and refetches when the cache is older (or empty). Use it for
    callers that want "reasonably fresh, but never block for 12 seconds";
    `use_cache=False` remains a hard bypass for the explicit Refresh button.

    When include_aged=True, includes jobs with old unreviewed submissions even if
    they have zero new responses. Used by the Old Submissions page.

    Excludes jobs from clients handled by a third party (Cloud Factory).
    """
    _ensure_warmer_started()
    now = time.time()
    ttl = _CACHE_TTL_SECONDS if max_age is None else max_age
    if use_cache and _CACHE["rows"] and (now - _CACHE["fetched_at"]) < ttl:
        return _CACHE["rows"]

    # Single-flight: only one caller does the 10-12s upstream call. The rest
    # queue here and then re-check the cache below, so a burst of simultaneous
    # misses costs one fetch rather than one each.
    with _FETCH_LOCK:
        now = time.time()
        if use_cache and _CACHE["rows"] and (now - _CACHE["fetched_at"]) < ttl:
            # Someone else refreshed it while we waited for the lock. Skipped for
            # use_cache=False so the Refresh button still forces a real fetch.
            return _CACHE["rows"]
        return _fetch_and_cache_prioritized_jobs()


def _fetch_and_cache_prioritized_jobs():
    """Do the actual upstream fetch and populate _CACHE. Caller holds _FETCH_LOCK."""
    now = time.time()
    jobs = _fetch_prioritized_jobs_raw()

    min_date = _earliest_start_date_iso(jobs)
    cf_denied_counts = _fetch_cf_denied_counts(min_date) if min_date else {}

    aged_by_job = fetch_aged_submissions()

    # Defensive: skip malformed records with no job id — they can't be assigned
    # or completed, and would render as blank rows in the UI.
    #
    # `new > 0` is the whole filter. Jobs with nothing in FieldAgent's queue are
    # excluded even when work is parked at Cloud Factory, because a reviewer
    # handed one finds nothing in Review and has to "Mark done anyway".
    #
    # This used to also admit `old_sub > 0`, reading it off the top level of the
    # raw row — where it does not exist (it lives under `priority_details`), so
    # the clause was always false and this filter has only ever been `new > 0`.
    # Removed rather than repointed: repointing it would let exactly those
    # empty jobs back in.
    #
    # Uses _safe_int (not bare int()) since the feed sends "" for some counts.
    rows = [
        _row_from_api(
            job,
            cf_denied_counts.get(str(job.get("id") or ""), 0),
            aged_by_job.get(str(job.get("id") or "")),
        )
        for job in jobs
        if isinstance(job, dict) and job.get("id") not in (None, "")
        and (_safe_int(job.get("new")) or 0) > 0
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
    # The aged-submission cache outlives the job cache by design (5 min vs 60s)
    # so the warmer doesn't re-query it every 45s. Clear it here anyway: a
    # Force refresh should re-read aged work too, and leaving it set would
    # leak between tests.
    _AGED_CACHE["fetched_at"] = 0.0
    _AGED_CACHE["min_days"] = None
    _AGED_CACHE["by_job"] = {}
    clear_project_name_cache()
