import os
import tempfile
import pytest
import sqlite3
import time
from claude_swarm.bus import (
    init_db,
    register_session,
    update_session,
    get_all_sessions,
    send_message,
    get_inbox,
    mark_read,
    create_review,
    get_pending_reviews,
    get_all_pending_reviews,
    get_all_messages,
    resolve_review,
    can_merge,
    claim_file,
    release_claim,
    get_claims,
    release_all_claims,
    add_context,
    get_context,
    get_context_since,
    sync_session,
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

    def test_approve_notifies_requester(self, db_path):
        register_session("A", "python-sdk", "feat/python-sdk", db_path=db_path)
        register_session("B", "ts-sdk", "feat/ts-sdk", db_path=db_path)
        review_id = create_review("A", "B", "diff content", db_path=db_path)
        # Clear B's inbox (the review_request notification)
        for msg in get_inbox("B", db_path=db_path):
            mark_read(msg["id"], db_path=db_path)
        resolve_review(review_id, "approved", comments="lgtm", db_path=db_path)
        inbox = get_inbox("A", db_path=db_path)
        assert len(inbox) == 1
        assert "approved" in inbox[0]["body"]
        assert "merge-ok" in inbox[0]["body"]
        assert inbox[0]["type"] == "review_approved"

    def test_reject_notifies_requester(self, db_path):
        register_session("A", "python-sdk", "feat/python-sdk", db_path=db_path)
        register_session("B", "ts-sdk", "feat/ts-sdk", db_path=db_path)
        review_id = create_review("A", "B", "diff content", db_path=db_path)
        resolve_review(review_id, "rejected", comments="fix types", db_path=db_path)
        inbox = get_inbox("A", db_path=db_path)
        assert len(inbox) == 1
        assert "rejected" in inbox[0]["body"]
        assert "fix types" in inbox[0]["body"]
        assert inbox[0]["type"] == "review_rejected"


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


class TestGetAllPendingReviews:
    def test_returns_all_pending_across_reviewers(self, db_path):
        register_session("A", "python-sdk", "feat/python-sdk", db_path=db_path)
        register_session("B", "ts-sdk", "feat/ts-sdk", db_path=db_path)
        register_session("C", "website", "feat/website", db_path=db_path)
        create_review("A", "B", "diff1", db_path=db_path)
        create_review("A", "C", "diff2", db_path=db_path)
        pending = get_all_pending_reviews(db_path=db_path)
        assert len(pending) == 2
        reviewers = {r["reviewer"] for r in pending}
        assert reviewers == {"B", "C"}

    def test_excludes_approved_reviews(self, db_path):
        register_session("A", "python-sdk", "feat/python-sdk", db_path=db_path)
        register_session("B", "ts-sdk", "feat/ts-sdk", db_path=db_path)
        review_id = create_review("A", "B", "diff", db_path=db_path)
        resolve_review(review_id, "approved", comments="lgtm", db_path=db_path)
        pending = get_all_pending_reviews(db_path=db_path)
        assert len(pending) == 0


class TestGetAllMessages:
    def test_returns_all_unread(self, db_path):
        register_session("A", "python-sdk", "feat/python-sdk", db_path=db_path)
        register_session("B", "ts-sdk", "feat/ts-sdk", db_path=db_path)
        send_message("A", "B", "msg1", db_path=db_path)
        send_message("B", "A", "msg2", db_path=db_path)
        msgs = get_all_messages(db_path=db_path)
        assert len(msgs) >= 2
        bodies = {m["body"] for m in msgs}
        assert "msg1" in bodies
        assert "msg2" in bodies

    def test_includes_read_when_unread_only_false(self, db_path):
        register_session("A", "python-sdk", "feat/python-sdk", db_path=db_path)
        register_session("B", "ts-sdk", "feat/ts-sdk", db_path=db_path)
        msg_id = send_message("A", "B", "msg1", db_path=db_path)
        mark_read(msg_id, db_path=db_path)
        # unread_only=True should exclude it
        unread = get_all_messages(unread_only=True, db_path=db_path)
        assert all(m["body"] != "msg1" for m in unread)
        # unread_only=False should include it
        all_msgs = get_all_messages(unread_only=False, db_path=db_path)
        assert any(m["body"] == "msg1" for m in all_msgs)


class TestSharedContext:
    def test_add_and_get_context(self, db_path):
        register_session("A", "python-sdk", "feat/python-sdk", db_path=db_path)
        ctx_id = add_context("A", "API uses snake_case", "decision", db_path=db_path)
        assert ctx_id > 0
        entries = get_context(db_path=db_path)
        assert len(entries) == 1
        assert entries[0]["body"] == "API uses snake_case"
        assert entries[0]["category"] == "decision"
        assert entries[0]["session_id"] == "A"

    def test_invalid_category_raises(self, db_path):
        register_session("A", "python-sdk", "feat/python-sdk", db_path=db_path)
        with pytest.raises(ValueError, match="Invalid category"):
            add_context("A", "something", "invalid_category", db_path=db_path)

    def test_get_context_since_filters_by_time(self, db_path):
        register_session("A", "python-sdk", "feat/python-sdk", db_path=db_path)
        add_context("A", "first", "decision", db_path=db_path)
        time.sleep(0.05)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        ts = conn.execute("SELECT created_at FROM shared_context ORDER BY id DESC LIMIT 1").fetchone()["created_at"]
        conn.close()
        time.sleep(0.05)
        add_context("A", "second", "interface", db_path=db_path)
        entries = get_context_since(ts, db_path=db_path)
        assert len(entries) == 1
        assert entries[0]["body"] == "second"

    def test_multiple_sessions_contribute_context(self, db_path):
        register_session("A", "python-sdk", "feat/python-sdk", db_path=db_path)
        register_session("B", "ts-sdk", "feat/ts-sdk", db_path=db_path)
        add_context("A", "decision from A", "decision", db_path=db_path)
        add_context("B", "warning from B", "warning", db_path=db_path)
        entries = get_context(db_path=db_path)
        assert len(entries) == 2
        sessions = {e["session_id"] for e in entries}
        assert sessions == {"A", "B"}

    def test_all_valid_categories_accepted(self, db_path):
        register_session("A", "python-sdk", "feat/python-sdk", db_path=db_path)
        for cat in ("decision", "interface", "warning", "convention", "discovery"):
            ctx_id = add_context("A", f"test {cat}", cat, db_path=db_path)
            assert ctx_id > 0


class TestSyncSession:
    def test_sync_returns_unread_messages(self, db_path):
        register_session("A", "python-sdk", "feat/python-sdk", db_path=db_path)
        register_session("B", "ts-sdk", "feat/ts-sdk", db_path=db_path)
        send_message("B", "A", "hello", db_path=db_path)
        result = sync_session("A", db_path=db_path)
        assert len(result["messages"]) == 1
        assert result["messages"][0]["body"] == "hello"

    def test_sync_marks_messages_as_read(self, db_path):
        register_session("A", "python-sdk", "feat/python-sdk", db_path=db_path)
        register_session("B", "ts-sdk", "feat/ts-sdk", db_path=db_path)
        send_message("B", "A", "hello", db_path=db_path)
        sync_session("A", db_path=db_path)
        result = sync_session("A", db_path=db_path)
        assert len(result["messages"]) == 0

    def test_sync_returns_context_since_last_sync(self, db_path):
        register_session("A", "python-sdk", "feat/python-sdk", db_path=db_path)
        register_session("B", "ts-sdk", "feat/ts-sdk", db_path=db_path)
        add_context("B", "old context", "decision", db_path=db_path)
        result1 = sync_session("A", db_path=db_path)
        assert len(result1["context"]) == 1
        time.sleep(0.05)
        add_context("B", "new context", "interface", db_path=db_path)
        result2 = sync_session("A", db_path=db_path)
        assert len(result2["context"]) == 1
        assert result2["context"][0]["body"] == "new context"

    def test_sync_updates_heartbeat(self, db_path):
        register_session("A", "python-sdk", "feat/python-sdk", db_path=db_path)
        sessions_before = get_all_sessions(db_path=db_path)
        before_ts = sessions_before[0]["updated_at"]
        time.sleep(0.05)
        sync_session("A", db_path=db_path)
        sessions_after = get_all_sessions(db_path=db_path)
        after_ts = sessions_after[0]["updated_at"]
        assert after_ts > before_ts

    def test_sync_updates_last_sync_at(self, db_path):
        register_session("A", "python-sdk", "feat/python-sdk", db_path=db_path)
        sync_session("A", db_path=db_path)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT last_sync_at FROM sessions WHERE id = 'A'").fetchone()
        conn.close()
        assert row["last_sync_at"] is not None

    def test_sync_empty_returns_no_messages_no_context(self, db_path):
        register_session("A", "python-sdk", "feat/python-sdk", db_path=db_path)
        result = sync_session("A", db_path=db_path)
        assert len(result["messages"]) == 0
        assert len(result["context"]) == 0

    def test_first_sync_returns_all_existing_context(self, db_path):
        register_session("A", "python-sdk", "feat/python-sdk", db_path=db_path)
        register_session("B", "ts-sdk", "feat/ts-sdk", db_path=db_path)
        add_context("B", "entry1", "decision", db_path=db_path)
        add_context("B", "entry2", "interface", db_path=db_path)
        result = sync_session("A", db_path=db_path)
        assert len(result["context"]) == 2
