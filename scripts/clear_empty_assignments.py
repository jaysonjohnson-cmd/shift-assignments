"""Remove empty jobs (0 live unreviewed, not checked off) from the active shift.

Only ACTIVE work belongs in the workflow. Jobs whose unreviewed count has
dropped to 0 but were never checked off linger in reviewers' queues as "old"
JIDs with nothing to review. This rewrites each reviewer's reviewer_shift docs
for the current snapshot, dropping rows that are both:
  * absent from the live prioritized feed OR at 0 live unreviewed, AND
  * not marked complete (a completed row is kept — it's done, not empty).

Completed jobs and active jobs (unreviewed > 0) are preserved. Completion docs
are never touched.

Usage (from repo root):
    LOCAL_DEV=1 TOOL_SLUG=qc-shift-assignments \\
      python3 scripts/clear_empty_assignments.py            # dry run, all reviewers
    LOCAL_DEV=1 TOOL_SLUG=qc-shift-assignments \\
      python3 scripts/clear_empty_assignments.py --apply     # write
"""

import sys

import internal_api
import bloom
import main
import roles


def run(apply):
    snap_id, _ = main._latest_snapshot()
    if not snap_id:
        print("No active shift snapshot — nothing to do.")
        return

    feed = bloom.fetch_prioritized_jobs(use_cache=False)
    live = {str(j.get("jobId")): int(j.get("unreviewedCount") or 0)
            for j in feed if j.get("jobId")}

    docs = [
        d for d in roles.list_docs_by_kind("reviewer_shift", force=True)
        if (d.get("data") or {}).get("shift_snapshot_id") == snap_id
    ]
    if not docs:
        print(f"No reviewer_shift docs for snapshot {snap_id}.")
        return

    total_removed = 0
    # Group docs by reviewer so we can re-chunk each reviewer's surviving rows.
    by_email = {}
    for d in docs:
        em = (d.get("data") or {}).get("reviewer_email", "")
        by_email.setdefault(em, []).append(d)

    for email, rdocs in by_email.items():
        done = {main._completion_job_key(c)
                for c in main._list_completions_for_snapshot(snap_id, reviewer_email=email)}
        rdocs.sort(key=lambda d: int((d.get("data") or {}).get("part") or 0))
        kept, removed = [], 0
        for d in rdocs:
            for r in (d.get("data") or {}).get("rows") or []:
                k = main._job_key(r)
                is_done = k in done
                is_empty = live.get(k, 0) <= 0
                if is_empty and not is_done:
                    removed += 1
                else:
                    kept.append(r)
        if removed == 0:
            continue
        total_removed += removed
        print(f"{email}: removing {removed} empty row(s), keeping {len(kept)}")
        if not apply:
            continue
        # Rewrite the reviewer's docs with the surviving rows; delete surplus.
        chunks = main._chunk_rows_for_storage(kept) if kept else []
        for i, chunk in enumerate(chunks):
            data = {
                "kind": "reviewer_shift",
                "shift_snapshot_id": snap_id,
                "reviewer_email": email,
                "rows": chunk,
                "part": i,
                "part_count": len(chunks),
            }
            internal_api.put(f"{main._STORAGE_PATH}/{rdocs[i]['id']}", json={"data": data})
        for d in rdocs[len(chunks):]:
            internal_api.delete(f"{main._STORAGE_PATH}/{d['id']}")

    print(f"\nTOTAL empty rows {'removed' if apply else 'that would be removed'}: {total_removed}")
    if apply:
        roles.invalidate_doc_cache("reviewer_shift")
        print("Done — only active work and completed jobs remain.")
    elif total_removed:
        print("DRY RUN — re-run with --apply to write.")


if __name__ == "__main__":
    run(apply="--apply" in sys.argv)
