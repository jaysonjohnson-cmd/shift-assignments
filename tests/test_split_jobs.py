"""Split JIDs: one large job handed to several reviewers at once.

Collection Review keeps the sharers off each other's responses, so the tool's
job is to let the job sit on more than one shift without the one-owner safety
nets (publish dedupe, the unreviewed-responses guard, leaderboard credit)
either dropping it or paying it out once per sharer.
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
AUSTIN = "austin@storesight.com"


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
        yield c, token_file


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


def _rows_of(docs, email):
    return [r for d in docs
            if d["data"].get("kind") == "reviewer_shift"
            and d["data"].get("reviewer_email") == email
            for r in d["data"]["rows"]]


def _job(job_id, n=120, split=None):
    row = {"jobId": job_id, "projectId": "P" + job_id, "name": "Job " + job_id,
           "unreviewedCount": n}
    if split:
        row["splitWith"] = split
    return row


# ---------- publish ----------

def test_split_job_is_published_to_every_sharer(client, storage):
    c, token_file = client
    token_file.write_text(_token(roles.ROOT_ADMIN_EMAIL))
    split = [SAM, ALEX]
    resp = c.post("/api/shifts/publish", json={"assignments": {
        SAM: [_job("BIG", split=split)],
        ALEX: [_job("BIG", split=split), _job("J2", 5)],
    }})
    assert resp.status_code == 201, resp.get_json()
    assert [r["jobId"] for r in _rows_of(storage, SAM)] == ["BIG"]
    assert [r["jobId"] for r in _rows_of(storage, ALEX)] == ["BIG", "J2"]
    assert _rows_of(storage, SAM)[0]["splitWith"] == sorted(split)


def test_unsplit_duplicate_is_still_deduped(client, storage):
    c, token_file = client
    token_file.write_text(_token(roles.ROOT_ADMIN_EMAIL))
    resp = c.post("/api/shifts/publish", json={"assignments": {
        SAM: [_job("BIG")], ALEX: [_job("BIG")]}})
    assert resp.status_code == 201
    holders = [e for e in (SAM, ALEX) if _rows_of(storage, e)]
    assert holders == [ALEX]  # first alphabetically keeps it, as before


def test_split_naming_someone_else_is_not_a_split(client, storage):
    """A copy whose splitWith leaves out its own reviewer is an accident, not a share."""
    c, token_file = client
    token_file.write_text(_token(roles.ROOT_ADMIN_EMAIL))
    resp = c.post("/api/shifts/publish", json={"assignments": {
        SAM: [_job("BIG", split=[SAM, ALEX])],
        AUSTIN: [_job("BIG", split=[SAM, ALEX])],
    }})
    assert resp.status_code == 201
    # Austin's copy loses its split, so BIG falls back to one owner.
    holders = [e for e in (SAM, ALEX, AUSTIN) if _rows_of(storage, e)]
    assert holders == [AUSTIN]
    assert "splitWith" not in _rows_of(storage, AUSTIN)[0]


def test_merge_publish_splits_a_job_someone_already_holds(client, storage):
    c, token_file = client
    token_file.write_text(_token(roles.ROOT_ADMIN_EMAIL))
    r1 = c.post("/api/shifts/publish", json={"assignments": {SAM: [_job("BIG")]}})
    assert r1.status_code == 201
    split = [SAM, ALEX]
    r2 = c.post("/api/shifts/publish", json={"assignments": {
        SAM: [_job("BIG", split=split)], ALEX: [_job("BIG", split=split)]}})
    assert r2.status_code == 201, r2.get_json()
    assert [r["jobId"] for r in _rows_of(storage, ALEX)] == ["BIG"]
    sam_rows = _rows_of(storage, SAM)
    assert [r["jobId"] for r in sam_rows] == ["BIG"]
    assert sam_rows[0]["splitWith"] == sorted(split)


def test_merge_publish_never_splits_without_the_holder(client, storage):
    """Sam holds BIG; a split between Alex and Austin must not take it from him."""
    c, token_file = client
    token_file.write_text(_token(roles.ROOT_ADMIN_EMAIL))
    c.post("/api/shifts/publish", json={"assignments": {SAM: [_job("BIG")]}})
    split = [ALEX, AUSTIN]
    r2 = c.post("/api/shifts/publish", json={"assignments": {
        ALEX: [_job("BIG", split=split), _job("J2", 5)],
        AUSTIN: [_job("BIG", split=split), _job("J3", 5)]}})
    assert r2.status_code == 201
    assert [r["jobId"] for r in _rows_of(storage, ALEX)] == ["J2"]
    assert [r["jobId"] for r in _rows_of(storage, AUSTIN)] == ["J3"]


# ---------- completion ----------

def _live_split_shift(storage, monkeypatch, live_unreviewed, reviewed_total=None):
    split = [SAM, ALEX]
    for email in split:
        storage.append({"id": f"rs-{email}", "data": {
            "kind": "reviewer_shift", "shift_snapshot_id": "snap1",
            "reviewer_email": email, "rows": [_job("BIG", 100, split=split)]}})
    monkeypatch.setattr(main, "_latest_snapshot", lambda **k: (
        "snap1", {"published_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}))
    monkeypatch.setattr(main, "_live_unreviewed_count", lambda job_id, force=False: live_unreviewed)
    monkeypatch.setattr(main.bloom, "count_reviewed_since", lambda job_id, since: reviewed_total)
    monkeypatch.setattr(main, "_auto_refill_reviewer", lambda *a, **k: [])
    credited = []
    monkeypatch.setattr(main, "_record_review_event",
                        lambda email, at, responses=0: credited.append((email, responses)))
    return credited


def test_first_sharer_confirms_their_share_while_teammate_works(client, storage, monkeypatch):
    """Bloom can't say who reviewed what, so a sharer checking off with responses
    left is asked to confirm their share is done — never waved through, which
    let a sharer check off a job with nothing reviewed."""
    c, token_file = client
    credited = _live_split_shift(storage, monkeypatch, live_unreviewed=40, reviewed_total=60)
    token_file.write_text(_token(SAM))
    blocked = c.post("/api/shifts/my/complete", json={"job_id": "BIG"})
    assert blocked.status_code == 409
    assert blocked.get_json()["split"] is True
    assert blocked.get_json()["unreviewed"] == 40
    resp = c.post("/api/shifts/my/complete", json={"job_id": "BIG", "override": True})
    assert resp.status_code == 201, resp.get_json()
    assert resp.get_json()["data"]["overridden"] is True
    # 60 reviewed so far, Alex still on it: Sam takes half.
    assert credited == [(SAM, 30)]


def test_last_sharer_is_still_held_to_the_guard(client, storage, monkeypatch):
    c, token_file = client
    _live_split_shift(storage, monkeypatch, live_unreviewed=40, reviewed_total=60)
    token_file.write_text(_token(SAM))
    assert c.post("/api/shifts/my/complete",
                  json={"job_id": "BIG", "override": True}).status_code == 201
    token_file.write_text(_token(ALEX))
    resp = c.post("/api/shifts/my/complete", json={"job_id": "BIG"})
    assert resp.status_code == 409
    assert resp.get_json()["unreviewed"] == 40
    assert "split" not in resp.get_json()  # last one on it: the normal block


def test_last_sharer_is_credited_the_remainder(client, storage, monkeypatch):
    c, token_file = client
    credited = _live_split_shift(storage, monkeypatch, live_unreviewed=40, reviewed_total=60)
    token_file.write_text(_token(SAM))
    c.post("/api/shifts/my/complete", json={"job_id": "BIG", "override": True})
    # Alex clears the rest: 100 reviewed in all, 30 already credited to Sam.
    monkeypatch.setattr(main, "_live_unreviewed_count", lambda job_id, force=False: 0)
    monkeypatch.setattr(main.bloom, "count_reviewed_since", lambda job_id, since: 100)
    token_file.write_text(_token(ALEX))
    assert c.post("/api/shifts/my/complete", json={"job_id": "BIG"}).status_code == 201
    assert credited == [(SAM, 30), (ALEX, 70)]


def test_my_tasks_names_split_partners(client, storage, monkeypatch):
    c, token_file = client
    _live_split_shift(storage, monkeypatch, live_unreviewed=40)
    monkeypatch.setattr(main, "_live_counts_by_job", lambda: None)
    token_file.write_text(_token(SAM))
    rows = c.get("/api/shifts/my").get_json()["data"]["rows"]
    assert rows[0]["splitPartners"] == [{"email": ALEX, "name": "Alex", "done": False}]
    assert rows[0]["splitShare"] == 50


def test_split_share_holds_while_the_job_is_worked(client, storage, monkeypatch):
    """Shares come from the count at hand-out: reviewing doesn't shrink them."""
    c, token_file = client
    _live_split_shift(storage, monkeypatch, live_unreviewed=40)
    monkeypatch.setattr(main, "_live_counts_by_job", lambda: {
        "BIG": {"reviewable": 40, "new": 40, "aged": 0, "possibleReject": 0}})
    token_file.write_text(_token(ALEX))
    row = c.get("/api/shifts/my").get_json()["data"]["rows"][0]
    assert row["unreviewedCount"] == 40
    assert row["splitShare"] == 50


