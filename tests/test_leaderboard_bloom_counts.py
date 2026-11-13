"""Leaderboard responses counted straight from Bloom.

Check-offs only credit reviews on jobs someone checks off, so work released at
close-out, left open at the 5:30 clear, or done on unassigned jobs never
counted. For reviewers whose FieldAgent id is learned, the board now uses
Bloom's own per-day review counts across every job.
"""

import datetime
import os

import jwt
import pytest

os.environ["LOCAL_DEV"] = "1"

import bloom  # noqa: E402
import internal_api  # noqa: E402
import main  # noqa: E402
import roles  # noqa: E402

SAM = "sam@storesight.com"
ALEX = "alex@storesight.com"
CT = main._SHIFT_TZ
UTC = datetime.timezone.utc


def _token(email):
    now = datetime.datetime.now(UTC)
    return jwt.encode({"email": email, "name": "U", "iat": now,
                       "exp": now + datetime.timedelta(hours=8)},
                      "irrelevant", algorithm="HS256")


@pytest.fixture
def client(tmp_path, monkeypatch):
    token_dir = tmp_path / ".storesight"
    token_dir.mkdir()
    token_file = token_dir / "dev-token"
    monkeypatch.setattr("main._dev_token_path", lambda: token_file)
    main.app.config["TESTING"] = True
    with main.app.test_client() as c:
        token_file.write_text(_token(roles.ROOT_ADMIN_EMAIL))
        yield c


@pytest.fixture
def storage(monkeypatch):
    docs = []

    def post(path, json=None):
        doc_id = f"doc-{len(docs) + 1}"
        docs.append({"id": doc_id, "data": json["data"]})
        return {"data": {"id": doc_id}}

    def put(path, json=None):
        doc_id = path.rsplit("/", 1)[-1]
        for d in docs:
            if d["id"] == doc_id:
                d["data"] = json["data"]
        return {"data": {}}

    monkeypatch.setattr(internal_api, "post", post)
    monkeypatch.setattr(internal_api, "put", put)
    monkeypatch.setattr(roles, "list_docs_by_kind", lambda kind, force=False: [
        d for d in reversed(docs) if (d["data"] or {}).get("kind") == kind])
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [
        {"id": "1", "name": "Sam", "email": SAM},
        {"id": "2", "name": "Alex", "email": ALEX},
    ])
    return docs


