"""One-off cleanup: remove completions written against a PID instead of a job.

Checking off a "By PID" group card in My Tasks sent the group's PID as the
job_id. The completion endpoint accepted it, wrote a completion doc keyed to the
PID, and tallied it onto the reviewer's weekly leaderboard (one job, plus
whatever Bloom reported for a *job* with that number). The endpoint now refuses
ids that aren't on the caller's shift; this removes what got through before.

A completion is treated as a PID orphan only when its key matches a projectId
on some row in its shift AND matches no job anywhere in that shift. Rows are read
across every reviewer, because closing out deletes a reviewer's own
reviewer_shift docs. Anything that matches neither is reported, never touched.

For each orphan the matching review_tally is decremented by the one job and the
responses it was credited, recomputed over the same window the server used
(shift publish, or the previous checkmark on that key, up to the orphan's own
completed_at).

Completions from earlier days are purged at the next publish, so orphans from
those shifts are already gone and their tally credit can't be identified.

Usage (from repo root):
    LOCAL_DEV=1 TOOL_SLUG=qc-shift-assignments BLOOM_WARMER=0 \\
      python3 scripts/cleanup_orphan_completions.py            # dry run
    LOCAL_DEV=1 TOOL_SLUG=qc-shift-assignments BLOOM_WARMER=0 \\
      python3 scripts/cleanup_orphan_completions.py --apply    # write
"""

import datetime
import sys

import bloom
import internal_api
import main
import roles


def _reviewed_between(job_id, since, until):
    """Human-reviewed response groups on `job_id` with since <= review_ts <= until.
    Mirrors bloom.count_reviewed_since, bounded above. None if Bloom can't be read."""
    count = 0
    try:
        for status in bloom.REVIEWED_STATUSES:
            page = 1
            while page <= bloom.MAX_REVIEWED_PAGES:
                resp = internal_api.get("/api/responsegroups", params={
                    "job_id": job_id, "status": status, "sort": "-review_ts",
                    "page": page, "per_page": bloom.PAGE_SIZE,
                })
                batch = resp.get("data", []) if isinstance(resp, dict) else []
                passed = False
                for rg in batch:
                    reviewed_at = bloom._parse_submission_date(rg.get("review_ts"))
                    if reviewed_at is None or reviewed_at > until:
                        continue
                    if reviewed_at < since:
                        passed = True
                        break
                    if rg.get("reviewed_by_id") != bloom.SYSTEM_REVIEWER_ID:
                        count += 1
                if passed or len(batch) < bloom.PAGE_SIZE:
                    break
                page += 1
    except Exception as exc:  # noqa: BLE001
        print(f"    ! Bloom lookup failed for {job_id}: {exc}")
        return None
    return count


def _credited(orphan, snap, same_key):
    """Responses the server credited this orphan (see main._responses_to_credit)."""
    done_at = main._parse_iso_utc(orphan.get("completed_at"))
    since = main._parse_iso_utc((snap or {}).get("published_at"))
    for c in same_key:
        ts = main._parse_iso_utc(c.get("completed_at"))
        if c["id"] != orphan["id"] and ts and done_at and ts < done_at \
                and (since is None or ts > since):
            since = ts
    if since is None or done_at is None:
        return 0  # server fell back to the rows, where a PID matches nothing
    n = _reviewed_between(main._completion_job_key(orphan), since, done_at)
    return 0 if n is None else n


