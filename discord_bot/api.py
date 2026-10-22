# language: Python 3.11, file: api.py, target: Flask HTTP — Lua executor verify endpoint
# run: python api.py  (separate process from bot.py)
# your Lua script hits POST http://your-server:5000/verify

from flask import Flask, request, jsonify
import database as db

app = Flask(__name__)

SECRET = "TN_API_SECRET_CHANGE_ME"   # set in .env → API_SECRET


@app.route("/verify", methods=["POST"])
def verify():
    data = request.get_json(silent=True) or {}
    if data.get("secret") != SECRET:
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    key  = data.get("key", "")
    hwid = data.get("hwid", "")
    user = data.get("user_id", "")   # optional Discord ID

    if not key:
        return jsonify({"ok": False, "error": "missing key"})

    info = db.check_key_info(key)
    if not info:
        return jsonify({"ok": False, "error": "key not found"})
    if not info["is_active"]:
        return jsonify({"ok": False, "error": "key inactive"})

    # expiry check
    if info["expires_at"]:
        import datetime
        exp = datetime.datetime.fromisoformat(info["expires_at"])
        if datetime.datetime.utcnow() > exp:
            import sqlite3
            with sqlite3.connect(db.DB_PATH) as c:
                c.execute("UPDATE keys SET is_active=0 WHERE key=?", (key,))
            return jsonify({"ok": False, "error": "key expired"})

    # HWID bind check (optional — only when hwid provided)
    if hwid and info["redeemed_by"]:
        uid = info["redeemed_by"]
        res = db.bind_hwid(uid, hwid)
        if not res["ok"]:
            return jsonify({"ok": False, "error": "hwid mismatch"})

    return jsonify({
        "ok":         True,
        "key":        info["key"],
        "expires_at": info["expires_at"],
        "duration_h": info["duration_h"],
    })


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    load_dotenv()
    SECRET = os.getenv("API_SECRET", SECRET)
    db.init_db()
    app.run(host="0.0.0.0", port=int(os.getenv("API_PORT", 5000)))
