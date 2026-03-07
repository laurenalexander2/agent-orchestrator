"""Git operations and lock management for agent-orchestrator."""

import subprocess
import time
from datetime import datetime, timezone

from agent_orchestrator.bus import _connect, DEFAULT_DB_PATH


def _run(args: list[str], repo_path: str = ".") -> tuple[int, str]:
    result = subprocess.run(
        ["git"] + args,
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    return result.returncode, output.strip()


# --- Lock ---

def acquire_lock(session_id: str, *, timeout: int = 60, db_path: str | None = None) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        conn = _connect(db_path)
        row = conn.execute("SELECT held_by FROM git_lock WHERE id = 1").fetchone()
        if row["held_by"] is None:
            conn.execute(
                "UPDATE git_lock SET held_by = ?, acquired_at = ? WHERE id = 1",
                (session_id, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            conn.close()
            return True
        conn.close()
        time.sleep(0.2)
    return False


def release_lock(session_id: str, *, db_path: str | None = None) -> None:
    conn = _connect(db_path)
    conn.execute(
        "UPDATE git_lock SET held_by = NULL, acquired_at = NULL WHERE id = 1 AND held_by = ?",
        (session_id,),
    )
    conn.commit()
    conn.close()


def get_lock_holder(*, db_path: str | None = None) -> str | None:
    conn = _connect(db_path)
    row = conn.execute("SELECT held_by FROM git_lock WHERE id = 1").fetchone()
    conn.close()
    return row["held_by"] if row else None


# --- Git operations ---

def add(files: list[str] | str = ".", *, repo_path: str = ".") -> None:
    if isinstance(files, str):
        files = [files]
    _run(["add"] + files, repo_path=repo_path)


def commit(session_id: str, message: str, *, repo_path: str = ".") -> tuple[bool, str]:
    full_message = f"[Session {session_id}] {message}"
    code, output = _run(["commit", "-m", full_message], repo_path=repo_path)
    return code == 0, output


def pull_rebase(*, repo_path: str = ".") -> tuple[bool, str]:
    code, output = _run(["pull", "--rebase"], repo_path=repo_path)
    return code == 0, output


def push(session_id: str, *, repo_path: str = ".", db_path: str | None = None, branch: str | None = None) -> tuple[bool, str]:
    # 1. Pull --rebase
    ok, output = pull_rebase(repo_path=repo_path)
    if not ok and "CONFLICT" in output.upper():
        return False, f"Conflict during rebase: {output}"

    # 2. Acquire lock
    if not acquire_lock(session_id, db_path=db_path):
        return False, "Could not acquire git lock (timeout)"

    try:
        # 3. Push
        push_args = ["push"]
        if branch:
            push_args += ["origin", branch]
        code, push_output = _run(push_args, repo_path=repo_path)
        return code == 0, push_output
    finally:
        # 4. Release lock
        release_lock(session_id, db_path=db_path)


def status(*, repo_path: str = ".") -> str:
    _, output = _run(["status", "--short"], repo_path=repo_path)
    return output


def diff(*, staged: bool = False, repo_path: str = ".") -> str:
    args = ["diff"]
    if staged:
        args.append("--cached")
    _, output = _run(args, repo_path=repo_path)
    return output


def log(*, n: int = 10, repo_path: str = ".") -> str:
    _, output = _run(["log", f"--oneline", f"-{n}"], repo_path=repo_path)
    return output
