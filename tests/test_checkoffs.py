"""Team Assignments Check-offs: who checked off what, and when.

Built from completion docs, not the reviewers' rows — close-out deletes the
rows, and a manager still needs to see what that reviewer checked off.
"""

import datetime
import os

import jwt
import pytest
import requests

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

    def delete(path):
        doc_id = path.rsplit("/", 1)[-1]
        docs[:] = [d for d in docs if d["id"] != doc_id]
        return {"data": {}}

    def get(path, params=None):
        doc_id = path.rsplit("/", 1)[-1]
        for d in docs:
            if d["id"] == doc_id:
                return {"data": {"id": doc_id, "data": d["data"]}}
        resp = requests.Response()
        resp.status_code = 404
        raise requests.exceptions.HTTPError(response=resp)

    def put(path, json=None):
        doc_id = path.rsplit("/", 1)[-1]
        for d in docs:
            if d["id"] == doc_id:
                d["data"] = json["data"]
        return {"data": {"id": doc_id}}

    monkeypatch.setattr(internal_api, "post", post)
    monkeypatch.setattr(internal_api, "get", get)
    monkeypatch.setattr(internal_api, "put", put)
    monkeypatch.setattr(internal_api, "delete", delete)
    monkeypatch.setattr(roles, "cache_upsert_doc", lambda *a, **k: None)
    monkeypatch.setattr(roles, "cache_remove_doc", lambda *a, **k: None)
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


# ---------- the two-week log ----------

def _today():
    return datetime.datetime.now(main._SHIFT_TZ).date()


def _log(docs, email, job_id, day, completion_id=None, **extra):
    docs.append({"id": f"log-{job_id}-{day}", "data": {
        "kind": "checkoff_log", "completion_id": completion_id or f"gone-{job_id}",
        "reviewer_email": email, "job_id": job_id, "local_date": day.isoformat(),
        "completed_at": f"{day.isoformat()}T15:00:00+00:00", **extra}})


def _check_off(c, token_file, storage, monkeypatch, job_id="J3"):
    storage.append({"id": "rs1", "data": {
        "kind": "reviewer_shift", "shift_snapshot_id": "snap1",
        "reviewer_email": SAM, "rows": [
            {"jobId": job_id, "projectId": "P3", "name": "Shelf photos"}]}})
    monkeypatch.setattr(main, "_live_unreviewed_count", lambda job_id, force=False: 0)
    monkeypatch.setattr(main, "_auto_refill_reviewer", lambda *a, **k: [])
    token_file.write_text(_token(SAM))
    assert c.post("/api/shifts/my/complete", json={"job_id": job_id}).status_code == 201
    token_file.write_text(_token(BOSS))


def _kind(storage, kind):
    return [d for d in storage if d["data"]["kind"] == kind]


def _entries(storage):
    """Every logged checkmark across the day docs, with its doc's reviewer/day."""
    return [{**e, "reviewer_email": d["data"]["reviewer_email"],
             "local_date": d["data"]["local_date"]}
            for d in _kind(storage, "checkoff_day") for e in d["data"]["entries"]]


def test_checking_off_writes_a_log_entry(client, storage, monkeypatch):
    c, token_file = client
    _check_off(c, token_file, storage, monkeypatch)
    assert _kind(storage, "checkoff_log") == []
    (day_doc,) = _kind(storage, "checkoff_day")
    (entry,) = _entries(storage)
    (comp,) = _kind(storage, "completion")
    assert entry["completion_id"] == comp["id"]
    assert entry["local_date"] == _today().isoformat()
    assert entry["reviewer_email"] == SAM
    assert entry["job_name"] == "Shelf photos"
    assert [x["jobId"] for x in _checkoffs(c)] == ["J3"]  # listed once, not twice


def test_a_reviewers_day_shares_one_doc(client, storage, monkeypatch):
    """The point of the day doc: N checkmarks are one Storage doc, not N."""
    c, token_file = client
    _check_off(c, token_file, storage, monkeypatch, job_id="J3")
    _check_off(c, token_file, storage, monkeypatch, job_id="J4")
    assert len(_kind(storage, "checkoff_day")) == 1
    assert sorted(e["job_id"] for e in _entries(storage)) == ["J3", "J4"]


def test_a_full_day_doc_starts_another(storage, monkeypatch):
    monkeypatch.setattr(main, "_CHECKOFF_DAY_MAX_BYTES", 400)
    at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for i in range(4):
        main._log_checkoff(f"c{i}", {"reviewer_email": SAM, "job_id": f"J{i}",
                                     "completed_at": at, "job_name": "x" * 60})
    assert len(_kind(storage, "checkoff_day")) > 1
    assert sorted(e["completion_id"] for e in _entries(storage)) == ["c0", "c1", "c2", "c3"]


