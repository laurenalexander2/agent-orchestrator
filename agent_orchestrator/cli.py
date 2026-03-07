"""CLI interface for agent-orchestrator."""

import sys

import click
from rich.console import Console
from rich.table import Table

from agent_orchestrator import bus
from agent_orchestrator import git as agent_git

console = Console()


def _db(ctx):
    return ctx.obj.get("db") if ctx.obj else None


@click.group()
@click.option("--db", default=None, help="Path to bus database", envvar="AGENT_ORCHESTRATOR_DB")
@click.pass_context
def main(ctx, db):
    ctx.ensure_object(dict)
    if db:
        ctx.obj["db"] = db


# --- Init ---

@main.command()
@click.option("--sessions", required=True, help='Space-separated "ID:workstream" pairs')
@click.pass_context
def init(ctx, sessions):
    """Initialize the orchestrator database and register sessions."""
    db_path = _db(ctx)
    bus.init_db(db_path)
    if db_path:
        ctx.obj["db"] = db_path

    for pair in sessions.split():
        parts = pair.split(":", 1)
        if len(parts) != 2:
            console.print(f"[red]Invalid session format: {pair} (expected ID:workstream)[/red]")
            sys.exit(1)
        sid, workstream = parts
        branch = f"feat/{workstream}"
        bus.register_session(sid, workstream, branch, db_path=db_path)

    console.print(f"[green]Initialized with {len(sessions.split())} sessions[/green]")


# --- Status ---

@main.command()
@click.pass_context
def status(ctx):
    """Show all sessions and their status."""
    sessions = bus.get_all_sessions(db_path=_db(ctx))
    table = Table(title="Sessions")
    table.add_column("ID", style="bold")
    table.add_column("Workstream")
    table.add_column("Status")
    table.add_column("Branch")
    table.add_column("Note")
    table.add_column("Updated")

    for s in sessions:
        status_style = {"running": "green", "blocked": "red", "done": "blue"}.get(s["status"], "")
        table.add_row(
            s["id"],
            s["workstream"],
            f"[{status_style}]{s['status']}[/{status_style}]" if status_style else s["status"],
            s["branch"] or "",
            s["note"] or "",
            s["updated_at"] or "",
        )

    console.print(table)


# --- Messages ---

@main.command()
@click.argument("to_id")
@click.argument("body")
@click.option("--from", "from_id", required=True, help="Sender session ID")
@click.pass_context
def message(ctx, to_id, body, from_id):
    """Send a message to another session."""
    msg_id = bus.send_message(from_id, to_id, body, db_path=_db(ctx))
    console.print(f"[green]Message #{msg_id} sent to {to_id}[/green]")


@main.command()
@click.option("--session", required=True, help="Session ID to check inbox for")
@click.pass_context
def inbox(ctx, session):
    """Check unread messages for a session."""
    db_path = _db(ctx)
    messages = bus.get_inbox(session, db_path=db_path)
    pending_reviews = bus.get_pending_reviews(session, db_path=db_path)

    if pending_reviews:
        console.print(f"[bold yellow]You have {len(pending_reviews)} pending review(s) to action[/bold yellow]")
        for r in pending_reviews:
            console.print(f"  Review #{r['id']} from {r['requester']} — run: agent-orchestrator review show {r['id']}")
        console.print()

    if not messages and not pending_reviews:
        console.print("[dim]No unread messages or pending reviews[/dim]")
        return

    if not messages:
        return

    table = Table(title=f"Inbox for {session}")
    table.add_column("ID")
    table.add_column("From")
    table.add_column("Body")
    table.add_column("Type")
    table.add_column("Time")

    for m in messages:
        table.add_row(str(m["id"]), m["from_id"], m["body"], m["type"], m["created_at"])

    console.print(table)


