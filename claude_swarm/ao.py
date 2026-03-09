"""ao — the quick-start entry point for claude-swarm.

Usage:
    ao setup                            # check that claude CLI is installed
    ao start "Build a REST API"         # launch orchestrator mode in Claude Code
"""

import os
import shutil
import subprocess
import sys

import click
from rich.console import Console

console = Console()

CLAUDE_MD_SECTION = """
## Claude Swarm

This project uses [claude-swarm](https://github.com/laurenalexander2/agent-orchestrator) to coordinate parallel Claude Code sessions.

### Database

All coordination state is stored in `.claude-swarm/bus.db`. Every command below uses `--db` to point at it.

```
export CLAUDE_SWARM_DB=".claude-swarm/bus.db"
```

### Commands

**Sync (do this between every task — checks inbox, reads new context, sends heartbeat):**
```
claude-swarm --db .claude-swarm/bus.db sync --session {YOUR_SESSION_ID}
```

**Add shared context (share decisions, interfaces, warnings with all sessions):**
```
claude-swarm --db .claude-swarm/bus.db context add "description" --session {YOUR_SESSION_ID} --category decision
```
Categories: decision, interface, warning, convention, discovery

**View all shared context:**
```
claude-swarm --db .claude-swarm/bus.db context show
```

**Report what you're working on:**
```
claude-swarm --db .claude-swarm/bus.db update {YOUR_SESSION_ID} --status running --note "what you're doing"
```

**Report blocked:**
```
claude-swarm --db .claude-swarm/bus.db update {YOUR_SESSION_ID} --status blocked --note "why you're blocked"
```

**Message another session:**
```
claude-swarm --db .claude-swarm/bus.db message {TARGET_SESSION} "your question" --from {YOUR_SESSION_ID}
```

**Message the orchestrator:**
```
claude-swarm --db .claude-swarm/bus.db message orchestrator "your question" --from {YOUR_SESSION_ID}
```

**Claim a file before editing it:**
```
claude-swarm --db .claude-swarm/bus.db claim path/to/file --session {YOUR_SESSION_ID}
```

**Release a file claim:**
```
claude-swarm --db .claude-swarm/bus.db unclaim path/to/file --session {YOUR_SESSION_ID}
```

**Request a review:**
```
claude-swarm --db .claude-swarm/bus.db review request --from {YOUR_SESSION_ID} --to orchestrator --diff "$(git diff main)"
```

**Check if you're clear to merge:**
```
claude-swarm --db .claude-swarm/bus.db merge-ok {YOUR_SESSION_ID}
```

**Commit (auto-prefixes with session ID):**
```
claude-swarm --db .claude-swarm/bus.db commit "description" --session {YOUR_SESSION_ID}
```

**Push (acquires lock, rebases, pushes, releases):**
```
claude-swarm --db .claude-swarm/bus.db push --session {YOUR_SESSION_ID}
```

**Pull before starting work:**
```
claude-swarm --db .claude-swarm/bus.db pull --session {YOUR_SESSION_ID}
```

**Check orchestrator dashboard:**
```
claude-swarm --db .claude-swarm/bus.db orchestrate dashboard
```

### Workflow

1. Pull before starting
2. Sync between every task (replaces manual inbox check)
3. Share decisions and interfaces to shared context
4. Claim files before editing
5. Update status when starting, blocking, or completing
6. Commit often
7. Request review when ready
8. Wait for merge-ok before pushing
9. Push only via the CLI (never raw git push)
10. Never force push
"""

SESSION_PROMPT_TEMPLATE = """You are session "{session_id}" working on: {workstream}

This project uses claude-swarm for coordination. Read the CLAUDE.md in this directory for the full protocol.

Your task:
{task}

Your session ID is: {session_id}
The database is at: .claude-swarm/bus.db

Commands you must use (your session ID is already filled in):

  Sync (do this between every task — checks inbox, reads new context, sends heartbeat):
    claude-swarm --db .claude-swarm/bus.db sync --session {session_id}

  Add shared context (share decisions/interfaces/warnings with all sessions):
    claude-swarm --db .claude-swarm/bus.db context add "description" --session {session_id} --category decision
    Categories: decision, interface, warning, convention, discovery

  View all shared context:
    claude-swarm --db .claude-swarm/bus.db context show

  Update your status:
    claude-swarm --db .claude-swarm/bus.db update {session_id} --status running --note "what you're doing"

  Report blocked:
    claude-swarm --db .claude-swarm/bus.db update {session_id} --status blocked --note "why"

  Claim a file before editing:
    claude-swarm --db .claude-swarm/bus.db claim path/to/file --session {session_id}

  Release a file claim:
    claude-swarm --db .claude-swarm/bus.db unclaim path/to/file --session {session_id}

  Message another session:
    claude-swarm --db .claude-swarm/bus.db message TARGET "message" --from {session_id}

  Message the orchestrator:
    claude-swarm --db .claude-swarm/bus.db message orchestrator "message" --from {session_id}

  Commit:
    claude-swarm --db .claude-swarm/bus.db commit "description" --session {session_id}

  Request review:
    claude-swarm --db .claude-swarm/bus.db review request --from {session_id} --to orchestrator --diff "$(git diff main)"

  Check merge status:
    claude-swarm --db .claude-swarm/bus.db merge-ok {session_id}

  Push (acquires lock, rebases, pushes):
    claude-swarm --db .claude-swarm/bus.db push --session {session_id}

  Pull before starting:
    claude-swarm --db .claude-swarm/bus.db pull --session {session_id}

Workflow:
1. Pull before starting
2. Update status to running
3. Sync between every task (this checks inbox, reads shared context, and sends heartbeat)
4. Share decisions/interfaces/warnings to shared context so other sessions stay informed
5. Claim files before editing
6. Commit often
7. Request review when ready
8. Wait for merge-ok before pushing
9. Push only via claude-swarm (never raw git push)
10. Never force push
"""

