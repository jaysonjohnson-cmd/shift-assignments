"""Options are team-wide, except for _OWN_OPTIONS_REVIEWERS.

They were per reviewer for a while: each reviewer's top-ups followed the
options they were last published with. In practice the team is run on one
set of options, and per-reviewer stamps meant turning Retail Pipeline on for
a newly added reviewer left everyone published earlier topping up with any
work (2026-10-28). The snapshot now holds the team's options, set by any
publish that includes a teammate; only the exempt reviewers keep their own.
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
OWN = "jayson.johnson@storesight.com"


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
        {"id": "3", "name": "Own", "email": OWN},
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


def _team_flags(docs):
    snaps = [d["data"] for d in docs if d["data"].get("kind") == "shift_snapshot"]
    assert len(snaps) == 1
    return snaps[0].get("prioritization_flags")


def test_exempt_set_is_only_jayson():
    assert main._OWN_OPTIONS_REVIEWERS == frozenset({OWN})


def test_a_teammate_publish_sets_the_team_options(client, storage):
    client.post("/api/shifts/publish", json={
        "assignments": {SAM: [_job("S1")]}, "flags": ORDINARY})
    r = client.post("/api/shifts/publish", json={
        "assignments": {ALEX: [_job("A1")]}, "flags": SPECIAL})
    assert r.status_code == 201, r.get_json()
    # Sam was published earlier on ordinary work, and now follows the team.
    assert _team_flags(storage) == SPECIAL


def test_publishing_only_the_exempt_reviewer_leaves_the_team_alone(client, storage):
    client.post("/api/shifts/publish", json={
        "assignments": {SAM: [_job("S1")]}, "flags": SPECIAL})
    r = client.post("/api/shifts/publish", json={
        "assignments": {OWN: [_job("J1")]}, "flags": ORDINARY})
    assert r.status_code == 201, r.get_json()
    assert _team_flags(storage) == SPECIAL
    # Their own options are still stamped on their docs, for their top-ups.
    assert _flags_of(storage, OWN) == [ORDINARY]


def test_a_shift_started_by_the_exempt_reviewer_has_no_team_options(client, storage):
    r = client.post("/api/shifts/publish", json={
        "assignments": {OWN: [_job("J1")]}, "flags": SPECIAL})
    assert r.status_code == 201, r.get_json()
    assert _team_flags(storage) == {}
