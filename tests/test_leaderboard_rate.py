"""Responses per hour on the leaderboard.

A reviewer's hours for a day run from their shift start to their last
check-off. The start is stamped on their reviewer_shift docs as `assigned_at`
at publish and carried through re-publishes and top-ups; each check-off widens
that day's [start, last] span on the weekly tally. Days logged before spans were
recorded are left out of the rate rather than divided by hours that don't
include them.
"""

import datetime
import os

import jwt
import pytest

os.environ["LOCAL_DEV"] = "1"

import internal_api  # noqa: E402
import main  # noqa: E402
import roles  # noqa: E402

SAM = "sam@storesight.com"
ALEX = "alex@storesight.com"


def _token(email):
    now = datetime.datetime.now(datetime.timezone.utc)
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
    """In-memory Storage: post/put/delete write to `docs`, list reads it."""
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

    def delete(path):
        doc_id = path.rsplit("/", 1)[-1]
        docs[:] = [d for d in docs if d["id"] != doc_id]
        return {"data": {}}

    monkeypatch.setattr(internal_api, "post", post)
    monkeypatch.setattr(internal_api, "put", put)
    monkeypatch.setattr(internal_api, "delete", delete)
    monkeypatch.setattr(roles, "list_docs_by_kind", lambda kind, force=False: [
        d for d in reversed(docs) if (d["data"] or {}).get("kind") == kind])
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [
        {"id": "1", "name": "Sam", "email": SAM},
        {"id": "2", "name": "Alex", "email": ALEX},
    ])
    return docs


def _job(job_id, n=10):
    return {"jobId": job_id, "projectId": "P" + job_id, "name": "Job " + job_id,
            "unreviewedCount": n}


def _shift_docs(docs, email):
    return [d["data"] for d in docs
            if d["data"].get("kind") == "reviewer_shift"
            and d["data"].get("reviewer_email") == email]


def _tally(docs):
    (t,) = [d["data"] for d in docs if d["data"].get("kind") == "review_tally"]
    return t


# ── Shift start ─────────────────────────────────────────────────────────────


def test_republishing_a_reviewer_keeps_their_original_start(client, storage):
    client.post("/api/shifts/publish", json={"assignments": {SAM: [_job("S1")]}})
    early = "2026-10-27T13:00:00+00:00"
    for d in _shift_docs(storage, SAM):
        d["assigned_at"] = early

    r = client.post("/api/shifts/publish", json={
        "assignments": {SAM: [_job("S1"), _job("S2")], ALEX: [_job("A1")]}})
    assert r.status_code == 201, r.get_json()

    assert {d["assigned_at"] for d in _shift_docs(storage, SAM)} == {early}
    # A teammate joining later starts when they were published, not at 8 AM.
    (alex,) = _shift_docs(storage, ALEX)
    assert alex["assigned_at"] > early


def test_shift_start_is_the_earliest_stamp_else_the_publish_time(storage):
    snap = {"published_at": "2026-10-27T13:00:00+00:00"}
    storage.extend([
        {"id": "a", "data": {"kind": "reviewer_shift", "shift_snapshot_id": "s1",
                             "reviewer_email": SAM, "assigned_at": "2026-10-27T15:00:00+00:00"}},
        {"id": "b", "data": {"kind": "reviewer_shift", "shift_snapshot_id": "s1",
                             "reviewer_email": SAM, "assigned_at": "2026-10-27T14:00:00+00:00"}},
        # Written before the stamp existed.
        {"id": "c", "data": {"kind": "reviewer_shift", "shift_snapshot_id": "s1",
                             "reviewer_email": ALEX}},
    ])
    assert main._reviewer_shift_start("s1", snap, SAM) == "2026-10-27T14:00:00+00:00"
    assert main._reviewer_shift_start("s1", snap, ALEX) == snap["published_at"]


# ── Recording hours on the tally ────────────────────────────────────────────


def test_check_offs_widen_the_days_span(storage):
    start = "2026-10-27T13:00:00+00:00"
    main._record_review_event(SAM, "2026-10-27T14:30:00+00:00", 40, shift_start=start)
    main._record_review_event(SAM, "2026-10-27T17:00:00+00:00", 60, shift_start=start)
    assert _tally(storage)["active_days"]["2026-10-27"] == [
        "2026-10-27T13:00:00+00:00", "2026-10-27T17:00:00+00:00"]


def test_a_start_after_the_check_off_is_clamped(storage):
    main._record_review_event(SAM, "2026-10-27T14:00:00+00:00", 5,
                              shift_start="2026-10-27T15:00:00+00:00")
    assert _tally(storage)["active_days"]["2026-10-27"] == [
        "2026-10-27T14:00:00+00:00", "2026-10-27T14:00:00+00:00"]


# ── Leaderboard ─────────────────────────────────────────────────────────────


def test_leaderboard_reports_hours_and_leaves_untimed_days_out(client, storage):
    now = datetime.datetime.now(datetime.timezone.utc)
    week_start = main._week_start_utc(now).date()
    mon, tue = week_start.isoformat(), (week_start + datetime.timedelta(days=1)).isoformat()
    storage.extend([
        # Two docs for the same week (a race) — spans merge like the counts.
        {"id": "t1", "data": {
            "kind": "review_tally", "reviewer_email": SAM, "week": main._iso_week_key(now),
            "total": 3, "days": {mon: 1, tue: 2}, "resp_total": 500,
            "resp_days": {mon: 100, tue: 400},
            # Monday predates hours, so its 100 responses can't be rated.
            "active_days": {tue: [f"{tue}T13:00:00+00:00", f"{tue}T15:00:00+00:00"]}}},
        {"id": "t2", "data": {
            "kind": "review_tally", "reviewer_email": SAM, "week": main._iso_week_key(now),
            "total": 0, "days": {}, "resp_total": 0, "resp_days": {},
            "active_days": {tue: [f"{tue}T14:00:00+00:00", f"{tue}T17:00:00+00:00"]}}},
    ])

    body = client.get("/api/shifts/leaderboard").get_json()["data"]
    (sam,) = body["reviewers"]
    assert sam["hours"] == 4.0  # 13:00–17:00 Tuesday
    assert sam["hours_days"][0] == 0 and sam["hours_days"][1] == 4.0
    assert sam["timed_responses"] == 400
    assert body["team_hours"] == 4.0 and body["team_timed_responses"] == 400


def test_span_hours_counts_overlap_once():
    spans = [["2026-10-27T13:00:00+00:00", "2026-10-28T00:30:00+00:00"],
             ["2026-10-27T13:00:00+00:00", "2026-10-27T23:00:00+00:00"],
             ["2026-10-28T13:00:00+00:00", "2026-10-28T14:00:00+00:00"]]
    assert main._span_hours(spans) == pytest.approx(11.5 + 1)
