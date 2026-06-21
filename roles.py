"""Role derivation backed by the Internal Storage API.

Reviewers and admins are stored as documents in the tool's Storage API
namespace (`qc-shift-assignments`) with a `kind` field so they share the
same namespace. Each record is `{kind: "reviewer"|"admin", name, email}`.

`ROOT_ADMIN_EMAIL` is always treated as an admin, even when the stored
admin list is empty — this guarantees the system is never admin-less and
gives the initial owner a way to bootstrap the tool.

## Rate-limit strategy

Every `list_docs_by_kind` call scans the entire namespace in pages of 100.
With 700+ docs that is 7 HTTP requests per call, and the overview endpoint
alone needs 4 different kinds — 28 requests per page load. The Storage API
allows 60 req/min, so without caching the app hits the limit on the 3rd
page load.

`list_docs_by_kind` caches each kind independently for `_DOC_CACHE_TTL`
seconds (30s). Writes (create/update/delete) call `invalidate_doc_cache()`
to drop the affected kinds immediately. On failure the stale value is kept
and the TTL is reset to prevent retry storms.
"""

import logging
import time

import internal_api

ROOT_ADMIN_EMAIL = "jayson.johnson@storesight.com"

_STORAGE_PATH = "/api/storage/qc-shift-assignments"
_PAGE_SIZE = 100
_MAX_PAGES = 500

# Per-kind document cache. Each entry: {"data": [...], "fetched_at": float}
_DOC_CACHE: dict = {}
_DOC_CACHE_TTL = 30  # seconds — balance freshness vs. API budget


def _normalize_email(email):
    return (email or "").strip().lower()


def invalidate_doc_cache(*kinds):
    """Drop cached results for the given kinds (or all kinds if none specified)."""
    if kinds:
        for k in kinds:
            _DOC_CACHE.pop(k, None)
    else:
        _DOC_CACHE.clear()


def list_docs_by_kind(kind):
    """Return raw storage docs whose `data.kind == kind`, newest first.

    Results are cached per-kind for 30 seconds. On a cache miss the full
    namespace is scanned (paged at 100/page). On API failure the stale
    cached value is returned (if any) so callers degrade gracefully.
    """
    now = time.time()
    entry = _DOC_CACHE.get(kind)
    if entry and (now - entry["fetched_at"]) < _DOC_CACHE_TTL:
        return entry["data"]

    out = []
    try:
        page = 1
        while page <= _MAX_PAGES:
            resp = internal_api.get(
                _STORAGE_PATH, params={"page": page, "per_page": _PAGE_SIZE}
            )
            docs = resp.get("data", []) if isinstance(resp, dict) else []
            if not docs:
                break
            for doc in docs:
                if (doc.get("data") or {}).get("kind") == kind:
                    out.append(doc)
            page += 1
        _DOC_CACHE[kind] = {"data": out, "fetched_at": now}
    except Exception as exc:
        logging.warning("list_docs_by_kind(%s) failed: %s", kind, exc)
        # Keep the stale value if we have one; reset the TTL so we don't
        # hammer the API on every subsequent request while it recovers.
        if entry:
            _DOC_CACHE[kind] = {"data": entry["data"], "fetched_at": now}
            return entry["data"]
        # No prior data — cache empty list so callers don't crash.
        _DOC_CACHE[kind] = {"data": [], "fetched_at": now}

    return out


def _list_by_kind(kind):
    """Return docs as {id, name, email}, deduped by email."""
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
    admins = list_admins()
    if any(a["email"] == normalized for a in admins):
        return "admin"
    reviewers = list_reviewers()
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
    invalidate_doc_cache(kind)
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
    invalidate_doc_cache(kind)


def delete_record(doc_id):
    internal_api.delete(f"{_STORAGE_PATH}/{doc_id}")
    invalidate_doc_cache()
