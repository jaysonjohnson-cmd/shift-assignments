"""Team Assignments Check-offs: who checked off what, and when.

Built from completion docs, not the reviewers' rows — close-out deletes the
rows, and a manager still needs to see what that reviewer checked off.
"""

import datetime
import os

import jwt
import pytest

os.environ["LOCAL_DEV"] = "1"

import internal_api  # noqa: E402
import main  # noqa: E402
import roles  # noqa: E402

BOSS = "boss@storesight.com"
SAM = "sam@storesight.com"
ALEX = "alex@storesight.com"
LEAD = "lead@storesight.com"


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
    token_file.write_text(_token(BOSS))
    monkeypatch.setattr("main._dev_token_path", lambda: token_file)
    main.app.config["TESTING"] = True
    with main.app.test_client() as c:
        yield c, token_file


@pytest.fixture
def storage(monkeypatch):
    docs = []

    def post(path, json=None):
        doc_id = f"doc-{len(docs) + 1}"
        docs.append({"id": doc_id, "data": json["data"]})
        return {"data": {"id": doc_id}}

    monkeypatch.setattr(internal_api, "post", post)
    monkeypatch.setattr(roles, "list_docs_by_kind", lambda kind, force=False: [
        d for d in reversed(docs) if (d["data"] or {}).get("kind") == kind])
    monkeypatch.setattr(roles, "is_admin", lambda email: email == BOSS)
    monkeypatch.setattr(roles, "is_admin_or_lead", lambda email: email in (BOSS, LEAD))
    monkeypatch.setattr(roles, "list_reviewers", lambda: [
        {"id": "1", "name": "Sam", "email": SAM, "color": "#ff0000"},
        {"id": "2", "name": "Alex", "email": ALEX},
    ])
    monkeypatch.setattr(main, "_latest_snapshot", lambda **k: (
        "snap1", {"published_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}))
    monkeypatch.setattr(main.bloom, "fetch_prioritized_jobs", lambda *a, **k: [
        {"jobId": "J1", "name": "Feed name J1", "unreviewedCount": 4}])
    return docs


def _completion(docs, email, job_id, at, **extra):
    docs.append({"id": f"c-{job_id}", "data": {
        "kind": "completion", "reviewer_email": email, "job_id": job_id,
        "shift_snapshot_id": "snap1", "completed_at": at, **extra}})


def _checkoffs(c):
    return c.get("/api/shifts/checkoffs").get_json()["data"]["checkoffs"]


def test_lists_checkoffs_newest_first_with_who_and_how(client, storage):
    c, _ = client
    storage.append({"id": "rs1", "data": {
        "kind": "reviewer_shift", "shift_snapshot_id": "snap1",
        "reviewer_email": SAM, "rows": [
            {"jobId": "J2", "projectId": "P2", "name": "Row name J2"}]}})
    _completion(storage, SAM, "J2", "2026-10-21T15:00:00+00:00")
    _completion(storage, ALEX, "J1", "2026-10-21T16:00:00+00:00",
                overridden=True, note="alt-picture bug")
    out = _checkoffs(c)
    assert [x["jobId"] for x in out] == ["J1", "J2"]
    alex, sam = out
    assert alex["reviewerName"] == "Alex"
    assert alex["overridden"] is True and alex["note"] == "alt-picture bug"
    assert alex["jobName"] == "Feed name J1" and alex["unreviewedNow"] == 4
    assert sam["jobName"] == "Row name J2" and sam["projectId"] == "P2"
    assert sam["reviewerColor"] == "#ff0000" and sam["overridden"] is False
    assert sam["unreviewedNow"] is None


def test_closed_out_reviewer_still_shows(client, storage):
    """No reviewer_shift doc left — the name comes off the completion itself."""
    c, _ = client
    _completion(storage, SAM, "J7", "2026-10-21T15:00:00+00:00",
                job_name="Stored name", project_id="P7")
    (only,) = _checkoffs(c)
    assert only["reviewerEmail"] == SAM
    assert only["jobName"] == "Stored name" and only["projectId"] == "P7"


def test_superseded_checkoff_is_kept_and_flagged(client, storage):
    c, _ = client
    _completion(storage, SAM, "J1", "2026-10-21T15:00:00+00:00",
                superseded_at="2026-10-21T15:30:00+00:00")
    (only,) = _checkoffs(c)
    assert only["superseded"] is True


def test_other_shifts_are_left_out(client, storage):
    c, _ = client
    storage.append({"id": "old", "data": {
        "kind": "completion", "reviewer_email": SAM, "job_id": "J1",
        "shift_snapshot_id": "snap0", "completed_at": "2026-10-20T15:00:00+00:00"}})
    assert _checkoffs(c) == []


def test_reviewers_cannot_see_it(client, storage):
    c, token_file = client
    token_file.write_text(_token(SAM))
    assert c.get("/api/shifts/checkoffs").status_code == 403


def test_leads_cannot_see_it(client, storage):
    c, token_file = client
    token_file.write_text(_token(LEAD))
    assert c.get("/api/shifts/checkoffs").status_code == 403


def test_checking_off_records_the_job_name(client, storage, monkeypatch):
    c, token_file = client
    storage.append({"id": "rs1", "data": {
        "kind": "reviewer_shift", "shift_snapshot_id": "snap1",
        "reviewer_email": SAM, "rows": [
            {"jobId": "J3", "projectId": "P3", "name": "Shelf photos"}]}})
    monkeypatch.setattr(main, "_live_unreviewed_count", lambda job_id, force=False: 0)
    monkeypatch.setattr(main, "_auto_refill_reviewer", lambda *a, **k: [])
    token_file.write_text(_token(SAM))
    assert c.post("/api/shifts/my/complete", json={"job_id": "J3"}).status_code == 201
    doc = next(d["data"] for d in storage if d["data"]["kind"] == "completion")
    assert doc["job_name"] == "Shelf photos" and doc["project_id"] == "P3"
