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
    # Wait a moment after startup so Flask finishes binding before the first scan.
    time.sleep(2)
    retry_delay = 10  # seconds to wait after a failed scan before retrying
    while True:
        try:
            _full_namespace_scan(force=True)
            # Success — wait the full refresh interval (or wake early on invalidation).
            _NEEDS_REFRESH.wait(timeout=_REFRESH_INTERVAL)
            _NEEDS_REFRESH.clear()
            retry_delay = 10  # reset backoff after a successful scan
        except Exception:
            # Scan failed (e.g. 429 on startup). Retry quickly with backoff,
            # not after the full 3-minute interval — cache is empty until we succeed.
            logging.info("Cache scan failed; retrying in %ds", retry_delay)
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)  # cap at 60s


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

def _full_namespace_scan(want_kind=None, force=False):
    """Scan all namespace pages and populate every kind cache atomically.

    Protected by _SCAN_LOCK so it never runs more than once concurrently
    (e.g. if two threads call this somehow). Each page is spaced 0.5 s apart
    so 7 pages cost ~3.5 s and stay well under the 60 req/min limit.
    """
    global _BG_STARTED  # keep lint happy; _SCAN_LOCK is the real guard
    with _SCAN_LOCK:
        # Stampede guard: if several requests hit a cold cache at once, they
        # queue on _SCAN_LOCK. The first scans and populates the cache; the rest
        # find their kind already present and skip re-scanning. State-based (not
        # time-based) so there's no cross-call coupling. The bg loop passes
        # force=True to always refresh on its schedule.
        if not force and want_kind is not None:
            with _CACHE_LOCK:
                if _DOC_CACHE.get(want_kind) is not None:
                    return
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


def list_docs_by_kind(kind, force=False):
    """Return docs for `kind`, normally from the warm cache.

    Two cases trigger a synchronous Storage API scan instead of returning
    possibly-empty cached data:

    * ``force=True`` — the caller needs authoritative data (e.g. the
      "did this reviewer finish?" check before sending a Slack ping).
    * the cache for this kind is *cold* (no entry yet) — this happens right
      after startup or right after an ``invalidate_doc_cache`` wipe. Returning
      ``[]`` here is what produced wrong roles on cold start and missed finish
      pings. A forced scan is coalesced (see ``_SCAN_COALESCE_WINDOW``) so a
      burst of concurrent cold reads costs a single scan, not one each.
    """
    _ensure_bg_started()
    with _CACHE_LOCK:
        entry = _DOC_CACHE.get(kind)
    if force or entry is None:
        try:
            _full_namespace_scan(want_kind=kind, force=force)
        except Exception as exc:  # noqa: BLE001 — fall back to whatever we have
            logging.warning("synchronous scan for %s failed: %s", kind, exc)
        with _CACHE_LOCK:
            entry = _DOC_CACHE.get(kind)
    return entry["data"] if entry else []


def invalidate_doc_cache(*kinds):
    """Drop cached results and wake the background thread to refresh immediately.

    Used by bulk operations (publish, clear). Popped kinds become *cold*, so the
    next ``list_docs_by_kind`` for them does a synchronous refresh rather than
    serving stale/empty data. Hot single-doc writes should prefer
    ``cache_upsert_doc`` / ``cache_remove_doc`` to stay fast.
    """
    with _CACHE_LOCK:
        if kinds:
            for k in kinds:
                _DOC_CACHE.pop(k, None)
        else:
            _DOC_CACHE.clear()
    _NEEDS_REFRESH.set()  # wake bg thread so fresh data arrives within seconds


