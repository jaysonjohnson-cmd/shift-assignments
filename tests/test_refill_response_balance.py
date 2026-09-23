"""Tests for response-budget refills.

Auto-refill used to stop at a job count, so 20 two-response jobs and 20
forty-response jobs both counted as "a full batch". Small jobs vastly outnumber
big ones in a ~500-job queue, so reviewers were topped up with a long tail of
tiny jobs while heavy work sat unassigned. Refill now fills to a response
budget, biggest jobs first.
"""

import datetime
import os
import pathlib

import pytest

os.environ["LOCAL_DEV"] = "1"

import main  # noqa: E402
import internal_api  # noqa: E402

REVIEWER = "sam@storesight.com"


def _feed(spec, **extra):
    """spec: {jobId: reviewable_responses}; extra merges in more of the same."""
    spec = {**spec, **extra}
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

    def setup(feed, batch_size, batch_responses, flags=None, teammates=0):
        """`teammates` puts others on shift; the large-job cap divides by that."""
        setup.stored = stored
        shift_docs = [{"id": "rs-0", "data": {
            "kind": "reviewer_shift", "shift_snapshot_id": "snap1",
            "reviewer_email": REVIEWER, "rows": [], "part": 0,
            "batch_size": batch_size, "batch_responses": batch_responses,
        }}]
        shift_docs += [{"id": f"rs-mate{i}", "data": {
            "kind": "reviewer_shift", "shift_snapshot_id": "snap1",
            "reviewer_email": f"mate{i}@storesight.com", "rows": [], "part": 0,
            "batch_size": batch_size, "batch_responses": batch_responses,
        }} for i in range(teammates)]
        snap = {"id": "snap1", "data": {
            "kind": "shift_snapshot", "prioritization_flags": flags or {}}}

        monkeypatch.setattr(main.roles, "list_docs_by_kind", lambda kind, force=False: {
            "reviewer_shift": shift_docs, "shift_snapshot": [snap],
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
    added = refill(feed, batch_size=20, batch_responses=100,
                   flags={"balanceByResponses": True}, teammates=1)

    # Biggest first, so the heavy job leads the batch rather than trailing it.
    assert added[0]["jobId"] == "big1"
    # Two heavy jobs and two reviewers on shift is one each, so big2 waits for
    # the teammate. Taking both would hand one reviewer the whole heavy end.
    assert "big2" not in [r["jobId"] for r in added]


def test_refill_fills_to_the_job_target(refill):
    """The reported complaint: top-ups of ~2 jobs while the feed was full.

    A response budget used to stop the loop, and two heavy jobs spent it
    outright. The reviewer's own allotment is the target now — heaviest first,
    then progressively smaller, until the count is met.
    """
    feed = _feed({"h1": 80, "h2": 70, "h3": 60,
                  **{f"s{i}": 3 for i in range(40)}})
    added = refill(feed, batch_size=20, batch_responses=100,
                   flags={"balanceByResponses": True})

    assert len(added) == 20, f"expected a full batch of 20, got {len(added)}"
    # Heaviest first, so the big work leads rather than trails.
    assert added[0]["jobId"] == "h1"
    # The rest is filled out with smaller jobs to reach the count. How many of
    # the heavy three get in is _REFILL_MAX_LARGE_SHARE's call, not the
    # batch size's — the point here is that the batch fills either way.
    assert len(added) > sum(1 for r in added if int(r["unreviewedCount"]) >= 60)


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
    """Response ordering sorts within the aged tier, never across it.

    `balanceByResponses` is on so the assertion actually discriminates: without
    the aged tier, size ordering puts fresh_huge (400) ahead of aged_small (3).
    The earlier version of this test left it off, so feed order alone satisfied
    the assertion and it passed even with the aged tier removed entirely.
    """
    feed = _feed({"aged_small": 3, "fresh_huge": 400})
    feed[0]["extras"]["agedCount"] = 5   # aged_small holds aged pending work
    feed[1]["extras"]["agedCount"] = 0

    added = refill(feed, batch_size=20, batch_responses=50,
                   flags={"prioritizeAged": True, "balanceByResponses": True})
    assert added[0]["jobId"] == "aged_small", "aged tier must outrank raw size"


def test_aged_tier_ignores_the_feeds_own_old_sub_score(refill):
    """`old_sub` must not drive the aged tier.

    It is a priority *score* ("points for submissions older than 3 days"), not
    a count, and it reads 0 on every row the upstream returns — so a job whose
    only aged signal is old_sub has no aged work as far as we can tell, and
    must not jump the queue.
    """
    feed = _feed({"scored_small": 3, "fresh_huge": 400})
    feed[0]["extras"]["old_sub"] = 250   # score only, no measured aged rows
    feed[1]["extras"]["old_sub"] = 0

    added = refill(feed, batch_size=20, batch_responses=50,
                   flags={"prioritizeAged": True, "balanceByResponses": True})
    assert added[0]["jobId"] == "fresh_huge", "old_sub must not promote a job"


def test_refill_skips_fresh_auto_reject_only_jobs(refill):
    """0 reviewable and not aged → My Tasks hides it, so never refill with it."""
    feed = _feed({"normal": 5, "fresh_ar": 0})
    feed[1]["extras"]["newCount"] = 6   # 6 responses, none reviewable

    added = refill(feed, batch_size=20, batch_responses=100)
    assert [r["jobId"] for r in added] == ["normal"]


def test_refill_takes_aged_auto_reject_only_jobs(refill):
    """Once aged, clear-only work is visible in My Tasks — so it is refillable.

    Without this the job can never be worked: the composer and auto-refill both
    passed over it for having 0 reviewable responses, which is why it aged in
    the first place.
    """
    feed = _feed({"normal": 5, "aged_ar": 0})
    feed[1]["extras"]["newCount"] = 6
    feed[1]["extras"]["agedCount"] = 6

    added = refill(feed, batch_size=20, batch_responses=100)
    assert sorted(r["jobId"] for r in added) == ["aged_ar", "normal"]


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
    """The real queue shape: heavy jobs buried in hundreds of tiny ones.

    Budget alone doesn't protect this case — a big enough budget lets the first
    reviewer to finish swallow the entire heavy end in one top-up. The large-job
    cap leaves the rest for whoever finishes next.

    Sized with more large jobs than the cap on purpose. A real queue often holds
    only a handful above the threshold, and when there are fewer of them than
    _REFILL_MAX_LARGE_JOBS the cap never binds and one reviewer does take them
    all — so this fixture has to exceed the cap to exercise it at all.
    """
    feed = _feed({f"big{i}": 50 for i in range(10)},
                 **{f"tiny{i}": 2 for i in range(200)})
    added = refill(feed, batch_size=20, batch_responses=400,
                   flags={"balanceByResponses": True})

    sizes = sorted((int(r["unreviewedCount"]) for r in added), reverse=True)
    large = [n for n in sizes if n >= 10]
    assert len(large) == main._REFILL_MAX_LARGE_JOBS, (
        f"expected {main._REFILL_MAX_LARGE_JOBS} large jobs, got {large}"
    )
    # The remaining heavy jobs stay in the pool for the next reviewer.
    assert len(large) < 10
    # The rest of the budget is filled out with small jobs, so it's still real work.
    assert len(added) > len(large), "batch should be topped up with smaller jobs"


def test_a_short_pool_gives_what_it_has(refill):
    """Fewer jobs available than the target means take them all, not a fraction.

    Every job here is the same size, so none is an outlier and the large-job
    cap never engages — "large" is relative to the pool median. With no budget
    left to stop it, the batch is bounded only by what exists.
    """
    feed = _feed({f"big{i}": 50 for i in range(10)})
    added = refill(feed, batch_size=20, batch_responses=100)

    assert len(added) == len(feed) == 10


# --- balanceByResponses: the flag actually drives the ordering now -----------

def test_flag_on_orders_biggest_first(refill):
    """balanceByResponses is read from the snapshot and picks the heavy jobs."""
    feed = _feed({"small_first": 3, "huge": 200})
    added = refill(feed, batch_size=20, batch_responses=200,
                   flags={"balanceByResponses": True})
    assert added[0]["jobId"] == "huge"


def test_flag_off_keeps_the_feeds_own_priority_order(refill):
    """With it off, the feed's ranking stands — size no longer reorders."""
    feed = _feed({"small_first": 3, "huge": 200})
    added = refill(feed, batch_size=20, batch_responses=200,
                   flags={"balanceByResponses": False})
    assert added[0]["jobId"] == "small_first"


def test_budget_applies_even_with_the_flag_off(refill):
    """The workload fix is unconditional: never 20 jobs of trivial work again."""
    feed = _feed({f"tiny{i}": 1 for i in range(400)})
    added = refill(feed, batch_size=20, batch_responses=300,
                   flags={"balanceByResponses": False})
    # Job ceiling caps it, but it is the budget — not a job count — doing the work.
    assert len(added) == 20
    assert _responses(added) == 20


# --- the rest of the composer's options, honoured on refill ------------------

def test_prioritize_new_puts_fresh_submissions_first(refill):
    """prioritizeNew was persisted at publish but never read on the refill path."""
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    feed = _feed({"old_huge": 300, "brand_new": 4})
    feed[0]["oldestSubmission"] = (now - datetime.timedelta(days=9)).isoformat()
    feed[1]["oldestSubmission"] = (now - datetime.timedelta(minutes=5)).isoformat()

    added = refill(feed, batch_size=20, batch_responses=300,
                   flags={"prioritizeNew": True, "balanceByResponses": True})
    assert added[0]["jobId"] == "brand_new", "new tier must outrank raw size"


def test_urgency_is_a_tier_not_a_whole_pool_sort(refill):
    """An urgent job leads even when a much larger non-urgent one exists."""
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    feed = _feed({"fat_relaxed": 300, "urgent_small": 5})
    # Deadline tomorrow + old responses clears the 55-point threshold.
    feed[1]["extras"]["endDate"] = (now + datetime.timedelta(hours=20)).isoformat()
    feed[1]["oldestSubmission"] = (now - datetime.timedelta(days=20)).isoformat()
    feed[0]["extras"]["endDate"] = (now + datetime.timedelta(days=90)).isoformat()
    feed[0]["oldestSubmission"] = now.isoformat()

    added = refill(feed, batch_size=20, batch_responses=300,
                   flags={"prioritizeUrgency": True, "balanceByResponses": True})
    assert added[0]["jobId"] == "urgent_small"


def test_retail_pipeline_only_keeps_the_refill_scoped(refill):
    """A client-scoped shift must not top up with everyone else's work."""
    feed = _feed({"retail_job": 40, "other_client_job": 200})
    feed[0]["extras"]["client"] = "retailpipeline@fieldagent.net"
    feed[1]["extras"]["client"] = "someone@acme.com"

    added = refill(feed, batch_size=20, batch_responses=300,
                   flags={"retailPipelineOnly": True, "balanceByResponses": True})
    assert [r["jobId"] for r in added] == ["retail_job"]


def test_no_flags_means_no_tiering(refill):
    """With nothing set every job is one tier and the feed's order stands."""
    feed = _feed({"first": 2, "second": 3})
    added = refill(feed, batch_size=20, batch_responses=10, flags={})
    assert [r["jobId"] for r in added] == ["first", "second"]


def test_special_job_types_keeps_the_refill_scoped(refill):
    """A Special-Job-Types shift must not top up with ordinary work.

    The filter mirrors the composer's rule in app/assignments/page.tsx: a
    case-insensitive substring match for "ratings & review" (so both the
    singular and plural job names hit), "part 1" and "part 2".
    """
    feed = _feed({"rr_plural": 10, "rr_singular": 10, "p1": 10, "p2": 10, "ordinary": 200})
    names = {
        "rr_plural": "Acme Ratings & Reviews - Spring",
        "rr_singular": "Acme Ratings & Review - Spring",
        "p1": "Acme Audit Part 1/2",
        "p2": "Acme Audit Part 2/2",
        "ordinary": "Acme Shelf Check",
    }
    for row in feed:
        row["name"] = names[row["jobId"]]

    added = refill(feed, batch_size=20, batch_responses=300,
                   flags={"specialJobTypes": True})

    assert sorted(r["jobId"] for r in added) == ["p1", "p2", "rr_plural", "rr_singular"]


def _refill_with_team(monkeypatch, feed, holders, completions, target=REVIEWER,
                      batch_size=5):
    """Run a refill where teammates already hold rows.

    `holders`: {reviewer_email: [job_key, ...]} currently in their shift doc.
    `completions`: [(reviewer_email, job_key), ...] marked done.
    """
    stored = []
    shift_docs = []
    for i, (rev, keys) in enumerate(holders.items()):
        shift_docs.append({"id": f"rs-{i}", "data": {
            "kind": "reviewer_shift", "shift_snapshot_id": "snap1",
            "reviewer_email": rev, "part": 0, "batch_size": batch_size,
            "batch_responses": 100,
            "rows": [{"jobId": k, "id": k, "unreviewedCount": 1} for k in keys],
        }})
    snap = {"id": "snap1", "data": {"kind": "shift_snapshot", "prioritization_flags": {}}}
    monkeypatch.setattr(main.roles, "list_docs_by_kind", lambda kind, force=False: {
        "reviewer_shift": shift_docs, "shift_snapshot": [snap],
    }.get(kind, []))
    # Real completion docs carry an id; the supersede path needs it to PUT.
    monkeypatch.setattr(
        main, "_list_completions_for_snapshot",
        lambda *a, **k: [{"id": f"c-{rev}-{job}", "reviewer_email": rev,
                          "job_id": job, "shift_snapshot_id": "snap1"}
                         for rev, job in completions])
    monkeypatch.setattr(main.bloom, "fetch_prioritized_jobs", lambda *a, **k: feed)
    monkeypatch.setattr(main.bloom, "is_excluded_client", lambda c: False)
    monkeypatch.setattr(internal_api, "post",
                        lambda path, json=None: stored.append(json) or {"data": {"id": "new"}})
    monkeypatch.setattr(main, "_try_delete", lambda *a, **k: None)
    monkeypatch.setattr(main.roles, "cache_upsert_doc", lambda *a, **k: None)
    return main._auto_refill_reviewer("snap1", target, batch_size)


def test_refill_skips_a_job_a_teammate_still_holds_open(monkeypatch):
    """One reviewer completing a job must not release it from everyone else.

    Reproduces production job 1971325: Saylor completed it, and because the
    exclusion set subtracted a flat set of completed keys, the job lost its
    exclusion entirely and was handed out again on later refills — ending up
    pending in three queues at once.
    """
    feed = _feed({"shared": 1, "fresh": 1})
    added = _refill_with_team(
        monkeypatch, feed,
        holders={
            REVIEWER: [],
            "saylor@storesight.com": ["shared"],   # completed it
            "hudson@storesight.com": ["shared"],   # still working it
        },
        completions=[("saylor@storesight.com", "shared")],
    )
    assert [r["jobId"] for r in added] == ["fresh"], (
        "a job another reviewer still has open must not be reassigned"
    )


def test_refill_reopens_a_job_once_every_holder_has_finished(monkeypatch):
    """The release still works — it just needs ALL holders done, not one.

    This is the behaviour the flat subtraction existed for: the feed takes new
    submissions all day, so a job everyone has finished must become reachable
    again rather than staying excluded for the rest of the shift.
    """
    feed = _feed({"shared": 1})
    added = _refill_with_team(
        monkeypatch, feed,
        holders={
            REVIEWER: [],
            "saylor@storesight.com": ["shared"],
            "hudson@storesight.com": ["shared"],
        },
        completions=[("saylor@storesight.com", "shared"),
                     ("hudson@storesight.com", "shared")],
    )
    assert [r["jobId"] for r in added] == ["shared"], (
        "once every holder is done the job is assignable again"
    )


def test_refill_retires_the_checkmark_when_a_job_comes_back(monkeypatch):
    """A job handed back after new responses must not arrive already Done.

    My Tasks matches completions by job id alone, so a returning row would
    render with its old checkmark and the new responses would be unreachable —
    the "Done, 1 response" rows reviewers were seeing.
    """
    puts = []
    monkeypatch.setattr(internal_api, "put",
                        lambda path, json=None: puts.append((path, json)) or {"data": {}})
    feed = _feed({"comeback": 1})
    added = _refill_with_team(
        monkeypatch, feed,
        holders={REVIEWER: []},
        completions=[(REVIEWER, "comeback")],
    )

    assert [r["jobId"] for r in added] == ["comeback"], "job should be reassigned"
    assert len(puts) == 1, "its completion should have been stamped"
    assert puts[0][1]["data"]["superseded_at"], "stamped, not deleted"
    assert puts[0][1]["data"]["job_id"] == "comeback"


def test_superseded_completions_stop_counting_as_done(monkeypatch):
    """Once stamped, a completion is invisible to every 'is this done?' read."""
    docs = [
        {"id": "c1", "data": {"kind": "completion", "shift_snapshot_id": "snap1",
                              "reviewer_email": REVIEWER, "job_id": "a"}},
        {"id": "c2", "data": {"kind": "completion", "shift_snapshot_id": "snap1",
                              "reviewer_email": REVIEWER, "job_id": "b",
                              "superseded_at": "2026-09-22T12:00:00+00:00"}},
    ]
    monkeypatch.setattr(main.roles, "list_docs_by_kind",
                        lambda kind, force=False: docs if kind == "completion" else [])

    active = main._list_completions_for_snapshot("snap1", reviewer_email=REVIEWER)
    assert [c["job_id"] for c in active] == ["a"]

    everything = main._list_completions_for_snapshot(
        "snap1", reviewer_email=REVIEWER, include_superseded=True)
    assert sorted(c["job_id"] for c in everything) == ["a", "b"], (
        "the doc is kept so an admin dump and purge still see it"
    )


def _refill_with_lock(monkeypatch, feed, lock_docs, target=REVIEWER):
    """Run a refill with the given refill_lock docs already in storage."""
    stored = []
    shift_docs = [{"id": "rs-0", "data": {
        "kind": "reviewer_shift", "shift_snapshot_id": "snap1",
        "reviewer_email": target, "rows": [], "part": 0,
        "batch_size": 5, "batch_responses": 100,
    }}]
    snap = {"id": "snap1", "data": {"kind": "shift_snapshot", "prioritization_flags": {}}}
    monkeypatch.setattr(main.roles, "list_docs_by_kind", lambda kind, force=False: {
        "reviewer_shift": shift_docs, "shift_snapshot": [snap],
        "refill_lock": lock_docs,
    }.get(kind, []))
    monkeypatch.setattr(main, "_list_completions_for_snapshot", lambda *a, **k: [])
    monkeypatch.setattr(main.bloom, "fetch_prioritized_jobs", lambda *a, **k: feed)
    monkeypatch.setattr(main.bloom, "is_excluded_client", lambda c: False)
    monkeypatch.setattr(internal_api, "post",
                        lambda path, json=None: stored.append(json) or {"data": {"id": "new"}})
    monkeypatch.setattr(main, "_try_delete", lambda *a, **k: None)
    monkeypatch.setattr(main.roles, "cache_upsert_doc", lambda *a, **k: None)
    return main._auto_refill_reviewer("snap1", target, 5)


def _lock(reviewer, snap="snap1", age_seconds=0):
    when = (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(seconds=age_seconds)).isoformat()
    return {"id": f"lock-{reviewer}", "data": {
        "kind": "refill_lock", "shift_snapshot_id": snap,
        "reviewer_email": reviewer, "locked_at": when}}


def test_refill_waits_on_another_reviewers_refill(monkeypatch):
    """The lock covers the shared pool, so ANY in-flight refill blocks —
    after waiting it out for a few seconds, not on the first check.

    Scoped per reviewer it only stopped someone double-refilling themselves,
    which is not the race that hands two people the same job: two refills for
    different reviewers overlap on the read-modify-write and both take the
    same free jobs.
    """
    monkeypatch.setattr(main.time, "sleep", lambda *a, **k: None)
    added = _refill_with_lock(
        monkeypatch, _feed({"a": 1, "b": 1}),
        lock_docs=[_lock("saylor@storesight.com")])
    assert added == [], "a refill running for someone else the whole time must block this one"


def test_refill_retries_and_succeeds_once_the_lock_clears(monkeypatch):
    """A refill must not give up on the first sighting of a held lock.

    The finish-check that calls this has no retry of its own — it fires once,
    synchronously, right when a reviewer completes their last job. Confirmed
    in production 2026-09-23: four reviewers sat at 0 pending with no refill
    doc ever written, because the only prior behaviour was to bail on the
    first contended check and hope the reviewer's own browser polled again.
    A refill is seconds of work against a warm feed cache, so a held lock
    should almost always clear within the retry window.
    """
    calls = {"n": 0}
    sleeps = []
    stored = []
    shift_docs = [{"id": "rs-0", "data": {
        "kind": "reviewer_shift", "shift_snapshot_id": "snap1",
        "reviewer_email": REVIEWER, "rows": [], "part": 0,
        "batch_size": 5, "batch_responses": 100,
    }}]
    snap = {"id": "snap1", "data": {"kind": "shift_snapshot", "prioritization_flags": {}}}

    def flaky_list_docs(kind, force=False):
        if kind == "reviewer_shift":
            return shift_docs
        if kind == "shift_snapshot":
            return [snap]
        if kind != "refill_lock":
            return []
        calls["n"] += 1
        # Held for the first two checks, gone by the third.
        return [_lock("saylor@storesight.com")] if calls["n"] < 3 else []

    monkeypatch.setattr(main, "_cleanup_orphaned_refill_locks", lambda *a, **k: None)
    monkeypatch.setattr(main.roles, "list_docs_by_kind", flaky_list_docs)
    monkeypatch.setattr(main, "_list_completions_for_snapshot", lambda *a, **k: [])
    monkeypatch.setattr(main.bloom, "fetch_prioritized_jobs", lambda *a, **k: _feed({"a": 1, "b": 1}))
    monkeypatch.setattr(main.bloom, "is_excluded_client", lambda c: False)
    monkeypatch.setattr(internal_api, "post",
                        lambda path, json=None: stored.append(json) or {"data": {"id": "new"}})
    monkeypatch.setattr(main, "_try_delete", lambda *a, **k: None)
    monkeypatch.setattr(main.roles, "cache_upsert_doc", lambda *a, **k: None)
    monkeypatch.setattr(main.time, "sleep", lambda s: sleeps.append(s))

    added = main._auto_refill_reviewer("snap1", REVIEWER, 5)
    assert [r["jobId"] for r in added] == ["a", "b"], "should succeed once the lock clears"
    assert calls["n"] == 3, "should have rechecked the lock until it cleared"
    assert len(sleeps) == 2, "should wait between retries, not busy-loop"


def test_refill_ignores_a_lock_from_a_different_shift(monkeypatch):
    """A lock only guards its own snapshot."""
    added = _refill_with_lock(
        monkeypatch, _feed({"a": 1, "b": 1}),
        lock_docs=[_lock("saylor@storesight.com", snap="some-other-snapshot")])
    assert [r["jobId"] for r in added] == ["a", "b"]


def test_orphaned_locks_expire_in_two_minutes(monkeypatch):
    """A stuck lock now blocks the whole team, so the window has to be short."""
    assert main._REFILL_LOCK_MAX_AGE_SECONDS == 120

    deleted = []
    monkeypatch.setattr(main, "_try_delete", lambda doc_id: deleted.append(doc_id))
    monkeypatch.setattr(
        main.roles, "list_docs_by_kind",
        lambda kind, force=False: [
            _lock("stuck@storesight.com", age_seconds=300),
            _lock("fresh@storesight.com", age_seconds=5),
        ] if kind == "refill_lock" else [])

    main._cleanup_orphaned_refill_locks()
    assert deleted == ["lock-stuck@storesight.com"], "only the stale lock goes"


def test_pg_store_walk_only_keeps_the_refill_scoped(refill):
    """A P&G-scoped shift must not top up with anything else.

    Keyed off the feed's own png_store_walk flag rather than the job name:
    measured against the live feed the flag matched the 9 jobs named "P&G
    Display Store Walk" exactly, with no misses either way, so it carries the
    same meaning without breaking on a rename.
    """
    feed = _feed({"walk1": 5, "walk2": 5, "ordinary": 200})
    for row in feed:
        row["extras"]["pngStoreWalk"] = row["jobId"].startswith("walk")
    # A name that looks right but lacks the flag must NOT qualify.
    feed[2]["name"] = "P&G Display Store Walk (Lookalike)"

    added = refill(feed, batch_size=20, batch_responses=300,
                   flags={"pgStoreWalkOnly": True})

    assert sorted(r["jobId"] for r in added) == ["walk1", "walk2"]


def test_pg_store_walk_off_leaves_the_pool_alone(refill):
    """Without the flag the filter must not silently scope a normal shift."""
    feed = _feed({"walk": 5, "ordinary": 5})
    feed[0]["extras"]["pngStoreWalk"] = True
    feed[1]["extras"]["pngStoreWalk"] = False

    added = refill(feed, batch_size=20, batch_responses=300, flags={})

    assert sorted(r["jobId"] for r in added) == ["ordinary", "walk"]


def test_special_job_type_fragments_match_the_frontend_list():
    """The two fragment lists must stay identical.

    The composer filters the pool in TypeScript and auto-refill re-filters it
    in Python; if the lists drift, a Special-Job-Types shift gets topped up
    with jobs the composer would never have offered.
    """
    import re
    ts = (pathlib.Path(__file__).parent.parent
          / "shift-assignments" / "lib" / "types.ts").read_text()
    block = re.search(
        r"export const SPECIAL_JOB_TYPE_FRAGMENTS = \[(.*?)\] as const;",
        ts, re.S)
    assert block, "SPECIAL_JOB_TYPE_FRAGMENTS not found in types.ts"
    frontend = tuple(re.findall(r'"([^"]+)"', block.group(1)))
    assert frontend == main._SPECIAL_JOB_TYPE_FRAGMENTS


def test_special_job_types_matches_the_real_feed_names():
    """Names taken from the live feed, including the ones that must NOT match."""
    matches = [
        "Wild Delight® Songbird Food: Walmart Online Ratings & Reviews (Job 2/2)",
        "Chef Boyardee Protein Beefaroni: Walmart Online Ratings & Reviews (Job 1/2)",
        "Member's Mark Chicken Breast: Ratings & Review - Scavenger Hunt",
        # Truncated mid-phrase in the feed — "online" is what rescues it.
        "3 Gal. Ligustrum California Privet Flowering Live Shrub: Walmart Online Ratings &",
        "Acme Buy & Try - October",
        "Acme Buy and Try - October",
        "Acme Rating and Review Pilot",
        "Acme Audit Part 1/2",
        # Split jobs whose only qualifying signal is the slashed suffix.
        "Acme Shelf Audit (Job 1/2)",
        "Acme Shelf Audit (Job 2/2)",
    ]
    for name in matches:
        assert main._is_special_job_type(name), name

    # Bare-word fragments would have swept these in.
    non_matches = [
        "Storesight - September 2026 - Best Buy - Best Buy",
        "Ultrahuman Best Buy Mystery Shop US - Sept '26",
        "Storesight - September 2026 - Napa Auto Parts - Napa Auto Parts",
        # "Job 1" without the slash is a different job type entirely — this is
        # why the split-job fragments keep their "/2".
        "Storesight - September 2026 - Advance Auto Parts - Advance Auto Parts - Job 1",
        "Storesight - September 2026 - Kroger (all banners) - Job 2",
        "Storesight Non-Engine || Creamers (Danone Repeats - Non-Club)",
    ]
    for name in non_matches:
        assert not main._is_special_job_type(name), name


def test_special_job_types_off_leaves_the_pool_alone(refill):
    """Without the flag the filter must not silently scope a normal shift."""
    feed = _feed({"special": 10, "ordinary": 10})
    feed[0]["name"] = "Acme Ratings & Reviews"
    feed[1]["name"] = "Acme Shelf Check"

    added = refill(feed, batch_size=20, batch_responses=300, flags={})

    assert sorted(r["jobId"] for r in added) == ["ordinary", "special"]


def test_a_small_heavy_end_is_never_taken_whole(refill):
    """One reviewer must never walk off with all the heavy work.

    A fixed cap can't promise this: when the pool holds fewer large jobs than
    the cap, the cap never binds. Dividing by the reviewers on shift is what
    holds the line, so the guard scales with both the queue and the team.

    Needs teammates on shift to mean anything — a lone reviewer has nobody to
    share with, and taking every heavy job is then the right answer.
    """
    for heavy in (2, 3, 4, 6, 10):
        feed = _feed({f"big{i}": 50 for i in range(heavy)},
                     **{f"tiny{i}": 2 for i in range(50)})
        added = refill(feed, batch_size=20, batch_responses=400,
                       flags={"balanceByResponses": True}, teammates=2)
        taken = [r for r in added if int(r["unreviewedCount"]) >= 10]
        assert len(taken) < heavy, (
            f"{heavy} large jobs available, one reviewer took all {len(taken)}"
        )


def test_a_lone_large_job_is_not_stranded(refill):
    """The share must still let a single heavy job through, or it never moves."""
    feed = _feed({"big": 80, **{f"tiny{i}": 2 for i in range(20)}})
    added = refill(feed, batch_size=20, batch_responses=200,
                   flags={"balanceByResponses": True})

    assert "big" in [r["jobId"] for r in added]


def test_heavy_jobs_divide_by_the_team_on_shift(refill):
    """The heavy end is split across the team, not taken by whoever finishes first.

    Refills run independently, one reviewer at a time, so a per-reviewer cap
    alone hands the early finishers everything heavy and leaves the last one an
    all-small batch. Dividing by the reviewers on shift is what evens it out.
    """
    feed = _feed({f"big{i}": 50 for i in range(6)},
                 **{f"tiny{i}": 2 for i in range(50)})

    def heavy_taken(teammates):
        added = refill(feed, batch_size=20, batch_responses=400,
                       flags={"balanceByResponses": True}, teammates=teammates)
        return sum(1 for r in added if int(r["unreviewedCount"]) >= 50)

    # Alone: nobody to share with, so the ceiling is the only limit.
    assert heavy_taken(0) == main._REFILL_MAX_LARGE_JOBS
    # Three on shift over six heavy jobs is two each.
    assert heavy_taken(2) == 2
    # Six on shift over six is one each.
    assert heavy_taken(5) == 1
