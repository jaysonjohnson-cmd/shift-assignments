"""Unit tests for the Bloom feed + shift-snapshot + completion routes.

The Storage API and Internal Tool API are mocked so tests don't hit the live
internal-tool-api. Patterns mirror tests/test_roles.py.
"""

import datetime
import os

import jwt
import pytest
import requests

os.environ["LOCAL_DEV"] = "1"

import bloom  # noqa: E402
import internal_api  # noqa: E402
import main  # noqa: E402
import roles  # noqa: E402
from main import app  # noqa: E402


# ---------- bloom.fetch_prioritized_jobs — /api/prioritized-jobs feed ----------


def _prioritized_jobs(records):
    """Fake internal_api.get backing /api/prioritized-jobs with the given jobs."""
    def _get(path, params=None):
        if path == "/api/prioritized-jobs":
            return {"data": records}
        return {"data": []}
    return _get


def test_fetch_prioritized_jobs_maps_api_rows(monkeypatch):
    bloom.clear_cache()
    jobs = [
        {"id": 10, "project_id": 110, "priority": 1, "name": "Chair", "new": 3},
        {"id": 20, "project_id": 120, "priority": 2, "name": "Desk", "new": 1},
    ]
    monkeypatch.setattr(internal_api, "get", _prioritized_jobs(jobs))

    rows = bloom.fetch_prioritized_jobs(use_cache=False)
    # Order is preserved from the already-prioritized API.
    assert [r["id"] for r in rows] == ["10", "20"]
    assert [r["priority"] for r in rows] == [1, 2]
    assert rows[0]["unreviewedCount"] == 3
    assert rows[0]["name"] == "Chair"  # name comes straight from the API
    assert rows[0]["projectId"] == "110"
    assert rows[0]["jobId"] == "10"


def test_fetch_prioritized_jobs_uses_cache(monkeypatch):
    bloom.clear_cache()
    call_count = {"n": 0}

    base = _prioritized_jobs([
        {"id": 10, "project_id": 110, "priority": 1, "name": "Chair", "new": 1},
    ])

    def counting_get(path, params=None):
        call_count["n"] += 1
        return base(path, params)

    monkeypatch.setattr(internal_api, "get", counting_get)
    bloom.fetch_prioritized_jobs()
    first = call_count["n"]
    bloom.fetch_prioritized_jobs()  # second call, should hit cache
    assert call_count["n"] == first


def test_fetch_project_names_bulk_and_caches(monkeypatch):
    bloom.clear_cache()
    calls = []

    def fake_get(path, params=None):
        calls.append((path, dict(params or {})))
        if path == "/api/projects":
            page = (params or {}).get("page", 1)
            if page == 1:
                return {"data": [
                    {"id": 110, "name": "Nestlé Q1"},
                    {"id": 111, "name": "Pepsi FY26"},
                ]}
            return {"data": []}
        return {"data": []}

    monkeypatch.setattr(internal_api, "get", fake_get)

    names = bloom.fetch_project_names({"110", "111", "999"})
    assert names["110"] == "Nestlé Q1"
    assert names["111"] == "Pepsi FY26"
    assert names["999"] == ""  # unknown → empty
    first_calls = len(calls)
    # Second invocation hits the cache — no extra upstream fetches.
    bloom.fetch_project_names({"110", "111"})
    assert len(calls) == first_calls


def test_project_summaries_one_entry_per_project(monkeypatch):
    bloom.clear_cache()
    monkeypatch.setattr(internal_api, "get", _prioritized_jobs([
        {"id": 10, "project_id": 110, "priority": 1, "name": "A", "new": 1},
        {"id": 11, "project_id": 110, "priority": 2, "name": "B", "new": 1},
        {"id": 20, "project_id": 120, "priority": 3, "name": "C", "new": 1},
    ]))

    rows = bloom.fetch_prioritized_jobs(use_cache=False)
    summaries = bloom.project_summaries(rows)
    by_pid = {s["projectId"]: s for s in summaries}
    assert set(by_pid.keys()) == {"110", "120"}
    assert by_pid["110"]["jidCount"] == 2
    assert by_pid["120"]["jidCount"] == 1


def test_bloom_projects_route_returns_summaries(client, monkeypatch):
    c, token_file = client
    _as_admin(token_file)
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])

    monkeypatch.setattr(
        bloom,
        "fetch_prioritized_jobs",
        lambda status=None, use_cache=True: [
            {"id": "10", "projectId": "110", "projectName": "Nestlé", "unreviewedCount": 3, "oldestSubmission": "2026-04-18"},
            {"id": "11", "projectId": "110", "projectName": "Nestlé", "unreviewedCount": 1, "oldestSubmission": "2026-04-20"},
            {"id": "20", "projectId": "120", "projectName": "", "unreviewedCount": 1, "oldestSubmission": "2026-04-22"},
        ],
    )
    resp = c.get("/api/bloom/projects")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    by_pid = {d["projectId"]: d for d in data}
    assert by_pid["110"]["jidCount"] == 2
    assert by_pid["110"]["projectName"] == "Nestlé"
    assert by_pid["120"]["jidCount"] == 1


