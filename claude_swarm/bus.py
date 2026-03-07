"""Message bus and coordination database for claude-swarm."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = ".claude-swarm/bus.db"


def _connect(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or DEFAULT_DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(path: str | None = None) -> None:
    p = Path(path or DEFAULT_DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(str(p))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id          TEXT PRIMARY KEY,
            workstream  TEXT NOT NULL,
            status      TEXT DEFAULT 'running',
            branch      TEXT,
            note        TEXT,
            updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_sync_at DATETIME
        );

        CREATE TABLE IF NOT EXISTS messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            from_id     TEXT NOT NULL,
            to_id       TEXT NOT NULL,
            body        TEXT NOT NULL,
            type        TEXT DEFAULT 'message',
            status      TEXT DEFAULT 'unread',
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS reviews (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            requester   TEXT NOT NULL,
            reviewer    TEXT NOT NULL,
            diff        TEXT,
            status      TEXT DEFAULT 'pending',
            comments    TEXT,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS git_lock (
            id          INTEGER PRIMARY KEY CHECK (id = 1),
            held_by     TEXT,
            acquired_at DATETIME
        );

        CREATE TABLE IF NOT EXISTS file_claims (
            file_path   TEXT PRIMARY KEY,
            session_id  TEXT NOT NULL,
            claimed_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS shared_context (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT NOT NULL,
            category    TEXT NOT NULL,
            body        TEXT NOT NULL,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        INSERT OR IGNORE INTO git_lock (id, held_by, acquired_at) VALUES (1, NULL, NULL);
    """)
    # Migration for existing databases that don't have last_sync_at
    try:
        conn.execute("ALTER TABLE sessions ADD COLUMN last_sync_at DATETIME")
    except sqlite3.OperationalError:
        pass  # Column already exists
    conn.commit()
    conn.close()


# --- Sessions ---