@main.command()
@click.argument("message_id", type=int)
@click.argument("body")
@click.option("--from", "from_id", required=True, help="Sender session ID")
@click.pass_context
def reply(ctx, message_id, body, from_id):
    """Reply to a message (marks original as read and sends reply)."""
    db_path = _db(ctx)
    # Get original message to find sender
    messages = bus.get_inbox(from_id, db_path=db_path)
    # Find the original message by checking all messages
    conn = bus._connect(db_path)
    original = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    conn.close()

    if not original:
        console.print(f"[red]Message #{message_id} not found[/red]")
        sys.exit(1)

    bus.mark_read(message_id, db_path=db_path)
    msg_id = bus.send_message(from_id, original["from_id"], body, db_path=db_path)
    console.print(f"[green]Reply #{msg_id} sent to {original['from_id']}[/green]")


# --- Session Update ---

@main.command()
@click.argument("session_id")
@click.option("--status", "new_status", help="New status")
@click.option("--note", help="Status note")
@click.pass_context
def update(ctx, session_id, new_status, note):
    """Update a session's status or note."""
    bus.update_session(session_id, status=new_status, note=note, db_path=_db(ctx))
    console.print(f"[green]Session {session_id} updated[/green]")


# --- Reviews ---

@main.group()
@click.pass_context
def review(ctx):
    """Review management commands."""
    pass


@review.command()
@click.option("--from", "from_id", required=True, help="Requester session ID")
@click.option("--to", "to_id", required=True, help="Reviewer session ID")
@click.option("--diff", "diff_text", required=True, help="Diff content")
@click.pass_context
def request(ctx, from_id, to_id, diff_text):
    """Request a review from another session."""
    review_id = bus.create_review(from_id, to_id, diff_text, db_path=_db(ctx.parent))
    console.print(f"[green]Review #{review_id} requested from {to_id}[/green]")


@review.command(name="list")
@click.pass_context
def list_reviews(ctx):
    """List all reviews."""
    db_path = _db(ctx.parent)
    conn = bus._connect(db_path)
    rows = conn.execute("SELECT * FROM reviews ORDER BY id").fetchall()
    conn.close()

    table = Table(title="Reviews")
    table.add_column("ID")
    table.add_column("Requester")
    table.add_column("Reviewer")
    table.add_column("Status")
    table.add_column("Comments")
    table.add_column("Created")

    for r in rows:
        table.add_row(
            str(r["id"]), r["requester"], r["reviewer"],
            r["status"], r["comments"] or "", r["created_at"],
        )

    console.print(table)


@review.command()
@click.argument("review_id", type=int)
@click.pass_context
def show(ctx, review_id):
    """Show details of a specific review."""
    db_path = _db(ctx.parent)
    conn = bus._connect(db_path)
    r = conn.execute("SELECT * FROM reviews WHERE id = ?", (review_id,)).fetchone()
    conn.close()

    if not r:
        console.print(f"[red]Review #{review_id} not found[/red]")
        sys.exit(1)

    console.print(f"[bold]Review #{r['id']}[/bold]")
    console.print(f"Requester: {r['requester']}")
    console.print(f"Reviewer:  {r['reviewer']}")
    console.print(f"Status:    {r['status']}")
    console.print(f"Comments:  {r['comments'] or 'none'}")
    console.print(f"Diff:\n{r['diff']}")


@review.command()
@click.argument("review_id", type=int)
@click.option("--from", "from_id", required=True, help="Reviewer session ID")
@click.option("--comment", default="", help="Approval comment")
@click.pass_context
def approve(ctx, review_id, from_id, comment):
    """Approve a review."""
    bus.resolve_review(review_id, "approved", comments=comment, db_path=_db(ctx.parent))
    console.print(f"[green]Review #{review_id} approved[/green]")


