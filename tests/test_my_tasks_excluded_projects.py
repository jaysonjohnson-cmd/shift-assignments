"""My Tasks flags rows whose project also holds an excluded (video) job.

A "By PID" card opens the whole project in Collection Review, so a project
with a video job in it would hand that job to the reviewer even though the
tool never assigned it. The flag keeps those rows as single-job cards.
"""

import datetime
import os

import jwt
import pytest

os.environ["LOCAL_DEV"] = "1"

import main  # noqa: E402
import roles  # noqa: E402

SAM = "sam@storesight.com"


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
    token_file.write_text(_token(SAM))
    monkeypatch.setattr("main._dev_token_path", lambda: token_file)
    main.app.config["TESTING"] = True
    with main.app.test_client() as c:
        yield c


def _row(job_id, project_id, name):
    return {"id": job_id, "jobId": job_id, "projectId": project_id,
            "name": name, "unreviewedCount": 10}


@pytest.fixture
def shift(monkeypatch):
    rows = [_row("J1", "1635913", "Shelf photos"),
            _row("J2", "1635913", "Endcap photos"),
            _row("J3", "P2", "Plain job")]
    docs = [{"id": "rs1", "data": {
        "kind": "reviewer_shift", "shift_snapshot_id": "snap1",
        "reviewer_email": SAM, "rows": rows}}]
    monkeypatch.setattr(roles, "list_docs_by_kind", lambda kind, force=False: [
        d for d in docs if d["data"]["kind"] == kind])
    monkeypatch.setattr(roles, "list_admins", lambda: [])
    monkeypatch.setattr(roles, "list_reviewers", lambda: [
        {"id": "1", "name": "Sam", "email": SAM}])
    monkeypatch.setattr(main, "_latest_snapshot", lambda **k: (
        "snap1", {"published_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}))
    monkeypatch.setattr(main, "_live_counts_by_job", lambda: None)
    monkeypatch.setattr(main, "_auto_refill_reviewer", lambda *a, **k: [])


def _projects(monkeypatch, jobs_by_project):
    """Stub /api/jobs?project_id= — every job in each project, any status."""
    calls = []

    def fetch(pid):
        calls.append(pid)
        if pid not in jobs_by_project:
            raise RuntimeError("upstream down")
        return [{"jobId": jid, "name": name} for jid, name in jobs_by_project[pid]]
    monkeypatch.setattr(main.bloom, "fetch_project_jobs", fetch)
    return calls


def _my_rows(client):
    return {r["jobId"]: r for r in client.get("/api/shifts/my").get_json()["data"]["rows"]}


def test_project_with_a_video_job_is_flagged(client, shift, monkeypatch):
    """PID 1635913: NON-VIDEO jobs assigned, VIDEO siblings never in the queue."""
    _projects(monkeypatch, {"1635913": [
        ("J1", "Shelf photos"), ("J2", "Endcap photos"),
        ("J9", "Colgate Shelf Observation Study - VIDEO - Kroger")]})
    rows = _my_rows(client)
    assert rows["J1"].get("projectHasExcluded") is True
    assert rows["J2"].get("projectHasExcluded") is True
    assert "projectHasExcluded" not in rows["J3"]


def test_non_video_sibling_does_not_flag_its_project(client, shift, monkeypatch):
    _projects(monkeypatch, {"1635913": [
        ("J1", "Shelf photos"),
        ("J9", "Colgate Shelf Observation Study - NON-VIDEO - Meijer")]})
    assert not any(r.get("projectHasExcluded") for r in _my_rows(client).values())


def test_only_projects_that_would_group_are_looked_up(client, shift, monkeypatch):
    """P2 holds one of the reviewer's jobs, so it never becomes a By-PID card."""
    calls = _projects(monkeypatch, {"1635913": []})
    _my_rows(client)
    assert calls == ["1635913"]


def test_unreadable_project_counts_as_mixed(client, shift, monkeypatch):
    _projects(monkeypatch, {})
    rows = _my_rows(client)
    assert rows["J1"].get("projectHasExcluded") is True
    assert "projectHasExcluded" not in rows["J3"]
