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

## Context window budget (CRITICAL — read first)

Each session runs in its own Claude Code instance with a finite context window.
If you hand a session too much work, it will exhaust its context mid-task and fail.

Hard rules when planning workstreams — these override any other consideration:
- No single session may own more than {max_files_per_session} files. If a workstream
  needs to touch more than that, split it into multiple sessions (e.g. A1, A2)
  with clearly partitioned file ownership.
- Prefer many small, focused sessions over a few large ones.
- Each session's task should be expressible in 5–10 concrete sub-steps.
- If you cannot describe what a session does in two sentences, it is too big — split it.
- Avoid sessions whose task requires reading large swaths of unfamiliar code; scope
  each session to a localized area (one module, one feature, one layer).
- When in doubt, split.

You MUST verify every workstream against these rules before presenting the plan
to the user, and call out the file count for each session in the plan.

## Phase 1: Plan

1. Explore the codebase and analyze what exists vs. what needs to be built.
2. Decompose the project into parallel workstreams. For each one, define:
   - Session ID and name (e.g., A:backend, B:frontend)
   - Specific task: what this session will build (must fit in 5–10 sub-steps)
   - Files owned: which files/directories this session will create or modify
     (MUST be ≤ {max_files_per_session} files — split the workstream if not)
   - Estimated file count (and confirm it is within the budget)
   - Dependencies: what it needs from other sessions before it can start or finish
   - Output: what it produces that other sessions need (types, APIs, configs)
3. Present the plan to the user. Show all workstreams, their tasks, file ownership
   (with file counts), dependencies, and suggested order of operations. Explicitly
   note that every session is within the {max_files_per_session}-file budget.
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


def _stdin_is_pipe() -> bool:
    """True when stdin is connected to a pipe/file rather than a terminal."""
    try:
        return not sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


EDITOR_TEMPLATE = """\
# Write or paste your plan below.
# Lines starting with '#' are ignored.
# Save and quit when done. Leave empty to abort.

"""


def _prompt_via_editor():
    """Open $EDITOR for the user to type/paste a plan. Returns the plan
    string with comment lines stripped, or None if the user aborted or
    left it empty."""
    edited = click.edit(EDITOR_TEMPLATE, extension=".md")
    if edited is None:
        return None
    plan = "\n".join(
        line for line in edited.splitlines()
        if not line.lstrip().startswith("#")
    ).strip()
    return plan or None


@click.group()
def main():
    """ao — orchestrate parallel Claude Code sessions.

    Commands:
        ao setup     Check that Claude Code is installed
        ao start     Launch orchestrator mode (opens editor for your plan)
    """
    pass


@main.command()
def setup():
    """Check that Claude Code CLI is installed."""
    if _check_claude():
        console.print("[green]Claude Code CLI found[/green]")
        console.print("[green]You're all set![/green]")
        console.print("\nTry:")
        console.print("  ao start                  # opens editor for your plan")
        console.print('  ao start "Build a REST API with auth and rate limiting"')
    else:
        console.print("[red]Claude Code CLI not found.[/red]")
        console.print("\nInstall Claude Code:")
        console.print("  https://docs.anthropic.com/en/docs/claude-code")
        sys.exit(1)


@main.command()
@click.argument("description", nargs=-1, required=False)
@click.option("--file", "-f", "file_path", type=click.Path(exists=True, dir_okay=False),
              help="Read the plan from a file (sidesteps shell quoting).")
@click.option("--project-dir", default=".", help="Project directory")
@click.option("--max-files-per-session", default=10, type=int, show_default=True,
              help="Hard cap on the number of files any single session may own. "
                   "Forces the orchestrator to split workstreams that exceed this "
                   "budget so no session's context window risks filling up.")
def start(description, file_path, project_dir, max_files_per_session):
    """Launch orchestrator mode in Claude Code.

    With no arguments, opens your $EDITOR so you can write or paste the
    plan without fighting shell quoting. The plan can also be provided
    in several other ways:

    \b
        ao start                                 # opens $EDITOR (recommended)
        ao start "Build a REST API with auth"   # quoted args (short plans)
        ao start --file plan.md                  # from a file
        pbpaste | ao start                       # from stdin
        ao start < plan.md                       # from stdin redirect
        ao start -                               # explicit stdin

    The editor and --file/stdin paths sidestep shell quoting issues with
    characters like !, ', ", (, ), and $ that zsh/bash may interpret.

    Use --max-files-per-session to tighten or loosen the per-session scope
    budget. Smaller values force the orchestrator to plan more, smaller
    sessions, which protects each session's context window.
    """
    args_text = " ".join(description).strip() if description else ""
    explicit_stdin = args_text == "-"

    if file_path and args_text and not explicit_stdin:
        console.print("[red]Cannot combine --file with a description argument.[/red]")
        sys.exit(1)

    consumed_stdin = False
    description_text = None

    if file_path:
        with open(file_path) as f:
            description_text = f.read().strip()
    elif explicit_stdin:
        description_text = sys.stdin.read().strip()
        consumed_stdin = True
    elif args_text:
        description_text = args_text
    elif _stdin_is_pipe():
        # Piped input with no args: read it automatically.
        description_text = sys.stdin.read().strip()
        consumed_stdin = True
    else:
        # Interactive shell with no plan provided: open $EDITOR.
        console.print("[bold]Opening editor for your plan...[/bold]")
        description_text = _prompt_via_editor()
        if not description_text:
            console.print("[red]Aborted: no plan written.[/red]")
            sys.exit(1)

    if not description_text:
        console.print("[red]Plan is empty.[/red]")
        sys.exit(1)

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

    prompt = ORCHESTRATOR_PROMPT_TEMPLATE.format(
        description=description_text,
        max_files_per_session=max_files_per_session,
    )

    # If we consumed stdin from a pipe/redirect, reopen the controlling
    # terminal so `claude` still has an interactive stdin.
    stdin_for_claude = None
    if consumed_stdin:
        try:
            stdin_for_claude = open("/dev/tty", "r")
        except OSError:
            pass

    subprocess.run(
        ["claude", prompt],
        cwd=project_dir,
        stdin=stdin_for_claude,
    )
