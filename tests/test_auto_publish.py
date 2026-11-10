"""Auto-publish fixes from the 2026-11-09 review.

It used to: run for any signed-in user; deal to the whole roster whenever the
Team Scheduler lookup failed (always, from Cloud Scheduler, which has no
cookie to forward); and write a brand-new snapshot every run, hiding every
reviewer outside the batch.
"""

import datetime
import os

import jwt
import pytest

os.environ["LOCAL_DEV"] = "1"

import main  # noqa: E402
import roles  # noqa: E402
import internal_api  # noqa: E402

SAM = "sam.smith@storesight.com"
ALEX = "alex.jones@storesight.com"
STAYING = "kim.lee@storesight.com"


def _today():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _job(jid, pid=None, names=()):
    return {"id": jid, "jobId": jid, "projectId": pid or f"P{jid}", "name": f"Job {jid}",
            "unreviewedCount": 5,
            "extras": {"newCount": 5, "activeReviewerNames": list(names)}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Signed-in admin, auto-publish on, and an in-memory Storage."""
    now = datetime.datetime.now(datetime.timezone.utc)
    token = jwt.encode({"email": "boss@storesight.com", "name": "B", "iat": now,
                        "exp": now + datetime.timedelta(hours=8)}, "x", algorithm="HS256")
    d = tmp_path / ".storesight"
    d.mkdir()
    (d / "dev-token").write_text(token)
    monkeypatch.setattr("main._dev_token_path", lambda: d / "dev-token")
    role = {"r": "admin"}
    monkeypatch.setattr(roles, "get_role", lambda email: role["r"])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [
        {"id": "1", "name": "Sam", "email": SAM}, {"id": "2", "name": "Alex", "email": ALEX},
        {"id": "3", "name": "Kim", "email": STAYING}])
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(main, "_get_auto_publish_enabled", lambda *a, **k: True)
    monkeypatch.setattr(main, "_purge_stale_shift_docs", lambda: None)
    monkeypatch.setattr(main, "_send_slack_notification", lambda *a, **k: None)

    docs = []

    def post(path, json=None):
        docs.append({"id": f"doc-{len(docs) + 1}", "created_at": len(docs), "data": json["data"]})
        return {"data": {"id": docs[-1]["id"]}}

    def delete(path):
        docs[:] = [x for x in docs if x["id"] != path.rsplit("/", 1)[-1]]
        return {"data": {}}
    monkeypatch.setattr(internal_api, "post", post)
    monkeypatch.setattr(internal_api, "put", lambda path, json=None: {"data": {}})
    monkeypatch.setattr(internal_api, "delete", delete)
    monkeypatch.setattr(main, "_try_delete", lambda doc_id: delete(f"x/{doc_id}"))
    monkeypatch.setattr(roles, "list_docs_by_kind", lambda kind, force=False: [
        x for x in reversed(docs) if x["data"].get("kind") == kind])
    main.app.config["TESTING"] = True
    return {"client": main.app.test_client(), "docs": docs, "role": role, "mp": monkeypatch}


def _feed(env, rows):
    env["mp"].setattr(main.bloom, "fetch_prioritized_jobs", lambda *a, **k: rows)


def _scheduled(env, emails):
    env["mp"].setattr(main, "_get_todays_scheduled_reviewers", lambda **k: emails)


def _held(env):
    out = {}
    for x in env["docs"]:
        if x["data"].get("kind") == "reviewer_shift":
            out.setdefault(x["data"]["reviewer_email"], []).extend(
                r["jobId"] for r in x["data"]["rows"])
    return out


def test_a_reviewer_cannot_run_it(env):
    env["role"]["r"] = "reviewer"
    assert env["client"].post("/api/shifts/auto-publish").status_code == 403
    assert env["docs"] == []


def test_a_failed_schedule_lookup_publishes_nothing(env):
    def boom(**k):
        raise RuntimeError("401 from Team Scheduler")
    env["mp"].setattr(main, "_get_todays_scheduled_reviewers", boom)
    _feed(env, [_job("1")])
    resp = env["client"].post("/api/shifts/auto-publish?shift_time=8:30 AM")
    assert resp.status_code == 503
    assert env["docs"] == [], "never falls back to dealing the whole roster"


def test_nobody_scheduled_is_a_skip_not_the_whole_roster(env):
    _scheduled(env, [])
    _feed(env, [_job("1")])
    resp = env["client"].post("/api/shifts/auto-publish")
    assert resp.get_json()["skipped"] is True
    assert env["docs"] == []


def test_a_second_run_merges_into_the_running_shift(env):
    # The 8:00 team is already working; Kim holds a job in project PX.
    env["docs"].extend([
        {"id": "snap-0", "created_at": -2, "data": {
            "kind": "shift_snapshot", "published_at": _today(), "reviewer_emails": [STAYING]}},
        {"id": "rs-kim", "created_at": -1, "data": {
            "kind": "reviewer_shift", "shift_snapshot_id": "snap-0", "reviewer_email": STAYING,
            "part": 0, "batch_size": 20,
            "rows": [{"jobId": "K1", "id": "K1", "projectId": "PX", "unreviewedCount": 3}]}},
    ])
    _scheduled(env, [SAM, ALEX])
    _feed(env, [_job("K1", "PX"), _job("K2", "PX"), _job("1"), _job("2")])
    resp = env["client"].post("/api/shifts/auto-publish?shift_time=8:30 AM")
    assert resp.status_code == 200, resp.get_json()

    snaps = [x for x in env["docs"] if x["data"].get("kind") == "shift_snapshot"]
    assert [x["id"] for x in snaps] == ["snap-0"], "no new snapshot"
    held = _held(env)
    assert held[STAYING] == ["K1"], "Kim keeps her work"
    new = held.get(SAM, []) + held.get(ALEX, [])
    assert sorted(new) == ["1", "2"], "Kim's project PX stays with her"
    assert all(x["data"]["shift_snapshot_id"] == "snap-0"
               for x in env["docs"] if x["data"].get("kind") == "reviewer_shift")


def test_a_job_someone_is_in_review_on_goes_to_them_or_nobody(env):
    _scheduled(env, [SAM, ALEX])
    _feed(env, [_job("mine", names=["samS"]), _job("stranger", names=["zedq"]), _job("free")])
    env["client"].post("/api/shifts/auto-publish")
    held = _held(env)
    assert "mine" in held.get(SAM, [])
    assert "stranger" not in held.get(SAM, []) + held.get(ALEX, [])


def test_deal_keeps_a_project_together():
    rows = [_job("a1", "PA"), _job("b1", "PB"), _job("a2", "PA"), _job("a3", "PA")]
    out = main._distribute_evenly(rows, [SAM, ALEX])
    owners = {e for e, v in out.items() if any(r["projectId"] == "PA" for r in v)}
    assert len(owners) == 1


# ── Size-threshold splits ───────────────────────────────────────────────────

def _big(jid, n, pid=None):
    return {**_job(jid, pid), "unreviewedCount": n}


def _rows_of(env, email):
    return [r for x in env["docs"] if x["data"].get("kind") == "reviewer_shift"
            and x["data"]["reviewer_email"] == email for r in x["data"]["rows"]]


def test_a_big_job_is_split_across_the_batch(env):
    _scheduled(env, [SAM, ALEX])
    _feed(env, [_big("big", 320), _job("1"), _job("2")])
    resp = env["client"].post("/api/shifts/auto-publish")
    assert resp.status_code == 200, resp.get_json()
    for email in (SAM, ALEX):
        row = next(r for r in _rows_of(env, email) if r["jobId"] == "big")
        assert row["splitWith"] == sorted([SAM, ALEX])
        assert row.get("splitAt")
    small = [j for e in (SAM, ALEX) for j in _held(env)[e] if j != "big"]
    assert sorted(small) == ["1", "2"], "the rest is dealt normally, once each"


def test_under_50_is_not_split():
    out, rest = main._pick_auto_splits([_big("j", 49)], [SAM, ALEX])
    assert out == {} and [r["jobId"] for r in rest] == ["j"]


def test_50_to_100_goes_to_two():
    for n in (50, 100):
        out, _ = main._pick_auto_splits([_big("j", n)], [SAM, ALEX, STAYING])
        assert len(out) == 2, n


def test_over_100_goes_to_everyone():
    out, _ = main._pick_auto_splits([_big("j", 101)], [SAM, ALEX, STAYING])
    assert set(out) == {SAM, ALEX, STAYING}
    assert out[SAM][0]["splitWith"] == sorted([SAM, ALEX, STAYING])


def test_one_reviewer_cannot_split():
    out, _ = main._pick_auto_splits([_big("j", 500)], [SAM])
    assert out == {}


def test_every_big_job_is_split_and_pairs_rotate():
    rows = [_big("a", 60), _big("b", 70), _big("c", 500)]
    out, rest = main._pick_auto_splits(rows, [SAM, ALEX, STAYING])
    assert rest == []
    by_job = {}
    for email, v in out.items():
        for r in v:
            by_job.setdefault(r["jobId"], set()).add(email)
    assert by_job["c"] == {SAM, ALEX, STAYING}
    assert len(by_job["a"]) == len(by_job["b"]) == 2
    assert by_job["a"] != by_job["b"], "pairs rotate by load"


def test_sharers_are_the_least_loaded():
    out, _ = main._pick_auto_splits([_big("j", 60)], [SAM, ALEX, STAYING],
                                    load={SAM: 9, ALEX: 1, STAYING: 0})
    assert set(out) == {ALEX, STAYING}


def test_a_reviewer_at_the_cap_takes_no_splits():
    out, _ = main._pick_auto_splits([_big("j", 500)], [SAM, ALEX, STAYING],
                                    load={SAM: 20}, cap=20)
    assert set(out) == {ALEX, STAYING}


def test_an_excluded_reviewer_is_left_out_of_everyone(env):
    env["mp"].setattr(main, "_is_excluded_job",
                      lambda row, allowed=(): row.get("jobId") == "vid" and "video" not in allowed)
    out, _ = main._pick_auto_splits([_big("vid", 500)], [SAM, ALEX, STAYING],
                                    exceptions={ALEX: {"video"}, STAYING: {"video"}})
    assert set(out) == {ALEX, STAYING}


def test_tied_jobs_are_never_split():
    rows = [_big("held", 900), _big("projheld", 900, "PH"), _big("owned", 900),
            _big("multi", 900, "PM"), _job("m2", "PM")]
    out, rest = main._pick_auto_splits(
        rows, [SAM, ALEX], owner_jobs={"owned": {SAM}},
        taken_keys={"held"}, taken_projects={"PH"})
    assert out == {}
    assert [r["jobId"] for r in rest] == [r["jobId"] for r in rows], "feed order kept"


def test_a_job_someone_is_in_review_on_is_not_split(env):
    _scheduled(env, [SAM, ALEX])
    _feed(env, [{**_big("big", 900), "extras": {"activeReviewerNames": ["samS"]}}])
    env["client"].post("/api/shifts/auto-publish")
    assert _held(env) == {SAM: ["big"]}
    assert not _rows_of(env, SAM)[0].get("splitWith")


def test_a_full_reviewer_is_not_made_a_sharer(env):
    env["docs"].extend([
        {"id": "snap-0", "created_at": -2, "data": {
            "kind": "shift_snapshot", "published_at": _today(), "reviewer_emails": [SAM]}},
        {"id": "rs-sam", "created_at": -1, "data": {
            "kind": "reviewer_shift", "shift_snapshot_id": "snap-0", "reviewer_email": SAM,
            "part": 0, "batch_size": 20,
            "rows": [{"jobId": f"S{i}", "id": f"S{i}", "projectId": f"PS{i}",
                      "unreviewedCount": 3} for i in range(20)]}},
    ])
    _scheduled(env, [SAM, ALEX, STAYING])
    _feed(env, [_big("big", 320)])
    env["client"].post("/api/shifts/auto-publish")
    assert "big" not in _held(env).get(SAM, [])
    for email in (ALEX, STAYING):
        row = next(r for r in _rows_of(env, email) if r["jobId"] == "big")
        assert row["splitWith"] == sorted([ALEX, STAYING])
