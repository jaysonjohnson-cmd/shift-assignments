"""Leaderboard tally fixes from the 2026-11-09 review."""

import os

os.environ["LOCAL_DEV"] = "1"

import main  # noqa: E402
import roles  # noqa: E402
import internal_api  # noqa: E402

SAM = "sam@storesight.com"


def _storage(monkeypatch):
    docs = []

    def post(path, json=None):
        docs.append({"id": f"t{len(docs)}", "data": json["data"]})
        return {"data": {"id": docs[-1]["id"]}}

    def put(path, json=None):
        doc_id = path.rsplit("/", 1)[-1]
        for d in docs:
            if d["id"] == doc_id:
                d["data"] = json["data"]
        return {"data": {}}
    monkeypatch.setattr(internal_api, "post", post)
    monkeypatch.setattr(internal_api, "put", put)
    monkeypatch.setattr(roles, "list_docs_by_kind", lambda kind, force=False: [
        d for d in docs if d["data"].get("kind") == kind])
    monkeypatch.setattr(roles, "cache_upsert_doc", lambda *a, **k: None)
    return docs


def test_unchecking_takes_the_credit_back(monkeypatch):
    docs = _storage(monkeypatch)
    at = "2026-11-09T16:00:00+00:00"
    main._record_review_event(SAM, at, responses=12)
    main._record_review_event(SAM, at, responses=12, undo=True)
    main._record_review_event(SAM, at, responses=12)
    tally = docs[0]["data"]
    assert tally["total"] == 1 and tally["resp_total"] == 12
    assert tally["days"]["2026-11-09"] == 1


def test_undo_with_nothing_tallied_writes_nothing(monkeypatch):
    docs = _storage(monkeypatch)
    main._record_review_event(SAM, "2026-11-09T16:00:00+00:00", responses=5, undo=True)
    assert docs == []


def test_an_evening_checkoff_counts_on_its_central_day(monkeypatch):
    # 7:30 PM CST on Sunday Nov 8 is 01:30 UTC Monday — it belongs to Sunday
    # and to the week ending that Sunday.
    docs = _storage(monkeypatch)
    main._record_review_event(SAM, "2026-11-09T01:30:00+00:00", responses=3)
    tally = docs[0]["data"]
    assert list(tally["days"]) == ["2026-11-08"]
    assert tally["week"] == "2026-W45"
