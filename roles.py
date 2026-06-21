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
allows 60 req/min, so without caching the app hits the limit after 2 loads.

A background thread runs a full namespace scan every 4 minutes and keeps
ALL kind caches warm. Request handlers read from the cache and never touch
the Storage API directly — so 429s can only happen during the very first
scan after startup, not on any subsequent request.
"""

import logging
import threading
import time

import internal_api

ROOT_ADMIN_EMAIL = "jayson.johnson@storesight.com"

_STORAGE_PATH = "/api/storage/qc-shift-assignments"
_PAGE_SIZE = 100
_MAX_PAGES = 500

# Per-kind document cache. {kind: {"data": [...], "fetched_at": float}}
_DOC_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()
_SCAN_LOCK = threading.Lock()   # only one scan runs at a time; others wait then read cache
_DOC_CACHE_TTL = 240       # 4 minutes — background thread refreshes before expiry
_REFRESH_INTERVAL = 210    # background refresh every 3.5 minutes (before TTL expires)
_FULL_SCAN_AT = 0.0
_BG_STARTED = False


# ---------------------------------------------------------------------------
# Background cache warmer
# ---------------------------------------------------------------------------

def _bg_refresh_loop():
    """Daemon thread: keep the namespace cache warm so requests never scan."""
    while True:
        try:
            _full_namespace_scan()
        except Exception:
            pass  # logged inside _full_namespace_scan; retry next interval
        time.sleep(_REFRESH_INTERVAL)


def _ensure_bg_started():
    global _BG_STARTED
    if _BG_STARTED:
        return
    t = threading.Thread(target=_bg_refresh_loop, daemon=True, name="roles-cache-warmer")
    t.start()
    _BG_STARTED = True


# ---------------------------------------------------------------------------
# Core scan + cache
# ---------------------------------------------------------------------------

def _full_namespace_scan():
    """Scan all namespace pages once and populate every kind cache atomically.

    Only one scan runs at a time (_SCAN_LOCK). Concurrent callers block until
    the scan finishes, then read from the freshly-populated cache. This prevents
    the thundering-herd where N simultaneous requests each kick off 7 API calls.
    """
    global _FULL_SCAN_AT
    with _SCAN_LOCK:
        # Re-check after acquiring lock — another thread may have just finished.
        now = time.time()
        if (now - _FULL_SCAN_AT) < _DOC_CACHE_TTL:
            return  # cache is already fresh; nothing to do

        by_kind: dict = {}
        page = 1
        try:
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
        except Exception as exc:
            logging.warning("Storage namespace scan failed (page %d): %s", page, exc)
            # Back off 10 s before next attempt — don't freeze for the full TTL.
            _FULL_SCAN_AT = now - _DOC_CACHE_TTL + 10
            raise

        with _CACHE_LOCK:
            for k, docs in by_kind.items():
                _DOC_CACHE[k] = {"data": docs, "fetched_at": now}
            _FULL_SCAN_AT = now
        logging.info(
            "Storage scan complete: %d docs across %d kinds",
            sum(len(v) for v in by_kind.values()),
            len(by_kind),
        )


def list_docs_by_kind(kind):
    """Return raw storage docs for `kind`, served from cache.

    On a warm cache: 0 API calls. On a cold cache: triggers _full_namespace_scan
    (7 API calls) which populates all kinds simultaneously. The background thread
    keeps the cache warm so cold misses only happen once after deployment.
    """
    _ensure_bg_started()
    now = time.time()

    with _CACHE_LOCK:
        entry = _DOC_CACHE.get(kind)

    if entry and (now - entry["fetched_at"]) < _DOC_CACHE_TTL:
        return entry["data"]

    # Cache miss or expired — run a fresh scan if not already recent.
    if (now - _FULL_SCAN_AT) >= _DOC_CACHE_TTL:
        try:
            _full_namespace_scan()
        except Exception:
            pass  # stale data or empty list returned below

    with _CACHE_LOCK:
        entry = _DOC_CACHE.get(kind)
    if entry:
        return entry["data"]

    # Kind genuinely absent from the namespace.
    with _CACHE_LOCK:
        _DOC_CACHE[kind] = {"data": [], "fetched_at": now}
    return []


def invalidate_doc_cache(*kinds):
    """Mark cached results as stale so the background thread refreshes them.

    We don't reset _FULL_SCAN_AT to 0 because that would trigger every
    concurrent request to race into _full_namespace_scan simultaneously
    (thundering herd). Instead we expire the specific kind entries so the
    next background loop (within 3.5 min) repopulates them cleanly.
    """
    with _CACHE_LOCK:
        if kinds:
            for k in kinds:
                _DOC_CACHE.pop(k, None)
        else:
            _DOC_CACHE.clear()


# ---------------------------------------------------------------------------
# Reviewer / admin helpers
# ---------------------------------------------------------------------------

def _normalize_email(email):
    return (email or "").strip().lower()


def _list_by_kind(kind):
    """Return docs as {id, name, email}, deduped by email (newest wins)."""
    out = []
    seen = set()
    for doc in list_docs_by_kind(kind):
        data = doc.get("data") or {}
        email = _normalize_email(data.get("email"))
        if email and email in seen:
            continue
        if email:
            seen.add(email)
        out.append({"id": doc.get("id"), "name": data.get("name", ""), "email": email})
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
    if any(a["email"] == normalized for a in list_admins()):
        return "admin"
    if any(r["email"] == normalized for r in list_reviewers()):
        return "reviewer"
    return "viewer"


def is_admin(email):
    return get_role(email) == "admin"


def create_record(kind, name, email):
    """Create a reviewer or admin record. Returns the Storage API doc id."""
    resp = internal_api.post(_STORAGE_PATH, json={"data": {
        "kind": kind,
        "name": (name or "").strip(),
        "email": _normalize_email(email),
    }})
    invalidate_doc_cache(kind)
    return resp["data"]["id"]


def update_record(doc_id, kind, name, email):
    internal_api.put(f"{_STORAGE_PATH}/{doc_id}", json={"data": {
        "kind": kind,
        "name": (name or "").strip(),
        "email": _normalize_email(email),
    }})
    invalidate_doc_cache(kind)


def delete_record(doc_id):
    internal_api.delete(f"{_STORAGE_PATH}/{doc_id}")
    invalidate_doc_cache()