def test_bloom_projects_requires_admin(client, monkeypatch):
    c, token_file = client
    _as_reviewer(token_file, "stranger@storesight.com")
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])
    resp = c.get("/api/bloom/projects")
    assert resp.status_code == 403


def test_fetch_prioritized_jobs_skips_records_without_id(monkeypatch):
    bloom.clear_cache()
    jobs = [
        {"id": None, "project_id": 110, "priority": 1, "name": "Bogus", "new": 1},
        {"id": 10, "project_id": 110, "priority": 2, "name": "Chair", "new": 1},
    ]
    monkeypatch.setattr(internal_api, "get", _prioritized_jobs(jobs))
    rows = bloom.fetch_prioritized_jobs(use_cache=False)
    assert len(rows) == 1
    assert rows[0]["id"] == "10"




# ---------- HTTP route tests ----------


def _make_dev_token(email, name="User"):
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "email": email,
        "name": name,
        "iat": now,
        "exp": now + datetime.timedelta(hours=8),
    }
    return jwt.encode(payload, "irrelevant", algorithm="HS256")


@pytest.fixture
def client(tmp_path, monkeypatch):
    token_dir = tmp_path / ".storesight"
    token_dir.mkdir()
    token_file = token_dir / "dev-token"
    monkeypatch.setattr("main._dev_token_path", lambda: token_file)
    app.config["TESTING"] = True
    bloom.clear_cache()
    with app.test_client() as c:
        yield c, token_file


def _as_admin(token_file):
    token_file.write_text(_make_dev_token(roles.ROOT_ADMIN_EMAIL))


def _as_reviewer(token_file, email):
    token_file.write_text(_make_dev_token(email))


# /api/bloom/jobs


def test_bloom_jobs_requires_admin(client, monkeypatch):
    c, token_file = client
    _as_reviewer(token_file, "stranger@storesight.com")
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])
    resp = c.get("/api/bloom/jobs")
    assert resp.status_code == 403


def test_bloom_jobs_returns_rows(client, monkeypatch):
    c, token_file = client
    _as_admin(token_file)
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])

    monkeypatch.setattr(
        bloom,
        "fetch_prioritized_jobs",
        lambda status=None, use_cache=True: [
            {"id": "1", "projectId": "10", "priority": 1, "name": "Job"},
        ],
    )
    resp = c.get("/api/bloom/jobs")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert len(data) == 1
    assert data[0]["projectId"] == "10"


# /api/shifts/publish + /api/shifts/latest round-trip


def test_publish_then_latest_round_trip(client, monkeypatch):
    c, token_file = client
    _as_admin(token_file)
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])

    published_docs = []

    def fake_post(path, json=None):
        doc_id = f"doc-{len(published_docs) + 1}"
        published_docs.append({"id": doc_id, "data": json["data"]})
        return {"data": {"id": doc_id}}

    def fake_list_docs_by_kind(kind):
        return [d for d in reversed(published_docs) if (d["data"] or {}).get("kind") == kind]

    monkeypatch.setattr(internal_api, "post", fake_post)
    monkeypatch.setattr(roles, "list_docs_by_kind", fake_list_docs_by_kind)

    assignments = {
        "sam@storesight.com": [{"id": "1", "projectId": "10", "name": "Job A"}],
        "alex@storesight.com": [{"id": "2", "projectId": "20", "name": "Job B"}],
    }
    resp = c.post("/api/shifts/publish", json={"assignments": assignments})
    assert resp.status_code == 201, resp.get_json()
    body = resp.get_json()["data"]
    # doc-1 = shift_snapshot index; doc-2/3 = per-reviewer docs.
    assert body["id"] == "doc-1"
    assert "published_at" in body

    # Index doc has no rows — just metadata + reviewer list.
    latest = c.get("/api/shifts/latest").get_json()["data"]
    assert latest["id"] == "doc-1"
    assert latest["kind"] == "shift_snapshot"
    assert "assignments" not in latest
    assert latest["reviewer_emails"] == ["alex@storesight.com", "sam@storesight.com"]

    # One reviewer_shift doc per reviewer, linked back to the index.
    reviewer_docs = [d for d in published_docs if d["data"].get("kind") == "reviewer_shift"]
    assert len(reviewer_docs) == 2
    for rd in reviewer_docs:
        assert rd["data"]["shift_snapshot_id"] == "doc-1"
    by_email = {d["data"]["reviewer_email"]: d["data"]["rows"] for d in reviewer_docs}
    assert by_email["sam@storesight.com"] == assignments["sam@storesight.com"]
    assert by_email["alex@storesight.com"] == assignments["alex@storesight.com"]


