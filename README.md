# agent-orchestrator

A local CLI for parallel Claude Code sessions to coordinate with each other. Sessions can message, claim files, request peer reviews, and safely push through a shared git lock — all backed by a lightweight SQLite bus.

## Why

When you run multiple Claude Code sessions on the same codebase (e.g. one on the Python SDK, one on the TypeScript SDK, one on docs), they need a way to:

- **Communicate** — ask questions, share decisions, report blockers
- **Avoid conflicts** — claim files before editing, enforce exclusive ownership
- **Review each other** — gate merges behind peer approval
- **Push safely** — serialize pushes through a lock so rebases don't collide

agent-orchestrator handles all of this with a single SQLite database and a CLI that sessions call between tasks.

## Install

```bash
pip install -e .
```

Requires Python 3.11+.

## Quick Start

```bash
# Initialize with your sessions
agent-orchestrator init --sessions "A:python-sdk B:ts-sdk C:website"

# Check status
agent-orchestrator status
```

```
                          Sessions
┏━━━━┳━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━┓
┃ ID ┃ Workstream ┃ Status  ┃ Branch          ┃ Note ┃
┡━━━━╇━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━┩
│ A  │ python-sdk │ running │ feat/python-sdk │      │
│ B  │ ts-sdk     │ running │ feat/ts-sdk     │      │
│ C  │ website    │ running │ feat/website    │      │
└────┴────────────┴─────────┴─────────────────┴──────┘
```

## Commands

### Messaging

```bash
# Send a message
agent-orchestrator message B "Can you expose the auth types?" --from A

# Check inbox
agent-orchestrator inbox --session B

# Reply (marks original as read)
agent-orchestrator reply 1 "Done, exported from auth/types.ts" --from B
```

### Session Updates

```bash
agent-orchestrator update A --status blocked --note "waiting on B for types"
agent-orchestrator update A --status running --note "implementing rate limiter"
```

### File Claims

```bash
# Claim a file (prevents other sessions from claiming it)
agent-orchestrator claim src/auth/middleware.py --session A

# List all claims
agent-orchestrator claims

# Release
agent-orchestrator unclaim src/auth/middleware.py --session A
```

### Peer Review

```bash
# Request review
agent-orchestrator review request --from A --to B --diff "$(git diff main)"

# List pending reviews
agent-orchestrator review list

# Show review details
agent-orchestrator review show 1

# Approve or reject
agent-orchestrator review approve 1 --from B --comment "lgtm"
agent-orchestrator review reject 1 --from B --comment "fix the error types first"

# Check merge eligibility (exit code 0 = clear, 1 = blocked)
agent-orchestrator merge-ok A
```

### Git Operations

```bash
# Commit (auto-prefixes message with session ID)
agent-orchestrator commit "add PII detection middleware" --session A
# → [Session A] add PII detection middleware

# Push (acquires lock → rebase → push → release lock)
agent-orchestrator push --session A

# Pull
agent-orchestrator pull --session A

# Info
agent-orchestrator git-status
agent-orchestrator git-diff --staged
agent-orchestrator git-log
```

## Session Coordination Protocol

Inject this into each Claude Code session's system prompt:

```
COORDINATION RULES — follow between every task:

1. Check inbox: agent-orchestrator inbox --session {ID}
2. Report status: agent-orchestrator update {ID} --status running --note "what you're doing"
3. Claim files before touching them: agent-orchestrator claim path/to/file.py --session {ID}
4. Message another session: agent-orchestrator message {TARGET} "question" --from {ID}
5. When ready for review: agent-orchestrator review request --from {ID} --to {REVIEWER} --diff "$(git diff main)"
6. Before pushing: agent-orchestrator merge-ok {ID}
7. Push only via CLI: agent-orchestrator push --session {ID}
8. When complete: agent-orchestrator unclaim --session {ID} --all

GIT RULES:
1. Pull before starting: agent-orchestrator pull --session {ID}
2. Commit often: agent-orchestrator commit "description" --session {ID}
3. Push only via CLI (never raw git push)
4. If push fails with conflict: update status to blocked, message orchestrator, STOP
5. Never force push. Never.

Your session ID: {ID}
Your workstream: {WORKSTREAM}
Your branch: {BRANCH}
```

## Architecture

```
agent-orchestrator/
├── agent_orchestrator/
│   ├── bus.py       # SQLite message bus (sessions, messages, reviews, claims)
│   ├── git.py       # Git operations + push lock
│   ├── merge.py     # Conflict self-heal + file owner notifications
│   └── cli.py       # Click CLI commands
├── tests/
│   ├── test_bus.py   # 13 tests
│   ├── test_git.py   # 10 tests
│   ├── test_merge.py #  4 tests
│   └── test_cli.py   # 10 tests
└── pyproject.toml
```

### Database Schema (5 tables)

| Table | Purpose |
|-------|---------|
| `sessions` | Registered sessions with status and workstream |
| `messages` | Inter-session messages with read/unread tracking |
| `reviews` | Peer review requests with approval/rejection flow |
| `git_lock` | Single-row lock for serializing git pushes |
| `file_claims` | Exclusive file ownership per session |

## Testing

```bash
pytest tests/ -v
```

37 tests covering:
- Message exchange and inbox filtering
- Review gating (blocks merge until approved, rejection resets to pending)
- File claim conflicts and release semantics
- Git lock acquire/release and concurrent push safety
- Commit message prefixing with session ID
- Conflict self-healing for non-overlapping changes
- File owner notifications
- Full CLI flows (init → status → message → review → merge-ok)

## Roadmap (v2)

- Session spawning (auto-launch Claude Code sessions)
- Supervisor / orchestrator loop
- Deadlock detection
- Pause / resume / kill sessions
- Per-session branches + PR workflow
- Web UI

## License

MIT
