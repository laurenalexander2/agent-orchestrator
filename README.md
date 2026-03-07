# claude-swarm

A local CLI for parallel Claude Code sessions to coordinate with each other. Sessions share context, sync automatically, claim files, request peer reviews, and safely push through a shared git lock — all backed by a lightweight SQLite bus.

## Why

When you run multiple Claude Code sessions on the same codebase (e.g. one on the frontend, one on the backend, one on infra), they need a way to:

- **Stay in sync** — shared context (decisions, interfaces, warnings) that all sessions can read and write
- **Communicate** — ask questions, share decisions, report blockers
- **Avoid conflicts** — claim files before editing, enforce exclusive ownership
- **Review each other** — gate merges behind peer approval
- **Push safely** — serialize pushes through a lock so rebases don't collide

claude-swarm handles all of this with a single SQLite database and a CLI that sessions call between tasks.

## Install

```bash
pip install claude-swarm
```

Requires Python 3.11+.

## Quick Start

The fastest way to get going:

```bash
# 1. Check that Claude Code is installed
ao setup

# 2. Launch orchestrator mode — it plans, prints prompts, and monitors
ao start "Build a REST API with auth and rate limiting"
```

This will:
- Write a `CLAUDE.md` with the full coordination protocol
- Create `.claude-swarm/` directory
- Launch Claude Code in orchestrator mode
- The orchestrator decomposes your project into workstreams and prints copy-pasteable prompts for each session
- You paste each prompt into a new Claude Code terminal
- The orchestrator monitors all sessions, handling messages, reviews, and blocked sessions

### Manual Setup

If you prefer to set things up yourself:

```bash
# Initialize with your sessions
claude-swarm init --sessions "A:frontend B:backend C:infra"

# Check status
claude-swarm status
```

```
                          Sessions
┏━━━━┳━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━┓
┃ ID ┃ Workstream ┃ Status  ┃ Branch          ┃ Note ┃
┡━━━━╇━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━┩
│ A  │ frontend   │ running │ feat/frontend   │      │
│ B  │ backend    │ running │ feat/backend    │      │
│ C  │ infra      │ running │ feat/infra      │      │
└────┴────────────┴─────────┴─────────────────┴──────┘
```

## Commands

### Sync (the most important command)

Every session should run this between every task. It does three things in one call:
1. Checks inbox (returns new messages, marks them read)
2. Reads shared context (only entries since last sync)
3. Sends a heartbeat (so the orchestrator knows the session is alive)

```bash
claude-swarm sync --session A
```

When nothing is new, it outputs nothing. When there IS something new:

```
[SYNC] 1 new message(s), 2 new context
MSG from=B: "Auth types are exported now"
CTX [decision] by=B: "JWT for auth, not session cookies"
CTX [interface] by=C: "POST /users returns {id, email, created_at}"
```

### Shared Context

A team wiki that all sessions read and write. Use this to share decisions, interfaces, warnings, conventions, and discoveries.

```bash
# Add a decision
claude-swarm context add "API uses snake_case for all fields" --session A --category decision

# Add an interface definition
claude-swarm context add "POST /users returns {id, email, created_at}" --session B --category interface

# Add a warning
claude-swarm context add "Don't touch src/db.py — migration in progress" --session C --category warning

# View all shared context
claude-swarm context show
```

Categories: `decision`, `interface`, `warning`, `convention`, `discovery`

### Messaging

```bash
# Send a message
claude-swarm message B "Can you expose the auth types?" --from A

# Check inbox (sync does this automatically, but you can also check manually)
claude-swarm inbox --session B

# Reply (marks original as read)
claude-swarm reply 1 "Done, exported from auth/types.ts" --from B
```

### Session Updates

```bash
claude-swarm update A --status blocked --note "waiting on B for types"
claude-swarm update A --status running --note "implementing rate limiter"
```

### File Claims

