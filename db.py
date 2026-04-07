"""
db.py — SQLite database layer for RoseBot
Tables: users, bank, transactions, bans, command_stats, room_stats,
        reminders, polls, poll_votes, bot_config
"""

import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "rosebot.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                mxid        TEXT PRIMARY KEY,
                display_name TEXT,
                first_seen  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                last_seen   INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                message_count INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS bank (
                mxid        TEXT PRIMARY KEY,
                balance     INTEGER NOT NULL DEFAULT 1000,
                total_won   INTEGER NOT NULL DEFAULT 0,
                total_lost  INTEGER NOT NULL DEFAULT 0,
                games_played INTEGER NOT NULL DEFAULT 0,
                last_daily  INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(mxid) REFERENCES users(mxid)
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                mxid        TEXT NOT NULL,
                amount      INTEGER NOT NULL,
                reason      TEXT,
                ts          INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            );

            CREATE TABLE IF NOT EXISTS bans (
                mxid        TEXT PRIMARY KEY,
                room_id     TEXT NOT NULL,
                reason      TEXT,
                banned_by   TEXT NOT NULL,
                banned_at   INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            );

            CREATE TABLE IF NOT EXISTS command_stats (
                mxid        TEXT NOT NULL,
                command     TEXT NOT NULL,
                count       INTEGER NOT NULL DEFAULT 1,
                last_used   INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                PRIMARY KEY(mxid, command),
                FOREIGN KEY(mxid) REFERENCES users(mxid)
            );

            CREATE TABLE IF NOT EXISTS room_stats (
                room_id     TEXT NOT NULL,
                mxid        TEXT NOT NULL,
                message_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(room_id, mxid)
            );

            CREATE TABLE IF NOT EXISTS reminders (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                mxid        TEXT NOT NULL,
                room_id     TEXT NOT NULL,
                message     TEXT NOT NULL,
                fire_at     INTEGER NOT NULL,
                fired       INTEGER NOT NULL DEFAULT 0,
                created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            );

            CREATE TABLE IF NOT EXISTS polls (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id     TEXT NOT NULL,
                creator     TEXT NOT NULL,
                question    TEXT NOT NULL,
                options     TEXT NOT NULL,
                closed      INTEGER NOT NULL DEFAULT 0,
                created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            );

            CREATE TABLE IF NOT EXISTS poll_votes (
                poll_id     INTEGER NOT NULL,
                mxid        TEXT NOT NULL,
                option_idx  INTEGER NOT NULL,
                PRIMARY KEY(poll_id, mxid),
                FOREIGN KEY(poll_id) REFERENCES polls(id)
            );

            CREATE TABLE IF NOT EXISTS bot_config (
                key         TEXT PRIMARY KEY,
                value       TEXT NOT NULL,
                updated_by  TEXT,
                updated_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
            );
        """)


# ─── User helpers ─────────────────────────────────────────────────────────────

def upsert_user(mxid: str, display_name: str = None):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO users(mxid, display_name) VALUES(?, ?)
            ON CONFLICT(mxid) DO UPDATE SET
                last_seen = strftime('%s','now'),
                display_name = COALESCE(?, display_name)
        """, (mxid, display_name, display_name))


def increment_message(mxid: str, room_id: str):
    with get_conn() as conn:
        conn.execute("""
            UPDATE users SET message_count = message_count + 1,
                last_seen = strftime('%s','now')
            WHERE mxid = ?
        """, (mxid,))
        conn.execute("""
            INSERT INTO room_stats(room_id, mxid, message_count) VALUES(?, ?, 1)
            ON CONFLICT(room_id, mxid) DO UPDATE SET message_count = message_count + 1
        """, (room_id, mxid))


