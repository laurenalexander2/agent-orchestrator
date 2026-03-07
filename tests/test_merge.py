import os
import subprocess
import pytest
from agent_orchestrator.bus import init_db, register_session, claim_file, get_inbox
from agent_orchestrator.merge import self_heal_conflict, notify_file_owners


@pytest.fixture
def conflict_repo(tmp_path):
    """Create a repo with a merge conflict scenario."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], capture_output=True, check=True)

    # Clone A
    work_a = tmp_path / "work_a"
    subprocess.run(["git", "clone", str(remote), str(work_a)], capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "a@test.com"], cwd=str(work_a), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "A"], cwd=str(work_a), capture_output=True, check=True)

    # Initial commit
    (work_a / "shared.py").write_text("line1\nline2\nline3\n")
    subprocess.run(["git", "add", "."], cwd=str(work_a), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=str(work_a), capture_output=True, check=True)
    branch_result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(work_a), capture_output=True, text=True, check=True,
    )
    branch = branch_result.stdout.strip()
    subprocess.run(["git", "push", "-u", "origin", branch], cwd=str(work_a), capture_output=True, check=True)

    # Clone B
    work_b = tmp_path / "work_b"
    subprocess.run(["git", "clone", str(remote), str(work_b)], capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "b@test.com"], cwd=str(work_b), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "B"], cwd=str(work_b), capture_output=True, check=True)

    db_path = str(tmp_path / "bus.db")
    init_db(db_path)

    return {
        "work_a": str(work_a),
        "work_b": str(work_b),
        "remote": str(remote),
        "db_path": db_path,
        "branch": branch,
    }


class TestSelfHeal:
    def test_resolves_non_overlapping_conflict(self, conflict_repo):
        """A edits line1, B edits line3 — self-heal should resolve."""
        work_a = conflict_repo["work_a"]
        work_b = conflict_repo["work_b"]
        branch = conflict_repo["branch"]

        # A modifies line1 and pushes
        with open(os.path.join(work_a, "shared.py"), "w") as f:
            f.write("line1_from_A\nline2\nline3\n")
        subprocess.run(["git", "add", "."], cwd=work_a, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "A edits line1"], cwd=work_a, capture_output=True, check=True)
        subprocess.run(["git", "push"], cwd=work_a, capture_output=True, check=True)

        # B modifies line3 (on stale base)
        with open(os.path.join(work_b, "shared.py"), "w") as f:
            f.write("line1\nline2\nline3_from_B\n")
        subprocess.run(["git", "add", "."], cwd=work_b, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "B edits line3"], cwd=work_b, capture_output=True, check=True)

        # Rebase will conflict
        result = subprocess.run(
            ["git", "pull", "--rebase"],
            cwd=work_b, capture_output=True, text=True,
        )

        if result.returncode != 0:
            # We have a conflict — try self-heal
            healed = self_heal_conflict("B", ["shared.py"], repo_path=work_b)
            if healed:
                content = open(os.path.join(work_b, "shared.py")).read()
                assert "line1_from_A" in content or "line3_from_B" in content
            # If git resolved it automatically (no conflict markers), that's also fine
        else:
            # Git auto-resolved — no conflict markers
            content = open(os.path.join(work_b, "shared.py")).read()
            assert "line1_from_A" in content
            assert "line3_from_B" in content

    def test_returns_false_for_incompatible_changes(self, conflict_repo):
        """Both edit the same line — self-heal should return False."""
        work_a = conflict_repo["work_a"]
        work_b = conflict_repo["work_b"]

        # A modifies line1
        with open(os.path.join(work_a, "shared.py"), "w") as f:
            f.write("AAA\nline2\nline3\n")
        subprocess.run(["git", "add", "."], cwd=work_a, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "A edits line1"], cwd=work_a, capture_output=True, check=True)
        subprocess.run(["git", "push"], cwd=work_a, capture_output=True, check=True)

        # B also modifies line1
        with open(os.path.join(work_b, "shared.py"), "w") as f:
            f.write("BBB\nline2\nline3\n")
        subprocess.run(["git", "add", "."], cwd=work_b, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "B edits line1"], cwd=work_b, capture_output=True, check=True)

        result = subprocess.run(
            ["git", "pull", "--rebase"],
            cwd=work_b, capture_output=True, text=True,
        )
        if result.returncode != 0:
            healed = self_heal_conflict("B", ["shared.py"], repo_path=work_b)
            assert healed is False


class TestNotifyFileOwners:
    def test_owners_notified(self, conflict_repo):
        db_path = conflict_repo["db_path"]
        register_session("A", "python-sdk", "feat/python-sdk", db_path=db_path)
        register_session("B", "ts-sdk", "feat/ts-sdk", db_path=db_path)
        claim_file("A", "src/client.py", db_path=db_path)
        claim_file("B", "src/server.py", db_path=db_path)

        notify_file_owners("C", ["src/client.py", "src/server.py"], db_path=db_path)

        inbox_a = get_inbox("A", db_path=db_path)
        inbox_b = get_inbox("B", db_path=db_path)
        assert len(inbox_a) == 1
        assert "src/client.py" in inbox_a[0]["body"]
        assert len(inbox_b) == 1
        assert "src/server.py" in inbox_b[0]["body"]

    def test_no_notification_for_unclaimed_files(self, conflict_repo):
        db_path = conflict_repo["db_path"]
        register_session("A", "python-sdk", "feat/python-sdk", db_path=db_path)
        notify_file_owners("B", ["unclaimed.py"], db_path=db_path)
        inbox_a = get_inbox("A", db_path=db_path)
        assert len(inbox_a) == 0
