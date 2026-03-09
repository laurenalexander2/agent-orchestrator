import os
import pytest
from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from claude_swarm.ao import main as ao_main


@pytest.fixture
def ao_env(tmp_path):
    runner = CliRunner()
    project_dir = str(tmp_path / "my-project")
    os.makedirs(project_dir)
    return runner, project_dir


class TestSetup:
    def test_setup_passes_when_claude_exists(self, ao_env):
        runner, _ = ao_env
        with patch("claude_swarm.ao._check_claude", return_value=True):
            result = runner.invoke(ao_main, ["setup"])
        assert result.exit_code == 0
        assert "set" in result.output.lower()

    def test_setup_fails_when_claude_missing(self, ao_env):
        runner, _ = ao_env
        with patch("claude_swarm.ao._check_claude", return_value=False):
            result = runner.invoke(ao_main, ["setup"])
        assert result.exit_code == 1
        assert "claude" in result.output.lower()


class TestStart:
    def test_writes_claude_md(self, ao_env):
        runner, project_dir = ao_env
        with patch("claude_swarm.ao._check_claude", return_value=True), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(ao_main, ["start", "Build a REST API", "--project-dir", project_dir])
        claude_md = os.path.join(project_dir, "CLAUDE.md")
        assert os.path.exists(claude_md)
        content = open(claude_md).read()
        assert "claude-swarm" in content
        assert "inbox" in content
        assert "claim" in content

    def test_creates_claude_swarm_dir(self, ao_env):
        runner, project_dir = ao_env
        with patch("claude_swarm.ao._check_claude", return_value=True), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(ao_main, ["start", "Build a REST API", "--project-dir", project_dir])
        ao_dir = os.path.join(project_dir, ".claude-swarm")
        assert os.path.isdir(ao_dir)

    def test_launches_claude_with_prompt(self, ao_env):
        runner, project_dir = ao_env
        with patch("claude_swarm.ao._check_claude", return_value=True), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(ao_main, ["start", "Build a REST API with auth", "--project-dir", project_dir])
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert cmd[0] == "claude"
        prompt = cmd[1]
        assert "Build a REST API with auth" in prompt

    def test_fails_without_claude(self, ao_env):
        runner, project_dir = ao_env
        with patch("claude_swarm.ao._check_claude", return_value=False):
            result = runner.invoke(ao_main, ["start", "Build something", "--project-dir", project_dir])
        assert result.exit_code == 1

    def test_does_not_overwrite_existing_claude_md(self, ao_env):
        runner, project_dir = ao_env
        claude_md = os.path.join(project_dir, "CLAUDE.md")
        with open(claude_md, "w") as f:
            f.write("# Existing project instructions\nDo not delete this.\n")
        with patch("claude_swarm.ao._check_claude", return_value=True), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(ao_main, ["start", "Build a CLI", "--project-dir", project_dir])
        content = open(claude_md).read()
        assert "Existing project instructions" in content
        assert "claude-swarm" in content

    def test_claude_md_has_db_path(self, ao_env):
        runner, project_dir = ao_env
        with patch("claude_swarm.ao._check_claude", return_value=True), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(ao_main, ["start", "Build X", "--project-dir", project_dir])
        claude_md = os.path.join(project_dir, "CLAUDE.md")
        content = open(claude_md).read()
        assert ".claude-swarm/bus.db" in content

    def test_prompt_uses_copy_paste_not_task_tool(self, ao_env):
        runner, project_dir = ao_env
        with patch("claude_swarm.ao._check_claude", return_value=True), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = runner.invoke(ao_main, ["start", "Build a CLI", "--project-dir", project_dir])
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        prompt = cmd[1]
        assert "copy-paste" in prompt.lower() or "copy" in prompt.lower()
        assert "Do NOT use the Task tool" in prompt
        assert "spawn a sub-agent" not in prompt
        assert "print" in prompt.lower() and "prompt" in prompt.lower()

    def test_session_prompt_template_exists(self, ao_env):
        from claude_swarm.ao import SESSION_PROMPT_TEMPLATE
        assert "{session_id}" in SESSION_PROMPT_TEMPLATE
        assert "{workstream}" in SESSION_PROMPT_TEMPLATE
        assert "{task}" in SESSION_PROMPT_TEMPLATE
        assert "inbox" in SESSION_PROMPT_TEMPLATE
        assert "claim" in SESSION_PROMPT_TEMPLATE
        assert "commit" in SESSION_PROMPT_TEMPLATE
        assert "push" in SESSION_PROMPT_TEMPLATE

    def test_claude_md_has_sync_and_context(self, ao_env):
        runner, project_dir = ao_env
        with patch("claude_swarm.ao._check_claude", return_value=True), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            runner.invoke(ao_main, ["start", "Build X", "--project-dir", project_dir])
        content = open(os.path.join(project_dir, "CLAUDE.md")).read()
        assert "sync" in content
        assert "context" in content

    def test_session_prompt_template_has_sync_and_context(self, ao_env):
        from claude_swarm.ao import SESSION_PROMPT_TEMPLATE
        assert "sync" in SESSION_PROMPT_TEMPLATE
        assert "context" in SESSION_PROMPT_TEMPLATE

    def test_skips_duplicate_claude_md_section(self, ao_env):
        runner, project_dir = ao_env
        with patch("claude_swarm.ao._check_claude", return_value=True), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            runner.invoke(ao_main, ["start", "Build X", "--project-dir", project_dir])
            runner.invoke(ao_main, ["start", "Build Y", "--project-dir", project_dir])
        claude_md = os.path.join(project_dir, "CLAUDE.md")
        content = open(claude_md).read()
        assert content.count("## Claude Swarm") == 1

    def test_prompt_has_plan_phase(self, ao_env):
        runner, project_dir = ao_env
        with patch("claude_swarm.ao._check_claude", return_value=True), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            runner.invoke(ao_main, ["start", "Build an API", "--project-dir", project_dir])
        prompt = mock_run.call_args[0][0][1]
        assert "Phase 1" in prompt or "PLAN" in prompt.upper()
        assert "approve" in prompt.lower()
        assert "Phase 2" in prompt or "EXECUTE" in prompt.upper()
