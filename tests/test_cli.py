import os
import pytest
from click.testing import CliRunner
from claude_swarm.cli import main
from claude_swarm.bus import init_db


@pytest.fixture
def cli_env(tmp_path):
    db_path = str(tmp_path / "bus.db")
    runner = CliRunner()
    return runner, db_path


def _db(db_path):
    """Return ['--db', db_path] for insertion right after 'main'."""
    return ["--db", db_path]


class TestInit:
    def test_init_creates_db_and_registers_sessions(self, cli_env):
        runner, db_path = cli_env
        result = runner.invoke(main, [*_db(db_path), "init", "--sessions", "A:python-sdk B:ts-sdk"])
        assert result.exit_code == 0
        assert "Initialized" in result.output
        # Verify sessions registered via status
        result = runner.invoke(main, [*_db(db_path), "status"])
        assert result.exit_code == 0
        assert "A" in result.output
        assert "B" in result.output


class TestStatus:
    def test_status_renders_table(self, cli_env):
        runner, db_path = cli_env
        runner.invoke(main, [*_db(db_path), "init", "--sessions", "A:python-sdk B:ts-sdk C:website"])
        result = runner.invoke(main, [*_db(db_path), "status"])
        assert result.exit_code == 0
        assert "A" in result.output
        assert "python-sdk" in result.output
        assert "running" in result.output


class TestMessageFlow:
    def test_send_inbox_reply(self, cli_env):
        runner, db_path = cli_env
        runner.invoke(main, [*_db(db_path), "init", "--sessions", "A:python-sdk B:ts-sdk"])

        # A sends message to B
        result = runner.invoke(main, [*_db(db_path), "message", "B", "hello from A", "--from", "A"])
        assert result.exit_code == 0

        # B checks inbox
        result = runner.invoke(main, [*_db(db_path), "inbox", "--session", "B"])
        assert result.exit_code == 0
        assert "hello from A" in result.output

        # B replies to message 1
        result = runner.invoke(main, [*_db(db_path), "reply", "1", "got it", "--from", "B"])
        assert result.exit_code == 0

        # A checks inbox
        result = runner.invoke(main, [*_db(db_path), "inbox", "--session", "A"])
        assert result.exit_code == 0
        assert "got it" in result.output


class TestReviewFlow:
    def test_request_approve_merge_ok(self, cli_env):
        runner, db_path = cli_env
        runner.invoke(main, [*_db(db_path), "init", "--sessions", "A:python-sdk B:ts-sdk"])

        # A requests review from B
        result = runner.invoke(main, [*_db(db_path), "review", "request", "--from", "A", "--to", "B", "--diff", "test diff"])
        assert result.exit_code == 0

        # merge-ok should fail (pending review)
        result = runner.invoke(main, [*_db(db_path), "merge-ok", "A"])
        assert result.exit_code == 1

        # B approves
        result = runner.invoke(main, [*_db(db_path), "review", "approve", "1", "--from", "B", "--comment", "lgtm"])
        assert result.exit_code == 0

        # merge-ok should succeed
        result = runner.invoke(main, [*_db(db_path), "merge-ok", "A"])
        assert result.exit_code == 0

    def test_reject_keeps_blocked(self, cli_env):
        runner, db_path = cli_env
        runner.invoke(main, [*_db(db_path), "init", "--sessions", "A:python-sdk B:ts-sdk"])

        runner.invoke(main, [*_db(db_path), "review", "request", "--from", "A", "--to", "B", "--diff", "test"])
        runner.invoke(main, [*_db(db_path), "review", "reject", "1", "--from", "B", "--comment", "fix types"])

        result = runner.invoke(main, [*_db(db_path), "merge-ok", "A"])
        assert result.exit_code == 1

    def test_review_list_and_show(self, cli_env):
        runner, db_path = cli_env
        runner.invoke(main, [*_db(db_path), "init", "--sessions", "A:python-sdk B:ts-sdk"])
        runner.invoke(main, [*_db(db_path), "review", "request", "--from", "A", "--to", "B", "--diff", "test diff"])

        result = runner.invoke(main, [*_db(db_path), "review", "list"])
        assert result.exit_code == 0
        assert "pending" in result.output

        result = runner.invoke(main, [*_db(db_path), "review", "show", "1"])
        assert result.exit_code == 0
        assert "test diff" in result.output


class TestFileClaims:
    def test_claim_and_list(self, cli_env):
        runner, db_path = cli_env
        runner.invoke(main, [*_db(db_path), "init", "--sessions", "A:python-sdk B:ts-sdk"])

        result = runner.invoke(main, [*_db(db_path), "claim", "src/client.py", "--session", "A"])
        assert result.exit_code == 0

        result = runner.invoke(main, [*_db(db_path), "claims"])
        assert result.exit_code == 0
        assert "src/client.py" in result.output
        assert "A" in result.output

    def test_claim_conflict(self, cli_env):
        runner, db_path = cli_env
        runner.invoke(main, [*_db(db_path), "init", "--sessions", "A:python-sdk B:ts-sdk"])

        runner.invoke(main, [*_db(db_path), "claim", "src/client.py", "--session", "A"])
        result = runner.invoke(main, [*_db(db_path), "claim", "src/client.py", "--session", "B"])
        assert result.exit_code == 1

    def test_unclaim(self, cli_env):
        runner, db_path = cli_env
        runner.invoke(main, [*_db(db_path), "init", "--sessions", "A:python-sdk"])

        runner.invoke(main, [*_db(db_path), "claim", "src/client.py", "--session", "A"])
        result = runner.invoke(main, [*_db(db_path), "unclaim", "src/client.py", "--session", "A"])
        assert result.exit_code == 0