def test_merge_publish_drops_jobs_held_by_retained_reviewers(client, monkeypatch):
    """No overlap: republishing into a live shift must not hand a job to a new
    reviewer when a reviewer who is being kept already holds it."""
    c, token_file = client
    _as_admin(token_file)
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])

    published_docs = []

    def fake_post(path, json=None):
        doc_id = f"doc-{len(published_docs) + 1}"
        published_docs.append({"id": doc_id, "data": json["data"]})
        return {"data": {"id": doc_id}}

    def fake_list_docs_by_kind(kind, force=False):
        return [d for d in reversed(published_docs) if (d["data"] or {}).get("kind") == kind]

    deleted = []
    monkeypatch.setattr(internal_api, "post", fake_post)
    monkeypatch.setattr(internal_api, "put", lambda path, json=None: {"data": {}})
    monkeypatch.setattr(internal_api, "delete", lambda path: deleted.append(path) or {"data": {}})
    monkeypatch.setattr(roles, "list_docs_by_kind", fake_list_docs_by_kind)

    # First publish: Sam gets job J1.
    r1 = c.post("/api/shifts/publish", json={"assignments": {
        "sam@storesight.com": [{"jobId": "J1", "projectId": "10", "name": "Job 1"}],
    }})
    assert r1.status_code == 201, r1.get_json()

    # Second publish adds Alex only, but the pool still contains J1 (already Sam's).
    r2 = c.post("/api/shifts/publish", json={"assignments": {
        "alex@storesight.com": [
            {"jobId": "J1", "projectId": "10", "name": "Job 1"},
            {"jobId": "J2", "projectId": "20", "name": "Job 2"},
        ],
    }})
    assert r2.status_code == 201, r2.get_json()

    # Alex must keep only J2 — J1 stays with Sam, no overlap.
    alex_docs = [
        d for d in published_docs
        if d["data"].get("kind") == "reviewer_shift"
        and d["data"].get("reviewer_email") == "alex@storesight.com"
    ]
    alex_jobs = [r["jobId"] for d in alex_docs for r in d["data"]["rows"]]
    assert alex_jobs == ["J2"]


def test_publish_compacts_rows_to_subset_needed_by_my_tasks(client, monkeypatch):
    """Oversized fields (`groupIds`, `extras`) must never reach Storage API."""
    c, token_file = client
    _as_admin(token_file)
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])

    published_docs = []

    def fake_post(path, json=None):
        doc_id = f"doc-{len(published_docs) + 1}"
        published_docs.append({"id": doc_id, "data": json["data"]})
        return {"data": {"id": doc_id}}

    monkeypatch.setattr(internal_api, "post", fake_post)

    fat_row = {
        "id": "10",
        "projectId": "P10",
        "jobId": "J10",
        "priority": 1,
        "name": "Job A",
        "unreviewedCount": 3,
        "oldestSubmission": "2026-04-20T00:00:00Z",
        # Fields that must be stripped before writing to Storage API.
        "groupIds": ["g1", "g2", "g3"],
        "extras": {"junk": "x" * 1000},
        "completedAt": None,
        "internalNote": "should not be persisted",
    }

    resp = c.post(
        "/api/shifts/publish",
        json={"assignments": {"sam@storesight.com": [fat_row]}},
    )
    assert resp.status_code == 201, resp.get_json()

    # One index + one reviewer_shift.
    assert len(published_docs) == 2
    reviewer_doc = next(d for d in published_docs if d["data"].get("kind") == "reviewer_shift")
    persisted_row = reviewer_doc["data"]["rows"][0]

    # Only the whitelisted fields survive.
    assert set(persisted_row.keys()) == {
        "id", "projectId", "jobId", "priority", "name",
        "unreviewedCount", "oldestSubmission",
    }
    # Values that did survive must match the input exactly.
    for key in persisted_row:
        assert persisted_row[key] == fat_row[key]


def test_publish_rolls_back_reviewer_docs_when_a_write_fails(client, monkeypatch):
    """A mid-publish failure must not leave an orphan index or partial fan-out."""
    c, token_file = client
    _as_admin(token_file)
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])

    posts = []
    deletes = []

    def fake_post(path, json=None):
        kind = json["data"].get("kind")
        doc_id = f"doc-{len(posts) + 1}"
        posts.append({"id": doc_id, "kind": kind})
        # First reviewer_shift write succeeds; second fails (simulates 50KB cap).
        if kind == "reviewer_shift" and sum(1 for p in posts if p["kind"] == "reviewer_shift") == 2:
            fake_resp = requests.Response()
            fake_resp.status_code = 400
            fake_resp._content = b'{"error":"document exceeds 50KB limit"}'
            raise requests.exceptions.HTTPError(response=fake_resp)
        return {"data": {"id": doc_id}}

    def fake_delete(path):
        deletes.append(path)

    monkeypatch.setattr(internal_api, "post", fake_post)
    monkeypatch.setattr(internal_api, "delete", fake_delete)

    resp = c.post(
        "/api/shifts/publish",
        json={
            "assignments": {
                "a@storesight.com": [{"id": "1", "projectId": "10"}],
                "b@storesight.com": [{"id": "2", "projectId": "20"}],
            }
        },
    )
    assert resp.status_code == 400
    assert "document exceeds 50KB limit" in resp.get_json()["error"]

    # doc-1 = index, doc-2 = reviewer-a (written), doc-3 = reviewer-b (raised).
    # Rollback deletes doc-2 then doc-1.
    assert "/api/storage/qc-shift-assignments/doc-2" in deletes
    assert "/api/storage/qc-shift-assignments/doc-1" in deletes


