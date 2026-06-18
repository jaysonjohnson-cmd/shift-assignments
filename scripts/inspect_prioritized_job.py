"""Read-only diagnostic: dump raw /api/prioritized-jobs records so we can see
which field actually holds the FieldAgent job id used by collection-review.

Usage (from repo root, with a fresh dev token):
    TOOL_SLUG=qc-shift-assignments LOCAL_DEV=1 python3 scripts/inspect_prioritized_job.py
Optionally filter to a needle (project id, job id, or name substring):
    ... python3 scripts/inspect_prioritized_job.py 1632244
"""

import json
import sys

import internal_api

NEEDLE = sys.argv[1] if len(sys.argv) > 1 else "1632244"


def main():
    resp = internal_api.get("/api/prioritized-jobs")
    records = resp.get("data") if isinstance(resp, dict) else resp
    records = records or []
    print(f"Total records returned: {len(records)}")

    if records:
        print("\n=== All field names on the first record ===")
        print(sorted(records[0].keys()))

    matches = [
        r
        for r in records
        if NEEDLE.lower()
        in json.dumps(r, default=str).lower()
    ]
    print(f"\n=== Records matching '{NEEDLE}': {len(matches)} ===")
    for r in matches[:5]:
        print(json.dumps(r, indent=2, default=str))


if __name__ == "__main__":
    main()
