# language: Python 3.11, file: database.py, target: SQLite3
# *all key state lives here — expiry checked at read time, never background*

import sqlite3
import secrets
import string
import datetime
from typing import Optional

DB_PATH = "keys.db"


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS keys (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                key          TEXT    UNIQUE NOT NULL,
                duration_h   INTEGER,                     -- NULL = permanent
                created_at   TEXT    NOT NULL,
                expires_at   TEXT,                        -- NULL = never
                redeemed_by  TEXT,                        -- Discord user ID
                redeemed_at  TEXT,
                is_active    INTEGER DEFAULT 1,
                note         TEXT,                        -- optional label
                created_by   TEXT                         -- admin Discord ID
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS hwid_binds (
                user_id   TEXT PRIMARY KEY,
                hwid      TEXT NOT NULL,
                bound_at  TEXT NOT NULL
            )
        """)


# ── key generation ───────────────────────────────────────────────────────────

def _make_key() -> str:
    # TN-XXXX-XXXX-XXXX  (uppercase alphanum, no ambiguous chars)
    chars = string.ascii_uppercase + string.digits
    chars = chars.replace("0", "").replace("O", "").replace("I", "").replace("1", "")
    seg = lambda: "".join(secrets.choice(chars) for _ in range(4))
    return f"TN-{seg()}-{seg()}-{seg()}"


def _parse_duration(raw: str) -> Optional[int]:
    """'1h' '12h' '1d' '7d' '30d' '1w' '1m' 'perm' → hours or None"""
    raw = raw.strip().lower()
    if raw in ("perm", "permanent", "lifetime", "∞"):
        return None
    units = {"h": 1, "d": 24, "w": 168, "m": 720}
    for suffix, mult in units.items():
        if raw.endswith(suffix):
            try:
                return int(raw[:-1]) * mult
            except ValueError:
                return -1
    return -1  # parse error


def generate_key(duration_raw: str, note: str, admin_id: str) -> dict:
    duration_h = _parse_duration(duration_raw)
    if duration_h == -1:
        return {"ok": False, "error": f"مش فاهم المدة `{duration_raw}` — جرب: 1h, 12h, 1d, 7d, 30d, 1w, perm"}

    key      = _make_key()
    now      = datetime.datetime.utcnow().isoformat()
    exp      = None
    if duration_h is not None:
        exp = (datetime.datetime.utcnow() + datetime.timedelta(hours=duration_h)).isoformat()

    with _conn() as c:
        c.execute(
            "INSERT INTO keys (key, duration_h, created_at, expires_at, note, created_by) VALUES (?,?,?,?,?,?)",
            (key, duration_h, now, exp, note or None, admin_id)
        )
    return {"ok": True, "key": key, "duration_h": duration_h, "expires_at": exp}


# ── redemption ───────────────────────────────────────────────────────────────

def redeem_key(key: str, user_id: str) -> dict:
    key = key.strip().upper()
    with _conn() as c:
        row = c.execute("SELECT * FROM keys WHERE key = ?", (key,)).fetchone()
        if not row:
            return {"ok": False, "error": "الكي مش موجود"}
        if not row["is_active"]:
            return {"ok": False, "error": "الكي مألغى"}
        if row["redeemed_by"] and row["redeemed_by"] != user_id:
            return {"ok": False, "error": "الكي مستخدم من شخص ثاني"}
        if row["redeemed_by"] == user_id:
            return {"ok": False, "error": "انت حاليًا عندك هاد الكي مفعّل", "already": True}

        # expiry check
        if row["expires_at"]:
            exp = datetime.datetime.fromisoformat(row["expires_at"])
            if datetime.datetime.utcnow() > exp:
                c.execute("UPDATE keys SET is_active=0 WHERE key=?", (key,))
                return {"ok": False, "error": "الكي انتهت صلاحيته"}

        now = datetime.datetime.utcnow().isoformat()
        c.execute(
            "UPDATE keys SET redeemed_by=?, redeemed_at=? WHERE key=?",
            (user_id, now, key)
        )
        return {"ok": True, "key": key, "expires_at": row["expires_at"], "duration_h": row["duration_h"]}


# ── status check ─────────────────────────────────────────────────────────────

def check_user_key(user_id: str) -> dict:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM keys WHERE redeemed_by=? AND is_active=1 ORDER BY redeemed_at DESC LIMIT 1",
            (user_id,)
        ).fetchone()
        if not row:
            return {"ok": False}

        if row["expires_at"]:
            exp = datetime.datetime.fromisoformat(row["expires_at"])
            if datetime.datetime.utcnow() > exp:
                c.execute("UPDATE keys SET is_active=0 WHERE key=?", (row["key"],))
                return {"ok": False}
            remaining = exp - datetime.datetime.utcnow()
            return {"ok": True, "key": row["key"], "expires_at": row["expires_at"], "remaining": remaining}

        return {"ok": True, "key": row["key"], "expires_at": None, "remaining": None}


def check_key_info(key: str) -> Optional[dict]:
    key = key.strip().upper()
    with _conn() as c:
        row = c.execute("SELECT * FROM keys WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        return dict(row)


# ── admin ops ────────────────────────────────────────────────────────────────

def revoke_key(key: str) -> bool:
    key = key.strip().upper()
    with _conn() as c:
        cur = c.execute("UPDATE keys SET is_active=0 WHERE key=?", (key,))
        return cur.rowcount > 0


def list_keys(limit: int = 20, active_only: bool = False) -> list:
    with _conn() as c:
        q = "SELECT * FROM keys"
        if active_only:
            q += " WHERE is_active=1"
        q += " ORDER BY created_at DESC LIMIT ?"
        return [dict(r) for r in c.execute(q, (limit,)).fetchall()]


# ── HWID binding ─────────────────────────────────────────────────────────────

def bind_hwid(user_id: str, hwid: str) -> dict:
    with _conn() as c:
        existing = c.execute("SELECT * FROM hwid_binds WHERE user_id=?", (user_id,)).fetchone()
        if existing:
            if existing["hwid"] == hwid:
                return {"ok": True, "already": True}
            return {"ok": False, "error": "HWID مربوط بالفعل بجهاز ثاني"}
        now = datetime.datetime.utcnow().isoformat()
        c.execute("INSERT INTO hwid_binds (user_id, hwid, bound_at) VALUES (?,?,?)", (user_id, hwid, now))
        return {"ok": True, "already": False}


def verify_hwid(user_id: str, hwid: str) -> bool:
    with _conn() as c:
        row = c.execute("SELECT hwid FROM hwid_binds WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            return True  # not bound yet — bind on first call
        return row["hwid"] == hwid


def reset_hwid(user_id: str) -> bool:
    with _conn() as c:
        cur = c.execute("DELETE FROM hwid_binds WHERE user_id=?", (user_id,))
        return cur.rowcount > 0