def test_publish_storage_error_surfaces_upstream_message(client, monkeypatch):
    """When Storage API rejects a write, the client sees the real reason."""
    c, token_file = client
    _as_admin(token_file)
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])

    def fake_post(path, json=None):
        fake_resp = requests.Response()
        fake_resp.status_code = 400
        fake_resp._content = b'{"error": "document exceeds 50KB limit"}'
        raise requests.exceptions.HTTPError(response=fake_resp)

    monkeypatch.setattr(internal_api, "post", fake_post)

    resp = c.post(
        "/api/shifts/publish",
        json={"assignments": {"sam@storesight.com": [{"id": "1"}]}},
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert "400" in body["error"]
    assert "document exceeds 50KB limit" in body["error"]


def test_http_error_response_falls_back_to_raw_text(client, monkeypatch):
    """Non-JSON error bodies (e.g. HTML from a proxy) still surface to the client."""
    c, token_file = client
    _as_admin(token_file)
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])

    def fake_list(kind, force=False):
        fake_resp = requests.Response()
        fake_resp.status_code = 502
        fake_resp._content = b"<html>Bad Gateway</html>"
        raise requests.exceptions.HTTPError(response=fake_resp)

    monkeypatch.setattr(roles, "list_docs_by_kind", fake_list)

    resp = c.get("/api/shifts/latest")
    assert resp.status_code == 502
    body = resp.get_json()
    assert "502" in body["error"]
    assert "Bad Gateway" in body["error"]


def test_http_error_response_without_upstream_body(client, monkeypatch):
    """Falls back cleanly to status-only when upstream returns no body."""
    c, token_file = client
    _as_admin(token_file)
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])

    def fake_list(kind, force=False):
        fake_resp = requests.Response()
        fake_resp.status_code = 503
        fake_resp._content = b""
        raise requests.exceptions.HTTPError(response=fake_resp)

    monkeypatch.setattr(roles, "list_docs_by_kind", fake_list)

    resp = c.get("/api/shifts/latest")
    assert resp.status_code == 503
    assert resp.get_json()["error"] == "storage api returned 503"


def test_publish_requires_admin(client, monkeypatch):
    c, token_file = client
    _as_reviewer(token_file, "nobody@storesight.com")
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])
    resp = c.post("/api/shifts/publish", json={"assignments": {}})
    assert resp.status_code == 403


def test_publish_rejects_non_dict_assignments(client, monkeypatch):
    c, token_file = client
    _as_admin(token_file)
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])
    resp = c.post("/api/shifts/publish", json={"assignments": [1, 2]})
    assert resp.status_code == 400


def test_latest_returns_null_when_no_snapshot(client, monkeypatch):
    c, token_file = client
    _as_admin(token_file)
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])
    monkeypatch.setattr(roles, "list_docs_by_kind", lambda kind: [])
    resp = c.get("/api/shifts/latest")
    assert resp.status_code == 200
    assert resp.get_json()["data"] is None


# /api/shifts/my — filters to signed-in reviewer + joins completions


def test_shifts_my_filters_and_adds_completed_at(client, monkeypatch):
    c, token_file = client
    _as_reviewer(token_file, "sam@storesight.com")
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(
        roles,
        "list_reviewers",
        lambda: [{"id": "r", "name": "Sam", "email": "sam@storesight.com"}],
    )

    snapshot = {
        "id": "snap-1",
        "data": {
            "kind": "shift_snapshot",
            "published_at": "2026-04-21T00:00:00+00:00",
            "assignments": {
                "sam@storesight.com": [
                    {"projectId": "10", "name": "A"},
                    {"projectId": "20", "name": "B"},
                ],
                "alex@storesight.com": [{"projectId": "30", "name": "C"}],
            },
        },
    }
    completion = {
        "id": "comp-1",
        "data": {
            "kind": "completion",
            "reviewer_email": "sam@storesight.com",
            "project_id": "10",
            "shift_snapshot_id": "snap-1",
            "completed_at": "2026-04-21T01:00:00+00:00",
        },
    }

    def fake_list(kind, force=False):
        if kind == "shift_snapshot":
            return [snapshot]
        if kind == "completion":
            return [completion]
        return []

    monkeypatch.setattr(roles, "list_docs_by_kind", fake_list)

    resp = c.get("/api/shifts/my")
    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["snapshot_id"] == "snap-1"
    rows = body["rows"]
    assert [r["projectId"] for r in rows] == ["10", "20"]
    assert rows[0]["completedAt"] == "2026-04-21T01:00:00+00:00"
    assert rows[1]["completedAt"] is None


