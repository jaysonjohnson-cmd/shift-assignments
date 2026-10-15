"""Each reviewer keeps the filters they were published with.

Filters used to live only on the shift snapshot, and every publish into the
shift overwrote them for everyone. Publishing one reviewer on Special Job
Types and then adding a teammate on ordinary work silently rescoped the first
reviewer's top-ups. The flags are now stamped on each reviewer's own
reviewer_shift docs, and refill reads those.
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


def _flags_of(docs, email):
    return [d["data"].get("prioritization_flags") for d in docs
            if d["data"].get("kind") == "reviewer_shift"
            and d["data"].get("reviewer_email") == email]


SPECIAL = {"specialJobTypes": True, "balanceByResponses": True}
ORDINARY = {"specialJobTypes": False, "balanceByResponses": True}


def test_publish_stamps_each_reviewer_with_its_own_filters(client, storage):
    r1 = client.post("/api/shifts/publish", json={
        "assignments": {SAM: [_job("S1")]}, "flags": SPECIAL})
    assert r1.status_code == 201, r1.get_json()
    # Merge-publish a teammate into the same shift with different filters.
    r2 = client.post("/api/shifts/publish", json={
        "assignments": {ALEX: [_job("A1")]}, "flags": ORDINARY})
    assert r2.status_code == 201, r2.get_json()

    assert _flags_of(storage, SAM) == [SPECIAL]
    assert _flags_of(storage, ALEX) == [ORDINARY]


def test_republishing_a_reviewer_changes_only_theirs(client, storage):
    client.post("/api/shifts/publish", json={
        "assignments": {SAM: [_job("S1")], ALEX: [_job("A1")]}, "flags": ORDINARY})
    r = client.post("/api/shifts/publish", json={
        "assignments": {SAM: [_job("S1"), _job("S2")]}, "flags": SPECIAL})
    assert r.status_code == 201, r.get_json()

    assert _flags_of(storage, SAM) == [SPECIAL]
    assert _flags_of(storage, ALEX) == [ORDINARY]
