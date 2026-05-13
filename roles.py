"""Role derivation backed by the Internal Storage API.

Reviewers and admins are stored as documents in the tool's Storage API
namespace (`qc-shift-assignments`) with a `kind` field so they share the
same namespace. Each record is `{kind: "reviewer"|"admin", name, email}`.

`ROOT_ADMIN_EMAIL` is always treated as an admin, even when the stored
admin list is empty — this guarantees the system is never admin-less and
gives the initial owner a way to bootstrap the tool.
"""

import logging

import internal_api

ROOT_ADMIN_EMAIL = "micah.mccollum@storesight.com"

_STORAGE_PATH = "/api/storage/qc-shift-assignments"
_PAGE_SIZE = 200


def _normalize_email(email):
    return (email or "").strip().lower()


def _list_by_kind(kind):
    """Return all storage docs whose `data.kind == kind` as {id,name,email} tuples.

    Used for the reviewer/admin directory. For richer kinds (shift_snapshot,
    completion), use `list_docs_by_kind` which returns the full {id, data, ...}
    document so callers can see every field.
    """
    out = []
    for doc in list_docs_by_kind(kind):
        data = doc.get("data") or {}
        out.append(
            {
                "id": doc.get("id"),
                "name": data.get("name", ""),
                "email": _normalize_email(data.get("email")),
            }
        )
    return out


def list_docs_by_kind(kind):
    """Return raw storage docs whose `data.kind == kind`, newest first."""
    out = []
    page = 1
    while True:
        resp = internal_api.get(
            _STORAGE_PATH, params={"page": page, "per_page": _PAGE_SIZE}
        )
        docs = resp.get("data", []) if isinstance(resp, dict) else []
        if not docs:
            break
        for doc in docs:
            data = doc.get("data") or {}
            if data.get("kind") == kind:
                out.append(doc)
        if len(docs) < _PAGE_SIZE:
            break
        page += 1
    return out


def list_reviewers():
    return _list_by_kind("reviewer")


def list_admins():
    return _list_by_kind("admin")


def get_role(email):
    """Return "admin" | "reviewer" | "viewer" for the given email."""
    normalized = _normalize_email(email)
    if not normalized:
        return "viewer"
    if normalized == _normalize_email(ROOT_ADMIN_EMAIL):
        return "admin"
    try:
        admins = list_admins()
    except Exception as exc:  # noqa: BLE001 — Storage API is not load-bearing for read-only pages
        logging.warning("Failed to load admins, treating as empty: %s", exc)
        admins = []
    if any(a["email"] == normalized for a in admins):
        return "admin"
    try:
        reviewers = list_reviewers()
    except Exception as exc:  # noqa: BLE001
        logging.warning("Failed to load reviewers, treating as empty: %s", exc)
        reviewers = []
    if any(r["email"] == normalized for r in reviewers):
        return "reviewer"
    return "viewer"


def is_admin(email):
    return get_role(email) == "admin"


def create_record(kind, name, email):
    """Create a reviewer or admin record. Returns the Storage API doc id."""
    payload = {
        "data": {
            "kind": kind,
            "name": (name or "").strip(),
            "email": _normalize_email(email),
        }
    }
    resp = internal_api.post(_STORAGE_PATH, json=payload)
    return resp["data"]["id"]


def update_record(doc_id, kind, name, email):
    payload = {
        "data": {
            "kind": kind,
            "name": (name or "").strip(),
            "email": _normalize_email(email),
        }
    }
    internal_api.put(f"{_STORAGE_PATH}/{doc_id}", json=payload)


def delete_record(doc_id):
    internal_api.delete(f"{_STORAGE_PATH}/{doc_id}")