def run(apply):
    snaps = {d["id"]: d.get("data") or {}
             for d in roles.list_docs_by_kind("shift_snapshot", force=True)}
    shift_docs = roles.list_docs_by_kind("reviewer_shift", force=True)
    completions = [{"id": d.get("id"), **(d.get("data") or {})}
                   for d in roles.list_docs_by_kind("completion", force=True)]
    tallies = roles.list_docs_by_kind("review_tally", force=True)

    job_keys, pids = {}, {}
    for d in shift_docs:
        data = d.get("data") or {}
        sid = data.get("shift_snapshot_id")
        for r in data.get("rows") or []:
            job_keys.setdefault(sid, set()).add(main._job_key(r))
            if r.get("projectId"):
                pids.setdefault(sid, set()).add(str(r["projectId"]))

    orphans, unknown = [], []
    for c in completions:
        sid, key = c.get("shift_snapshot_id"), main._completion_job_key(c)
        if key in job_keys.get(sid, set()):
            continue
        (orphans if key in pids.get(sid, set()) else unknown).append(c)

    print(f"snapshots: {len(snaps)}  completion docs: {len(completions)}  "
          f"reviewer_shift docs: {len(shift_docs)}")
    print(f"PID orphans: {len(orphans)}   unmatched (left alone): {len(unknown)}\n")

    # (email, week, day) -> [jobs, responses] to take back
    corrections = {}
    for o in sorted(orphans, key=lambda c: (c.get("reviewer_email") or "", c.get("completed_at") or "")):
        sid, key = o.get("shift_snapshot_id"), main._completion_job_key(o)
        same_key = [c for c in completions
                    if c.get("shift_snapshot_id") == sid and main._completion_job_key(c) == key]
        resp = _credited(o, snaps.get(sid), same_key)
        try:
            dt = datetime.datetime.fromisoformat(o.get("completed_at"))
        except (TypeError, ValueError):
            dt = None
        print(f"  {o.get('reviewer_email')}  PID {key}  at {o.get('completed_at')}  "
              f"override={bool(o.get('overridden'))}  credited~{resp} responses  doc={o['id']}")
        if dt is not None:
            k = ((o.get("reviewer_email") or "").lower(), main._iso_week_key(dt), dt.date().isoformat())
            acc = corrections.setdefault(k, [0, 0])
            acc[0] += 1
            acc[1] += resp

    for u in unknown:
        print(f"  ? unmatched: {u.get('reviewer_email')}  key={main._completion_job_key(u)}  "
              f"snapshot={u.get('shift_snapshot_id')}  doc={u['id']}")

    # Plan tally edits against the doc holding the most for that day.
    edits = {}
    for (email, week, day), (jobs, resp) in corrections.items():
        cands = [t for t in tallies
                 if (t.get("data") or {}).get("week") == week
                 and ((t.get("data") or {}).get("reviewer_email") or "").lower() == email]
        if not cands:
            print(f"  ! no tally doc for {email} {week}; nothing to decrement")
            continue
        t = max(cands, key=lambda t: int(((t.get("data") or {}).get("days") or {}).get(day, 0)))
        data = edits.setdefault(t["id"], {**t["data"],
                                          "days": {**(t["data"].get("days") or {})},
                                          "resp_days": {**(t["data"].get("resp_days") or {})}})
        take_jobs = min(jobs, int(data["days"].get(day, 0)))
        take_resp = min(resp, int(data["resp_days"].get(day, 0)))
        data["days"][day] = int(data["days"].get(day, 0)) - take_jobs
        data["total"] = max(0, int(data.get("total", 0)) - take_jobs)
        data["resp_days"][day] = int(data["resp_days"].get(day, 0)) - take_resp
        data["resp_total"] = max(0, int(data.get("resp_total", 0)) - take_resp)
        print(f"  tally {email} {week} {day}: -{take_jobs} job(s), -{take_resp} responses "
              f"-> total {data['total']}, responses {data['resp_total']}")

    if not orphans:
        print("\nNothing to clean up.")
        return
    if not apply:
        print("\nDRY RUN — re-run with --apply to delete the orphans and fix the tallies.")
        return

    for tid, data in edits.items():
        internal_api.put(f"{roles._STORAGE_PATH}/{tid}", json={"data": data})
    for o in orphans:
        internal_api.delete(f"{roles._STORAGE_PATH}/{o['id']}")
    roles.invalidate_doc_cache("completion")
    roles.invalidate_doc_cache("review_tally")
    print(f"\nDONE — deleted {len(orphans)} orphan completion(s), updated {len(edits)} tally doc(s).")


if __name__ == "__main__":
    run(apply="--apply" in sys.argv)
