import sqlite3
from utils.helpers import resource_path

db_path = "config/bot_data.db"


def init_memory():
    conn = sqlite3.connect(resource_path(db_path))
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_memory (
            user_id INTEGER,
            key TEXT,
            value TEXT,
            PRIMARY KEY (user_id, key)
        )
    """)
    conn.commit()
    conn.close()


def get_memory(user_id: int) -> dict:
    conn = sqlite3.connect(resource_path(db_path))
    cursor = conn.cursor()
    cursor.execute("SELECT key, value FROM user_memory WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return {row[0]: row[1] for row in rows}


def set_memory(user_id: int, key: str, value: str):
    conn = sqlite3.connect(resource_path(db_path))
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO user_memory (user_id, key, value) VALUES (?, ?, ?)",
        (user_id, key, value)
    )
    conn.commit()
    conn.close()


def delete_memory(user_id: int, key: str):
    conn = sqlite3.connect(resource_path(db_path))
    cursor = conn.cursor()
    cursor.execute("DELETE FROM user_memory WHERE user_id = ? AND key = ?", (user_id, key))
    conn.commit()
    conn.close()


def clear_memory(user_id: int):
    conn = sqlite3.connect(resource_path(db_path))
    cursor = conn.cursor()
    cursor.execute("DELETE FROM user_memory WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def format_memory_for_prompt(memory: dict) -> str:
    if not memory:
        return ""
    lines = "\n".join(f"- {k}: {v}" for k, v in memory.items())
    return f"\nWhat you remember about this person:\n{lines}"