def _rfc(dt):
    return dt.astimezone(UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")


# ── bloom.reviews_by_day ────────────────────────────────────────────────────


def test_reviews_by_day_buckets_by_central_day(monkeypatch):
    since = datetime.datetime(2026, 11, 12, tzinfo=CT)
    # 9:30 PM Central on the 12th is the 13th in UTC; it belongs to the 12th.
    late_12th = datetime.datetime(2026, 11, 12, 21, 30, tzinfo=CT)
    on_13th = datetime.datetime(2026, 11, 13, 9, 0, tzinfo=CT)
    before = datetime.datetime(2026, 11, 11, 16, 0, tzinfo=CT)
    rows = {
        "A": [{"review_ts": _rfc(on_13th), "reviewed_by_id": 1},
              {"review_ts": _rfc(on_13th), "reviewed_by_id": bloom.SYSTEM_REVIEWER_ID},
              {"review_ts": _rfc(late_12th), "reviewed_by_id": 1},
              {"review_ts": _rfc(before), "reviewed_by_id": 1}],
        "X": [{"review_ts": _rfc(on_13th), "reviewed_by_id": 2}],
    }
    seen = []

    def fake_get(path, params=None):
        seen.append(params)
        return {"data": rows.get(params["status"], [])}
    monkeypatch.setattr(internal_api, "get", fake_get)

    out = bloom.reviews_by_day(since, CT)
    assert out == {"2026-11-13": {1: {"A": 1}, 2: {"X": 1}},
                   "2026-11-12": {1: {"A": 1}}}
    # Bounded by submission date, or the sort is far too slow.
    assert all(p["submission_date_from"] == "2026-09-13" for p in seen)


def test_reviews_by_day_failure_is_none(monkeypatch):
    def boom(path, params=None):
        raise RuntimeError("down")
    monkeypatch.setattr(internal_api, "get", boom)
    assert bloom.reviews_by_day(datetime.datetime(2026, 11, 12, tzinfo=CT), CT) is None


# ── what gets scanned ───────────────────────────────────────────────────────


def _day_doc(day, counts, final=False, computed_at=None):
    return {"id": f"rd-{day}", "data": {
        "kind": main._REVIEW_DAY_KIND, "day": day, "counts": counts, "final": final,
        "computed_at": computed_at or datetime.datetime.now(UTC).isoformat()}}


def test_days_needing_sync(storage):
    now = datetime.datetime(2026, 11, 13, 12, 0, tzinfo=CT)
    fresh = (now - datetime.timedelta(minutes=2)).isoformat()
    storage.extend([
        _day_doc("2026-11-11", {}, final=True),
        _day_doc("2026-11-12", {}, final=False),   # scanned mid-day: redo once
        _day_doc("2026-11-13", {}, computed_at=fresh),
    ])
    keys = ["2026-10-01", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13", "2026-11-14"]
    # Too old, final, fresh and future are all skipped.
    assert main._review_days_needing_sync(keys, now=now) == ["2026-11-10", "2026-11-12"]
    stale = now + datetime.timedelta(seconds=main._REVIEW_SYNC_TTL + 300)
    assert "2026-11-13" in main._review_days_needing_sync(keys, now=stale)


def test_sync_stores_each_day_and_marks_past_days_final(storage, monkeypatch):
    today = datetime.datetime.now(CT).date()
    yesterday = (today - datetime.timedelta(days=1)).isoformat()
    monkeypatch.setattr(main.bloom, "reviews_by_day", lambda since, tz: {
        today.isoformat(): {1: {"A": 5}}, yesterday: {1: {"A": 2, "X": 1}}})
    main._sync_review_days([yesterday, today.isoformat()])
    docs = {d["data"]["day"]: d["data"] for d in storage}
    assert docs[yesterday]["counts"] == {"1": {"A": 2, "X": 1}} and docs[yesterday]["final"]
    assert docs[today.isoformat()]["counts"] == {"1": {"A": 5}}
    assert not docs[today.isoformat()]["final"]


def test_failed_scan_stores_nothing(storage, monkeypatch):
    monkeypatch.setattr(main.bloom, "reviews_by_day", lambda since, tz: None)
    main._sync_review_days([datetime.datetime.now(CT).date().isoformat()])
    assert storage == []


# ── the leaderboard ─────────────────────────────────────────────────────────


def test_leaderboard_counts_learned_reviewers_from_bloom(client, storage, monkeypatch):
    now = datetime.datetime.now(CT)
    week = main._iso_week_key(now)
    mon = main._week_start_utc(now).date().isoformat()
    monkeypatch.setattr(main, "_run_in_background", lambda fn, *a: None)  # no live scan
    storage.extend([
        {"id": "fa1", "data": {"kind": main._FA_ID_KIND, "reviewer_email": SAM, "fa_id": 1}},
        {"id": "fa2", "data": {"kind": main._FA_ID_KIND, "reviewer_email": ALEX, "fa_id": 2}},
        # Sam checked off 3 jobs worth 40; Bloom says he reviewed 70, 60 approved.
        {"id": "t1", "data": {"kind": "review_tally", "reviewer_email": SAM, "week": week,
                              "total": 3, "days": {mon: 3}, "resp_total": 40,
                              "resp_days": {mon: 40}}},
        _day_doc(mon, {"1": {"A": 60, "X": 10}, "2": {"A": 25}, "999": {"A": 500}}),
    ])
    body = client.get("/api/shifts/leaderboard").get_json()["data"]
    by = {r["email"]: r for r in body["reviewers"]}
    assert by[SAM]["responses"] == 70 and by[SAM]["resp_days"][0] == 70
    assert by[SAM]["approved"] == 60
    assert by[SAM]["total"] == 3  # jobs still come from check-offs
    # Alex never checked anything off but did review: he's on the board now.
    assert by[ALEX]["responses"] == 25 and by[ALEX]["total"] == 0


def test_unlearned_reviewer_keeps_check_off_count(client, storage, monkeypatch):
    now = datetime.datetime.now(CT)
    mon = main._week_start_utc(now).date().isoformat()
    monkeypatch.setattr(main, "_run_in_background", lambda fn, *a: None)
    storage.extend([
        {"id": "fa1", "data": {"kind": main._FA_ID_KIND, "reviewer_email": ALEX, "fa_id": 2}},
        {"id": "t1", "data": {"kind": "review_tally", "reviewer_email": SAM,
                              "week": main._iso_week_key(now), "total": 3, "days": {mon: 3},
                              "resp_total": 40, "resp_days": {mon: 40}}},
        _day_doc(mon, {"1": {"A": 70}}),
    ])
    body = client.get("/api/shifts/leaderboard").get_json()["data"]
    (sam,) = [r for r in body["reviewers"] if r["email"] == SAM]
    assert sam["responses"] == 40 and sam["approved"] is None


def test_opening_the_leaderboard_starts_a_scan(client, storage, monkeypatch):
    storage.append(
        {"id": "fa1", "data": {"kind": main._FA_ID_KIND, "reviewer_email": SAM, "fa_id": 1}})
    started = []
    monkeypatch.setattr(main, "_run_in_background", lambda fn, *a: started.append((fn, a)))
    client.get("/api/shifts/leaderboard")
    ((fn, (days,)),) = started
    assert fn is main._sync_review_days
    assert datetime.datetime.now(CT).date().isoformat() in days
