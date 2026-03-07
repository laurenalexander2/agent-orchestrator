"""ao — the quick-start entry point for agent-orchestrator.

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
## Agent Orchestrator

This project uses [agent-orchestrator](https://github.com/laurenalexander2/agent-orchestrator) to coordinate parallel Claude Code sessions.

### Database

All coordination state is stored in `.agent-orchestrator/bus.db`. Every command below uses `--db` to point at it.

```
export AGENT_ORCHESTRATOR_DB=".agent-orchestrator/bus.db"
```

### Commands

**Check inbox (do this between every task):**
```
agent-orchestrator --db .agent-orchestrator/bus.db inbox --session {YOUR_SESSION_ID}
```

**Report what you're working on:**
```
agent-orchestrator --db .agent-orchestrator/bus.db update {YOUR_SESSION_ID} --status running --note "what you're doing"
```

**Report blocked:**
```
agent-orchestrator --db .agent-orchestrator/bus.db update {YOUR_SESSION_ID} --status blocked --note "why you're blocked"
```

**Message another session:**
```
agent-orchestrator --db .agent-orchestrator/bus.db message {TARGET_SESSION} "your question" --from {YOUR_SESSION_ID}
```

**Message the orchestrator:**
```
agent-orchestrator --db .agent-orchestrator/bus.db message orchestrator "your question" --from {YOUR_SESSION_ID}
```

**Claim a file before editing it:**
```
agent-orchestrator --db .agent-orchestrator/bus.db claim path/to/file --session {YOUR_SESSION_ID}
```

**Release a file claim:**
```
agent-orchestrator --db .agent-orchestrator/bus.db unclaim path/to/file --session {YOUR_SESSION_ID}
```

**Request a review:**
```
agent-orchestrator --db .agent-orchestrator/bus.db review request --from {YOUR_SESSION_ID} --to orchestrator --diff "$(git diff main)"
```

**Check if you're clear to merge:**
```
agent-orchestrator --db .agent-orchestrator/bus.db merge-ok {YOUR_SESSION_ID}
```

**Commit (auto-prefixes with session ID):**
```
agent-orchestrator --db .agent-orchestrator/bus.db commit "description" --session {YOUR_SESSION_ID}
```

**Push (acquires lock, rebases, pushes, releases):**
```
agent-orchestrator --db .agent-orchestrator/bus.db push --session {YOUR_SESSION_ID}
```

**Pull before starting work:**
```
agent-orchestrator --db .agent-orchestrator/bus.db pull --session {YOUR_SESSION_ID}
```

**Check orchestrator dashboard:**
```
agent-orchestrator --db .agent-orchestrator/bus.db orchestrate dashboard
```

### Workflow

1. Pull before starting
2. Check inbox between every task
3. Claim files before editing
4. Update status when starting, blocking, or completing
5. Commit often
6. Request review when ready
7. Wait for merge-ok before pushing
8. Push only via the CLI (never raw git push)
9. Never force push
"""

ORCHESTRATOR_PROMPT_TEMPLATE = """You are the orchestrator for a multi-session project.

The user wants to build:
{description}

You have agent-orchestrator installed. Read the CLAUDE.md in this directory for the full command reference.

Your job:
1. Enter plan mode and decompose this into workstreams (e.g., A:frontend, B:backend, C:infra)
2. Run: agent-orchestrator --db .agent-orchestrator/bus.db init --sessions "orchestrator:orchestrator A:workstream-name B:workstream-name ..."
3. For each workstream, use the Task tool to spawn a sub-agent. In each sub-agent's prompt:
   - Tell it its session ID and workstream
   - Tell it to read CLAUDE.md for the coordination protocol
   - Give it specific instructions for what to build
   - Tell it to replace {{YOUR_SESSION_ID}} with its actual session ID in all commands
4. After spawning agents, run: agent-orchestrator --db .agent-orchestrator/bus.db orchestrate run --interval 10
5. Monitor the dashboard and respond to messages, reviews, and blocked sessions

Important:
- You are session "orchestrator"
- Each sub-agent should check inbox and update status between tasks
- Sub-agents should claim files before editing and request reviews before pushing
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
        if "Agent Orchestrator" in existing:
            return claude_md_path
        with open(claude_md_path, "a") as f:
            f.write("\n" + CLAUDE_MD_SECTION)
    else:
        with open(claude_md_path, "w") as f:
            f.write(CLAUDE_MD_SECTION.lstrip())

    return claude_md_path


def _ensure_ao_dir(project_dir: str) -> str:
    ao_dir = os.path.join(project_dir, ".agent-orchestrator")
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
    console.print("[green]Created .agent-orchestrator/[/green]")
    console.print("[bold]Launching Claude Code...[/bold]\n")

    prompt = ORCHESTRATOR_PROMPT_TEMPLATE.format(description=description)

    subprocess.run(
        ["claude", prompt],
        cwd=project_dir,
    )
