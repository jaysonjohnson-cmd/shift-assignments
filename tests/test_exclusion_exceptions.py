"""Per-reviewer exceptions to the job exclusions.

Settings can let a reviewer be given an excluded kind of job (video,
screeners, …) anyway. Everyone else must still never see it: the feed tags
these jobs, publish and refill re-check per reviewer, and My Tasks only shows
them to whoever is excepted.
"""

import datetime
import os

import jwt
import pytest

os.environ["LOCAL_DEV"] = "1"

import bloom  # noqa: E402
import internal_api  # noqa: E402
import main  # noqa: E402
import roles  # noqa: E402

SAM = "sam@storesight.com"
ALEX = "alex@storesight.com"
VIDEO = "Colgate Shelf Study - VIDEO - Kroger"


def _token(email):
    now = datetime.datetime.now(datetime.timezone.utc)
    return jwt.encode({"email": email, "name": "U", "iat": now,
                       "exp": now + datetime.timedelta(hours=8)},
                      "irrelevant", algorithm="HS256")


@pytest.fixture
def client(tmp_path, monkeypatch):
    token_dir = tmp_path / ".storesight"
    token_dir.mkdir()
    token_file = token_dir / "dev-token"
    monkeypatch.setattr("main._dev_token_path", lambda: token_file)
    main.app.config["TESTING"] = True
    bloom.clear_cache()
    with main.app.test_client() as c:
        yield c, token_file


@pytest.fixture
def roster(monkeypatch):
    """Sam may be given video jobs; Alex has no exceptions."""
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_leads", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [
        {"id": "r1", "name": "Sam", "email": SAM, "exclusionExceptions": ["video"]},
        {"id": "r2", "name": "Alex", "email": ALEX},
    ])


def _row(job_id, name, **extra):
    return {"id": job_id, "jobId": job_id, "projectId": f"P{job_id}",
            "name": name, "unreviewedCount": 5, **extra}


# ---------- the rules ----------


def test_each_exclusion_names_its_rule():
    assert main._exclusion_rules(_row("1", VIDEO)) == {"video"}
    assert main._exclusion_rules(_row("1", "Store Screener v2")) == {"screener"}
    assert main._exclusion_rules(_row("1966569", "Anything")) == {"bayer_cvs"}
    assert main._exclusion_rules(
        _row("1", "Shelf", extras={"client": "Joanna.Riney@menasha.com"})) == {"third_party_client"}
    assert main._exclusion_rules(_row("1", "Shelf - NON-VIDEO")) == set()


def test_every_rule_used_is_one_settings_can_offer():
    used = set(main.EXCLUDED_JOB_IDS.values()) | set(main.EXCLUDED_JOB_NAMES.values())
    used |= {"video", "third_party_client"}
    assert used == set(main.EXCLUSION_RULES)


def test_an_exception_lifts_only_its_own_rule():
    video = _row("1", VIDEO)
    assert main._is_excluded_job(video)
    assert not main._is_excluded_job(video, {"video"})
    assert main._is_excluded_job(video, {"screener"})


def test_a_job_under_two_rules_needs_both_excepted():
    both = _row("1", "Screener VIDEO")
    assert main._is_excluded_job(both, {"video"})
    assert not main._is_excluded_job(both, {"video", "screener"})


def test_reviewer_exceptions_reads_the_roster(roster):
    assert main._reviewer_exceptions(" SAM@storesight.com ") == {"video"}
    assert main._reviewer_exceptions(ALEX) == frozenset()
    assert main._reviewer_exceptions("nobody@storesight.com") == frozenset()


def test_unreadable_roster_means_no_exceptions(monkeypatch):
    def boom():
        raise RuntimeError("storage down")
    monkeypatch.setattr(roles, "list_reviewers", boom)
    assert main._reviewer_exceptions(SAM) == frozenset()


# ---------- the composer feed ----------


def test_feed_keeps_excepted_jobs_tagged_and_drops_the_rest(client, roster, monkeypatch):
    c, token_file = client
    token_file.write_text(_token(roles.ROOT_ADMIN_EMAIL))
    monkeypatch.setattr(bloom, "fetch_prioritized_jobs", lambda **k: [
        _row("1", "Shelf photos"), _row("2", VIDEO), _row("3", "Store Screener")])
    data = c.get("/api/bloom/jobs").get_json()["data"]
    by_id = {r["jobId"]: r for r in data}
    assert set(by_id) == {"1", "2"}
    assert "excludedRules" not in by_id["1"]
    assert by_id["2"]["excludedRules"] == ["video"]


# ---------- publish ----------


def test_publish_gives_an_excepted_job_only_to_the_excepted_reviewer(client, roster, monkeypatch):
    c, token_file = client
    token_file.write_text(_token(roles.ROOT_ADMIN_EMAIL))
    docs = []

    def fake_post(path, json=None):
        docs.append({"id": f"doc-{len(docs) + 1}", "data": json["data"]})
        return {"data": {"id": docs[-1]["id"]}}
    monkeypatch.setattr(internal_api, "post", fake_post)
    monkeypatch.setattr(roles, "list_docs_by_kind", lambda kind, force=False: [
        d for d in reversed(docs) if d["data"].get("kind") == kind])

    resp = c.post("/api/shifts/publish", json={"assignments": {
        SAM: [_row("1", VIDEO), _row("2", "Shelf")],
        ALEX: [_row("3", VIDEO), _row("4", "Endcap")],
    }})
    assert resp.status_code == 201, resp.get_json()
    rows = {d["data"]["reviewer_email"]: [r["jobId"] for r in d["data"]["rows"]]
            for d in docs if d["data"].get("kind") == "reviewer_shift"}
    assert rows == {SAM: ["1", "2"], ALEX: ["4"]}


