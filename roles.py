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
_DOC_CACHE_TTL = 300  # 5 minutes — writes invalidate immediately so this is safe
# Tracks the last time we did a full namespace scan (all pages, all kinds).
_FULL_SCAN_AT = 0.0


def _normalize_email(email):
    return (email or "").strip().lower()


def invalidate_doc_cache(*kinds):
    """Drop cached results for the given kinds (or all kinds if none specified)."""
    global _FULL_SCAN_AT
    if kinds:
        for k in kinds:
            _DOC_CACHE.pop(k, None)
    else:
        _DOC_CACHE.clear()
    _FULL_SCAN_AT = 0.0


def _full_namespace_scan():
    """Fetch every page of the namespace once and populate ALL kind caches.

    This is the key rate-limit fix: instead of 4 separate kind-scans that each
    read all 7 pages (28 API calls), we do a single 7-page scan and sort the
    results into per-kind buckets in one pass.
    """
    global _FULL_SCAN_AT
    now = time.time()
    by_kind: dict = {}
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
                kind = (doc.get("data") or {}).get("kind")
                if kind:
                    by_kind.setdefault(kind, []).append(doc)
            page += 1
        # Populate cache for every kind we found.
        for k, docs in by_kind.items():
            _DOC_CACHE[k] = {"data": docs, "fetched_at": now}
        _FULL_SCAN_AT = now
        logging.info("Storage full scan: %d docs across %d kinds", sum(len(v) for v in by_kind.values()), len(by_kind))
    except Exception as exc:
        logging.warning("_full_namespace_scan failed: %s", exc)
        # Back off for 10 seconds before retrying — long enough to avoid a
        # retry storm but short enough to recover quickly once the rate limit clears.
        _FULL_SCAN_AT = now - _DOC_CACHE_TTL + 10
        raise


def list_docs_by_kind(kind):
    """Return raw storage docs whose `data.kind == kind`, newest first.

    On a cache hit: 0 API calls. On a miss: triggers a single full namespace
    scan that populates ALL kind caches at once (7 API calls for ~700 docs).
    On failure the stale cached value is returned so callers degrade gracefully.
    """
    now = time.time()
    entry = _DOC_CACHE.get(kind)
    if entry and (now - entry["fetched_at"]) < _DOC_CACHE_TTL:
        return entry["data"]

    # Cache miss — do one full scan to fill all kind caches simultaneously.
    # If the full scan already ran recently (another kind triggered it), skip.
    if (now - _FULL_SCAN_AT) >= _DOC_CACHE_TTL:
        try:
            _full_namespace_scan()
        except Exception:
            # Fall through: return stale data if available, else empty list.
            pass

    entry = _DOC_CACHE.get(kind)
    if entry:
        return entry["data"]

    # Kind not found in the namespace at all — cache empty list.
    _DOC_CACHE[kind] = {"data": [], "fetched_at": now}
    return []


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