def test_shifts_my_reads_from_per_reviewer_doc(client, monkeypatch):
    """New split shape — rows live in `reviewer_shift` docs keyed off the snapshot."""
    c, token_file = client
    _as_reviewer(token_file, "sam@storesight.com")
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(
        roles,
        "list_reviewers",
        lambda: [{"id": "r", "name": "Sam", "email": "sam@storesight.com"}],
    )

    snapshot = {
        "id": "snap-1",
        "data": {
            "kind": "shift_snapshot",
            "published_at": "2026-04-21T00:00:00+00:00",
            "reviewer_emails": ["alex@storesight.com", "sam@storesight.com"],
        },
    }
    reviewer_shifts = [
        {
            "id": "rs-1",
            "data": {
                "kind": "reviewer_shift",
                "shift_snapshot_id": "snap-1",
                "reviewer_email": "sam@storesight.com",
                "rows": [
                    {"projectId": "10", "name": "A"},
                    {"projectId": "20", "name": "B"},
                ],
            },
        },
        {
            "id": "rs-2",
            "data": {
                "kind": "reviewer_shift",
                "shift_snapshot_id": "snap-1",
                "reviewer_email": "alex@storesight.com",
                "rows": [{"projectId": "30", "name": "C"}],
            },
        },
    ]

    def fake_list(kind, force=False):
        if kind == "shift_snapshot":
            return [snapshot]
        if kind == "reviewer_shift":
            return list(reviewer_shifts)
        return []

    monkeypatch.setattr(roles, "list_docs_by_kind", fake_list)

    resp = c.get("/api/shifts/my")
    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["snapshot_id"] == "snap-1"
    # Only Sam's rows, ignoring Alex's doc.
    assert [r["projectId"] for r in body["rows"]] == ["10", "20"]


def test_shifts_my_returns_empty_when_no_snapshot(client, monkeypatch):
    c, token_file = client
    _as_reviewer(token_file, "sam@storesight.com")
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])
    monkeypatch.setattr(roles, "list_docs_by_kind", lambda kind: [])
    resp = c.get("/api/shifts/my")
    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["snapshot_id"] is None
    assert body["rows"] == []


# /api/shifts/my/complete — idempotency


def test_complete_is_idempotent(client, monkeypatch):
    c, token_file = client
    _as_reviewer(token_file, "sam@storesight.com")
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(
        roles,
        "list_reviewers",
        lambda: [{"id": "r", "name": "Sam", "email": "sam@storesight.com"}],
    )

    created_docs = []

    snapshot_doc = {"id": "snap-1", "data": {"kind": "shift_snapshot", "assignments": {}}}

    def fake_list(kind, force=False):
        if kind == "shift_snapshot":
            return [snapshot_doc]
        if kind == "completion":
            return list(created_docs)
        return []

    def fake_post(path, json=None):
        doc_id = f"comp-{len(created_docs) + 1}"
        created_docs.append({"id": doc_id, "data": json["data"]})
        return {"data": {"id": doc_id}}

    monkeypatch.setattr(roles, "list_docs_by_kind", fake_list)
    monkeypatch.setattr(internal_api, "post", fake_post)

    first = c.post("/api/shifts/my/complete", json={"project_id": "10"})
    assert first.status_code == 201, first.get_json()
    assert len(created_docs) == 1

    second = c.post("/api/shifts/my/complete", json={"project_id": "10"})
    assert second.status_code == 200  # existing doc returned, no create
    assert len(created_docs) == 1


def test_complete_blocked_when_job_still_unreviewed(client, monkeypatch):
    c, token_file = client
    _as_reviewer(token_file, "sam@storesight.com")
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(
        roles, "list_reviewers",
        lambda: [{"id": "r", "name": "Sam", "email": "sam@storesight.com"}],
    )
    snapshot_doc = {"id": "snap-1", "data": {"kind": "shift_snapshot"}}
    reviewer_doc = {"id": "rs-1", "data": {"kind": "reviewer_shift",
                    "shift_snapshot_id": "snap-1", "reviewer_email": "sam@storesight.com",
                    "rows": [{"jobId": "55", "id": "55"}], "part": 0}}
    created = []

    def fake_list(kind, force=False):
        if kind == "shift_snapshot":
            return [snapshot_doc]
        if kind == "reviewer_shift":
            return [reviewer_doc]
        if kind == "completion":
            return list(created)
        return []

    monkeypatch.setattr(roles, "list_docs_by_kind", fake_list)
    monkeypatch.setattr(
        internal_api, "post",
        lambda path, json=None: created.append({"id": "c1", "data": json["data"]}) or {"data": {"id": "c1"}},
    )
    # Bloom still shows 7 unreviewed responses for job 55 → must not complete.
    monkeypatch.setattr(
        main.bloom, "fetch_prioritized_jobs",
        lambda status=None, use_cache=True: [{"jobId": "55", "id": "55", "unreviewedCount": 7}],
    )

    resp = c.post("/api/shifts/my/complete", json={"job_id": "55"})
    assert resp.status_code == 409, resp.get_json()
    assert resp.get_json()["unreviewed"] == 7
    assert created == []  # nothing recorded


