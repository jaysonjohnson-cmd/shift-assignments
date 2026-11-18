"""The Prioritized Jobs feed is a direct POST to Field Agent with the client
API key in the JSON body (no internal-tool-api hop, no Authorization header).
401/403 are configuration errors and must not be retried."""

import pytest
import requests

import bloom


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(response=self)


# Captured at import, before conftest swaps it for the internal_api shim.
_REAL_POST = bloom._post_prioritized_jobs


@pytest.fixture
def real_post(monkeypatch):
    monkeypatch.setattr(bloom, "_post_prioritized_jobs", _REAL_POST)
    monkeypatch.setenv("FA_API_KEY", "test-key")
    calls = []

    def install(resp):
        def fake_post(url, json=None, timeout=None, **kw):
            calls.append({"url": url, "json": json, "kw": kw})
            return resp
        monkeypatch.setattr(bloom.requests, "post", fake_post)
        return calls
    return install


def test_posts_api_key_in_body_to_field_agent(real_post):
    calls = real_post(_Resp(200, {"data": [{"id": 1, "new": 2}], "count": 1,
                                  "generated_at": "2026-11-18T20:40:00+00:00"}))
    assert bloom._fetch_prioritized_jobs_raw() == [{"id": 1, "new": 2}]
    assert calls[0]["url"] == "https://my.fieldagent.net/admin/fieldagent/prioritizedJobs/api/"
    assert calls[0]["json"] == {"api_key": "test-key"}
    assert "headers" not in calls[0]["kw"]  # no Authorization header


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failures_raise_auth_error(real_post, status):
    calls = real_post(_Resp(status))
    with pytest.raises(bloom.FieldAgentAuthError) as exc:
        bloom._fetch_prioritized_jobs_raw()
    assert exc.value.status == status
    assert len(calls) == 1  # no retry


def test_missing_key_is_an_auth_error_without_a_request(real_post, monkeypatch):
    calls = real_post(_Resp(200))
    monkeypatch.delenv("FA_API_KEY")
    with pytest.raises(bloom.FieldAgentAuthError):
        bloom._fetch_prioritized_jobs_raw()
    assert calls == []