def get_user(mxid: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users WHERE mxid = ?", (mxid,)).fetchone()


def get_top_users(room_id: str, limit: int = 10) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("""
            SELECT u.mxid, u.display_name, rs.message_count
            FROM room_stats rs
            JOIN users u ON u.mxid = rs.mxid
            WHERE rs.room_id = ?
            ORDER BY rs.message_count DESC
            LIMIT ?
        """, (room_id, limit)).fetchall()


def get_global_top(limit: int = 10) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("""
            SELECT mxid, display_name, message_count
            FROM users
            ORDER BY message_count DESC
            LIMIT ?
        """, (limit,)).fetchall()


# ─── Command stat helpers ──────────────────────────────────────────────────────

def log_command(mxid: str, command: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO command_stats(mxid, command) VALUES(?, ?)
            ON CONFLICT(mxid, command) DO UPDATE SET
                count = count + 1,
                last_used = strftime('%s','now')
        """, (mxid, command))


def get_top_commands(limit: int = 10) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("""
            SELECT command, SUM(count) as total
            FROM command_stats
            GROUP BY command
            ORDER BY total DESC
            LIMIT ?
        """, (limit,)).fetchall()


def get_user_commands(mxid: str, limit: int = 5) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("""
            SELECT command, count FROM command_stats
            WHERE mxid = ?
            ORDER BY count DESC
            LIMIT ?
        """, (mxid, limit)).fetchall()


def get_all_users(limit: int = 100) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("""
            SELECT u.mxid, u.display_name, u.message_count,
                   COALESCE(SUM(cs.count), 0) as command_count
            FROM users u
            LEFT JOIN command_stats cs ON u.mxid = cs.mxid
            GROUP BY u.mxid
            ORDER BY u.message_count DESC
            LIMIT ?
        """, (limit,)).fetchall()


def get_banned_mxids(room_id: str) -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT mxid FROM bans WHERE room_id = ?",
            (room_id,)
        ).fetchall()
        return [r["mxid"] for r in rows]


# ─── Bank helpers ─────────────────────────────────────────────────────────────

def get_or_create_bank(mxid: str, starting: int = 1000) -> sqlite3.Row:
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO bank(mxid, balance) VALUES(?, ?)
        """, (mxid, starting))
        return conn.execute("SELECT * FROM bank WHERE mxid = ?", (mxid,)).fetchone()


def get_balance(mxid: str) -> int:
    row = get_or_create_bank(mxid)
    return row["balance"]


def update_balance(mxid: str, delta: int, reason: str = None) -> int:
    """Add delta (negative to subtract). Returns new balance."""
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO bank(mxid, balance) VALUES(?, 0)", (mxid,))
        conn.execute("""
            UPDATE bank SET balance = MAX(0, balance + ?) WHERE mxid = ?
        """, (delta, mxid))
        conn.execute("""
            INSERT INTO transactions(mxid, amount, reason) VALUES(?, ?, ?)
        """, (mxid, delta, reason))
        row = conn.execute("SELECT balance FROM bank WHERE mxid = ?", (mxid,)).fetchone()
        return row["balance"]


def record_game(mxid: str, won: int, lost: int):
    with get_conn() as conn:
        conn.execute("""
            UPDATE bank SET
                total_won = total_won + ?,
                total_lost = total_lost + ?,
                games_played = games_played + 1
            WHERE mxid = ?
        """, (won, lost, mxid))


def claim_daily(mxid: str, reward: int = 500) -> tuple[bool, int]:
    """Returns (success, seconds_remaining)."""
    now = int(time.time())
    row = get_or_create_bank(mxid)
    last = row["last_daily"]
    cooldown = 86400  # 24 hours
    if now - last < cooldown:
        return False, cooldown - (now - last)
    with get_conn() as conn:
        conn.execute("""
            UPDATE bank SET balance = balance + ?, last_daily = ? WHERE mxid = ?
        """, (reward, now, mxid))
    return True, 0


def get_rich_list(limit: int = 10) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("""
            SELECT b.mxid, u.display_name, b.balance, b.total_won, b.total_lost, b.games_played
            FROM bank b
            LEFT JOIN users u ON u.mxid = b.mxid
            ORDER BY b.balance DESC
            LIMIT ?
        """, (limit,)).fetchall()


# ─── Ban helpers ──────────────────────────────────────────────────────────────

def add_ban(mxid: str, room_id: str, reason: str, banned_by: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO bans(mxid, room_id, reason, banned_by)
            VALUES(?, ?, ?, ?)
        """, (mxid, room_id, reason, banned_by))


def remove_ban(mxid: str, room_id: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM bans WHERE mxid = ? AND room_id = ?", (mxid, room_id))


def is_banned(mxid: str, room_id: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM bans WHERE mxid = ? AND room_id = ?", (mxid, room_id)
        ).fetchone()
        return row is not None


def get_ban_list(room_id: str) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM bans WHERE room_id = ?", (room_id,)
        ).fetchall()


# ─── Reminder helpers ─────────────────────────────────────────────────────────

def add_reminder(mxid: str, room_id: str, message: str, fire_at: int) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO reminders(mxid, room_id, message, fire_at) VALUES(?,?,?,?)",
            (mxid, room_id, message, fire_at)
        )
        return cur.lastrowid


def get_due_reminders() -> list:
    now = int(time.time())
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM reminders WHERE fired=0 AND fire_at <= ?", (now,)
        ).fetchall()
        return rows


def mark_reminder_fired(reminder_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE reminders SET fired=1 WHERE id=?", (reminder_id,))


def get_user_reminders(mxid: str) -> list:
    now = int(time.time())
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM reminders WHERE mxid=? AND fired=0 AND fire_at > ? ORDER BY fire_at",
            (mxid, now)
        ).fetchall()


def cancel_reminder(reminder_id: int, mxid: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM reminders WHERE id=? AND mxid=?", (reminder_id, mxid)
        )
        return cur.rowcount > 0


# ─── Poll helpers ──────────────────────────────────────────────────────────────

import json as _json


def create_poll(room_id: str, creator: str, question: str, options: list[str]) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO polls(room_id, creator, question, options) VALUES(?,?,?,?)",
            (room_id, creator, question, _json.dumps(options))
        )
        return cur.lastrowid


def get_poll(poll_id: int):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM polls WHERE id=?", (poll_id,)).fetchone()


def get_active_poll(room_id: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM polls WHERE room_id=? AND closed=0 ORDER BY created_at DESC LIMIT 1",
            (room_id,)
        ).fetchone()


def vote_poll(poll_id: int, mxid: str, option_idx: int) -> tuple[bool, str]:
    poll = get_poll(poll_id)
    if not poll:
        return False, "Poll not found."
    if poll["closed"]:
        return False, "Poll is already closed."
    options = _json.loads(poll["options"])
    if option_idx < 0 or option_idx >= len(options):
        return False, f"Invalid option. Choose 1-{len(options)}."
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO poll_votes(poll_id, mxid, option_idx) VALUES(?,?,?)",
            (poll_id, mxid, option_idx)
        )
    return True, ""


def get_poll_results(poll_id: int) -> dict:
    poll = get_poll(poll_id)
    if not poll:
        return {}
    options = _json.loads(poll["options"])
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT option_idx, COUNT(*) as cnt FROM poll_votes WHERE poll_id=? GROUP BY option_idx",
            (poll_id,)
        ).fetchall()
        total_votes = conn.execute(
            "SELECT COUNT(*) as cnt FROM poll_votes WHERE poll_id=?", (poll_id,)
        ).fetchone()["cnt"]
    counts = {r["option_idx"]: r["cnt"] for r in rows}
    return {
        "question": poll["question"],
        "options": options,
        "counts": counts,
        "total": total_votes,
        "closed": bool(poll["closed"]),
        "creator": poll["creator"],
    }


def close_poll(poll_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE polls SET closed=1 WHERE id=?", (poll_id,))


# ─── Bot config helpers ────────────────────────────────────────────────────────

_CONFIG_DEFAULTS = {
    "command_prefix":      "!",
    "daily_reward":        "500",
    "bank_starting":       "1000",
    "crash_max_bet":       "10000",
    "command_rate_limit":  "10",
    "command_rate_window": "60",
    "mention_required":    "false",
    "cleanup_temp_messages": "true",
    "welcome_message":     "",
    "banned_words":        "",
    "max_download_mb":     "50",
}


def config_get(key: str) -> str:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM bot_config WHERE key=?", (key,)).fetchone()
        if row:
            return row["value"]
        return _CONFIG_DEFAULTS.get(key, "")


def config_set(key: str, value: str, updated_by: str = None):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO bot_config(key,value,updated_by) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=?, updated_by=?, updated_at=strftime('%s','now')",
            (key, value, updated_by, value, updated_by)
        )


def config_list() -> dict:
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM bot_config").fetchall()
        result = dict(_CONFIG_DEFAULTS)
        for r in rows:
            result[r["key"]] = r["value"]
        return result


# ─── Dashboard stats helpers ───────────────────────────────────────────────────

def get_dashboard_stats() -> dict:
    with get_conn() as conn:
        user_count = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
        cmd_count  = conn.execute("SELECT COALESCE(SUM(count),0) as c FROM command_stats").fetchone()["c"]
        coins      = conn.execute("SELECT COALESCE(SUM(balance),0) as c FROM bank").fetchone()["c"]
        rich       = conn.execute("""
            SELECT b.mxid, u.display_name, b.balance, b.games_played
            FROM bank b LEFT JOIN users u ON u.mxid=b.mxid
            ORDER BY b.balance DESC LIMIT 5
        """).fetchall()
        top_users  = conn.execute("""
            SELECT mxid, display_name, message_count
            FROM users ORDER BY message_count DESC LIMIT 5
        """).fetchall()
        top_cmds   = conn.execute("""
            SELECT command, SUM(count) as total
            FROM command_stats GROUP BY command ORDER BY total DESC LIMIT 8
        """).fetchall()
    return {
        "user_count": user_count,
        "cmd_count":  cmd_count,
        "coins":      coins,
        "rich":       [dict(r) for r in rich],
        "top_users":  [dict(r) for r in top_users],
        "top_cmds":   [dict(r) for r in top_cmds],
    }
