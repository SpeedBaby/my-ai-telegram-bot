import os
import sqlite3
import threading

DB_PATH = os.environ.get("DB_PATH", "memory.db")
MAX_MESSAGES = int(os.environ.get("MAX_MESSAGES", "20"))

_lock = threading.Lock()


def _connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


def add_message(user_id: int, role: str, content: str):
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO messages(user_id, role, content) VALUES (?, ?, ?)",
                (user_id, role, content),
            )
            # Keep only the newest messages.
            conn.execute("""
                DELETE FROM messages
                WHERE user_id = ?
                  AND id NOT IN (
                    SELECT id FROM messages
                    WHERE user_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                  )
            """, (user_id, user_id, MAX_MESSAGES))
            conn.commit()
        finally:
            conn.close()


def get_history(user_id: int) -> list[tuple[str, str]]:
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT role, content FROM messages "
                "WHERE user_id = ? ORDER BY id ASC",
                (user_id,),
            ).fetchall()
            return [(row[0], row[1]) for row in rows]
        finally:
            conn.close()


def clear_history(user_id: int):
    with _lock:
        conn = _connect()
        try:
            conn.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
            conn.commit()
        finally:
            conn.close()