def register_session(id: str, workstream: str, branch: str, *, db_path: str | None = None) -> None:
    conn = _connect(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO sessions (id, workstream, branch, status, updated_at) VALUES (?, ?, ?, 'running', ?)",
        (id, workstream, branch, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def update_session(id: str, *, status: str | None = None, note: str | None = None, db_path: str | None = None) -> None:
    conn = _connect(db_path)
    if status is not None:
        conn.execute("UPDATE sessions SET status = ?, updated_at = ? WHERE id = ?",
                      (status, datetime.now(timezone.utc).isoformat(), id))
    if note is not None:
        conn.execute("UPDATE sessions SET note = ?, updated_at = ? WHERE id = ?",
                      (note, datetime.now(timezone.utc).isoformat(), id))
    conn.commit()
    conn.close()


def get_all_sessions(*, db_path: str | None = None) -> list[dict]:
    conn = _connect(db_path)
    rows = conn.execute("SELECT * FROM sessions ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Messages ---

def send_message(from_id: str, to_id: str, body: str, *, type: str = "message", db_path: str | None = None) -> int:
    conn = _connect(db_path)
    cur = conn.execute(
        "INSERT INTO messages (from_id, to_id, body, type) VALUES (?, ?, ?, ?)",
        (from_id, to_id, body, type),
    )
    conn.commit()
    msg_id = cur.lastrowid
    conn.close()
    return msg_id


def get_inbox(session_id: str, *, db_path: str | None = None) -> list[dict]:
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT * FROM messages WHERE to_id = ? AND status = 'unread' ORDER BY created_at",
        (session_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_read(message_id: int, *, db_path: str | None = None) -> None:
    conn = _connect(db_path)
    conn.execute("UPDATE messages SET status = 'read' WHERE id = ?", (message_id,))
    conn.commit()
    conn.close()


# --- Reviews ---

def create_review(requester: str, reviewer: str, diff: str, *, db_path: str | None = None) -> int:
    conn = _connect(db_path)
    cur = conn.execute(
        "INSERT INTO reviews (requester, reviewer, diff) VALUES (?, ?, ?)",
        (requester, reviewer, diff),
    )
    conn.commit()
    review_id = cur.lastrowid
    conn.close()
    # Notify the reviewer via a message so it shows up in their inbox
    send_message(
        requester, reviewer,
        f"Review #{review_id} requested — run 'claude-swarm review show {review_id}' to see diff",
        type="review_request",
        db_path=db_path,
    )
    return review_id


def get_pending_reviews(reviewer: str, *, db_path: str | None = None) -> list[dict]:
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT * FROM reviews WHERE reviewer = ? AND status = 'pending' ORDER BY created_at",
        (reviewer,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_pending_reviews(*, db_path: str | None = None) -> list[dict]:
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT * FROM reviews WHERE status = 'pending' ORDER BY created_at"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_messages(*, unread_only: bool = True, db_path: str | None = None) -> list[dict]:
    conn = _connect(db_path)
    if unread_only:
        rows = conn.execute(
            "SELECT * FROM messages WHERE status = 'unread' ORDER BY created_at"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM messages ORDER BY created_at"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def resolve_review(review_id: int, status: str, *, comments: str = "", db_path: str | None = None) -> None:
    conn = _connect(db_path)
    # Look up the review to get requester/reviewer for notifications
    review = conn.execute("SELECT * FROM reviews WHERE id = ?", (review_id,)).fetchone()
    if status == "rejected":
        # Rejection resets to pending — requester must address and re-request
        conn.execute(
            "UPDATE reviews SET status = 'pending', comments = ? WHERE id = ?",
            (comments, review_id),
        )
    else:
        conn.execute(
            "UPDATE reviews SET status = ?, comments = ? WHERE id = ?",
            (status, comments, review_id),
        )
    conn.commit()
    conn.close()
    # Notify the requester about the resolution
    if review:
        reviewer = review["reviewer"]
        requester = review["requester"]
        if status == "approved":
            send_message(
                reviewer, requester,
                f"Review #{review_id} approved by {reviewer}: '{comments}' — you're clear to merge-ok and push",
                type="review_approved",
                db_path=db_path,
            )
        elif status == "rejected":
            send_message(
                reviewer, requester,
                f"Review #{review_id} rejected by {reviewer}: '{comments}' — address comments and re-request",
                type="review_rejected",
                db_path=db_path,
            )


def can_merge(session_id: str, *, db_path: str | None = None) -> bool:
    conn = _connect(db_path)
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM reviews WHERE requester = ? AND status != 'approved'",
        (session_id,),
    ).fetchone()
    conn.close()
    return row["cnt"] == 0


# --- File Claims ---

def claim_file(session_id: str, file_path: str, *, db_path: str | None = None) -> bool:
    conn = _connect(db_path)
    existing = conn.execute(
        "SELECT session_id FROM file_claims WHERE file_path = ?", (file_path,)
    ).fetchone()
    if existing and existing["session_id"] != session_id:
        conn.close()
        return False
    conn.execute(
        "INSERT OR REPLACE INTO file_claims (file_path, session_id, claimed_at) VALUES (?, ?, ?)",
        (file_path, session_id, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    return True


def release_claim(session_id: str, file_path: str, *, db_path: str | None = None) -> None:
    conn = _connect(db_path)
    conn.execute(
        "DELETE FROM file_claims WHERE file_path = ? AND session_id = ?",
        (file_path, session_id),
    )
    conn.commit()
    conn.close()


def get_claims(*, session_id: str | None = None, db_path: str | None = None) -> list[dict]:
    conn = _connect(db_path)
    if session_id:
        rows = conn.execute(
            "SELECT * FROM file_claims WHERE session_id = ? ORDER BY file_path",
            (session_id,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM file_claims ORDER BY file_path").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def release_all_claims(session_id: str, *, db_path: str | None = None) -> None:
    conn = _connect(db_path)
    conn.execute("DELETE FROM file_claims WHERE session_id = ?", (session_id,))
    conn.commit()
    conn.close()


# --- Shared Context ---

VALID_CATEGORIES = ("decision", "interface", "warning", "convention", "discovery")


def add_context(session_id: str, body: str, category: str, *, db_path: str | None = None) -> int:
    if category not in VALID_CATEGORIES:
        raise ValueError(f"Invalid category '{category}'. Must be one of: {VALID_CATEGORIES}")
    conn = _connect(db_path)
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO shared_context (session_id, category, body, created_at) VALUES (?, ?, ?, ?)",
        (session_id, category, body, now),
    )
    conn.commit()
    ctx_id = cur.lastrowid
    conn.close()
    return ctx_id


def get_context(*, db_path: str | None = None) -> list[dict]:
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT * FROM shared_context ORDER BY created_at"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_context_since(timestamp: str, *, db_path: str | None = None) -> list[dict]:
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT * FROM shared_context WHERE created_at > ? ORDER BY created_at",
        (timestamp,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Sync ---

def sync_session(session_id: str, *, db_path: str | None = None) -> dict:
    """Perform a sync: check inbox, get new context, update heartbeat.

    Returns dict with keys:
        messages: list[dict] - new unread messages (now marked as read)
        context: list[dict] - new context entries since last sync
    """
    conn = _connect(db_path)
    now = datetime.now(timezone.utc).isoformat()

    # 1. Get last_sync_at
    session = conn.execute(
        "SELECT last_sync_at FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    last_sync = session["last_sync_at"] if session else None

    # 2. Get unread messages
    messages = conn.execute(
        "SELECT * FROM messages WHERE to_id = ? AND status = 'unread' ORDER BY created_at",
        (session_id,),
    ).fetchall()
    messages = [dict(m) for m in messages]

    # 3. Mark messages as read
    for m in messages:
        conn.execute("UPDATE messages SET status = 'read' WHERE id = ?", (m["id"],))

    # 4. Get new context entries
    if last_sync:
        context_rows = conn.execute(
            "SELECT * FROM shared_context WHERE created_at > ? ORDER BY created_at",
            (last_sync,),
        ).fetchall()
    else:
        context_rows = conn.execute(
            "SELECT * FROM shared_context ORDER BY created_at"
        ).fetchall()
    context = [dict(r) for r in context_rows]

    # 5. Update heartbeat and last_sync_at
    conn.execute(
        "UPDATE sessions SET updated_at = ?, last_sync_at = ? WHERE id = ?",
        (now, now, session_id),
    )

    conn.commit()
    conn.close()

    return {"messages": messages, "context": context}
