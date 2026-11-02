"""Independent, revocable terminal credentials. Never uses browser sessions."""
import hashlib
import secrets
import time

from . import siteconf


def setting_int(name, default, maximum=31536000):
    try:
        value = int(siteconf.get(name, str(default)))
    except (ValueError, TypeError):
        raise ValueError("%s 必须为正整数" % name) from None
    if not 1 <= value <= maximum:
        raise ValueError("%s 必须在 1-%d 之间" % (name, maximum))
    return value


def require_https():
    value = siteconf.get("TERMINAL_REQUIRE_HTTPS", "0")
    if value not in ("0", "1"):
        raise ValueError("TERMINAL_REQUIRE_HTTPS 必须为 0 或 1")
    return value == "1"


def init_schema(db):
    setting_int("TERMINAL_TOKEN_DAYS", 7, 3650)
    setting_int("TERMINAL_LOGIN_MAX_ATTEMPTS", 5)
    setting_int("TERMINAL_LOGIN_IP_MAX_ATTEMPTS", 50)
    setting_int("TERMINAL_LOGIN_WINDOW_S", 300)
    require_https()
    db.conn().executescript("""
        CREATE TABLE IF NOT EXISTS terminal_tokens (
          token_hash TEXT PRIMARY KEY,
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          device TEXT NOT NULL DEFAULT '',
          issued_at INTEGER NOT NULL,
          expires_at INTEGER NOT NULL,
          revoked_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS ix_terminal_tokens_user ON terminal_tokens(user_id);
        CREATE TABLE IF NOT EXISTS terminal_login_failures (
          bucket TEXT PRIMARY KEY,
          started_at INTEGER NOT NULL,
          failures INTEGER NOT NULL
        );
        CREATE TRIGGER IF NOT EXISTS terminal_revoke_password
        AFTER UPDATE OF passwd ON users WHEN OLD.passwd != NEW.passwd
        BEGIN
          UPDATE terminal_tokens SET revoked_at=CAST(strftime('%s','now') AS INTEGER)
          WHERE user_id=NEW.id AND revoked_at IS NULL;
        END;
        CREATE TRIGGER IF NOT EXISTS terminal_revoke_disabled
        AFTER UPDATE OF is_active ON users WHEN NEW.is_active=0
        BEGIN
          UPDATE terminal_tokens SET revoked_at=CAST(strftime('%s','now') AS INTEGER)
          WHERE user_id=NEW.id AND revoked_at IS NULL;
        END;
    """)


def digest(raw):
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def issue(db, user, device="", now=None):
    now = int(time.time() if now is None else now)
    expires = now + setting_int("TERMINAL_TOKEN_DAYS", 7, 3650) * 86400
    token = secrets.token_urlsafe(32)
    # A password change/deactivation between password verification and INSERT
    # must not issue a credential from an out-of-date user snapshot.
    inserted = db.exec("INSERT INTO terminal_tokens(token_hash,user_id,device,issued_at,expires_at) "
        "SELECT ?,id,?,?,? FROM users WHERE id=? AND passwd=? AND is_active=1",
        (digest(token), device[:100], now, expires, user["id"], user["passwd"]))
    if not inserted.rowcount:
        return None
    db.exec("DELETE FROM terminal_tokens WHERE expires_at<=?", (now,))
    return {"token": token, "token_type": "Bearer", "username": user["username"],
            "display_name": user["display_name"], "issued_at": now, "expires_at": expires}


def authenticate(db, raw, now=None):
    if not isinstance(raw, str) or not 43 <= len(raw) <= 128:
        return None
    now = int(time.time() if now is None else now)
    row = db.q1("SELECT t.issued_at,t.expires_at,t.device,t.user_id FROM terminal_tokens t "
        "JOIN users u ON u.id=t.user_id WHERE t.token_hash=? AND t.revoked_at IS NULL "
        "AND t.expires_at>? AND u.is_active=1", (digest(raw), now))
    if row is None:
        return None
    return db.user_by_id(row["user_id"]), dict(row)


def revoke(db, raw):
    db.exec("UPDATE terminal_tokens SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
            (int(time.time()), digest(raw)))


def login_buckets(username, address):
    # Username-wide bucket protects an account even across source addresses.
    return [(digest("user:" + username), setting_int("TERMINAL_LOGIN_MAX_ATTEMPTS", 5)),
            (digest("ip:" + (address or "unknown")), setting_int("TERMINAL_LOGIN_IP_MAX_ATTEMPTS", 50))]


def retry_after(db, buckets):
    now = int(time.time())
    window = setting_int("TERMINAL_LOGIN_WINDOW_S", 300)
    for key, limit in buckets:
        row = db.q1("SELECT * FROM terminal_login_failures WHERE bucket=?", (key,))
        if row and row["started_at"] + window > now and row["failures"] >= limit:
            return row["started_at"] + window - now
    return 0


def record_failure(db, buckets):
    now = int(time.time())
    window = setting_int("TERMINAL_LOGIN_WINDOW_S", 300)
    db.exec("DELETE FROM terminal_login_failures WHERE started_at<=?", (now - window,))
    for key, _ in buckets:
        db.exec("INSERT INTO terminal_login_failures(bucket,started_at,failures) VALUES(?,?,1) "
                "ON CONFLICT(bucket) DO UPDATE SET failures=failures+1", (key, now))