@review.command()
@click.argument("review_id", type=int)
@click.option("--from", "from_id", required=True, help="Reviewer session ID")
@click.option("--comment", default="", help="Rejection comment")
@click.pass_context
def reject(ctx, review_id, from_id, comment):
    """Reject a review."""
    bus.resolve_review(review_id, "rejected", comments=comment, db_path=_db(ctx.parent))
    console.print(f"[yellow]Review #{review_id} rejected — requester must address and re-request[/yellow]")


# --- Merge OK ---

@main.command("merge-ok")
@click.argument("session_id")
@click.pass_context
def merge_ok(ctx, session_id):
    """Check if a session is clear to merge (all reviews approved)."""
    if bus.can_merge(session_id, db_path=_db(ctx)):
        console.print(f"[green]{session_id} is clear to merge[/green]")
        sys.exit(0)
    else:
        console.print(f"[red]{session_id} has pending reviews — cannot merge[/red]")
        sys.exit(1)


# --- File Claims ---

@main.command()
@click.argument("file_path")
@click.option("--session", required=True, help="Session ID")
@click.pass_context
def claim(ctx, file_path, session):
    """Claim a file for exclusive editing."""
    if bus.claim_file(session, file_path, db_path=_db(ctx)):
        console.print(f"[green]{session} claimed {file_path}[/green]")
    else:
        console.print(f"[red]{file_path} is already claimed by another session[/red]")
        sys.exit(1)


@main.command()
@click.pass_context
def claims(ctx):
    """List all file claims."""
    all_claims = bus.get_claims(db_path=_db(ctx))
    if not all_claims:
        console.print("[dim]No active claims[/dim]")
        return

    table = Table(title="File Claims")
    table.add_column("File")
    table.add_column("Session")
    table.add_column("Claimed At")

    for c in all_claims:
        table.add_row(c["file_path"], c["session_id"], c["claimed_at"])

    console.print(table)


@main.command()
@click.argument("file_path")
@click.option("--session", required=True, help="Session ID")
@click.pass_context
def unclaim(ctx, file_path, session):
    """Release a file claim."""
    bus.release_claim(session, file_path, db_path=_db(ctx))
    console.print(f"[green]{session} released {file_path}[/green]")


# --- Git Operations ---

@main.command("commit")
@click.argument("description")
@click.option("--session", required=True, help="Session ID")
@click.pass_context
def git_commit(ctx, description, session):
    """Commit staged changes with session-prefixed message."""
    ok, output = agent_git.commit(session, description)
    if ok:
        console.print(f"[green]Committed: [Session {session}] {description}[/green]")
    else:
        console.print(f"[red]Commit failed: {output}[/red]")
        sys.exit(1)


@main.command("push")
@click.option("--session", required=True, help="Session ID")
@click.pass_context
def git_push(ctx, session):
    """Push changes (acquires lock, rebases, pushes, releases)."""
    ok, output = agent_git.push(session, db_path=_db(ctx))
    if ok:
        console.print(f"[green]Push successful[/green]")
    else:
        console.print(f"[red]Push failed: {output}[/red]")
        bus.update_session(session, status="blocked", note="push failed", db_path=_db(ctx))
        sys.exit(1)


@main.command("pull")
@click.option("--session", required=True, help="Session ID")
@click.pass_context
def git_pull(ctx, session):
    """Pull and rebase."""
    ok, output = agent_git.pull_rebase()
    if ok:
        console.print(f"[green]Pull successful[/green]")
    else:
        console.print(f"[red]Pull failed: {output}[/red]")
        sys.exit(1)


@main.command("git-status")
@click.pass_context
def git_status(ctx):
    """Show git status."""
    console.print(agent_git.status())


@main.command("git-diff")
@click.option("--staged", is_flag=True, help="Show staged changes")
@click.pass_context
def git_diff(ctx, staged):
    """Show git diff."""
    console.print(agent_git.diff(staged=staged))


@main.command("git-log")
@click.pass_context
def git_log(ctx):
    """Show recent git log."""
    console.print(agent_git.log())


if __name__ == "__main__":
    main()