def cache_upsert_doc(kind, doc):
    """Reflect a single just-written doc in the warm cache immediately.

    Lets read-after-write be consistent on this instance without waiting for the
    next background scan. Only mutates an already-populated cache — when the
    kind's cache is cold we must not fabricate a one-doc list, so we just wake the
    background thread and let the next read do a full (coalesced) scan.
    """
    doc_id = (doc or {}).get("id")
    if not kind or not doc_id:
        _NEEDS_REFRESH.set()
        return
    with _CACHE_LOCK:
        entry = _DOC_CACHE.get(kind)
        if entry is not None:
            docs = [d for d in entry["data"] if d.get("id") != doc_id]
            docs.append(doc)
            _DOC_CACHE[kind] = {"data": docs, "fetched_at": entry["fetched_at"]}
    _NEEDS_REFRESH.set()


def cache_remove_doc(kind, doc_id):
    """Drop a single just-deleted doc from the warm cache immediately."""
    if not kind or not doc_id:
        _NEEDS_REFRESH.set()
        return
    with _CACHE_LOCK:
        entry = _DOC_CACHE.get(kind)
        if entry is not None:
            docs = [d for d in entry["data"] if d.get("id") != doc_id]
            _DOC_CACHE[kind] = {"data": docs, "fetched_at": entry["fetched_at"]}
    _NEEDS_REFRESH.set()


# ---------------------------------------------------------------------------
# Reviewer / admin helpers
# ---------------------------------------------------------------------------

def _normalize_email(email):
    return (email or "").strip().lower()


def _list_by_kind(kind):
    """Return docs as {id, name, email, color}, deduped by email (newest wins)."""
    out = []
    seen = set()
    for doc in list_docs_by_kind(kind):
        data = doc.get("data") or {}
        email = _normalize_email(data.get("email"))
        if email and email in seen:
            continue
        if email:
            seen.add(email)
        out.append({
            "id": doc.get("id"),
            "name": data.get("name", ""),
            "email": email,
            "color": data.get("color") or None,
        })
    return out


def list_reviewers():
    return _list_by_kind("reviewer")


def list_admins():
    return _list_by_kind("admin")


def list_leads():
    return _list_by_kind("lead")


def get_role(email):
    """Return "admin" | "lead" | "reviewer" | "viewer" for the given email."""
    normalized = _normalize_email(email)
    if not normalized:
        return "viewer"
    if normalized == _normalize_email(ROOT_ADMIN_EMAIL):
        return "admin"
    # Never let a Storage API failure crash the request that resolves a role —
    # degrade to viewer (least privilege). The root admin is handled above so
    # the system is never locked out even when the roster is unreadable.
    try:
        if any(a["email"] == normalized for a in list_admins()):
            return "admin"
        if any(l["email"] == normalized for l in list_leads()):
            return "lead"
        if any(r["email"] == normalized for r in list_reviewers()):
            return "reviewer"
    except Exception:  # noqa: BLE001 — any roster read failure degrades to viewer
        logging.warning("role lookup failed for %s; defaulting to viewer", normalized)
        return "viewer"
    return "viewer"


def is_admin(email):
    return get_role(email) == "admin"


def is_admin_or_lead(email):
    return get_role(email) in ("admin", "lead")


def create_record(kind, name, email, color=None):
    """Create a reviewer or admin record. Returns the Storage API doc id."""
    data = {
        "kind": kind,
        "name": (name or "").strip(),
        "email": _normalize_email(email),
    }
    if color:
        data["color"] = color
    resp = internal_api.post(_STORAGE_PATH, json={"data": data})
    new_id = resp["data"]["id"]
    cache_upsert_doc(kind, {"id": new_id, "data": data})
    return new_id


def update_record(doc_id, kind, name, email, color=None):
    data = {
        "kind": kind,
        "name": (name or "").strip(),
        "email": _normalize_email(email),
    }
    if color:
        data["color"] = color
    internal_api.put(f"{_STORAGE_PATH}/{doc_id}", json={"data": data})
    cache_upsert_doc(kind, {"id": doc_id, "data": data})


def delete_record(doc_id):
    internal_api.delete(f"{_STORAGE_PATH}/{doc_id}")
    invalidate_doc_cache()
