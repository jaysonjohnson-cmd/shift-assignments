"""Unit tests for the role-derivation module and API endpoints.

The Storage API is mocked so the tests don't hit the live internal-tool-api.
"""

import datetime
import os

import jwt
import pytest

os.environ["LOCAL_DEV"] = "1"

import roles  # noqa: E402
from main import app  # noqa: E402


# ---------- role derivation ----------


def test_root_admin_always_admin(monkeypatch):
    """Root admin is admin even if the storage API returns nothing."""
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])
    assert roles.get_role(roles.ROOT_ADMIN_EMAIL) == "admin"
    assert roles.get_role(roles.ROOT_ADMIN_EMAIL.upper()) == "admin"


def test_stored_admin_is_admin(monkeypatch):
    monkeypatch.setattr(
        roles,
        "list_admins",
        lambda: [{"id": "a1", "name": "Kelly", "email": "kelly@storesight.com"}],
    )
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])
    assert roles.get_role("kelly@storesight.com") == "admin"
    assert roles.get_role("KELLY@storesight.com") == "admin"  # case-insensitive


def test_stored_reviewer_is_reviewer(monkeypatch):
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(
        roles,
        "list_reviewers",
        lambda: [{"id": "r1", "name": "Sam", "email": "sam@storesight.com"}],
    )
    assert roles.get_role("sam@storesight.com") == "reviewer"


def test_unknown_user_is_viewer(monkeypatch):
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])
    assert roles.get_role("stranger@storesight.com") == "viewer"


def test_empty_email_is_viewer(monkeypatch):
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])
    assert roles.get_role("") == "viewer"
    assert roles.get_role(None) == "viewer"


def test_storage_api_failure_falls_back_to_viewer(monkeypatch):
    """If the Storage API errors, we don't crash — we fall back to viewer."""

    def boom():
        raise RuntimeError("storage api unavailable")

    monkeypatch.setattr(roles, "list_admins", boom)
    monkeypatch.setattr(roles, "list_reviewers", boom)
    # Root admin still wins even on failure because we check root before the API.
    assert roles.get_role(roles.ROOT_ADMIN_EMAIL) == "admin"
    # Everyone else degrades to viewer.
    assert roles.get_role("anyone@storesight.com") == "viewer"


# ---------- pagination (regression: added names disappearing) ----------


def test_list_docs_by_kind_finds_records_on_later_pages(monkeypatch):
    """A reviewer doc many pages deep must still be returned.

    Reviewers/admins share the namespace with shift + completion docs and are
    returned newest-first, so the roster ends up on later pages once the tool
    has been used. Regression for the old `len(docs) < PAGE_SIZE` early-break,
    which dropped the roster as soon as a page came back short.
    """
    # Page 1: a SHORT page (fewer than the requested per_page) of other kinds —
    # the old code would have stopped right here and missed the reviewer.
    # Page 2: the reviewer we care about. Page 3: empty (true end).
    pages = {
        1: [{"id": f"snap{i}", "data": {"kind": "shift_snapshot"}} for i in range(10)],
        2: [{"id": "rev1", "data": {"kind": "reviewer", "name": "Deep", "email": "deep@storesight.com"}}],
        3: [],
    }

    def fake_get(path, params=None):
        return {"data": pages.get((params or {}).get("page"), [])}

    monkeypatch.setattr(roles.internal_api, "get", fake_get)
    found = roles.list_reviewers()
    assert [r["email"] for r in found] == ["deep@storesight.com"]


def test_list_by_kind_dedupes_by_email(monkeypatch):
    """Duplicate reviewer records (same email) collapse to one entry."""
    docs = [
        {"id": "new", "data": {"kind": "reviewer", "name": "Blake Ward", "email": "blake@storesight.com"}},
        {"id": "old", "data": {"kind": "reviewer", "name": "Blake Ward", "email": "BLAKE@storesight.com"}},
        {"id": "x", "data": {"kind": "reviewer", "name": "Aubrey Ward", "email": "aubrey@storesight.com"}},
    ]
    monkeypatch.setattr(roles, "list_docs_by_kind", lambda kind: docs)
    out = roles.list_reviewers()
    emails = [r["email"] for r in out]
    assert emails == ["blake@storesight.com", "aubrey@storesight.com"]  # one Blake, case-insensitive
    assert out[0]["id"] == "new"  # keeps the first (newest) doc, which is deletable


def test_list_docs_by_kind_stops_on_empty_page(monkeypatch):
    """Termination is driven by an empty page, and the max-page cap holds."""
    calls = {"n": 0}

    def fake_get(path, params=None):
        calls["n"] += 1
        return {"data": []}  # empty immediately

    monkeypatch.setattr(roles.internal_api, "get", fake_get)
    assert roles.list_admins() == []
    assert calls["n"] == 1  # stopped after the first empty page


# ---------- API endpoints ----------


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
    with app.test_client() as c:
        yield c, token_file


def test_me_returns_admin_for_root(client, monkeypatch):
    c, token_file = client
    token_file.write_text(_make_dev_token(roles.ROOT_ADMIN_EMAIL, "Jayson"))
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])
    resp = c.get("/api/me")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["email"] == roles.ROOT_ADMIN_EMAIL
    assert body["role"] == "admin"


def test_me_returns_viewer_for_unknown(client, monkeypatch):
    c, token_file = client
    token_file.write_text(_make_dev_token("stranger@storesight.com"))
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])
    resp = c.get("/api/me")
    assert resp.status_code == 200
    assert resp.get_json()["role"] == "viewer"


def test_non_admin_cannot_create_reviewer(client, monkeypatch):
    c, token_file = client
    token_file.write_text(_make_dev_token("stranger@storesight.com"))
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])
    resp = c.post(
        "/api/reviewers", json={"name": "Sam", "email": "sam@storesight.com"}
    )
    assert resp.status_code == 403


def test_admin_can_create_reviewer(client, monkeypatch):
    c, token_file = client
    token_file.write_text(_make_dev_token(roles.ROOT_ADMIN_EMAIL))

    created = {}

    def fake_create(kind, name, email):
        created["kind"] = kind
        created["name"] = name
        created["email"] = email
        return "doc-123"

    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])
    monkeypatch.setattr(roles, "create_record", fake_create)

    resp = c.post(
        "/api/reviewers", json={"name": "Sam Smith", "email": "SAM@storesight.com"}
    )
    assert resp.status_code == 201, resp.get_json()
    body = resp.get_json()["data"]
    assert body["id"] == "doc-123"
    assert body["email"] == "sam@storesight.com"  # normalized
    assert created == {"kind": "reviewer", "name": "Sam Smith", "email": "sam@storesight.com"}


def test_create_reviewer_rejects_invalid_email(client, monkeypatch):
    c, token_file = client
    token_file.write_text(_make_dev_token(roles.ROOT_ADMIN_EMAIL))
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])
    resp = c.post("/api/reviewers", json={"name": "Sam", "email": "not-an-email"})
    assert resp.status_code == 400


def test_cannot_delete_root_admin(client, monkeypatch):
    c, token_file = client
    token_file.write_text(_make_dev_token(roles.ROOT_ADMIN_EMAIL))
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])
    resp = c.delete("/api/admins/__root__")
    assert resp.status_code == 400


def test_admins_list_always_includes_root(client, monkeypatch):
    c, token_file = client
    token_file.write_text(_make_dev_token(roles.ROOT_ADMIN_EMAIL))
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [])
    resp = c.get("/api/admins")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    emails = [a["email"] for a in data]
    assert roles.ROOT_ADMIN_EMAIL.lower() in emails
