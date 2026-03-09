# Contributing to claude-swarm

Thanks for your interest in contributing!

## Getting Started

```bash
git clone https://github.com/laurenalexander2/agent-orchestrator.git
cd claude-swarm
pip install -e .
pytest tests/ -v
```

## Development Workflow

1. Fork the repo and create a feature branch
2. Write tests first (TDD) — add them to the appropriate `tests/test_*.py` file
3. Implement the feature
4. Run `pytest tests/ -v` and make sure all tests pass
5. Open a PR

## Code Structure

| Module | Responsibility |
|--------|---------------|
| `bus.py` | All SQLite reads/writes — sessions, messages, reviews, file claims, shared context, sync |
| `git.py` | Git operations + push lock management |
| `merge.py` | Conflict self-healing + file owner notifications |
| `orchestrator.py` | Poll loop, blocked/stale detection, auto-approve |
| `cli.py` | Click CLI commands (thin layer over the other modules) |
| `ao.py` | Quick-start entry point (`ao setup`, `ao start`) |

If you're adding a new feature, it probably belongs in `bus.py` (data) or a new module (logic), with a CLI surface in `cli.py`.

## What We're Looking For

Check the roadmap in the README for v2 features. High-impact contributions:

- **Claude Code hooks integration** — automatic sync via PreToolUse hooks
- **Task queue** — shared backlog that sessions pull from (instead of fixed assignments)
- **Session dependencies** — DAG of task dependencies so orchestrator can sequence work
- **Deadlock detection** — identify circular blocking between sessions
- **Per-session branches** — each session works on its own branch with PR workflow
- **Dynamic scaling** — add/remove sessions while the swarm is running

## Guidelines

- Keep it simple. This is a coordination tool, not a framework.
- Every feature needs tests. No exceptions.
- CLI commands should be self-documenting — use `--help` text and clear naming.
- SQLite is the only dependency for state. No external databases, no Redis, no message queues.

## Reporting Issues

Open an issue with:
- What you expected to happen
- What actually happened
- Steps to reproduce
- Output of `claude-swarm status` if relevant
