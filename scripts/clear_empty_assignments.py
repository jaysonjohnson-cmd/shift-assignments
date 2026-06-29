"""Remove truly-empty jobs (no unreviewed responses at all, not checked off).

A job that has dropped out of the prioritized feed has zero unreviewed
responses left — nothing to review and nothing to auto-reject — so if it was
never checked off it's just dead weight in the queue. This rewrites each
reviewer's reviewer_shift docs for the current snapshot, dropping rows that are:
  * absent from the live prioritized feed (no unreviewed of any kind), AND
  * not marked complete.

Kept: completed jobs, jobs with reviewable work, AND jobs whose only leftover
responses are auto-rejected (still in the feed) — the reviewer clears those on
the Responses page. Completion docs are never touched.

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

    # A job present in the feed still has unreviewed responses of SOME kind —
    # reviewable OR auto-rejected (which the reviewer still has to clear on the
    # Responses page). Only a job that has dropped out of the feed entirely is
    # truly empty and safe to remove. (We intentionally do NOT key off the
    # reviewable/massReview count here, or we'd delete auto-reject-only jobs.)
    feed_ids = {str(j.get("jobId")) for j in bloom.fetch_prioritized_jobs(use_cache=False)
                if j.get("jobId")}

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
                is_empty = k not in feed_ids  # gone from feed = no unreviewed at all
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
