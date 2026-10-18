"""Database adapter: PostgreSQL in serverless deployments, SQLite for local development."""
import os
import sqlite3
from datetime import datetime, timezone
from .config import DATABASE_PATH, DATABASE_URL, MAX_HISTORY_MESSAGES


def _is_postgres():
    return bool(DATABASE_URL)


def _connect():
    if _is_postgres():
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=10)
    conn = sqlite3.connect(DATABASE_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def init_db():
    with _connect() as conn:
        if _is_postgres():
            conn.execute("""CREATE TABLE IF NOT EXISTS messages (
                id BIGSERIAL PRIMARY KEY,
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
        else:
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
        conn.execute("INSERT INTO messages(user_id,role,content,created_at) VALUES(%s,%s,%s,%s)" if _is_postgres()
                     else "INSERT INTO messages(user_id,role,content,created_at) VALUES(?,?,?,?)",
                     (str(user_id), role, content, _now()))
        if _is_postgres():
            conn.execute("""DELETE FROM messages WHERE user_id=%s AND id NOT IN
                         (SELECT id FROM messages WHERE user_id=%s ORDER BY id DESC LIMIT %s)""",
                         (str(user_id), str(user_id), MAX_HISTORY_MESSAGES * 3))
        else:
            conn.execute("""DELETE FROM messages WHERE user_id=? AND id NOT IN
                         (SELECT id FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?)""",
                         (str(user_id), str(user_id), MAX_HISTORY_MESSAGES * 3))


def get_history(user_id: str, limit: int = None):
    limit = limit or MAX_HISTORY_MESSAGES
    query = ("SELECT role,content FROM messages WHERE user_id=%s ORDER BY id DESC LIMIT %s" if _is_postgres()
             else "SELECT role,content FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?")
    with _connect() as conn:
        rows = conn.execute(query, (str(user_id), limit)).fetchall()
    return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]


def clear_history(user_id: str):
    with _connect() as conn:
        conn.execute("DELETE FROM messages WHERE user_id=%s" if _is_postgres()
                     else "DELETE FROM messages WHERE user_id=?", (str(user_id),))


def save_memory(user_id: str, fact: str):
    fact = fact.strip()[:1000]
    if not fact:
        return
    with _connect() as conn:
        if _is_postgres():
            conn.execute("INSERT INTO memories(user_id,fact,created_at) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                         (str(user_id), fact, _now()))
        else:
            conn.execute("INSERT OR IGNORE INTO memories(user_id,fact,created_at) VALUES(?,?,?)",
                         (str(user_id), fact, _now()))


def get_memories(user_id: str):
    query = "SELECT fact FROM memories WHERE user_id=%s ORDER BY created_at DESC LIMIT 50" if _is_postgres() else "SELECT fact FROM memories WHERE user_id=? ORDER BY created_at DESC LIMIT 50"
    with _connect() as conn:
        rows = conn.execute(query, (str(user_id),)).fetchall()
    return [r["fact"] for r in rows]


def clear_memories(user_id: str):
    with _connect() as conn:
        conn.execute("DELETE FROM memories WHERE user_id=%s" if _is_postgres()
                     else "DELETE FROM memories WHERE user_id=?", (str(user_id),))
