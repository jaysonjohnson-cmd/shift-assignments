"""Read-only diagnostic: dump raw Prioritized Jobs feed records so we can see
which field actually holds the FieldAgent job id used by collection-review.

Usage (from repo root, with a fresh dev token):
    FA_API_KEY=... python3 scripts/inspect_prioritized_job.py
Optionally filter to a needle (project id, job id, or name substring):
    ... python3 scripts/inspect_prioritized_job.py 1632244
"""

import json
import sys

import bloom

NEEDLE = sys.argv[1] if len(sys.argv) > 1 else "1632244"


def main():
    resp = bloom._post_prioritized_jobs()
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
