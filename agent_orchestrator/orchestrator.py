"""Orchestrator poll loop and coordination logic for agent-orchestrator."""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

from rich.console import Console
from rich.table import Table

from agent_orchestrator import bus

ORCHESTRATOR_SESSION_ID = "orchestrator"

console = Console()


@dataclass
class PollResult:
    timestamp: str
    inbox_messages: list[dict] = field(default_factory=list)
    sessions: list[dict] = field(default_factory=list)
    blocked_sessions: list[dict] = field(default_factory=list)
    stale_sessions: list[dict] = field(default_factory=list)
    pending_reviews: list[dict] = field(default_factory=list)
    actions_taken: list[str] = field(default_factory=list)
    has_events: bool = False


def poll_tick(
    *,
    auto_approve: bool = False,
    stale_minutes: int = 15,
    db_path: str | None = None,
) -> PollResult:
    """Execute one tick of the orchestrator poll loop."""
    now = datetime.now(timezone.utc)
    result = PollResult(timestamp=now.isoformat())

    # 1. Check orchestrator inbox
    result.inbox_messages = bus.get_inbox(ORCHESTRATOR_SESSION_ID, db_path=db_path)

    # 2. Get all sessions (exclude orchestrator itself)
    all_sessions = bus.get_all_sessions(db_path=db_path)
    result.sessions = [s for s in all_sessions if s["id"] != ORCHESTRATOR_SESSION_ID]

    # 3. Find blocked sessions
    result.blocked_sessions = [s for s in result.sessions if s["status"] == "blocked"]

    # 4. Find stale sessions (running but not updated recently)
    stale_threshold = now - timedelta(minutes=stale_minutes)
    for s in result.sessions:
        if s["status"] not in ("running",):
            continue
        updated = s.get("updated_at")
        if not updated:
            continue
        try:
            updated_dt = datetime.fromisoformat(updated)
            if updated_dt.tzinfo is None:
                updated_dt = updated_dt.replace(tzinfo=timezone.utc)
            if updated_dt < stale_threshold:
                result.stale_sessions.append(s)
        except (ValueError, TypeError):
            continue

    # 5. Check pending reviews assigned to orchestrator
    result.pending_reviews = bus.get_pending_reviews(
        ORCHESTRATOR_SESSION_ID, db_path=db_path
    )

    # 6. Auto-approve orchestrator reviews if enabled
    if auto_approve:
        for review in result.pending_reviews:
            bus.resolve_review(
                review["id"], "approved",
                comments="Auto-approved by orchestrator",
                db_path=db_path,
            )
            result.actions_taken.append(
                f"Auto-approved review #{review['id']} from {review['requester']}"
            )
        if result.actions_taken:
            result.pending_reviews = []

    # Determine if anything needs attention
    result.has_events = bool(
        result.inbox_messages
        or result.blocked_sessions
        or result.stale_sessions
        or result.pending_reviews
        or result.actions_taken
    )

    return result


def render_tick(result: PollResult, *, quiet: bool = False) -> None:
    """Render a poll tick result to the console."""
    ts = result.timestamp[:19].replace("T", " ")

    if not result.has_events:
        if not quiet:
            console.print(f"[dim][{ts}] ── all clear ──[/dim]")
        return

    console.print(f"\n[bold][{ts}] ── Orchestrator Tick ──[/bold]")

    # Session summary line
    parts = []
    for s in result.sessions:
        style = {"running": "green", "blocked": "red", "done": "blue"}.get(s["status"], "")
        if style:
            parts.append(f"[{style}]{s['id']}={s['status']}[/{style}]")
        else:
            parts.append(f"{s['id']}={s['status']}")
    console.print(f"  Sessions: {' '.join(parts)}")

    # Blocked alerts
    for s in result.blocked_sessions:
        note = s.get("note") or "no details"
        console.print(f"  [red]ALERT: {s['id']} blocked — \"{note}\"[/red]")

    # Stale warnings
    for s in result.stale_sessions:
        console.print(f"  [yellow]STALE: {s['id']} has not updated recently[/yellow]")

    # Inbox messages
    for m in result.inbox_messages:
        console.print(f"  [cyan]INBOX: {m['from_id']} says \"{m['body']}\"[/cyan]")

    # Pending reviews
    for r in result.pending_reviews:
        console.print(
            f"  [magenta]REVIEW: #{r['id']} from {r['requester']} pending "
            f"— run: agent-orchestrator review show {r['id']}[/magenta]"
        )

    # Actions taken
    for a in result.actions_taken:
        console.print(f"  [green]ACTION: {a}[/green]")


def run_loop(
    *,
    interval: int = 10,
    auto_approve: bool = False,
    stale_minutes: int = 15,
    quiet: bool = False,
    db_path: str | None = None,
) -> None:
    """Run the orchestrator poll loop until interrupted."""
    console.print(f"[bold]Orchestrator running (poll every {interval}s, Ctrl+C to stop)[/bold]")
    try:
        while True:
            result = poll_tick(
                auto_approve=auto_approve,
                stale_minutes=stale_minutes,
                db_path=db_path,
            )
            render_tick(result, quiet=quiet)
            time.sleep(interval)
    except KeyboardInterrupt:
        console.print("\n[bold]Orchestrator stopped[/bold]")
