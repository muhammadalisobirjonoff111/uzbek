"""
SQLite ustida ishlaydigan oddiy database qatlami.
Bitta fayl (bot.db) ikkala process (bot.py va monitor.py) tomonidan
ishlatiladi, shuning uchun WAL rejimi yoqilgan (parallel o'qish/yozish uchun).
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).parent / "bot.db"


def init_db():
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER PRIMARY KEY,
                username TEXT,
                reminder_time TEXT DEFAULT '09:00',
                reminders_on INTEGER DEFAULT 1,
                scenario_time TEXT DEFAULT '06:00',
                scenario_count INTEGER DEFAULT 10,
                scenarios_on INTEGER DEFAULT 0,
                challenge_start_date TEXT
            );

            CREATE TABLE IF NOT EXISTS sent_scenarios (
                chat_id INTEGER NOT NULL,
                scenario_index INTEGER NOT NULL,
                sent_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(chat_id) REFERENCES users(chat_id)
            );

            CREATE TABLE IF NOT EXISTS competitors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                channel_username TEXT NOT NULL,
                last_message_id INTEGER DEFAULT 0,
                UNIQUE(chat_id, channel_username),
                FOREIGN KEY(chat_id) REFERENCES users(chat_id)
            );
            """
        )


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---------- USERS ----------

def add_user(chat_id: int, username: str | None):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (chat_id, username) VALUES (?, ?)",
            (chat_id, username),
        )


def set_reminder_time(chat_id: int, time_str: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET reminder_time = ? WHERE chat_id = ?",
            (time_str, chat_id),
        )


def toggle_reminders(chat_id: int, on: bool):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET reminders_on = ? WHERE chat_id = ?",
            (1 if on else 0, chat_id),
        )


def get_all_users():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users").fetchall()


def get_user(chat_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE chat_id = ?", (chat_id,)
        ).fetchone()


# ---------- SCENARIOS ----------

def set_scenario_time(chat_id: int, time_str: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET scenario_time = ? WHERE chat_id = ?",
            (time_str, chat_id),
        )


def set_scenario_count(chat_id: int, count: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET scenario_count = ? WHERE chat_id = ?",
            (count, chat_id),
        )


def toggle_scenarios(chat_id: int, on: bool):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET scenarios_on = ? WHERE chat_id = ?",
            (1 if on else 0, chat_id),
        )


def start_challenge(chat_id: int, start_date: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET challenge_start_date = ? WHERE chat_id = ?",
            (start_date, chat_id),
        )


def get_sent_indices(chat_id: int) -> set[int]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT scenario_index FROM sent_scenarios WHERE chat_id = ?", (chat_id,)
        ).fetchall()
        return {r["scenario_index"] for r in rows}


def mark_scenarios_sent(chat_id: int, indices: list[int]):
    with get_conn() as conn:
        conn.executemany(
            "INSERT INTO sent_scenarios (chat_id, scenario_index) VALUES (?, ?)",
            [(chat_id, i) for i in indices],
        )


def reset_sent_scenarios(chat_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM sent_scenarios WHERE chat_id = ?", (chat_id,))


# ---------- TASKS ----------

def add_task(chat_id: int, text: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO tasks (chat_id, text) VALUES (?, ?)", (chat_id, text)
        )


def get_tasks(chat_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM tasks WHERE chat_id = ? ORDER BY id", (chat_id,)
        ).fetchall()


def delete_task(chat_id: int, task_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM tasks WHERE chat_id = ? AND id = ?", (chat_id, task_id)
        )
        return cur.rowcount > 0


def clear_tasks(chat_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM tasks WHERE chat_id = ?", (chat_id,))


# ---------- COMPETITORS ----------

def add_competitor(chat_id: int, channel_username: str) -> bool:
    channel_username = channel_username.lstrip("@").strip().lower()
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO competitors (chat_id, channel_username) VALUES (?, ?)",
                (chat_id, channel_username),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def get_competitors(chat_id: int | None = None):
    with get_conn() as conn:
        if chat_id is None:
            return conn.execute("SELECT * FROM competitors").fetchall()
        return conn.execute(
            "SELECT * FROM competitors WHERE chat_id = ?", (chat_id,)
        ).fetchall()


def delete_competitor(chat_id: int, channel_username: str) -> bool:
    channel_username = channel_username.lstrip("@").strip().lower()
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM competitors WHERE chat_id = ? AND channel_username = ?",
            (chat_id, channel_username),
        )
        return cur.rowcount > 0


def update_last_message_id(competitor_id: int, message_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE competitors SET last_message_id = ? WHERE id = ?",
            (message_id, competitor_id),
        )
