import sqlite3
from utils.helpers import resource_path

db_path = "config/bot_data.db"


def init_db():
    conn = sqlite3.connect(resource_path(db_path))
    cursor = conn.cursor()

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY
        )
    """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS ignored_users (
            id INTEGER PRIMARY KEY
        )
    """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS paused_users (
            id INTEGER PRIMARY KEY
        )
    """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS pictures (
            filename    TEXT PRIMARY KEY,
            description TEXT NOT NULL DEFAULT ''
        )
    """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS unresponded_messages (
            user_id     INTEGER NOT NULL,
            channel_id  INTEGER NOT NULL,
            content     TEXT    NOT NULL,
            received_at REAL    NOT NULL,
            nudge_sent  INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, channel_id)
        )
    """
    )

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Unresponded messages — nudge system
# ---------------------------------------------------------------------------

def add_unresponded(user_id: int, channel_id: int, content: str, received_at: float):
    """Record a message that the bot received but hasn't replied to yet."""
    conn = sqlite3.connect(resource_path(db_path))
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR REPLACE INTO unresponded_messages
            (user_id, channel_id, content, received_at, nudge_sent)
        VALUES (?, ?, ?, ?, 0)
        """,
        (user_id, channel_id, content, received_at),
    )
    conn.commit()
    conn.close()


def mark_responded(user_id: int, channel_id: int):
    """Remove a user's unresponded entry once the bot has replied."""
    conn = sqlite3.connect(resource_path(db_path))
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM unresponded_messages WHERE user_id = ? AND channel_id = ?",
        (user_id, channel_id),
    )
    conn.commit()
    conn.close()


def mark_nudge_sent(user_id: int, channel_id: int):
    """Flag that a nudge has already been sent so we don't send another."""
    conn = sqlite3.connect(resource_path(db_path))
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE unresponded_messages SET nudge_sent = 1 WHERE user_id = ? AND channel_id = ?",
        (user_id, channel_id),
    )
    conn.commit()
    conn.close()


def get_pending_nudges(threshold_seconds: float) -> list[dict]:
    """Return all unresponded messages older than threshold that haven't been nudged yet."""
    conn = sqlite3.connect(resource_path(db_path))
    cursor = conn.cursor()
    import time as _time
    cutoff = _time.time() - threshold_seconds
    cursor.execute(
        """
        SELECT user_id, channel_id, content, received_at
        FROM unresponded_messages
        WHERE nudge_sent = 0 AND received_at <= ?
        """,
        (cutoff,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [
        {"user_id": r[0], "channel_id": r[1], "content": r[2], "received_at": r[3]}
        for r in rows
    ]


def add_channel(channel_id):
    conn = sqlite3.connect(resource_path(db_path))
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO channels (id) VALUES (?)", (channel_id,))
    conn.commit()
    conn.close()


def remove_channel(channel_id):
    conn = sqlite3.connect(resource_path(db_path))
    cursor = conn.cursor()
    cursor.execute("DELETE FROM channels WHERE id = ?", (channel_id,))
    conn.commit()
    conn.close()


def get_channels():
    conn = sqlite3.connect(resource_path(db_path))
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM channels")
    channels = [row[0] for row in cursor.fetchall()]
    conn.close()
    return channels


def add_ignored_user(user_id):
    conn = sqlite3.connect(resource_path(db_path))
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO ignored_users (id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()


def remove_ignored_user(user_id):
    conn = sqlite3.connect(resource_path(db_path))
    cursor = conn.cursor()
    cursor.execute("DELETE FROM ignored_users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


def get_ignored_users():
    conn = sqlite3.connect(resource_path(db_path))
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM ignored_users")
    users = [row[0] for row in cursor.fetchall()]
    conn.close()
    return users


def add_paused_user(user_id):
    conn = sqlite3.connect(resource_path(db_path))
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO paused_users (id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()


def remove_paused_user(user_id):
    conn = sqlite3.connect(resource_path(db_path))
    cursor = conn.cursor()
    cursor.execute("DELETE FROM paused_users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


def get_paused_users():
    conn = sqlite3.connect(resource_path(db_path))
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM paused_users")
    users = [row[0] for row in cursor.fetchall()]
    conn.close()
    return users


# ---------------------------------------------------------------------------
# Pictures — description cache
# ---------------------------------------------------------------------------

def add_picture_description(filename: str, description: str):
    """Store (or update) the AI description for a picture file."""
    conn = sqlite3.connect(resource_path(db_path))
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO pictures (filename, description) VALUES (?, ?)",
        (filename, description),
    )
    conn.commit()
    conn.close()


def get_picture_description(filename: str) -> str | None:
    """Return the stored description for a filename, or None if not found."""
    conn = sqlite3.connect(resource_path(db_path))
    cursor = conn.cursor()
    cursor.execute("SELECT description FROM pictures WHERE filename = ?", (filename,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None


def delete_picture_db(filename: str):
    """Remove a picture's DB entry when the file is deleted."""
    conn = sqlite3.connect(resource_path(db_path))
    cursor = conn.cursor()
    cursor.execute("DELETE FROM pictures WHERE filename = ?", (filename,))
    conn.commit()
    conn.close()


def rename_picture_db(old_filename: str, new_filename: str):
    """Update the filename key when images are renumbered after a deletion."""
    conn = sqlite3.connect(resource_path(db_path))
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE pictures SET filename = ? WHERE filename = ?",
        (new_filename, old_filename),
    )
    conn.commit()
    conn.close()


def clear_all_pictures_db():
    """Wipe all picture descriptions — called when ,image delete all is used."""
    conn = sqlite3.connect(resource_path(db_path))
    cursor = conn.cursor()
    cursor.execute("DELETE FROM pictures")
    conn.commit()
    conn.close()


def get_all_picture_descriptions() -> dict[str, str]:
    """Return a {filename: description} dict for every stored picture."""
    conn = sqlite3.connect(resource_path(db_path))
    cursor = conn.cursor()
    cursor.execute("SELECT filename, description FROM pictures")
    rows = cursor.fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}
