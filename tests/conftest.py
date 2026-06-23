"""Shared pytest fixtures.

The role/document cache (`roles._DOC_CACHE`) is a module-level global that
persists for the life of the process. Across tests that leaks state — a kind
populated by one test makes the next test's cold-cache path behave differently.
Reset it before every test so cache-dependent tests are deterministic.

`list_docs_by_kind` now does a synchronous Storage scan on a cold cache, so an
unmocked test would otherwise make real network calls (with retry backoff).
Default-stub the Storage GET to an empty result; tests that need real data
monkeypatch it (or `list_docs_by_kind`) in their own body, which overrides this.
"""

import pytest

import internal_api
import roles


@pytest.fixture(autouse=True)
def _reset_doc_cache(monkeypatch):
    with roles._CACHE_LOCK:
        roles._DOC_CACHE.clear()
    monkeypatch.setattr(internal_api, "get", lambda *a, **k: {"data": []})
    yield
    with roles._CACHE_LOCK:
        roles._DOC_CACHE.clear()
