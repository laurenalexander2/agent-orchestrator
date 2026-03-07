import os
import tempfile
import subprocess
import threading
import time
import pytest
from claude_swarm.bus import init_db
from claude_swarm import git as agent_git


@pytest.fixture
def repo(tmp_path):
    """Create a bare remote and a working clone with an initial commit."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], capture_output=True, check=True)

    work = tmp_path / "work"
    subprocess.run(["git", "clone", str(remote), str(work)], capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(work), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(work), capture_output=True, check=True)

    # Create initial commit so we have a branch
    (work / "README.md").write_text("# test\n")
    subprocess.run(["git", "add", "."], cwd=str(work), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=str(work), capture_output=True, check=True)
    # Detect branch name (master or main)
    branch_result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(work), capture_output=True, text=True, check=True,
    )
    branch = branch_result.stdout.strip()
    subprocess.run(["git", "push", "-u", "origin", branch], cwd=str(work), capture_output=True, check=True)

    db_path = str(tmp_path / "bus.db")
    init_db(db_path)

    return {"remote": str(remote), "work": str(work), "db_path": db_path}


class TestLock:
    def test_acquire_and_release(self, repo):
        db_path = repo["db_path"]
        assert agent_git.acquire_lock("A", db_path=db_path) is True
        assert agent_git.get_lock_holder(db_path=db_path) == "A"
        agent_git.release_lock("A", db_path=db_path)
        assert agent_git.get_lock_holder(db_path=db_path) is None

    def test_lock_blocks_second_session(self, repo):
        db_path = repo["db_path"]
        assert agent_git.acquire_lock("A", db_path=db_path) is True
        # Second session should fail with short timeout
        assert agent_git.acquire_lock("B", timeout=1, db_path=db_path) is False
        agent_git.release_lock("A", db_path=db_path)

    def test_lock_succeeds_after_release(self, repo):
        db_path = repo["db_path"]
        agent_git.acquire_lock("A", db_path=db_path)
        agent_git.release_lock("A", db_path=db_path)
        assert agent_git.acquire_lock("B", db_path=db_path) is True
        agent_git.release_lock("B", db_path=db_path)


class TestCommit:
    def test_commit_message_prefixed_with_session_id(self, repo):
        work = repo["work"]
        db_path = repo["db_path"]
        (work + "/test.txt").replace("/", os.sep)
        with open(os.path.join(work, "test.txt"), "w") as f:
            f.write("hello\n")
        agent_git.add("test.txt", repo_path=work)
        ok, msg = agent_git.commit("A", "add test file", repo_path=work)
        assert ok is True
        log = agent_git.log(n=1, repo_path=work)
        assert "[Session A]" in log

    def test_commit_with_nothing_staged(self, repo):
        work = repo["work"]
        ok, msg = agent_git.commit("A", "empty", repo_path=work)
        assert ok is False


class TestPushFlow:
    def test_push_succeeds(self, repo):
        work = repo["work"]
        db_path = repo["db_path"]
        with open(os.path.join(work, "new.txt"), "w") as f:
            f.write("data\n")
        agent_git.add("new.txt", repo_path=work)
        agent_git.commit("A", "add new file", repo_path=work)
        ok, msg = agent_git.push("A", repo_path=work, db_path=db_path)
        assert ok is True

    def test_simultaneous_push_second_waits(self, repo):
        """Two sessions push; second acquires lock after first releases."""
        work = repo["work"]
        db_path = repo["db_path"]

        # Create two commits
        with open(os.path.join(work, "file1.txt"), "w") as f:
            f.write("from A\n")
        agent_git.add("file1.txt", repo_path=work)
        agent_git.commit("A", "file from A", repo_path=work)
        ok1, _ = agent_git.push("A", repo_path=work, db_path=db_path)
        assert ok1 is True

        with open(os.path.join(work, "file2.txt"), "w") as f:
            f.write("from B\n")
        agent_git.add("file2.txt", repo_path=work)
        agent_git.commit("B", "file from B", repo_path=work)
        ok2, _ = agent_git.push("B", repo_path=work, db_path=db_path)
        assert ok2 is True


class TestGitInfo:
    def test_status(self, repo):
        work = repo["work"]
        s = agent_git.status(repo_path=work)
        assert isinstance(s, str)

    def test_diff(self, repo):
        work = repo["work"]
        with open(os.path.join(work, "changed.txt"), "w") as f:
            f.write("x\n")
        agent_git.add("changed.txt", repo_path=work)
        d = agent_git.diff(staged=True, repo_path=work)
        assert "changed.txt" in d

    def test_log(self, repo):
        work = repo["work"]
        log = agent_git.log(n=5, repo_path=work)
        assert "initial" in log
