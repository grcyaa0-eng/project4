import sqlite3
from datetime import datetime, timezone

DB_PATH = "audit_log.db"


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_id TEXT,
            creator_id TEXT,
            timestamp TEXT,
            attribution TEXT,
            confidence REAL,
            llm_score REAL,
            style_score REAL,
            label TEXT,
            status TEXT,
            appeal_reasoning TEXT
        )
    """)
    conn.commit()
    conn.close()


def log_entry(entry):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT INTO log (content_id, creator_id, timestamp, attribution, confidence,
                          llm_score, style_score, label, status, appeal_reasoning)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        entry.get("content_id"),
        entry.get("creator_id"),
        entry.get("timestamp", datetime.now(timezone.utc).isoformat()),
        entry.get("attribution"),
        entry.get("confidence"),
        entry.get("llm_score"),
        entry.get("style_score"),
        entry.get("label"),
        entry.get("status", "classified"),
        entry.get("appeal_reasoning"),
    ))
    conn.commit()
    conn.close()


def get_log(limit=50):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_entry_by_content_id(content_id):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM log WHERE content_id = ? ORDER BY id DESC LIMIT 1",
        (content_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_status(content_id, status, appeal_reasoning=None):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE log SET status = ?, appeal_reasoning = ? WHERE content_id = ?",
        (status, appeal_reasoning, content_id),
    )
    conn.commit()
    conn.close()