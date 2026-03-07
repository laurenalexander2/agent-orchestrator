"""Conflict self-healing and arbitration for claude-swarm."""

import os
import re
import subprocess

from claude_swarm.bus import get_claims, send_message


def _run(args: list[str], repo_path: str = ".") -> tuple[int, str]:
    result = subprocess.run(
        ["git"] + args,
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    return result.returncode, (result.stdout + result.stderr).strip()


def self_heal_conflict(session_id: str, conflicted_files: list[str], *, repo_path: str = ".") -> bool:
    """Attempt to auto-resolve conflicts by choosing non-overlapping hunks.

    For each conflicted file, if conflict markers exist, attempts to resolve
    by keeping both sides (useful for non-overlapping changes). If any file
    has genuinely incompatible changes (both sides modify the same lines),
    returns False and aborts the rebase.
    """
    all_resolved = True
    for filepath in conflicted_files:
        full_path = os.path.join(repo_path, filepath)
        if not os.path.exists(full_path):
            all_resolved = False
            continue

        with open(full_path) as f:
            content = f.read()

        if "<<<<<<< " not in content:
            # No conflict markers — already resolved or not conflicted
            continue

        # Try to resolve: check if changes are on different lines
        # by seeing if we can take "theirs" for each conflict marker block
        # Simple heuristic: if ======= divides truly different sections, fail
        resolved = _try_resolve_markers(content)
        if resolved is None:
            all_resolved = False
            continue

        with open(full_path, "w") as f:
            f.write(resolved)
        _run(["add", filepath], repo_path=repo_path)

    if not all_resolved:
        _run(["rebase", "--abort"], repo_path=repo_path)
        return False

    # Continue the rebase
    code, _ = _run(["rebase", "--continue"], repo_path=repo_path)
    return code == 0


def _try_resolve_markers(content: str) -> str | None:
    """Try to resolve conflict markers. Returns resolved content or None if incompatible."""
    pattern = re.compile(
        r"<<<<<<< [^\n]*\n(.*?)=======\n(.*?)>>>>>>> [^\n]*\n",
        re.DOTALL,
    )

    matches = list(pattern.finditer(content))
    if not matches:
        return None

    for match in matches:
        ours = match.group(1).strip()
        theirs = match.group(2).strip()
        # If both sides changed the same content, we can't auto-resolve
        if ours and theirs and ours != theirs:
            # Both sides have content and it differs — genuinely incompatible
            return None

    # All conflicts are resolvable (one side empty or identical)
    def replacer(match):
        ours = match.group(1)
        theirs = match.group(2)
        if ours.strip() and theirs.strip():
            return ours  # identical, keep one
        return ours if ours.strip() else theirs

    return pattern.sub(replacer, content)


def resolve_via_arbitration(session_a: str, session_b: str, conflicted_file: str, *, repo_path: str = ".") -> str:
    """Request arbitration for a conflict that self-heal couldn't resolve.

    Returns the path to the resolved file. In v1, this is a placeholder
    that returns the file with "theirs" resolution.
    """
    full_path = os.path.join(repo_path, conflicted_file)
    # In v1, accept theirs to unblock
    _run(["checkout", "--theirs", conflicted_file], repo_path=repo_path)
    _run(["add", conflicted_file], repo_path=repo_path)
    return full_path


def notify_file_owners(session_id: str, modified_files: list[str], *, db_path: str | None = None) -> None:
    """Notify sessions that own files that were modified by another session."""
    claims = get_claims(db_path=db_path)
    owner_map = {c["file_path"]: c["session_id"] for c in claims}

    for filepath in modified_files:
        owner = owner_map.get(filepath)
        if owner and owner != session_id:
            send_message(
                session_id,
                owner,
                f"File you own was modified: {filepath}",
                type="notification",
                db_path=db_path,
            )
