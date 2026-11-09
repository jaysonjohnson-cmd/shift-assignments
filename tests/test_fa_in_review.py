"""FieldAgent's in-review list keeps top-ups off a job someone else is still
reviewing, and flags it on My Tasks.

Production 2026-11-09: 1975675 went to Liam while FieldAgent still had Laurel
("laurelm") in review on it, left over from before — so both saw it.
"""

import datetime
import os

import jwt
import pytest

os.environ["LOCAL_DEV"] = "1"

import main  # noqa: E402
import roles  # noqa: E402
import internal_api  # noqa: E402

LIAM = "liam.anderson@storesight.com"
LAUREL = "laurel.mountain@storesight.com"


# ---------- matching FieldAgent names to reviewers ----------


@pytest.mark.parametrize("name,email,expected", [
    ("laurelm", LAUREL, True),          # first name + last initial
    ("saylorw", "saylor.wilson@storesight.com", True),
    ("grayson.bandy@storesight.com", "grayson.bandy@storesight.com", True),
    ("GRAYSON.BANDY@storesight.com", "grayson.bandy@storesight.com", True),
    ("laurel.mountain", LAUREL, True),
    ("laurelm", LIAM, False),
    ("liama", LAUREL, False),
    ("laurel", LAUREL, False),          # first name alone is too loose
    ("", LAUREL, False),
])
def test_fa_name_is(name, email, expected):
    assert main._fa_name_is(name, email) is expected


def test_in_review_by_others_ignores_me_and_split_sharers():
    row = {"extras": {"activeReviewerNames": ["laurelm", "liama", "saylorw"]}}
    assert main._in_review_by_others(row, LIAM) == ["laurelm", "saylorw"]
    assert main._in_review_by_others(
        row, LIAM, also=["saylor.wilson@storesight.com"]) == ["laurelm"]
    assert main._in_review_by_others({"extras": {}}, LIAM) == []


def test_display_name_comes_from_the_roster():
    roster = [{"email": LAUREL, "name": "Laurel Mountain"}]
    assert main._fa_display_name("laurelm", roster) == "Laurel Mountain"
    assert main._fa_display_name("someoneelse", roster) == "someoneelse"


def test_feed_rows_carry_the_in_review_names():
    row = main.bloom._row_from_api({"id": 1, "new": 3,
                                    "activeReviewerNames": "laurelm, grayson.bandy@storesight.com"})
    assert row["extras"]["activeReviewerNames"] == ["laurelm", "grayson.bandy@storesight.com"]
    assert main.bloom._row_from_api({"id": 1, "new": 3})["extras"]["activeReviewerNames"] == []
    as_list = main.bloom._row_from_api({"id": 1, "new": 3, "activeReviewerNames": ["laurelm", " "]})
    assert as_list["extras"]["activeReviewerNames"] == ["laurelm"]


# ---------- top-ups ----------


def _refill(monkeypatch, feed):
    docs = [{"id": "rs-0", "data": {
        "kind": "reviewer_shift", "shift_snapshot_id": "snap1",
        "reviewer_email": LIAM, "part": 0, "batch_size": 5, "rows": []}}]
    snap = {"id": "snap1", "data": {"kind": "shift_snapshot", "prioritization_flags": {}}}
    monkeypatch.setattr(roles, "list_docs_by_kind", lambda kind, force=False: {
        "reviewer_shift": docs, "shift_snapshot": [snap]}.get(kind, []))
    monkeypatch.setattr(main, "_list_completions_for_snapshot", lambda *a, **k: [])
    monkeypatch.setattr(main.bloom, "fetch_prioritized_jobs", lambda *a, **k: feed)
    monkeypatch.setattr(main.bloom, "is_excluded_client", lambda c: False)
    monkeypatch.setattr(internal_api, "post", lambda path, json=None: {"data": {"id": "new"}})
    monkeypatch.setattr(main, "_try_delete", lambda *a, **k: None)
    monkeypatch.setattr(roles, "cache_upsert_doc", lambda *a, **k: None)
    return sorted(r["jobId"] for r in main._auto_refill_reviewer("snap1", LIAM, 5))