def test_uneven_split_gives_the_extra_response_to_one_sharer():
    assert main._split_shares(151, [SAM, ALEX]) == {ALEX: 76, SAM: 75}


def test_merge_split_cuts_every_share_from_the_same_total(client, storage):
    c, token_file = client
    token_file.write_text(_token(roles.ROOT_ADMIN_EMAIL))
    c.post("/api/shifts/publish", json={"assignments": {SAM: [_job("BIG", 90)]}})
    split = [SAM, ALEX]
    c.post("/api/shifts/publish", json={"assignments": {
        SAM: [_job("BIG", 150, split=split)], ALEX: [_job("BIG", 150, split=split)]}})
    assert _rows_of(storage, SAM)[0]["unreviewedCount"] == 150


def test_partner_who_left_the_job_drops_off_the_split(client, storage, monkeypatch):
    c, token_file = client
    _live_split_shift(storage, monkeypatch, live_unreviewed=40)
    monkeypatch.setattr(main, "_live_counts_by_job", lambda: None)
    storage[:] = [d for d in storage if d["id"] != f"rs-{ALEX}"]  # Alex closed out
    token_file.write_text(_token(SAM))
    rows = c.get("/api/shifts/my").get_json()["data"]["rows"]
    assert "splitPartners" not in rows[0]
    assert "splitWith" not in rows[0]
    assert "splitShare" not in rows[0]
