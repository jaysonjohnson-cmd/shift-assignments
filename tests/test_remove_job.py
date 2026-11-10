"""The X on Team Assignments' overview (POST /api/shifts/remove-job).

It used to clear the whole Storage doc cache after every removal, so the
reload right after the X rescanned everything under the 60 req/min limit and
the row took ages to go. Now only the changed doc is updated in the cache.
"""

import datetime
import os

import pytest

os.environ["LOCAL_DEV"] = "1"

import main  # noqa: E402
import roles  # noqa: E402
import internal_api  # noqa: E402

SAM = "sam@storesight.com"


@pytest.fixture
def env(monkeypatch):
    docs = [
        {"id": "snap", "data": {"kind": "shift_snapshot", "published_at":
                                datetime.datetime.now(datetime.timezone.utc).isoformat()}},
        {"id": "rs-sam", "data": {
            "kind": "reviewer_shift", "shift_snapshot_id": "snap", "reviewer_email": SAM,
            "rows": [{"id": "1", "jobId": "1"}, {"id": "2", "jobId": "2"}]}},
    ]
    calls = {"put": [], "invalidate": [], "upsert": []}
    monkeypatch.setattr(roles, "list_docs_by_kind", lambda kind, force=False: [
        d for d in docs if d["data"]["kind"] == kind])
    monkeypatch.setattr(roles, "invalidate_doc_cache", lambda *k: calls["invalidate"].append(k))
    monkeypatch.setattr(roles, "cache_upsert_doc",
                        lambda kind, doc: calls["upsert"].append((kind, doc)))
    monkeypatch.setattr(internal_api, "put",
                        lambda path, json=None: calls["put"].append((path, json)) or {"data": {}})
    monkeypatch.setattr(main, "_require_admin_or_lead", lambda: None)
    return calls


def _remove(job_id, email=SAM):
    with main.app.test_request_context("/api/shifts/remove-job", method="POST",
                                       json={"reviewer_email": email, "job_id": job_id}):
        from flask import g
        g.user = {"email": "boss@storesight.com"}
        resp = main.api_remove_job()
    return resp if isinstance(resp, tuple) else (resp, resp.status_code)


def test_removes_the_job_and_updates_only_that_doc_in_the_cache(env):
    resp, status = _remove("1")
    assert status == 200
    assert [r["jobId"] for r in env["put"][0][1]["data"]["rows"]] == ["2"]
    assert env["invalidate"] == [], "never wipes the whole cache"
    kind, doc = env["upsert"][0]
    assert kind == "reviewer_shift" and doc["id"] == "rs-sam"
    assert [r["jobId"] for r in doc["data"]["rows"]] == ["2"]


def test_a_missing_job_is_a_404(env):
    _, status = _remove("nope")
    assert status == 404
    assert env["put"] == [] and env["invalidate"] == []


def test_a_failed_write_drops_only_the_shift_cache(env, monkeypatch):
    import requests

    def boom(path, json=None):
        r = requests.Response()
        r.status_code = 500
        raise requests.exceptions.HTTPError(response=r)
    monkeypatch.setattr(internal_api, "put", boom)
    _, status = _remove("1")
    assert status >= 500
    assert env["invalidate"] == [("reviewer_shift",)]
    assert env["upsert"] == []