class TestSessionUpdate:
    def test_update_status_and_note(self, cli_env):
        runner, db_path = cli_env
        runner.invoke(main, [*_db(db_path), "init", "--sessions", "A:python-sdk"])

        result = runner.invoke(main, [*_db(db_path), "update", "A", "--status", "blocked", "--note", "waiting on B"])
        assert result.exit_code == 0

        result = runner.invoke(main, [*_db(db_path), "status"])
        assert "blocked" in result.output


class TestOrchestrateDashboard:
    def test_dashboard_shows_sessions(self, cli_env):
        runner, db_path = cli_env
        runner.invoke(main, [*_db(db_path), "init", "--sessions", "orchestrator:orchestrator A:python-sdk B:ts-sdk"])
        result = runner.invoke(main, [*_db(db_path), "orchestrate", "dashboard"])
        assert result.exit_code == 0
        assert "all clear" in result.output or "Orchestrator" in result.output

    def test_dashboard_shows_blocked(self, cli_env):
        runner, db_path = cli_env
        runner.invoke(main, [*_db(db_path), "init", "--sessions", "orchestrator:orchestrator A:python-sdk"])
        runner.invoke(main, [*_db(db_path), "update", "A", "--status", "blocked", "--note", "waiting"])
        result = runner.invoke(main, [*_db(db_path), "orchestrate", "dashboard"])
        assert result.exit_code == 0
        assert "ALERT" in result.output or "blocked" in result.output

    def test_dashboard_shows_inbox(self, cli_env):
        runner, db_path = cli_env
        runner.invoke(main, [*_db(db_path), "init", "--sessions", "orchestrator:orchestrator A:python-sdk"])
        runner.invoke(main, [*_db(db_path), "message", "orchestrator", "hello", "--from", "A"])
        result = runner.invoke(main, [*_db(db_path), "orchestrate", "dashboard"])
        assert result.exit_code == 0
        assert "hello" in result.output


class TestContextCommands:
    def test_context_add_and_show(self, cli_env):
        runner, db_path = cli_env
        runner.invoke(main, [*_db(db_path), "init", "--sessions", "A:python-sdk"])
        result = runner.invoke(main, [*_db(db_path), "context", "add", "API uses snake_case",
                                      "--session", "A", "--category", "decision"])
        assert result.exit_code == 0
        assert "Context #" in result.output

        result = runner.invoke(main, [*_db(db_path), "context", "show"])
        assert result.exit_code == 0
        assert "snake_case" in result.output
        assert "decision" in result.output

    def test_context_show_empty(self, cli_env):
        runner, db_path = cli_env
        runner.invoke(main, [*_db(db_path), "init", "--sessions", "A:python-sdk"])
        result = runner.invoke(main, [*_db(db_path), "context", "show"])
        assert result.exit_code == 0
        assert "No shared context" in result.output

    def test_context_invalid_category_rejected(self, cli_env):
        runner, db_path = cli_env
        runner.invoke(main, [*_db(db_path), "init", "--sessions", "A:python-sdk"])
        result = runner.invoke(main, [*_db(db_path), "context", "add", "test",
                                      "--session", "A", "--category", "invalid"])
        assert result.exit_code != 0


class TestSyncCommand:
    def test_sync_silent_when_nothing_new(self, cli_env):
        runner, db_path = cli_env
        runner.invoke(main, [*_db(db_path), "init", "--sessions", "A:python-sdk"])
        result = runner.invoke(main, [*_db(db_path), "sync", "--session", "A"])
        assert result.exit_code == 0
        assert result.output.strip() == ""

    def test_sync_shows_new_messages(self, cli_env):
        runner, db_path = cli_env
        runner.invoke(main, [*_db(db_path), "init", "--sessions", "A:python-sdk B:ts-sdk"])
        runner.invoke(main, [*_db(db_path), "message", "A", "hello from B", "--from", "B"])
        result = runner.invoke(main, [*_db(db_path), "sync", "--session", "A"])
        assert result.exit_code == 0
        assert "[SYNC]" in result.output
        assert "MSG" in result.output
        assert "hello from B" in result.output

    def test_sync_shows_new_context(self, cli_env):
        runner, db_path = cli_env
        runner.invoke(main, [*_db(db_path), "init", "--sessions", "A:python-sdk B:ts-sdk"])
        runner.invoke(main, [*_db(db_path), "context", "add", "use snake_case",
                             "--session", "B", "--category", "decision"])
        result = runner.invoke(main, [*_db(db_path), "sync", "--session", "A"])
        assert result.exit_code == 0
        assert "CTX" in result.output
        assert "snake_case" in result.output

    def test_sync_second_call_silent(self, cli_env):
        runner, db_path = cli_env
        runner.invoke(main, [*_db(db_path), "init", "--sessions", "A:python-sdk B:ts-sdk"])
        runner.invoke(main, [*_db(db_path), "message", "A", "hello", "--from", "B"])
        runner.invoke(main, [*_db(db_path), "sync", "--session", "A"])
        result = runner.invoke(main, [*_db(db_path), "sync", "--session", "A"])
        assert result.output.strip() == ""

    def test_sync_marks_messages_read(self, cli_env):
        runner, db_path = cli_env
        runner.invoke(main, [*_db(db_path), "init", "--sessions", "A:python-sdk B:ts-sdk"])
        runner.invoke(main, [*_db(db_path), "message", "A", "hello", "--from", "B"])
        runner.invoke(main, [*_db(db_path), "sync", "--session", "A"])
        result = runner.invoke(main, [*_db(db_path), "inbox", "--session", "A"])
        assert "hello" not in result.output
