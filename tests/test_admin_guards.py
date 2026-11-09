"""Admin-only and validation guards found by the 2026-11-09 code review."""

import datetime
import os

import jwt
import pytest

os.environ["LOCAL_DEV"] = "1"

import main  # noqa: E402
import roles  # noqa: E402
import internal_api  # noqa: E402


@pytest.fixture
def as_role(tmp_path, monkeypatch):
    def make(role):
        now = datetime.datetime.now(datetime.timezone.utc)
        token = jwt.encode({"email": "u@storesight.com", "name": "U", "iat": now,
                            "exp": now + datetime.timedelta(hours=8)}, "x", algorithm="HS256")
        d = tmp_path / role
        d.mkdir()
        (d / "dev-token").write_text(token)
        monkeypatch.setattr("main._dev_token_path", lambda: d / "dev-token")
        monkeypatch.setattr(roles, "get_role", lambda email: role)
        main.app.config["TESTING"] = True
        return main.app.test_client()
    return make


@pytest.mark.parametrize("path", [
    "@evil.example/x", "//evil.example/x", "/api/../x", "/api/x@evil.example",
    "https://evil.example/api/x", "/admin/x",
])
def test_probe_refuses_a_path_that_could_change_the_host(as_role, monkeypatch, path):
    calls = []
    monkeypatch.setattr(internal_api, "get", lambda *a, **k: calls.append(a) or {})
    resp = as_role("admin").get("/api/bloom/_probe", query_string={"path": path})
    assert resp.status_code == 400
    assert calls == []


def test_probe_still_reads_a_plain_api_path(as_role, monkeypatch):
    calls = []
    monkeypatch.setattr(internal_api, "get", lambda path, params=None: calls.append(path) or {"ok": 1})
    resp = as_role("admin").get("/api/bloom/_probe", query_string={"path": "/api/jobs"})
    assert resp.status_code == 200 and calls == ["/api/jobs"]


def test_a_lead_cannot_delete_all_shift_history(as_role, monkeypatch):
    deleted = []
    monkeypatch.setattr(main, "_try_delete", lambda doc_id: deleted.append(doc_id))
    monkeypatch.setattr(roles, "list_docs_by_kind", lambda kind, force=False: [
        {"id": f"{kind}-1", "data": {"kind": kind}}])
    resp = as_role("lead").post("/api/shifts/clear", json={"mode": "reset"})
    assert resp.status_code == 403
    assert deleted == []


def test_roster_delete_refuses_a_doc_of_another_kind(as_role, monkeypatch):
    deleted = []
    monkeypatch.setattr(internal_api, "delete", lambda path: deleted.append(path))
    monkeypatch.setattr(roles, "list_docs_by_kind", lambda kind, force=False: (
        [{"id": "lead-1", "data": {"kind": "lead"}}] if kind == "lead" else []))
    client = as_role("admin")
    assert client.delete("/api/leads/snapshot-1").status_code == 404
    assert deleted == []
    assert client.delete("/api/leads/lead-1").status_code == 200
    assert len(deleted) == 1


def test_renaming_a_reviewer_keeps_their_color_and_exceptions(as_role, monkeypatch):
    puts = []
    monkeypatch.setattr(internal_api, "put", lambda path, json=None: puts.append(json["data"]))
    monkeypatch.setattr(roles, "list_docs_by_kind", lambda kind, force=False: (
        [{"id": "r1", "data": {"kind": "reviewer", "name": "Sam", "email": "sam@storesight.com",
                               "color": "#ff0000", "exclusion_exceptions": ["video"]}}]
        if kind == "reviewer" else []))
    monkeypatch.setattr(roles, "cache_upsert_doc", lambda *a, **k: None)
    resp = as_role("admin").put("/api/reviewers/r1", json={"name": "Samuel", "email": "sam@storesight.com"})
    assert resp.status_code == 200, resp.get_json()
    assert puts[0]["color"] == "#ff0000"
    assert puts[0]["exclusion_exceptions"] == ["video"]
    assert puts[0]["name"] == "Samuel"
