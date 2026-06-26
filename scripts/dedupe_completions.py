"""One-off cleanup: remove duplicate completion docs for the current shift.

The override path ("mark done anyway") and the old stale-cache auto-refill bug
could write a `completion` doc for the same (reviewer, job) more than once. The
overview/dashboard math is now deduped (it intersects against a set), so these
duplicates no longer corrupt the display — but they bloat the namespace and were
the source of duplicate Slack "finished" pings. This script keeps the EARLIEST
completion per (reviewer_email, job_key) in the active snapshot and deletes the
rest. Reviewer assignment rows are never touched.

Usage (from repo root):
    LOCAL_DEV=1 TOOL_SLUG=qc-shift-assignments \\
      python3 scripts/dedupe_completions.py            # dry run (all reviewers)
    LOCAL_DEV=1 TOOL_SLUG=qc-shift-assignments \\
      python3 scripts/dedupe_completions.py --apply     # write
    LOCAL_DEV=1 TOOL_SLUG=qc-shift-assignments \\
      python3 scripts/dedupe_completions.py kendall.smith@storesight.com --apply
"""

import sys

import internal_api
import main
import roles


def main_cleanup(email_filter, apply):
    snap_id, _ = main._latest_snapshot()
    if not snap_id:
        print("No active shift snapshot — nothing to do.")
        return

    norm_filter = (email_filter or "").strip().lower()
    docs = [
        d for d in roles.list_docs_by_kind("completion", force=True)
        if (d.get("data") or {}).get("shift_snapshot_id") == snap_id
        and (not norm_filter
             or (d.get("data") or {}).get("reviewer_email", "").strip().lower() == norm_filter)
    ]
    if not docs:
        print(f"No completion docs for snapshot {snap_id}"
              + (f" / {norm_filter}" if norm_filter else "") + ".")
        return

    # Keep the earliest completion per (reviewer, job). Sort by completed_at so
    # "first wins" means the original completion, not a later duplicate.
    def sort_key(d):
        data = d.get("data") or {}
        return (data.get("completed_at") or "", d.get("id") or "")

    docs.sort(key=sort_key)

    seen, keep, surplus = set(), [], []
    for d in docs:
        data = d.get("data") or {}
        email = (data.get("reviewer_email") or "").strip().lower()
        jkey = main._completion_job_key(data)
        k = (email, jkey)
        if email and jkey and k in seen:
            surplus.append(d)
            continue
        if email and jkey:
            seen.add(k)
        keep.append(d)

    print(f"snapshot:           {snap_id}")
    if norm_filter:
        print(f"reviewer filter:    {norm_filter}")
    print(f"completion docs:    {len(docs)}")
    print(f"unique completions: {len(keep)}")
    print(f"duplicate docs:     {len(surplus)}")

    if not surplus:
        print("Nothing to dedupe.")
        return

    # Show a per-reviewer breakdown of what gets removed.
    by_reviewer = {}
    for d in surplus:
        em = (d.get("data") or {}).get("reviewer_email", "?")
        by_reviewer[em] = by_reviewer.get(em, 0) + 1
    for em, n in sorted(by_reviewer.items(), key=lambda kv: -kv[1]):
        print(f"  - {em}: {n} duplicate(s)")

    if not apply:
        print("\nDRY RUN — re-run with --apply to delete the duplicates.")
        return

    for d in surplus:
        internal_api.delete(f"{roles._STORAGE_PATH}/{d['id']}")

    roles.invalidate_doc_cache("completion")
    print(f"\nDONE — removed {len(surplus)} duplicate completion doc(s); "
          f"{len(keep)} unique completions remain. Assignments untouched.")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main_cleanup(args[0] if args else None, apply="--apply" in sys.argv)