def test_logging_the_same_completion_twice_keeps_one(storage):
    at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for _ in range(2):
        main._log_checkoff("c1", {"reviewer_email": SAM, "job_id": "J1", "completed_at": at})
    assert len(_entries(storage)) == 1


def test_log_survives_the_shift_being_cleared(client, storage, monkeypatch):
    c, token_file = client
    _check_off(c, token_file, storage, monkeypatch)
    storage[:] = [d for d in storage if d["data"]["kind"] == "checkoff_day"]
    (only,) = _checkoffs(c)
    assert only["reviewerEmail"] == SAM and only["jobName"] == "Shelf photos"


def test_unchecking_removes_the_log_entry(client, storage, monkeypatch):
    c, token_file = client
    _check_off(c, token_file, storage, monkeypatch, job_id="J3")
    _check_off(c, token_file, storage, monkeypatch, job_id="J4")
    token_file.write_text(_token(SAM))
    c.delete("/api/shifts/my/complete/J3")
    assert [e["job_id"] for e in _entries(storage)] == ["J4"]
    c.delete("/api/shifts/my/complete/J4")
    assert _kind(storage, "checkoff_day") == []  # an emptied day doc is deleted


def test_unchecking_removes_a_legacy_log_entry(client, storage, monkeypatch):
    c, token_file = client
    _check_off(c, token_file, storage, monkeypatch)
    (comp,) = _kind(storage, "completion")
    storage[:] = [d for d in storage if d["data"]["kind"] != "checkoff_day"]
    _log(storage, SAM, "J3", _today(), completion_id=comp["id"])
    token_file.write_text(_token(SAM))
    c.delete("/api/shifts/my/complete/J3")
    assert _kind(storage, "checkoff_log") == []


def test_an_earlier_day_reads_from_the_log(client, storage):
    c, _ = client
    yesterday = _today() - datetime.timedelta(days=1)
    _log(storage, ALEX, "J8", yesterday, job_name="Old job", overridden=True)
    _log(storage, SAM, "J9", _today() - datetime.timedelta(days=2))
    storage.append({"id": "day-1", "data": {
        "kind": "checkoff_day", "reviewer_email": SAM,
        "local_date": yesterday.isoformat(), "entries": [
            {"completion_id": "cX", "job_id": "J10", "job_name": "New shape",
             "completed_at": f"{yesterday.isoformat()}T16:00:00+00:00"}]}})
    data = c.get(f"/api/shifts/checkoffs?date={yesterday.isoformat()}").get_json()["data"]
    by_job = {x["jobId"]: x for x in data["checkoffs"]}
    assert set(by_job) == {"J8", "J10"}
    assert by_job["J8"]["jobName"] == "Old job" and by_job["J8"]["overridden"] is True
    assert by_job["J10"]["jobName"] == "New shape" and by_job["J10"]["reviewerEmail"] == SAM
    assert by_job["J8"]["unreviewedNow"] is None  # live counts are today only
    assert data["window"]["newest"] == _today().isoformat()


def test_a_migrated_entry_in_both_shapes_is_listed_once(client, storage):
    c, _ = client
    yesterday = _today() - datetime.timedelta(days=1)
    _log(storage, SAM, "J8", yesterday, completion_id="c8")
    storage.append({"id": "day-1", "data": {
        "kind": "checkoff_day", "reviewer_email": SAM,
        "local_date": yesterday.isoformat(), "entries": [
            {"completion_id": "c8", "job_id": "J8",
             "completed_at": f"{yesterday.isoformat()}T15:00:00+00:00"}]}})
    data = c.get(f"/api/shifts/checkoffs?date={yesterday.isoformat()}").get_json()["data"]
    assert [x["jobId"] for x in data["checkoffs"]] == ["J8"]


def test_dates_outside_two_weeks_are_refused(client, storage):
    c, _ = client
    too_old = _today() - datetime.timedelta(days=14)
    assert c.get(f"/api/shifts/checkoffs?date={too_old.isoformat()}").status_code == 400
    assert c.get("/api/shifts/checkoffs?date=nope").status_code == 400
    oldest = _today() - datetime.timedelta(days=13)
    assert c.get(f"/api/shifts/checkoffs?date={oldest.isoformat()}").status_code == 200


def test_purge_drops_log_entries_older_than_two_weeks(storage):
    _log(storage, SAM, "OLD", _today() - datetime.timedelta(days=14))
    _log(storage, SAM, "KEEP", _today() - datetime.timedelta(days=13))
    for days, doc_id in ((14, "day-old"), (13, "day-keep")):
        storage.append({"id": doc_id, "data": {
            "kind": "checkoff_day", "reviewer_email": SAM, "entries": [],
            "local_date": (_today() - datetime.timedelta(days=days)).isoformat()}})
    main._purge_stale_shift_docs()
    assert [d["data"]["job_id"] for d in _kind(storage, "checkoff_log")] == ["KEEP"]
    assert [d["id"] for d in _kind(storage, "checkoff_day")] == ["day-keep"]
