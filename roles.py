"""Role derivation backed by the Internal Storage API.

Reviewers and admins are stored as documents in the tool's Storage API
namespace (`qc-shift-assignments`) with a `kind` field so they share the
same namespace. Each record is `{kind: "reviewer"|"admin", name, email}`.

`ROOT_ADMIN_EMAIL` is always treated as an admin, even when the stored
admin list is empty — this guarantees the system is never admin-less and
gives the initial owner a way to bootstrap the tool.
"""

import logging
import time

import internal_api

ROOT_ADMIN_EMAIL = "jayson.johnson@storesight.com"

# Short-lived in-memory cache for reviewer/admin lists (rarely change).
_ROSTER_CACHE: dict = {"fetched_at": 0.0, "reviewers": None, "admins": None}
_ROSTER_TTL = 60  # seconds

_STORAGE_PATH = "/api/storage/qc-shift-assignments"
# Use the page size Bloom's API reliably honors (see bloom.py). Termination is
# driven by an empty page, not by a short page, so this value only affects how
# many requests a full scan takes — never correctness.
_PAGE_SIZE = 100
# Safety cap so a misbehaving API (e.g. one that ignores `page`) can't spin
# forever. 500 pages * 100 = 50k docs, well past the 10k namespace limit.
_MAX_PAGES = 500


def _normalize_email(email):
    return (email or "").strip().lower()


def _list_by_kind(kind):
    """Return docs whose `data.kind == kind` as {id,name,email}, one per email.

    Collapses duplicate records that share an email so each person appears
    once — older data accumulated duplicates before the create-time dedup
    could see past the first page. Keeps the first (newest) doc per email, so
    the id returned is a real, deletable document.

    For richer kinds (shift_snapshot, completion), use `list_docs_by_kind`
    which returns the full {id, data, ...} document so callers see every field.
    """
    out = []
    seen = set()
    for doc in list_docs_by_kind(kind):
        data = doc.get("data") or {}
        email = _normalize_email(data.get("email"))
        if email and email in seen:
            continue
        if email:
            seen.add(email)
        out.append(
            {
                "id": doc.get("id"),
                "name": data.get("name", ""),
                "email": email,
            }
        )
    return out


def list_docs_by_kind(kind):
    """Return raw storage docs whose `data.kind == kind`, newest first.

    Reviewers/admins share this namespace with every published shift and
    completion doc, so the records we want can sit many pages deep once the
    tool has been used for a while. We keep paging until the API returns an
    EMPTY page — never stopping early on a short page, which would silently
    drop the oldest records (e.g. the reviewer/admin roster) if the API caps
    a page below the requested size.
    """
    out = []
    page = 1
    while page <= _MAX_PAGES:
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
        page += 1
    return out


def list_reviewers(use_cache=True):
    now = time.time()
    if use_cache and _ROSTER_CACHE["reviewers"] is not None and (now - _ROSTER_CACHE["fetched_at"]) < _ROSTER_TTL:
        return _ROSTER_CACHE["reviewers"]
    try:
        result = _list_by_kind("reviewer")
    except Exception:
        # On failure (e.g. 429), cache the last known value or empty list for a
        # short TTL so we stop hammering the API on every request.
        if _ROSTER_CACHE["reviewers"] is None:
            _ROSTER_CACHE["reviewers"] = []
        _ROSTER_CACHE["fetched_at"] = now
        raise
    _ROSTER_CACHE["reviewers"] = result
    _ROSTER_CACHE["fetched_at"] = now
    return result


def invalidate_roster_cache():
    _ROSTER_CACHE["fetched_at"] = 0.0
    _ROSTER_CACHE["reviewers"] = None
    _ROSTER_CACHE["admins"] = None


def list_admins(use_cache=True):
    now = time.time()
    if use_cache and _ROSTER_CACHE["admins"] is not None and (now - _ROSTER_CACHE["fetched_at"]) < _ROSTER_TTL:
        return _ROSTER_CACHE["admins"]
    try:
        result = _list_by_kind("admin")
    except Exception:
        if _ROSTER_CACHE["admins"] is None:
            _ROSTER_CACHE["admins"] = []
        _ROSTER_CACHE["fetched_at"] = now
        raise
    _ROSTER_CACHE["admins"] = result
    _ROSTER_CACHE["fetched_at"] = now
    return result


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
    invalidate_roster_cache()
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
    invalidate_roster_cache()


def delete_record(doc_id):
    internal_api.delete(f"{_STORAGE_PATH}/{doc_id}")
    invalidate_roster_cache()
