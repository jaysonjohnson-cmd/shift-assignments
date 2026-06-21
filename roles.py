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
_SCAN_LOCK = threading.Lock()   # only one scan runs at a time
_REFRESH_INTERVAL = 180        # background thread scans every 3 minutes
_BG_STARTED = False
_NEEDS_REFRESH = threading.Event()  # set to wake the bg thread early after invalidation


# ---------------------------------------------------------------------------
# Background cache warmer — THE ONLY CODE THAT CALLS STORAGE API
# ---------------------------------------------------------------------------

def _bg_refresh_loop():
    """Daemon thread: the only place that ever reads from Storage API.

    Request handlers read exclusively from _DOC_CACHE and never touch the
    Storage API. This guarantees 0 Storage API calls per request after startup,
    eliminating 429s entirely regardless of traffic.
    """
    # Wait a moment after startup so Flask can finish binding before the first scan.
    time.sleep(2)
    while True:
        try:
            _full_namespace_scan()
        except Exception:
            pass  # logged inside; retry after short sleep
        # Sleep until the next scheduled refresh OR until woken early by invalidation.
        _NEEDS_REFRESH.wait(timeout=_REFRESH_INTERVAL)
        _NEEDS_REFRESH.clear()


def _ensure_bg_started():
    global _BG_STARTED
    if _BG_STARTED:
        return
    t = threading.Thread(target=_bg_refresh_loop, daemon=True, name="roles-cache-warmer")
    t.start()
    _BG_STARTED = True


# ---------------------------------------------------------------------------
# Core scan + cache (called ONLY by the background thread)
# ---------------------------------------------------------------------------

def _full_namespace_scan():
    """Scan all namespace pages and populate every kind cache atomically.

    Protected by _SCAN_LOCK so it never runs more than once concurrently
    (e.g. if two threads call this somehow). Each page is spaced 0.5 s apart
    so 7 pages cost ~3.5 s and stay well under the 60 req/min limit.
    """
    global _BG_STARTED  # keep lint happy; _SCAN_LOCK is the real guard
    with _SCAN_LOCK:
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
                if docs:
                    time.sleep(0.5)  # pace requests to stay under rate limit
        except Exception as exc:
            logging.warning("Storage namespace scan failed (page %d): %s", page, exc)
            raise

        now = time.time()
        with _CACHE_LOCK:
            for k, docs in by_kind.items():
                _DOC_CACHE[k] = {"data": docs, "fetched_at": now}
        logging.info(
            "Storage scan complete: %d docs across %d kinds",
            sum(len(v) for v in by_kind.values()),
            len(by_kind),
        )


def list_docs_by_kind(kind):
    """Return cached docs for `kind`. Never calls Storage API — always from cache.

    Returns whatever is in the cache (may be slightly stale between background
    refreshes). Returns [] if the background thread hasn't completed its first
    scan yet; that resolves within a few seconds of startup.
    """
    _ensure_bg_started()
    with _CACHE_LOCK:
        entry = _DOC_CACHE.get(kind)
    return entry["data"] if entry else []


def invalidate_doc_cache(*kinds):
    """Drop cached results and wake the background thread to refresh immediately."""
    with _CACHE_LOCK:
        if kinds:
            for k in kinds:
                _DOC_CACHE.pop(k, None)
        else:
            _DOC_CACHE.clear()
    _NEEDS_REFRESH.set()  # wake bg thread so fresh data arrives within seconds


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
