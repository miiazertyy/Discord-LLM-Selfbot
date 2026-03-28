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


# ---------------------------------------------------------------------------
# Per-user persona overrides
# ---------------------------------------------------------------------------

def get_persona(user_id: int) -> str | None:
    """Return a custom persona/tone instruction for this user, or None."""
    conn = sqlite3.connect(resource_path(db_path))
    cursor = conn.cursor()
    cursor.execute(
        "SELECT value FROM user_memory WHERE user_id = ? AND key = '__persona__'",
        (user_id,),
    )
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None


def set_persona(user_id: int, persona: str):
    """Store a custom persona/tone instruction for this user."""
    set_memory(user_id, "__persona__", persona)


def clear_persona(user_id: int):
    """Remove any custom persona for this user."""
    delete_memory(user_id, "__persona__")

def format_memory_for_prompt(memory: dict) -> str:
    visible = {k: v for k, v in memory.items() if not k.startswith("__")}
    if not visible:
        return ""
    lines = "\n".join(f"- {k}: {v}" for k, v in visible.items())
    return f"\nWhat you remember about this person:\n{lines}"
