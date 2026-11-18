"""One-off: fold the legacy one-doc-per-checkmark `checkoff_log` docs into
`checkoff_day` docs (one per reviewer per day), then delete the legacy docs.

Why: 6,000+ checkoff_log docs made every forced Storage read an 8-page scan,
and check-offs do several — see the CHECKOFF_DAY_KIND comment in main.py.

Run it only AFTER the code that reads `checkoff_day` is live, or the
Check-offs tab loses each entry as its legacy doc is deleted.

Two steps, each a dry run unless --apply:

    convert  writes day docs for every legacy entry inside the 14-day window
             that isn't already in one. Safe to re-run.
    delete   deletes legacy docs whose entry is now in a day doc (or that are
             past the window), paced to --per-min so the live tool keeps most
             of the 60 req/min Internal API budget. Safe to stop and re-run.

Usage (from repo root):
    LOCAL_DEV=1 TOOL_SLUG=qc-shift-assignments BLOOM_WARMER=0 \\
      python3 scripts/migrate_checkoff_log.py convert [--apply]
    LOCAL_DEV=1 TOOL_SLUG=qc-shift-assignments BLOOM_WARMER=0 \\
      python3 scripts/migrate_checkoff_log.py delete [--apply] [--per-min 20]
"""

import collections
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import internal_api  # noqa: E402
import main  # noqa: E402
import roles  # noqa: E402


def _docs(kind):
    return roles.list_docs_by_kind(kind, force=True)


def _logged_ids():
    """completion ids already held in some checkoff_day doc."""
    return {e.get("completion_id")
            for d in _docs(main.CHECKOFF_DAY_KIND)
            for e in (d.get("data") or {}).get("entries") or []}


def convert(apply):
    oldest, _ = main._checkoff_log_window()
    done = _logged_ids()
    groups = collections.defaultdict(list)
    skipped = 0
    for d in roles.list_docs_by_kind(main.CHECKOFF_LOG_KIND):
        data = d.get("data") or {}
        day = data.get("local_date") or ""
        cid = data.get("completion_id")
        if day < oldest.isoformat() or not cid or cid in done:
            skipped += 1
            continue
        email = (data.get("reviewer_email") or "").strip().lower()
        groups[(email, day)].append(main._checkoff_entry(cid, data))

    chunks = []
    for (email, day), entries in sorted(groups.items()):
        entries.sort(key=lambda e: e.get("completed_at") or "")
        cur = []
        for e in entries:
            if cur and len(json.dumps(cur + [e])) > main._CHECKOFF_DAY_MAX_BYTES:
                chunks.append((email, day, cur))
                cur = []
            cur.append(e)
        if cur:
            chunks.append((email, day, cur))

    n = sum(len(c) for _, _, c in chunks)
    print(f"{n} entries to fold into {len(chunks)} day docs "
          f"({len(groups)} reviewer-days); {skipped} skipped (done, expired or undated)")
    if not apply:
        print("dry run — pass --apply to write")
        return
    for i, (email, day, entries) in enumerate(chunks, 1):
        internal_api.post(main._STORAGE_PATH, json={"data": {
            "kind": main.CHECKOFF_DAY_KIND, "reviewer_email": email,
            "local_date": day, "entries": entries}})
        print(f"  [{i}/{len(chunks)}] {email} {day}: {len(entries)} entries", flush=True)
        time.sleep(1)


def delete(apply, per_min):
    oldest, _ = main._checkoff_log_window()
    done = _logged_ids()
    legacy = roles.list_docs_by_kind(main.CHECKOFF_LOG_KIND)
    ready, waiting = [], 0
    for d in legacy:
        data = d.get("data") or {}
        if data.get("completion_id") in done or (data.get("local_date") or "") < oldest.isoformat():
            ready.append(d.get("id"))
        else:
            waiting += 1
    print(f"{len(ready)} legacy docs safe to delete; {waiting} not yet in a day doc "
          f"(run convert first)")
    if not apply:
        print("dry run — pass --apply to delete")
        return
    gap = 60.0 / per_min
    for i, doc_id in enumerate(ready, 1):
        try:
            internal_api.delete(f"{main._STORAGE_PATH}/{doc_id}")
        except Exception as exc:  # noqa: BLE001 — keep going, re-run picks it up
            print(f"  delete {doc_id} failed: {exc}", flush=True)
        if i % 100 == 0 or i == len(ready):
            print(f"  deleted {i}/{len(ready)}", flush=True)
        time.sleep(gap)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] not in ("convert", "delete"):
        print(__doc__)
        sys.exit(2)
    apply = "--apply" in args
    per_min = 20
    if "--per-min" in args:
        per_min = max(1, min(50, int(args[args.index("--per-min") + 1])))
    if args[0] == "convert":
        convert(apply)
    else:
        delete(apply, per_min)