def test_complete_allowed_when_job_reviewed(client, monkeypatch):
    c, token_file = client
    _as_reviewer(token_file, "sam@storesight.com")
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(
        roles, "list_reviewers",
        lambda: [{"id": "r", "name": "Sam", "email": "sam@storesight.com"}],
    )
    snapshot_doc = {"id": "snap-1", "data": {"kind": "shift_snapshot"}}
    reviewer_doc = {"id": "rs-1", "data": {"kind": "reviewer_shift",
                    "shift_snapshot_id": "snap-1", "reviewer_email": "sam@storesight.com",
                    "rows": [{"jobId": "55", "id": "55"}], "part": 0}}
    created = []

    def fake_list(kind, force=False):
        if kind == "shift_snapshot":
            return [snapshot_doc]
        if kind == "reviewer_shift":
            return [reviewer_doc]
        if kind == "completion":
            return list(created)
        return []

    monkeypatch.setattr(roles, "list_docs_by_kind", fake_list)
    monkeypatch.setattr(
        internal_api, "post",
        lambda path, json=None: created.append({"id": "c1", "data": json["data"]}) or {"data": {"id": "c1"}},
    )
    # Job 55 is gone from the feed (fully reviewed) → completion goes through.
    monkeypatch.setattr(
        main.bloom, "fetch_prioritized_jobs",
        lambda status=None, use_cache=True: [],
    )

    resp = c.post("/api/shifts/my/complete", json={"job_id": "55"})
    assert resp.status_code == 201, resp.get_json()
    assert len(created) == 1


def test_complete_requires_project_id(client, monkeypatch):
    c, token_file = client
    _as_reviewer(token_file, "sam@storesight.com")
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])
    monkeypatch.setattr(roles, "list_docs_by_kind", lambda kind: [])
    resp = c.post("/api/shifts/my/complete", json={})
    assert resp.status_code == 400


def test_complete_requires_published_snapshot(client, monkeypatch):
    c, token_file = client
    _as_reviewer(token_file, "sam@storesight.com")
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])
    monkeypatch.setattr(roles, "list_docs_by_kind", lambda kind: [])
    resp = c.post("/api/shifts/my/complete", json={"project_id": "10"})
    assert resp.status_code == 409


# /api/shifts/my/complete/<pid> — delete


def test_uncomplete_deletes_matching_doc(client, monkeypatch):
    c, token_file = client
    _as_reviewer(token_file, "sam@storesight.com")
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])

    existing = [{
        "id": "comp-1",
        "data": {
            "kind": "completion",
            "reviewer_email": "sam@storesight.com",
            "project_id": "10",
            "shift_snapshot_id": "snap-1",
        },
    }]
    snapshot = [{"id": "snap-1", "data": {"kind": "shift_snapshot", "assignments": {}}}]

    def fake_list(kind, force=False):
        return snapshot if kind == "shift_snapshot" else existing

    deleted_paths = []

    def fake_delete(path):
        deleted_paths.append(path)

    monkeypatch.setattr(roles, "list_docs_by_kind", fake_list)
    monkeypatch.setattr(internal_api, "delete", fake_delete)

    resp = c.delete("/api/shifts/my/complete/10")
    assert resp.status_code == 200
    assert deleted_paths == ["/api/storage/qc-shift-assignments/comp-1"]


# /api/shifts/completions — admin reset


def test_list_completions_requires_admin(client, monkeypatch):
    c, token_file = client
    _as_reviewer(token_file, "sam@storesight.com")
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])
    resp = c.get("/api/shifts/completions")
    assert resp.status_code == 403


def test_list_completions_returns_all_for_current_snapshot(client, monkeypatch):
    c, token_file = client
    _as_admin(token_file)
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])

    completions = [
        {
            "id": "c1",
            "data": {
                "kind": "completion",
                "shift_snapshot_id": "snap-1",
                "project_id": "10",
                "reviewer_email": "sam@storesight.com",
                "completed_at": "2026-04-21T01:00:00+00:00",
            },
        },
        {
            "id": "c2",
            "data": {
                "kind": "completion",
                "shift_snapshot_id": "snap-OLD",
                "project_id": "99",
                "reviewer_email": "sam@storesight.com",
            },
        },
    ]
    snapshot = [{"id": "snap-1", "data": {"kind": "shift_snapshot", "assignments": {}}}]

    def fake_list(kind, force=False):
        return snapshot if kind == "shift_snapshot" else completions

    monkeypatch.setattr(roles, "list_docs_by_kind", fake_list)

    resp = c.get("/api/shifts/completions")
    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body["snapshot_id"] == "snap-1"
    assert [c["project_id"] for c in body["completions"]] == ["10"]


