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
    token_file.write_text(_make_dev_token(roles.ROOT_ADMIN_EMAIL, "Micah"))
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