ORCHESTRATOR_PROMPT_TEMPLATE = """You are the orchestrator for a multi-session project.

The user wants to build:
{description}

You have claude-swarm installed. Read the CLAUDE.md in this directory for the full command reference.

Your job has two phases: PLAN first, then EXECUTE.

## Phase 1: Plan

1. Explore the codebase and analyze what exists vs. what needs to be built.
2. Decompose the project into parallel workstreams. For each one, define:
   - Session ID and name (e.g., A:backend, B:frontend)
   - Specific task: what this session will build
   - Files owned: which files/directories this session will create or modify
   - Dependencies: what it needs from other sessions before it can start or finish
   - Output: what it produces that other sessions need (types, APIs, configs)
3. Present the plan to the user. Show all workstreams, their tasks, file ownership, dependencies, and suggested order of operations.
4. Ask the user to approve, or tell you what to change.
5. If the user requests changes, revise and present again.
6. Do NOT proceed to Phase 2 until the user approves the plan.

## Phase 2: Execute

1. Run the init command to register all sessions:
   claude-swarm --db .claude-swarm/bus.db init --sessions "orchestrator:orchestrator A:workstream-name B:workstream-name ..."
2. For each workstream, print a clearly labeled, copy-pasteable prompt block that the user will paste into a new Claude Code terminal. Each prompt block must:
   - Be enclosed in a visible border (use ``` or a clear delimiter like ═══)
   - Have a header like "SESSION A — Frontend" so the user knows which terminal it's for
   - Include the session ID, workstream description, and specific task instructions
   - Include ALL coordination commands with the session ID already filled in (sync, context add, context show, update, claim, unclaim, message, commit, review, merge-ok, push, pull)
   - Emphasize that the session must run `sync` between every task — this checks inbox, reads shared context, and sends a heartbeat all in one command
   - Tell the session to write to shared context whenever it makes a decision that affects other sessions (API formats, interfaces, conventions, warnings)
   - Be completely self-contained — the session should not need any other context
3. After printing all prompts, tell the user: "Open a new Claude Code terminal for each session above. Paste the prompt to start it. Then come back here and press Enter to start the orchestrator monitor."
4. Wait for the user to confirm, then run:
   claude-swarm --db .claude-swarm/bus.db orchestrate run --interval 10
5. Monitor the dashboard and respond to messages, reviews, and blocked sessions

Important:
- You are session "orchestrator"
- ALWAYS complete Phase 1 (plan + user approval) before Phase 2
- Do NOT use the Task tool to spawn sub-agents. Each session runs in its own terminal.
- Print prompts the user can copy-paste — that's how sessions get started.
- Each prompt must be ready to paste directly into `claude` in a new terminal
- Sessions use `sync` instead of manually checking inbox — it's one command that does inbox + shared context + heartbeat
- Use --auto-approve on the orchestrate run command if you want to auto-approve reviews
"""


def _check_claude() -> bool:
    return shutil.which("claude") is not None


def _write_claude_md(project_dir: str) -> str:
    """Write or append the orchestrator section to CLAUDE.md."""
    claude_md_path = os.path.join(project_dir, "CLAUDE.md")

    if os.path.exists(claude_md_path):
        with open(claude_md_path) as f:
            existing = f.read()
        if "Claude Swarm" in existing:
            return claude_md_path
        with open(claude_md_path, "a") as f:
            f.write("\n" + CLAUDE_MD_SECTION)
    else:
        with open(claude_md_path, "w") as f:
            f.write(CLAUDE_MD_SECTION.lstrip())

    return claude_md_path


def _ensure_ao_dir(project_dir: str) -> str:
    ao_dir = os.path.join(project_dir, ".claude-swarm")
    os.makedirs(ao_dir, exist_ok=True)
    return ao_dir


@click.group()
def main():
    """ao — orchestrate parallel Claude Code sessions.

    Commands:
        ao setup              Check that Claude Code is installed
        ao start "Build X"    Launch orchestrator mode
    """
    pass


@main.command()
def setup():
    """Check that Claude Code CLI is installed."""
    if _check_claude():
        console.print("[green]Claude Code CLI found[/green]")
        console.print("[green]You're all set![/green]")
        console.print("\nTry:")
        console.print('  ao start "Build a REST API with auth and rate limiting"')
    else:
        console.print("[red]Claude Code CLI not found.[/red]")
        console.print("\nInstall Claude Code:")
        console.print("  https://docs.anthropic.com/en/docs/claude-code")
        sys.exit(1)


@main.command()
@click.argument("description")
@click.option("--project-dir", default=".", help="Project directory")
def start(description, project_dir):
    """Launch orchestrator mode in Claude Code.

    Example:
        ao start "Build a REST API with auth and rate limiting"
    """
    project_dir = os.path.abspath(project_dir)

    if not _check_claude():
        console.print("[red]Claude Code CLI not found.[/red]")
        console.print("Install it: https://docs.anthropic.com/en/docs/claude-code")
        sys.exit(1)

    _ensure_ao_dir(project_dir)
    _write_claude_md(project_dir)

    console.print("[green]Wrote CLAUDE.md with orchestrator protocol[/green]")
    console.print("[green]Created .claude-swarm/ directory[/green]")
    console.print("[bold]Launching Claude Code...[/bold]\n")

    prompt = ORCHESTRATOR_PROMPT_TEMPLATE.format(description=description)

    subprocess.run(
        ["claude", prompt],
        cwd=project_dir,
    )