def test_overview_returns_per_reviewer_progress(client, monkeypatch):
    """Live check-in view: one card per reviewer with total/completed/pending."""
    c, token_file = client
    _as_admin(token_file)
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(
        roles,
        "list_reviewers",
        lambda: [
            {"id": "r1", "name": "Sam", "email": "sam@storesight.com"},
            {"id": "r2", "name": "Alex", "email": "alex@storesight.com"},
        ],
    )

    snapshot = {
        "id": "snap-1",
        "data": {
            "kind": "shift_snapshot",
            "published_at": "2026-04-22T00:00:00+00:00",
            "reviewer_emails": ["alex@storesight.com", "sam@storesight.com"],
        },
    }
    reviewer_docs = [
        {
            "id": "rs-sam",
            "data": {
                "kind": "reviewer_shift",
                "shift_snapshot_id": "snap-1",
                "reviewer_email": "sam@storesight.com",
                "rows": [
                    {"id": "1", "projectId": "10", "priority": 1},
                    {"id": "2", "projectId": "20", "priority": 5},
                    {"id": "3", "projectId": "30", "priority": 9},
                ],
            },
        },
        {
            "id": "rs-alex",
            "data": {
                "kind": "reviewer_shift",
                "shift_snapshot_id": "snap-1",
                "reviewer_email": "alex@storesight.com",
                "rows": [
                    {"id": "4", "projectId": "40", "priority": 2},
                    {"id": "5", "projectId": "50", "priority": 7},
                ],
            },
        },
    ]
    completions = [
        {
            "id": "comp-1",
            "data": {
                "kind": "completion",
                "shift_snapshot_id": "snap-1",
                "reviewer_email": "sam@storesight.com",
                "project_id": "10",
                "completed_at": "2026-04-22T01:00:00+00:00",
            },
        },
        {
            "id": "comp-2",
            "data": {
                "kind": "completion",
                "shift_snapshot_id": "snap-1",
                "reviewer_email": "sam@storesight.com",
                "project_id": "20",
                "completed_at": "2026-04-22T01:30:00+00:00",
            },
        },
    ]

    def fake_list(kind, force=False):
        if kind == "shift_snapshot":
            return [snapshot]
        if kind == "reviewer_shift":
            return reviewer_docs
        if kind == "completion":
            return completions
        return []

    monkeypatch.setattr(roles, "list_docs_by_kind", fake_list)

    resp = c.get("/api/shifts/overview")
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()["data"]
    assert body["snapshot_id"] == "snap-1"
    assert body["published_at"] == "2026-04-22T00:00:00+00:00"

    # Reviewers sorted by total desc — Sam has 3, Alex has 2.
    reviewers = body["reviewers"]
    assert [r["email"] for r in reviewers] == ["sam@storesight.com", "alex@storesight.com"]

    sam = reviewers[0]
    assert sam == {
        "email": "sam@storesight.com",
        "name": "Sam",
        "total": 3,
        "completed": 2,
        "pending": 1,
        "first_priority": 1,
        "last_priority": 9,
    }

    alex = reviewers[1]
    assert alex["total"] == 2
    assert alex["completed"] == 0
    assert alex["pending"] == 2
    assert alex["name"] == "Alex"


def test_overview_returns_empty_when_no_snapshot(client, monkeypatch):
    c, token_file = client
    _as_admin(token_file)
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])
    monkeypatch.setattr(roles, "list_docs_by_kind", lambda kind: [])
    resp = c.get("/api/shifts/overview")
    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body == {"snapshot_id": None, "reviewers": []}


def test_overview_requires_admin(client, monkeypatch):
    c, token_file = client
    _as_reviewer(token_file, "nobody@storesight.com")
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])
    resp = c.get("/api/shifts/overview")
    assert resp.status_code == 403


def _setup_clear_scenario(monkeypatch):
    """Current shift has 2 reviewers, each with 2 rows, 1 completion on sam."""
    snapshot = {
        "id": "snap-1",
        "data": {
            "kind": "shift_snapshot",
            "published_at": "2026-04-22T00:00:00+00:00",
            "reviewer_emails": ["alex@storesight.com", "sam@storesight.com"],
        },
    }
    reviewer_docs = [
        {
            "id": "rs-sam",
            "data": {
                "kind": "reviewer_shift",
                "shift_snapshot_id": "snap-1",
                "reviewer_email": "sam@storesight.com",
                "rows": [
                    {"id": "1", "projectId": "10"},
                    {"id": "2", "projectId": "20"},
                ],
            },
        },
        {
            "id": "rs-alex",
            "data": {
                "kind": "reviewer_shift",
                "shift_snapshot_id": "snap-1",
                "reviewer_email": "alex@storesight.com",
                "rows": [
                    {"id": "3", "projectId": "30"},
                    {"id": "4", "projectId": "40"},
                ],
            },
        },
    ]
    completions = [
        {
            "id": "comp-1",
            "data": {
                "kind": "completion",
                "shift_snapshot_id": "snap-1",
                "reviewer_email": "sam@storesight.com",
                "project_id": "10",
                "completed_at": "2026-04-22T01:00:00+00:00",
            },
        },
    ]

    def fake_list(kind, force=False):
        if kind == "shift_snapshot":
            return [snapshot]
        if kind == "reviewer_shift":
            return list(reviewer_docs)
        if kind == "completion":
            return list(completions)
        return []

    deletes = []
    puts = []

    def fake_delete(path):
        deletes.append(path)

    def fake_put(path, json=None):
        puts.append({"path": path, "data": json["data"]})
        return {"data": {"id": path.rsplit("/", 1)[-1]}}

    monkeypatch.setattr(roles, "list_docs_by_kind", fake_list)
    monkeypatch.setattr(internal_api, "delete", fake_delete)
    monkeypatch.setattr(internal_api, "put", fake_put)
    return deletes, puts


def test_clear_all_deletes_reviewer_shifts_and_completions(client, monkeypatch):
    c, token_file = client
    _as_admin(token_file)
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])
    deletes, puts = _setup_clear_scenario(monkeypatch)

    resp = c.post("/api/shifts/clear", json={"mode": "all"})
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()["data"]
    assert body["mode"] == "all"
    assert body["cleared_rows"] == 4  # 2 + 2 rows across both reviewers
    assert body["cleared_completions"] == 1

    # Both reviewer_shift docs + the completion doc deleted; no PUTs.
    assert puts == []
    assert "/api/storage/qc-shift-assignments/rs-sam" in deletes
    assert "/api/storage/qc-shift-assignments/rs-alex" in deletes
    assert "/api/storage/qc-shift-assignments/comp-1" in deletes


