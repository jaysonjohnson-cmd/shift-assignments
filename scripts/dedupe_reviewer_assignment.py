"""One-off cleanup: remove duplicate job rows from a reviewer's live assignment.

The auto-refill stale-cache bug (fixed going forward) re-added jobs a reviewer
already had, inflating their queue with duplicate rows. This script rewrites the
reviewer's `reviewer_shift` docs for the current snapshot so each job appears
once (first occurrence wins, preserving priority order). Completion docs are
never touched, so finished work stays finished.

Usage (from repo root):
    LOCAL_DEV=1 TOOL_SLUG=qc-shift-assignments \\
      python3 scripts/dedupe_reviewer_assignment.py kendall.smith@storesight.com          # dry run
    LOCAL_DEV=1 TOOL_SLUG=qc-shift-assignments \\
      python3 scripts/dedupe_reviewer_assignment.py kendall.smith@storesight.com --apply   # write
"""

import sys

import internal_api
import main
import roles


def main_cleanup(email, apply, batch_size_override=None):
    email = email.strip().lower()
    snap_id, _ = main._latest_snapshot()
    if not snap_id:
        print("No active shift snapshot — nothing to do.")
        return

    docs = [
        d for d in roles.list_docs_by_kind("reviewer_shift", force=True)
        if (d.get("data") or {}).get("shift_snapshot_id") == snap_id
        and (d.get("data") or {}).get("reviewer_email", "").strip().lower() == email
    ]
    docs.sort(key=lambda d: int((d.get("data") or {}).get("part") or 0))
    if not docs:
        print(f"No reviewer_shift docs for {email} in snapshot {snap_id}.")
        return

    rows = []
    # Preserve the reviewer's original batch_size (the refill allotment). Dropping
    # it would make auto-refill fall back to the current queue length and double
    # the batch each cycle. Use the smallest stamped value (the original).
    batch_size = None
    for d in docs:
        data = d.get("data") or {}
        rows.extend(data.get("rows") or [])
        bs = data.get("batch_size")
        if bs:
            batch_size = bs if batch_size is None else min(batch_size, bs)
    # Explicit override (e.g. --batch-size 20) wins — used to repair a queue whose
    # stored batch_size got corrupted by an earlier dedup-then-refill cycle.
    if batch_size_override:
        batch_size = batch_size_override

    seen, deduped, dropped = set(), [], 0
    for r in rows:
        k = main._job_key(r)
        if k and k in seen:
            dropped += 1
            continue
        if k:
            seen.add(k)
        deduped.append(r)

    print(f"reviewer:        {email}")
    print(f"snapshot:        {snap_id}")
    print(f"existing docs:   {len(docs)} (parts {[ (d.get('data') or {}).get('part') for d in docs ]})")
    print(f"rows before:     {len(rows)}")
    print(f"unique jobs:     {len(deduped)}")
    print(f"duplicate rows:  {dropped}")

    if dropped == 0:
        print("Nothing to dedupe.")
        return

    chunks = main._chunk_rows_for_storage(deduped)
    print(f"rewrite into:    {len(chunks)} chunk(s)")
    if not apply:
        print("\nDRY RUN — re-run with --apply to write the changes.")
        return

    # Write/update first (reusing existing doc ids), then delete any surplus docs,
    # so the reviewer is never momentarily left with zero rows.
    for i, chunk in enumerate(chunks):
        data = {
            "kind": "reviewer_shift",
            "shift_snapshot_id": snap_id,
            "reviewer_email": email,
            "rows": chunk,
            "part": i,
            "part_count": len(chunks),
        }
        if batch_size:
            data["batch_size"] = batch_size
        if i < len(docs):
            internal_api.put(f"{main._STORAGE_PATH}/{docs[i]['id']}", json={"data": data})
        else:
            internal_api.post(main._STORAGE_PATH, json={"data": data})
    for d in docs[len(chunks):]:
        internal_api.delete(f"{main._STORAGE_PATH}/{d['id']}")

    roles.invalidate_doc_cache("reviewer_shift")
    print(f"\nDONE — {email} now has {len(deduped)} jobs across {len(chunks)} doc(s); "
          f"removed {dropped} duplicate rows. Completions untouched.")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("usage: dedupe_reviewer_assignment.py <email> [--apply] [--batch-size N]")
        sys.exit(1)
    bs_override = None
    for a in sys.argv[1:]:
        if a.startswith("--batch-size="):
            bs_override = int(a.split("=", 1)[1])
        elif a == "--batch-size":
            idx = sys.argv.index(a)
            bs_override = int(sys.argv[idx + 1])
    main_cleanup(args[0], apply="--apply" in sys.argv, batch_size_override=bs_override)
