"""How many responses a completed job credits to the weekly leaderboard.

The credit is what was actually reviewed in Bloom, not the count stored on the
row at assignment time — responses that landed mid-shift still had to be
cleared before the checkmark would take, so they have to count.
"""

import datetime
import os

os.environ["LOCAL_DEV"] = "1"

import main  # noqa: E402
import bloom  # noqa: E402
import internal_api  # noqa: E402

UTC = datetime.timezone.utc
PUBLISHED = datetime.datetime(2026, 9, 23, 13, 0, tzinfo=UTC)


def _rfc(dt):
    return dt.strftime("%a, %d %b %Y %H:%M:%S GMT")


def _rg(minutes_after_publish, reviewer=12345):
    return {
        "review_ts": _rfc(PUBLISHED + datetime.timedelta(minutes=minutes_after_publish)),
        "reviewed_by_id": reviewer,
    }


def _stub_bloom(monkeypatch, by_status):
    """Serve /api/responsegroups from {status: [rows, newest first]}."""
    def fake_get(path, params=None):
        assert path == "/api/responsegroups"
        assert params["sort"] == "-review_ts"
        rows = by_status.get(params["status"], [])
        start = (params["page"] - 1) * params["per_page"]
        return {"data": rows[start:start + params["per_page"]]}
    monkeypatch.setattr(internal_api, "get", fake_get)


def test_counts_approvals_denials_and_paid_hidden(monkeypatch):
    # 50 approved + 7 denied + 2 paid/hidden = 59, all after the shift opened.
    _stub_bloom(monkeypatch, {
        "A": [_rg(30)] * 50,
        "X": [_rg(20)] * 7,
        "P": [_rg(10)] * 2,
    })
    assert bloom.count_reviewed_since(1, PUBLISHED) == 59


def test_ignores_system_reviews_and_reviews_before_the_window(monkeypatch):
    _stub_bloom(monkeypatch, {
        "A": [_rg(30), _rg(25, reviewer=bloom.SYSTEM_REVIEWER_ID), _rg(-5), _rg(-60)],
        "X": [{"review_ts": None, "reviewed_by_id": None}, _rg(5)],
    })
    assert bloom.count_reviewed_since(1, PUBLISHED) == 2


def test_pages_past_the_first_hundred(monkeypatch):
    _stub_bloom(monkeypatch, {"A": [_rg(30)] * 150 + [_rg(-1)] * 10})
    assert bloom.count_reviewed_since(1, PUBLISHED) == 150


def test_bloom_failure_returns_none(monkeypatch):
    def boom(path, params=None):
        raise RuntimeError("upstream down")
    monkeypatch.setattr(internal_api, "get", boom)
    assert bloom.count_reviewed_since(1, PUBLISHED) is None


def _stub_shift(monkeypatch, completions, stored_count=12):
    monkeypatch.setattr(
        main, "_list_completions_for_snapshot",
        lambda snap, reviewer_email=None, force=False, include_superseded=False: completions,
    )
    monkeypatch.setattr(
        main, "_rows_for_reviewer",
        lambda snap, email, force=False: [{"jobId": "J1", "unreviewedCount": stored_count}],
    )


def test_credit_uses_bloom_count_from_shift_start(monkeypatch):
    _stub_shift(monkeypatch, [{"id": "me", "job_id": "J1", "completed_at": "2026-09-23T15:00:00+00:00"}])
    seen = {}

    def fake_count(job_id, since):
        seen["since"] = since
        return 17
    monkeypatch.setattr(main.bloom, "count_reviewed_since", fake_count)

    snap = {"published_at": PUBLISHED.isoformat()}
    # Stored row says 12, but 17 were actually cleared — credit 17.
    assert main._responses_to_credit("snap1", snap, "r@x.com", "J1", "me") == 17
    assert seen["since"] == PUBLISHED


def test_rehanded_job_only_credits_reviews_since_the_last_checkmark(monkeypatch):
    first_check = datetime.datetime(2026, 9, 23, 15, 0, tzinfo=UTC)
    _stub_shift(monkeypatch, [
        {"id": "old", "job_id": "J1", "completed_at": first_check.isoformat(), "superseded_at": "x"},
        {"id": "other-job", "job_id": "J2", "completed_at": "2026-09-23T16:00:00+00:00"},
        {"id": "me", "job_id": "J1", "completed_at": "2026-09-23T17:00:00+00:00"},
    ])
    seen = {}
    monkeypatch.setattr(main.bloom, "count_reviewed_since",
                        lambda job_id, since: seen.setdefault("since", since) and 4)

    snap = {"published_at": PUBLISHED.isoformat()}
    assert main._responses_to_credit("snap1", snap, "r@x.com", "J1", "me") == 4
    assert seen["since"] == first_check


def test_falls_back_to_stored_count_when_bloom_unreachable(monkeypatch):
    _stub_shift(monkeypatch, [], stored_count=12)
    monkeypatch.setattr(main.bloom, "count_reviewed_since", lambda job_id, since: None)

    snap = {"published_at": PUBLISHED.isoformat()}
    assert main._responses_to_credit("snap1", snap, "r@x.com", "J1", "me") == 12