def test_clear_scoped_to_one_reviewer_keeps_shift_live(client, monkeypatch):
    c, token_file = client
    _as_admin(token_file)
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])
    deletes, puts = _setup_clear_scenario(monkeypatch)

    resp = c.post(
        "/api/shifts/clear",
        json={"mode": "all", "reviewer_email": "sam@storesight.com"},
    )
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()["data"]
    assert body["cleared_rows"] == 2  # only sam's two rows
    assert body["cleared_completions"] == 1  # only sam's completion

    # Sam's docs gone; Alex untouched; snapshot NOT deleted (shift stays live).
    assert "/api/storage/qc-shift-assignments/rs-sam" in deletes
    assert "/api/storage/qc-shift-assignments/comp-1" in deletes
    assert "/api/storage/qc-shift-assignments/rs-alex" not in deletes
    assert "/api/storage/qc-shift-assignments/snap-1" not in deletes


def test_clear_active_keeps_only_completed_rows(client, monkeypatch):
    c, token_file = client
    _as_admin(token_file)
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])
    deletes, puts = _setup_clear_scenario(monkeypatch)

    resp = c.post("/api/shifts/clear", json={"mode": "active"})
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()["data"]
    # Sam: 1 active removed (projectId 20); Alex: 2 active removed.
    assert body["cleared_rows"] == 3
    # Completion marks are preserved.
    assert body["cleared_completions"] == 0

    # Sam's doc is rewritten with only the completed row (project 10).
    sam_put = next(p for p in puts if p["path"].endswith("/rs-sam"))
    assert [r["projectId"] for r in sam_put["data"]["rows"]] == ["10"]
    # Alex had no completions, so their doc is deleted outright.
    assert "/api/storage/qc-shift-assignments/rs-alex" in deletes
    # Completion doc stays.
    assert "/api/storage/qc-shift-assignments/comp-1" not in deletes


def test_clear_completed_drops_completed_rows_and_completion_docs(client, monkeypatch):
    c, token_file = client
    _as_admin(token_file)
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])
    deletes, puts = _setup_clear_scenario(monkeypatch)

    resp = c.post("/api/shifts/clear", json={"mode": "completed"})
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()["data"]
    # Only Sam had a completed row (project 10).
    assert body["cleared_rows"] == 1
    assert body["cleared_completions"] == 1

    # Sam's doc is rewritten without the completed row.
    sam_put = next(p for p in puts if p["path"].endswith("/rs-sam"))
    assert [r["projectId"] for r in sam_put["data"]["rows"]] == ["20"]
    # Completion doc is deleted.
    assert "/api/storage/qc-shift-assignments/comp-1" in deletes


def test_clear_requires_admin(client, monkeypatch):
    c, token_file = client
    _as_reviewer(token_file, "nobody@storesight.com")
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])
    resp = c.post("/api/shifts/clear", json={"mode": "all"})
    assert resp.status_code == 403


def test_clear_rejects_bad_mode(client, monkeypatch):
    c, token_file = client
    _as_admin(token_file)
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])
    resp = c.post("/api/shifts/clear", json={"mode": "bogus"})
    assert resp.status_code == 400


def test_clear_noop_when_no_snapshot(client, monkeypatch):
    c, token_file = client
    _as_admin(token_file)
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])
    monkeypatch.setattr(roles, "list_docs_by_kind", lambda kind: [])
    resp = c.post("/api/shifts/clear", json={"mode": "all"})
    assert resp.status_code == 200
    body = resp.get_json()["data"]
    assert body == {"mode": "all", "cleared_rows": 0, "cleared_completions": 0}


def test_reset_completions_requires_admin(client, monkeypatch):
    c, token_file = client
    _as_reviewer(token_file, "sam@storesight.com")
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])
    resp = c.delete("/api/shifts/completions")
    assert resp.status_code == 403


def test_reset_completions_wipes_current_snapshot(client, monkeypatch):
    c, token_file = client
    _as_admin(token_file)
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])

    completions = [
        {"id": "c1", "data": {"kind": "completion", "shift_snapshot_id": "snap-1", "project_id": "10"}},
        {"id": "c2", "data": {"kind": "completion", "shift_snapshot_id": "snap-1", "project_id": "20"}},
        {"id": "c3", "data": {"kind": "completion", "shift_snapshot_id": "snap-OLD", "project_id": "99"}},
    ]
    snapshot = [{"id": "snap-1", "data": {"kind": "shift_snapshot", "assignments": {}}}]

    def fake_list(kind, force=False):
        return snapshot if kind == "shift_snapshot" else completions

    deleted = []
    monkeypatch.setattr(roles, "list_docs_by_kind", fake_list)
    monkeypatch.setattr(internal_api, "delete", lambda path: deleted.append(path))

    resp = c.delete("/api/shifts/completions")
    assert resp.status_code == 200
    assert resp.get_json()["data"]["deleted"] == 2
    assert all("snap-OLD" not in p for p in deleted)
