import sqlite3
from datetime import datetime, timezone, timedelta
import pytest
from agent_orchestrator.bus import (
    init_db,
    register_session,
    update_session,
    send_message,
    create_review,
    get_pending_reviews,
    resolve_review,
    get_inbox,
)
from agent_orchestrator.orchestrator import (
    ORCHESTRATOR_SESSION_ID,
    PollResult,
    poll_tick,
)


@pytest.fixture
def orchestrated_bus(tmp_path):
    db_path = str(tmp_path / "bus.db")
    init_db(db_path)
    register_session(ORCHESTRATOR_SESSION_ID, "orchestrator", "main", db_path=db_path)
    register_session("A", "python-sdk", "feat/python-sdk", db_path=db_path)
    register_session("B", "ts-sdk", "feat/ts-sdk", db_path=db_path)
    return db_path


class TestPollTick:
    def test_empty_tick_returns_no_events(self, orchestrated_bus):
        result = poll_tick(db_path=orchestrated_bus)
        assert isinstance(result, PollResult)
        assert result.has_events is False
        assert len(result.inbox_messages) == 0
        assert len(result.blocked_sessions) == 0
        assert len(result.stale_sessions) == 0
        assert len(result.pending_reviews) == 0
        assert len(result.actions_taken) == 0

    def test_detects_inbox_messages(self, orchestrated_bus):
        send_message("A", ORCHESTRATOR_SESSION_ID, "need help", db_path=orchestrated_bus)
        result = poll_tick(db_path=orchestrated_bus)
        assert result.has_events is True
        assert len(result.inbox_messages) == 1
        assert result.inbox_messages[0]["body"] == "need help"

    def test_detects_blocked_sessions(self, orchestrated_bus):
        update_session("A", status="blocked", note="waiting on types", db_path=orchestrated_bus)
        result = poll_tick(db_path=orchestrated_bus)
        assert result.has_events is True
        assert len(result.blocked_sessions) == 1
        assert result.blocked_sessions[0]["id"] == "A"

    def test_detects_stale_sessions(self, orchestrated_bus):
        # Manually set updated_at to 30 minutes ago
        conn = sqlite3.connect(orchestrated_bus)
        old_time = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        conn.execute("UPDATE sessions SET updated_at = ? WHERE id = 'A'", (old_time,))
        conn.commit()
        conn.close()
        result = poll_tick(stale_minutes=15, db_path=orchestrated_bus)
        assert result.has_events is True
        stale_ids = {s["id"] for s in result.stale_sessions}
        assert "A" in stale_ids

    def test_ignores_done_sessions_for_stale(self, orchestrated_bus):
        conn = sqlite3.connect(orchestrated_bus)
        old_time = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        conn.execute("UPDATE sessions SET updated_at = ?, status = 'done' WHERE id = 'A'", (old_time,))
        conn.commit()
        conn.close()
        result = poll_tick(stale_minutes=15, db_path=orchestrated_bus)
        stale_ids = {s["id"] for s in result.stale_sessions}
        assert "A" not in stale_ids

    def test_detects_pending_reviews_for_orchestrator(self, orchestrated_bus):
        create_review("A", ORCHESTRATOR_SESSION_ID, "diff", db_path=orchestrated_bus)
        result = poll_tick(db_path=orchestrated_bus)
        assert result.has_events is True
        assert len(result.pending_reviews) == 1

    def test_auto_approve_approves_orchestrator_reviews(self, orchestrated_bus):
        create_review("A", ORCHESTRATOR_SESSION_ID, "diff", db_path=orchestrated_bus)
        result = poll_tick(auto_approve=True, db_path=orchestrated_bus)
        assert any("Auto-approved" in a for a in result.actions_taken)
        # Verify the review is now approved
        pending = get_pending_reviews(ORCHESTRATOR_SESSION_ID, db_path=orchestrated_bus)
        assert len(pending) == 0

    def test_auto_approve_does_not_touch_peer_reviews(self, orchestrated_bus):
        create_review("A", "B", "diff", db_path=orchestrated_bus)
        result = poll_tick(auto_approve=True, db_path=orchestrated_bus)
        # Peer review should still be pending
        pending = get_pending_reviews("B", db_path=orchestrated_bus)
        assert len(pending) == 1

    def test_does_not_include_orchestrator_in_blocked_or_stale(self, orchestrated_bus):
        update_session(ORCHESTRATOR_SESSION_ID, status="blocked", db_path=orchestrated_bus)
        result = poll_tick(db_path=orchestrated_bus)
        blocked_ids = {s["id"] for s in result.blocked_sessions}
        assert ORCHESTRATOR_SESSION_ID not in blocked_ids

    def test_sessions_excludes_orchestrator(self, orchestrated_bus):
        result = poll_tick(db_path=orchestrated_bus)
        session_ids = {s["id"] for s in result.sessions}
        assert ORCHESTRATOR_SESSION_ID not in session_ids
        assert "A" in session_ids
        assert "B" in session_ids
