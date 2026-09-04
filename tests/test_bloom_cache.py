"""Tests for the Bloom feed cache: bounded staleness and single-flight.

/api/prioritized-jobs is one unpaginated request, but the upstream ranks ~500
jobs and takes 10-12s to answer. Nothing user-facing should ever pay that, so
the cache is warmed in the background, reads can accept bounded staleness, and
concurrent misses collapse into a single upstream call.
"""

import threading
import time

import pytest

import bloom


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch):
    # Never start the real warmer thread from a test.
    monkeypatch.setenv("BLOOM_WARMER", "0")
    bloom.clear_cache()
    bloom._CF_DENIED_CACHE.update({"fetched_at": 0.0, "min_date": None, "counts": {}})
    yield
    bloom.clear_cache()


def _stub_feed(monkeypatch, calls, jobs=None):
    """Count upstream calls; skip the CF-denied lookup (own cache, own test)."""
    payload = jobs if jobs is not None else [
        {"id": "1", "jid": "1", "new": 5, "name": "Job 1"},
    ]

    def fake_raw():
        calls.append(time.time())
        return payload

    monkeypatch.setattr(bloom, "_fetch_prioritized_jobs_raw", fake_raw)
    monkeypatch.setattr(bloom, "_earliest_start_date_iso", lambda jobs: None)


def test_max_age_accepts_a_slightly_stale_cache(monkeypatch):
    """A refill asking for <=120s-old rows is served from a 30s-old cache."""
    calls = []
    _stub_feed(monkeypatch, calls)

    bloom.fetch_prioritized_jobs()
    assert len(calls) == 1

    # Age the cache past the default 60s TTL but inside the caller's max_age.
    bloom._CACHE["fetched_at"] = time.time() - 90
    bloom.fetch_prioritized_jobs(max_age=120)
    assert len(calls) == 1, "should reuse the cache rather than refetch"


def test_max_age_refetches_once_the_cache_is_too_old(monkeypatch):
    """Past max_age it refetches, so a stalled warmer can't serve stale rankings."""
    calls = []
    _stub_feed(monkeypatch, calls)

    bloom.fetch_prioritized_jobs()
    bloom._CACHE["fetched_at"] = time.time() - 200
    bloom.fetch_prioritized_jobs(max_age=120)
    assert len(calls) == 2


def test_use_cache_false_still_forces_a_real_fetch(monkeypatch):
    """The Refresh button must bypass a warm cache, not be short-circuited by it."""
    calls = []
    _stub_feed(monkeypatch, calls)

    bloom.fetch_prioritized_jobs()
    bloom.fetch_prioritized_jobs(use_cache=False)
    assert len(calls) == 2


def test_concurrent_misses_collapse_into_one_upstream_call(monkeypatch):
    """Single-flight: a burst of reviewers finishing costs one 10-12s call, not N."""
    calls = []
    started = threading.Event()

    def slow_raw():
        calls.append(time.time())
        started.set()
        time.sleep(0.3)  # stand-in for the real ~11s
        return [{"id": "1", "jid": "1", "new": 5, "name": "Job 1"}]

    monkeypatch.setattr(bloom, "_fetch_prioritized_jobs_raw", slow_raw)
    monkeypatch.setattr(bloom, "_earliest_start_date_iso", lambda jobs: None)

    results = []
    threads = [
        threading.Thread(target=lambda: results.append(bloom.fetch_prioritized_jobs()))
        for _ in range(5)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(calls) == 1, f"expected one upstream call, got {len(calls)}"
    assert len(results) == 5
    assert all(len(r) == 1 for r in results), "every caller gets the rows"


def test_warmer_starts_once_and_is_opt_out(monkeypatch):
    """The warmer is lazy, single-start, and disabled by BLOOM_WARMER=0."""
    monkeypatch.setattr(bloom, "_WARMER_STARTED", False)
    spawned = []
    monkeypatch.setattr(bloom.threading, "Thread",
                        lambda *a, **k: spawned.append(k.get("name")) or type(
                            "T", (), {"start": lambda self: None})())

    monkeypatch.setenv("BLOOM_WARMER", "0")
    bloom._ensure_warmer_started()
    assert spawned == [], "opt-out must prevent the thread"

    monkeypatch.delenv("BLOOM_WARMER", raising=False)
    bloom._ensure_warmer_started()
    bloom._ensure_warmer_started()
    assert spawned == ["bloom-cache-warmer"], "exactly one warmer"
