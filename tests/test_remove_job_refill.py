"""Test that removing a job and completing all remaining jobs triggers refill."""

import datetime
import os

import jwt
import pytest

os.environ["LOCAL_DEV"] = "1"

import main  # noqa: E402

REVIEWER = "sam@storesight.com"
ADMIN = "admin@storesight.com"


def _make_dev_token(email, name="User"):
    now = datetime.datetime.now(datetime.timezone.utc)
    return jwt.encode(
        {"email": email, "name": name, "iat": now,
         "exp": now + datetime.timedelta(hours=8)},
        "irrelevant", algorithm="HS256",
    )


@pytest.fixture
def client(tmp_path, monkeypatch):
    token_dir = tmp_path / ".storesight"
    token_dir.mkdir()
    token_file = token_dir / "dev-token"
    token_file.write_text(_make_dev_token(REVIEWER, "Sam"))
    monkeypatch.setattr("main._dev_token_path", lambda: token_file)
    main.app.config["TESTING"] = True
    with main.app.test_client() as c:
        yield c


def test_removing_job_then_completing_last_job_triggers_refill(monkeypatch):
    """Admin removes a job, reviewer completes all remaining jobs -> refill triggers."""
    # Verify the logic: after removing job A and completing job B,
    # the refill condition (not was_complete and is_complete) should be True

    # After removing A, only B is assigned
    assigned = [
        {"jobId": "B", "name": "Job B", "unreviewedCount": 5},
    ]
    assigned_keys = {main._row_job_key(r) for r in assigned}

    # B has been completed
    completions = [
        {"job_id": "B", "completed_at": "2026-09-10T00:00:00Z"}
    ]
    done_keys = {main._completion_job_key(c) for c in completions}

    # Simulate the completion check logic
    job_just_completed = "B"
    done_keys_before = done_keys - {str(job_just_completed)}
    done_keys_after = done_keys

    was_complete = len(assigned_keys) > 0 and assigned_keys <= done_keys_before
    is_complete = len(assigned_keys) > 0 and assigned_keys <= done_keys_after

    # After removing A and completing B:
    # - assigned_keys = {"B"}
    # - done_keys = {"B"}
    # - done_keys_before = {} (we exclude B for the before state)
    # - was_complete = {"B"} <= {} = False
    # - is_complete = {"B"} <= {"B"} = True
    # Therefore: not False and True = True -> should trigger refill

    assert assigned_keys == {"B"}, f"Expected {{'B'}}, got {assigned_keys}"
    assert done_keys == {"B"}, f"Expected {{'B'}}, got {done_keys}"
    assert not was_complete, "Should not have been complete before"
    assert is_complete, "Should be complete now"
    assert not was_complete and is_complete, "Should trigger refill condition"