def _job(jid, names=()):
    return {"id": jid, "jobId": jid, "projectId": f"P{jid}", "name": f"Job {jid}",
            "unreviewedCount": 5,
            "extras": {"newCount": 5, "activeReviewerNames": list(names)}}


def test_top_up_skips_a_job_someone_else_is_in_review_on(monkeypatch):
    added = _refill(monkeypatch, [_job("1975675", ["laurelm"]), _job("fresh")])
    assert added == ["fresh"]


def test_top_up_still_takes_a_job_only_i_am_in_review_on(monkeypatch):
    added = _refill(monkeypatch, [_job("mine", ["liama"]), _job("fresh")])
    assert added == ["fresh", "mine"]


# ---------- My Tasks ----------


@pytest.fixture
def my_tasks(tmp_path, monkeypatch):
    now = datetime.datetime.now(datetime.timezone.utc)
    token = jwt.encode({"email": LIAM, "name": "Liam", "iat": now,
                        "exp": now + datetime.timedelta(hours=8)},
                       "irrelevant", algorithm="HS256")
    token_dir = tmp_path / ".storesight"
    token_dir.mkdir()
    (token_dir / "dev-token").write_text(token)
    monkeypatch.setattr("main._dev_token_path", lambda: token_dir / "dev-token")
    rows = [{"id": j, "jobId": j, "projectId": f"P{j}", "name": f"Job {j}",
             "unreviewedCount": 10} for j in ("1975675", "plain", "mine")]
    docs = [{"id": "rs1", "data": {
        "kind": "reviewer_shift", "shift_snapshot_id": "snap1",
        "reviewer_email": LIAM, "rows": rows}}]
    monkeypatch.setattr(roles, "list_docs_by_kind", lambda kind, force=False: [
        d for d in docs if d["data"]["kind"] == kind])
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [
        {"id": "1", "name": "Liam Anderson", "email": LIAM},
        {"id": "2", "name": "Laurel Mountain", "email": LAUREL}])
    monkeypatch.setattr(main, "_latest_snapshot", lambda **k: ("snap1", {"published_at": now.isoformat()}))
    monkeypatch.setattr(main, "_auto_refill_reviewer", lambda *a, **k: [])
    monkeypatch.setattr(main.bloom, "fetch_project_jobs", lambda pid: [])
    live = lambda names: {"reviewable": 10, "new": 10, "aged": 0,  # noqa: E731
                          "possibleReject": 0, "activeReviewerNames": names}
    monkeypatch.setattr(main, "_live_counts_by_job", lambda: {
        "1975675": live(["laurelm"]), "plain": live([]), "mine": live(["liama"])})
    main.app.config["TESTING"] = True
    with main.app.test_client() as c:
        yield c


def test_my_tasks_flags_a_teammate_still_in_review(my_tasks):
    rows = {r["jobId"]: r for r in my_tasks.get("/api/shifts/my").get_json()["data"]["rows"]}
    assert rows["1975675"]["inReviewBy"] == ["Laurel Mountain"]
    assert "inReviewBy" not in rows["plain"]
    assert "inReviewBy" not in rows["mine"]


def test_a_failed_top_up_write_reports_nothing_and_keeps_checkmarks(monkeypatch):
    """The refill used to retire old checkmarks up front and report the whole
    batch even when the write then failed."""
    puts = []
    added = None

    def failing_post(path, json=None):
        if json["data"].get("kind") == "reviewer_shift":
            raise RuntimeError("429")
        return {"data": {"id": "lock"}}
    _refill(monkeypatch, [_job("fresh")])  # wire the fakes
    monkeypatch.setattr(internal_api, "post", failing_post)
    monkeypatch.setattr(internal_api, "put", lambda path, json=None: puts.append(path))
    added = main._auto_refill_reviewer("snap1", LIAM, 5)
    assert added == []
    assert puts == []
