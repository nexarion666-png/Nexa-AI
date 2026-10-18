import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from .config import DATABASE_PATH, MAX_HISTORY_MESSAGES

def _connect():
    conn = sqlite3.connect(DATABASE_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with _connect() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('user','assistant')),
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_user_id ON messages(user_id, id)")
        conn.execute("""CREATE TABLE IF NOT EXISTS memories (
            user_id TEXT NOT NULL,
            fact TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(user_id, fact)
        )""")

def add_message(user_id: str, role: str, content: str):
    with _connect() as conn:
        conn.execute("INSERT INTO messages(user_id,role,content,created_at) VALUES(?,?,?,?)",
                     (str(user_id), role, content, datetime.now(timezone.utc).isoformat()))
        conn.execute("""DELETE FROM messages WHERE user_id=? AND id NOT IN
                     (SELECT id FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?)""",
                     (str(user_id), str(user_id), MAX_HISTORY_MESSAGES * 3))

def get_history(user_id: str, limit: int = None):
    limit = limit or MAX_HISTORY_MESSAGES
    with _connect() as conn:
        rows = conn.execute("SELECT role,content FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?",
                            (str(user_id), limit)).fetchall()
    return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]

def clear_history(user_id: str):
    with _connect() as conn:
        conn.execute("DELETE FROM messages WHERE user_id=?", (str(user_id),))

def save_memory(user_id: str, fact: str):
    fact = fact.strip()[:1000]
    if not fact:
        return
    with _connect() as conn:
        conn.execute("INSERT OR IGNORE INTO memories(user_id,fact,created_at) VALUES(?,?,?)",
                     (str(user_id), fact, datetime.now(timezone.utc).isoformat()))

def get_memories(user_id: str):
    with _connect() as conn:
        rows = conn.execute("SELECT fact FROM memories WHERE user_id=? ORDER BY created_at DESC LIMIT 50",
                            (str(user_id),)).fetchall()
    return [r["fact"] for r in rows]

def clear_memories(user_id: str):
    with _connect() as conn:
        conn.execute("DELETE FROM memories WHERE user_id=?", (str(user_id),))