```bash
# Claim a file (prevents other sessions from claiming it)
claude-swarm claim src/auth/middleware.py --session A

# List all claims
claude-swarm claims

# Release
claude-swarm unclaim src/auth/middleware.py --session A
```

### Peer Review

```bash
# Request review
claude-swarm review request --from A --to B --diff "$(git diff main)"

# List pending reviews
claude-swarm review list

# Show review details
claude-swarm review show 1

# Approve or reject
claude-swarm review approve 1 --from B --comment "lgtm"
claude-swarm review reject 1 --from B --comment "fix the error types first"

# Check merge eligibility (exit code 0 = clear, 1 = blocked)
claude-swarm merge-ok A
```

### Git Operations

```bash
# Commit (auto-prefixes message with session ID)
claude-swarm commit "add PII detection middleware" --session A
# -> [Session A] add PII detection middleware

# Push (acquires lock -> rebase -> push -> release lock)
claude-swarm push --session A

# Pull
claude-swarm pull --session A

# Info
claude-swarm git-status
claude-swarm git-diff --staged
claude-swarm git-log
```

### Orchestrator Mode

```bash
# Start the poll loop (monitors all sessions)
claude-swarm orchestrate run --interval 10 --auto-approve

# One-shot dashboard
claude-swarm orchestrate dashboard
```

The orchestrator detects blocked sessions, stale sessions, pending reviews, and incoming messages.

## How It Works

```
ao start "Build X"
     |
     v
Claude Code (orchestrator terminal)
     |
     ├── Plans workstreams
     ├── Runs: claude-swarm init --sessions "..."
     ├── Prints copy-paste prompts for each session
     └── Starts: claude-swarm orchestrate run
           |
           v
     Monitors all sessions via SQLite bus
           |
     ┌─────┴─────┐
     v            v
Session A      Session B      (each in its own terminal)
     |            |
     ├── sync     ├── sync     (checks inbox + context + heartbeat)
     ├── claim    ├── claim    (exclusive file access)
     ├── context  ├── context  (share decisions)
     ├── commit   ├── commit   (session-prefixed)
     ├── review   ├── review   (peer approval)
     └── push     └── push     (lock-serialized)
```

## Architecture

```
claude-swarm/
├── claude_swarm/
│   ├── bus.py          # SQLite message bus (sessions, messages, reviews, claims, context)
│   ├── git.py          # Git operations + push lock
│   ├── merge.py        # Conflict self-heal + file owner notifications
│   ├── orchestrator.py # Poll loop, blocked/stale detection, auto-approve
│   ├── cli.py          # Click CLI commands
│   └── ao.py           # Quick-start entry point (ao setup, ao start)
├── tests/              # 90 tests
└── pyproject.toml
```

### Database Schema (6 tables)

| Table | Purpose |
|-------|---------|
| `sessions` | Registered sessions with status, workstream, and sync tracking |
| `messages` | Inter-session messages with read/unread tracking |
| `reviews` | Peer review requests with approval/rejection flow |
| `git_lock` | Single-row lock for serializing git pushes |
| `file_claims` | Exclusive file ownership per session |
| `shared_context` | Team-wide decisions, interfaces, warnings, conventions |

## Testing

```bash
pytest tests/ -v
```

90 tests covering:
- Shared context CRUD and category validation
- Sync (inbox + context + heartbeat in one call)
- Message exchange and inbox filtering
- Review gating (blocks merge until approved, rejection resets to pending)
- Review notifications (reviewer gets request, requester gets approval/rejection)
- File claim conflicts and release semantics
- Git lock acquire/release and concurrent push safety
- Commit message prefixing with session ID
- Conflict self-healing for non-overlapping changes
- Orchestrator poll loop (blocked/stale detection, auto-approve)
- CLI integration flows
- ao setup and start commands
- Template generation (CLAUDE.md, session prompts, orchestrator prompt)

## License

MIT
