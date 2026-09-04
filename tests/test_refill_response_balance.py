"""Tests for response-budget refills.

Auto-refill used to stop at a job count, so 20 two-response jobs and 20
forty-response jobs both counted as "a full batch". Small jobs vastly outnumber
big ones in a ~500-job queue, so reviewers were topped up with a long tail of
tiny jobs while heavy work sat unassigned. Refill now fills to a response
budget, biggest jobs first.
"""

import os

import pytest

os.environ["LOCAL_DEV"] = "1"

import main  # noqa: E402
import internal_api  # noqa: E402

REVIEWER = "sam@storesight.com"


def _feed(spec):
    """spec: {jobId: reviewable_responses}."""
    return [
        {"id": j, "jobId": j, "name": f"Job {j}", "priority": 1,
         "unreviewedCount": n, "oldestSubmission": "",
         "extras": {"newCount": n, "client": "someone@acme.com"}}
        for j, n in spec.items()
    ]


@pytest.fixture
def refill(monkeypatch):
    """Wire up a reviewer with a stamped batch, returning the stored rows."""
    stored = []

    def setup(feed, batch_size, batch_responses, flags=None):
        setup.stored = stored
        shift_doc = {"id": "rs-0", "data": {
            "kind": "reviewer_shift", "shift_snapshot_id": "snap1",
            "reviewer_email": REVIEWER, "rows": [], "part": 0,
            "batch_size": batch_size, "batch_responses": batch_responses,
        }}
        snap = {"id": "snap1", "data": {
            "kind": "shift_snapshot", "prioritization_flags": flags or {}}}

        monkeypatch.setattr(main.roles, "list_docs_by_kind", lambda kind, force=False: {
            "reviewer_shift": [shift_doc], "shift_snapshot": [snap],
        }.get(kind, []))
        monkeypatch.setattr(main, "_list_completions_for_snapshot",
                            lambda *a, **k: [])
        monkeypatch.setattr(main.bloom, "fetch_prioritized_jobs", lambda *a, **k: feed)
        monkeypatch.setattr(main.bloom, "is_excluded_client", lambda c: False)
        monkeypatch.setattr(internal_api, "post",
                            lambda path, json=None: stored.append(json) or {"data": {"id": "new"}})
        monkeypatch.setattr(main, "_try_delete", lambda *a, **k: None)
        monkeypatch.setattr(main.roles, "cache_upsert_doc", lambda *a, **k: None)
        return main._auto_refill_reviewer("snap1", REVIEWER, batch_size)

    return setup


def _responses(rows):
    return sum(int(r.get("unreviewedCount") or 0) for r in rows)


def test_takes_big_jobs_before_small_ones(refill):
    """The whole complaint: heavy jobs should be picked over a tail of tiny ones."""
    feed = _feed({"tiny1": 1, "tiny2": 2, "big1": 60, "tiny3": 1, "big2": 55})
    added = refill(feed, batch_size=20, batch_responses=100)

    picked = {r["jobId"] for r in added}
    assert "big1" in picked and "big2" in picked, f"big jobs skipped: {picked}"


def test_a_budget_stops_one_reviewer_taking_every_big_job(refill):
    """Big-first must not mean one reviewer drains the entire heavy end."""
    feed = _feed({f"big{i}": 50 for i in range(10)})
    added = refill(feed, batch_size=20, batch_responses=100)

    # Budget 100 with 25% slack admits two 50-response jobs, not all ten.
    assert len(added) == 2, f"took {len(added)} big jobs, expected 2"
    assert _responses(added) == 100
    # Eight remain in the pool for whoever finishes next.
    assert len(added) < len(feed)


def test_small_jobs_yield_more_of_them_not_less_work(refill):
    """A tiny-job queue should produce many jobs, not a token handful."""
    feed = _feed({f"tiny{i}": 2 for i in range(60)})
    added = refill(feed, batch_size=20, batch_responses=100)

    # Job ceiling caps the count, but the reviewer gets real volume either way.
    assert len(added) == 20, f"expected the job ceiling to cap at 20, got {len(added)}"
    assert _responses(added) == 40


def test_job_ceiling_still_bounds_a_huge_tiny_job_queue(refill):
    """count remains a ceiling so a budget can't produce a 200-job batch."""
    feed = _feed({f"t{i}": 1 for i in range(500)})
    added = refill(feed, batch_size=15, batch_responses=400)
    assert len(added) == 15


def test_one_job_is_always_offered_even_if_it_busts_the_budget(refill):
    """A reviewer with a small previous batch must not be starved by a big queue."""
    feed = _feed({"huge": 900})
    added = refill(feed, batch_size=20, batch_responses=10)

    assert [r["jobId"] for r in added] == ["huge"], "should still get work to do"


def test_aged_jobs_still_come_first_when_that_flag_is_set(refill):
    """Response ordering sorts within the aged tier, never across it."""
    feed = _feed({"aged_small": 3, "fresh_huge": 400})
    feed[0]["extras"]["old_sub"] = 5   # aged_small is CF-denied work
    feed[1]["extras"]["old_sub"] = 0

    added = refill(feed, batch_size=20, batch_responses=50,
                   flags={"prioritizeAged": True})
    assert added[0]["jobId"] == "aged_small", "aged tier must outrank raw size"


def test_batch_responses_is_stamped_so_later_refills_keep_the_budget(refill):
    """Each refill records its budget, so the next top-up doesn't drift."""
    feed = _feed({"big1": 60, "big2": 55})
    refill(feed, batch_size=20, batch_responses=100)

    shift_writes = [
        d["data"] for d in refill.stored
        if (d or {}).get("data", {}).get("kind") == "reviewer_shift"
    ]
    assert shift_writes, "refill should have persisted a new part"
    assert shift_writes[0]["batch_responses"] == 100
    assert shift_writes[0]["batch_size"] == 20


def test_one_reviewer_cannot_take_every_large_job_in_a_skewed_queue(refill):
    """The real queue shape: a few heavy jobs buried in hundreds of tiny ones.

    Budget alone doesn't protect this case — with a 300-response budget the first
    reviewer to finish would swallow the 122, 97 and 90 jobs in one top-up. The
    large-job cap leaves the rest for whoever finishes next.
    """
    feed = _feed({"a": 122, "b": 97, "c": 90, "d": 45,
                  **{f"tiny{i}": 2 for i in range(200)}})
    added = refill(feed, batch_size=20, batch_responses=300)

    sizes = sorted((int(r["unreviewedCount"]) for r in added), reverse=True)
    large = [n for n in sizes if n >= 10]
    assert len(large) == 2, f"expected 2 large jobs, got {large}"
    assert large == [122, 97], "should take the biggest two available"
    # 90 and 45 stay in the pool for the next reviewer.
    assert 90 not in sizes and 45 not in sizes
    # The rest of the budget is filled out with small jobs, so it's still real work.
    assert len(added) > 2, "batch should be topped up with smaller jobs"


def test_large_is_relative_so_an_all_big_queue_is_governed_by_budget(refill):
    """When every job is heavy, none is an outlier — the budget does the limiting."""
    feed = _feed({f"big{i}": 50 for i in range(10)})
    added = refill(feed, batch_size=20, batch_responses=100)
    assert len(added) == 2, "budget, not the large-cap, should bound this"
