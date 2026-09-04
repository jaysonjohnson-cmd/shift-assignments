"""Shared pytest fixtures.

The role/document cache (`roles._DOC_CACHE`) is a module-level global that
persists for the life of the process. Across tests that leaks state — a kind
populated by one test makes the next test's cold-cache path behave differently.
Reset it before every test so cache-dependent tests are deterministic.

`list_docs_by_kind` now does a synchronous Storage scan on a cold cache, so an
unmocked test would otherwise make real network calls (with retry backoff).
Default-stub the Storage GET to an empty result; tests that need real data
monkeypatch it (or `list_docs_by_kind`) in their own body, which overrides this.

`main._self_heal_attempts` is module-level for the same reason and leaks the
same way: a self-heal refill attempted in one test would put the next test's
reviewer inside the cooldown window and silently skip its refill. Reset it too.
"""

import os

import pytest

# main.py starts the Bloom cache warmer at import so a fresh Cloud Run instance
# isn't cold. A background thread making real upstream calls would make the
# suite slow and non-hermetic, so opt out before any test module imports main.
os.environ["BLOOM_WARMER"] = "0"

import internal_api  # noqa: E402
import roles  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_doc_cache(monkeypatch):
    with roles._CACHE_LOCK:
        roles._DOC_CACHE.clear()
    monkeypatch.setattr(internal_api, "get", lambda *a, **k: {"data": []})
    _clear_self_heal_cooldown()
    yield
    with roles._CACHE_LOCK:
        roles._DOC_CACHE.clear()
    _clear_self_heal_cooldown()


def _clear_self_heal_cooldown():
    """Drop the per-reviewer self-heal cooldown between tests.

    Imported lazily: `main` pulls in Flask and the auth middleware, and the
    roles-only test modules should not have to pay for that just to get a
    clean cache.
    """
    try:
        import main
    except Exception:  # noqa: BLE001 — nothing to reset if main never loaded
        return
    with main._self_heal_lock:
        main._self_heal_attempts.clear()