# ---------- top-ups ----------


@pytest.mark.parametrize("email,expected", [(SAM, {"1", "2"}), (ALEX, {"2"})])
def test_top_up_hands_out_an_excluded_job_only_to_the_excepted(
        roster, monkeypatch, email, expected):
    shift_docs = [{"id": "rs-0", "data": {
        "kind": "reviewer_shift", "shift_snapshot_id": "snap1",
        "reviewer_email": email, "rows": [], "part": 0, "batch_size": 5}}]
    monkeypatch.setattr(roles, "list_docs_by_kind", lambda kind, force=False: {
        "reviewer_shift": shift_docs,
        "shift_snapshot": [{"id": "snap1", "data": {"kind": "shift_snapshot"}}],
    }.get(kind, []))
    monkeypatch.setattr(main, "_list_completions_for_snapshot", lambda *a, **k: [])
    monkeypatch.setattr(main.bloom, "fetch_prioritized_jobs", lambda *a, **k: [
        _row("1", VIDEO), _row("2", "Shelf"), _row("3", "Store Screener")])
    monkeypatch.setattr(internal_api, "post",
                        lambda path, json=None: {"data": {"id": "new"}})
    monkeypatch.setattr(main, "_try_delete", lambda *a, **k: None)
    monkeypatch.setattr(roles, "cache_upsert_doc", lambda *a, **k: None)
    added = main._auto_refill_reviewer("snap1", email, 5)
    assert {r["jobId"] for r in added} == expected


# ---------- My Tasks ----------


def _my_tasks_shift(monkeypatch, email):
    docs = [{"id": "rs1", "data": {
        "kind": "reviewer_shift", "shift_snapshot_id": "snap1",
        "reviewer_email": email, "rows": [_row("1", VIDEO), _row("2", "Shelf")]}}]
    monkeypatch.setattr(roles, "list_docs_by_kind", lambda kind, force=False: [
        d for d in docs if d["data"]["kind"] == kind])
    monkeypatch.setattr(main, "_latest_snapshot", lambda **k: (
        "snap1", {"published_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}))
    monkeypatch.setattr(main, "_live_counts_by_job", lambda: None)
    monkeypatch.setattr(main, "_auto_refill_reviewer", lambda *a, **k: [])
    monkeypatch.setattr(main.bloom, "fetch_project_jobs", lambda pid: [])


@pytest.mark.parametrize("email,expected", [(SAM, {"1", "2"}), (ALEX, {"2"})])
def test_my_tasks_shows_an_excluded_job_only_to_the_excepted(
        client, roster, monkeypatch, email, expected):
    c, token_file = client
    token_file.write_text(_token(email))
    _my_tasks_shift(monkeypatch, email)
    rows = c.get("/api/shifts/my").get_json()["data"]["rows"]
    assert {r["jobId"] for r in rows} == expected


# ---------- Settings ----------


def test_exceptions_endpoint_is_admin_only(client, roster):
    c, token_file = client
    token_file.write_text(_token(SAM))
    resp = c.put("/api/reviewers/r1/exceptions", json={"exclusionExceptions": []})
    assert resp.status_code == 403


def test_exceptions_endpoint_rejects_unknown_rules(client, roster):
    c, token_file = client
    token_file.write_text(_token(roles.ROOT_ADMIN_EMAIL))
    resp = c.put("/api/reviewers/r1/exceptions",
                 json={"exclusionExceptions": ["video", "everything"]})
    assert resp.status_code == 400


def test_exceptions_are_saved_on_the_reviewer_doc(client, roster, monkeypatch):
    c, token_file = client
    token_file.write_text(_token(roles.ROOT_ADMIN_EMAIL))
    stored = {"kind": "reviewer", "name": "Sam", "email": SAM, "color": "#123456"}
    monkeypatch.setattr(roles, "list_docs_by_kind", lambda kind, force=False: (
        [{"id": "r1", "data": dict(stored)}] if kind == "reviewer" else []))
    puts = []
    monkeypatch.setattr(internal_api, "put", lambda path, json=None: puts.append((path, json)))
    monkeypatch.setattr(roles, "cache_upsert_doc", lambda *a: None)

    resp = c.put("/api/reviewers/r1/exceptions",
                 json={"exclusionExceptions": ["screener", "video", "video"]})
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["data"]["exclusionExceptions"] == ["video", "screener"]
    path, body = puts[-1]
    assert path.endswith("/r1")
    assert body["data"] == {**stored, "exclusion_exceptions": ["video", "screener"]}


def test_recoloring_a_reviewer_keeps_their_exceptions(monkeypatch):
    stored = {"kind": "reviewer", "name": "Sam", "email": SAM,
              "exclusion_exceptions": ["video"]}
    monkeypatch.setattr(roles, "list_docs_by_kind", lambda kind, force=False: (
        [{"id": "r1", "data": dict(stored)}] if kind == "reviewer" else []))
    puts = []
    monkeypatch.setattr(internal_api, "put", lambda path, json=None: puts.append(json))
    monkeypatch.setattr(roles, "cache_upsert_doc", lambda *a: None)
    roles.update_record("r1", "reviewer", "Sam", SAM, "#abcdef")
    assert puts[-1]["data"]["exclusion_exceptions"] == ["video"]
    assert puts[-1]["data"]["color"] == "#abcdef"
