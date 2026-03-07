import os
import tempfile
import pytest
from agent_orchestrator.bus import (
    init_db,
    register_session,
    update_session,
    get_all_sessions,
    send_message,
    get_inbox,
    mark_read,
    create_review,
    get_pending_reviews,
    resolve_review,
    can_merge,
    claim_file,
    release_claim,
    get_claims,
    release_all_claims,
)


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "bus.db")
    init_db(path)
    return path


class TestSessions:
    def test_register_and_list(self, db_path):
        register_session("A", "python-sdk", "feat/python-sdk", db_path=db_path)
        register_session("B", "ts-sdk", "feat/ts-sdk", db_path=db_path)
        sessions = get_all_sessions(db_path=db_path)
        assert len(sessions) == 2
        ids = {s["id"] for s in sessions}
        assert ids == {"A", "B"}

    def test_update_session(self, db_path):
        register_session("A", "python-sdk", "feat/python-sdk", db_path=db_path)
        update_session("A", status="blocked", note="waiting on B", db_path=db_path)
        sessions = get_all_sessions(db_path=db_path)
        assert sessions[0]["status"] == "blocked"
        assert sessions[0]["note"] == "waiting on B"


class TestMessages:
    def test_two_sessions_exchange_message(self, db_path):
        register_session("A", "python-sdk", "feat/python-sdk", db_path=db_path)
        register_session("B", "ts-sdk", "feat/ts-sdk", db_path=db_path)
        msg_id = send_message("A", "B", "hello from A", db_path=db_path)
        assert msg_id > 0
        inbox = get_inbox("B", db_path=db_path)
        assert len(inbox) == 1
        assert inbox[0]["body"] == "hello from A"
        assert inbox[0]["from_id"] == "A"

    def test_inbox_only_returns_unread(self, db_path):
        register_session("A", "python-sdk", "feat/python-sdk", db_path=db_path)
        register_session("B", "ts-sdk", "feat/ts-sdk", db_path=db_path)
        msg1 = send_message("A", "B", "first", db_path=db_path)
        msg2 = send_message("A", "B", "second", db_path=db_path)
        mark_read(msg1, db_path=db_path)
        inbox = get_inbox("B", db_path=db_path)
        assert len(inbox) == 1
        assert inbox[0]["body"] == "second"

    def test_inbox_scoped_to_session(self, db_path):
        register_session("A", "python-sdk", "feat/python-sdk", db_path=db_path)
        register_session("B", "ts-sdk", "feat/ts-sdk", db_path=db_path)
        register_session("C", "website", "feat/website", db_path=db_path)
        send_message("A", "B", "for B", db_path=db_path)
        send_message("A", "C", "for C", db_path=db_path)
        inbox_b = get_inbox("B", db_path=db_path)
        inbox_c = get_inbox("C", db_path=db_path)
        assert len(inbox_b) == 1
        assert len(inbox_c) == 1
        assert inbox_b[0]["body"] == "for B"
        assert inbox_c[0]["body"] == "for C"


class TestReviews:
    def test_review_blocks_merge_until_approved(self, db_path):
        register_session("A", "python-sdk", "feat/python-sdk", db_path=db_path)
        register_session("B", "ts-sdk", "feat/ts-sdk", db_path=db_path)
        review_id = create_review("A", "B", "diff content", db_path=db_path)
        assert not can_merge("A", db_path=db_path)
        resolve_review(review_id, "approved", comments="lgtm", db_path=db_path)
        assert can_merge("A", db_path=db_path)

    def test_rejection_keeps_review_pending(self, db_path):
        register_session("A", "python-sdk", "feat/python-sdk", db_path=db_path)
        register_session("B", "ts-sdk", "feat/ts-sdk", db_path=db_path)
        review_id = create_review("A", "B", "diff content", db_path=db_path)
        resolve_review(review_id, "rejected", comments="fix types", db_path=db_path)
        # Rejection resets to pending so requester must re-request
        assert not can_merge("A", db_path=db_path)
        pending = get_pending_reviews("B", db_path=db_path)
        assert len(pending) == 1
        assert pending[0]["status"] == "pending"

    def test_no_reviews_means_can_merge(self, db_path):
        register_session("A", "python-sdk", "feat/python-sdk", db_path=db_path)
        assert can_merge("A", db_path=db_path)

    def test_create_review_notifies_reviewer(self, db_path):
        register_session("A", "python-sdk", "feat/python-sdk", db_path=db_path)
        register_session("B", "ts-sdk", "feat/ts-sdk", db_path=db_path)
        review_id = create_review("A", "B", "diff content", db_path=db_path)
        inbox = get_inbox("B", db_path=db_path)
        assert len(inbox) == 1
        assert "Review #" in inbox[0]["body"]
        assert inbox[0]["type"] == "review_request"


class TestFileClaims:
    def test_claim_prevents_double_claim(self, db_path):
        register_session("A", "python-sdk", "feat/python-sdk", db_path=db_path)
        register_session("B", "ts-sdk", "feat/ts-sdk", db_path=db_path)
        assert claim_file("A", "src/client.py", db_path=db_path) is True
        assert claim_file("B", "src/client.py", db_path=db_path) is False

    def test_release_then_reclaim(self, db_path):
        register_session("A", "python-sdk", "feat/python-sdk", db_path=db_path)
        register_session("B", "ts-sdk", "feat/ts-sdk", db_path=db_path)
        claim_file("A", "src/client.py", db_path=db_path)
        release_claim("A", "src/client.py", db_path=db_path)
        assert claim_file("B", "src/client.py", db_path=db_path) is True

    def test_get_claims_by_session(self, db_path):
        register_session("A", "python-sdk", "feat/python-sdk", db_path=db_path)
        claim_file("A", "src/a.py", db_path=db_path)
        claim_file("A", "src/b.py", db_path=db_path)
        claims = get_claims(session_id="A", db_path=db_path)
        assert len(claims) == 2
        paths = {c["file_path"] for c in claims}
        assert paths == {"src/a.py", "src/b.py"}

    def test_release_all_claims(self, db_path):
        register_session("A", "python-sdk", "feat/python-sdk", db_path=db_path)
        claim_file("A", "src/a.py", db_path=db_path)
        claim_file("A", "src/b.py", db_path=db_path)
        release_all_claims("A", db_path=db_path)
        claims = get_claims(session_id="A", db_path=db_path)
        assert len(claims) == 0

    def test_same_session_can_reclaim_own_file(self, db_path):
        register_session("A", "python-sdk", "feat/python-sdk", db_path=db_path)
        assert claim_file("A", "src/client.py", db_path=db_path) is True
        assert claim_file("A", "src/client.py", db_path=db_path) is True
