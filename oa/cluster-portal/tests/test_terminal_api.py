"""Terminal-only authentication and compatibility; fake ctl never touches a cluster."""
import hashlib
import importlib.util
from pathlib import Path
import sys
import time

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
spec = importlib.util.spec_from_file_location("terminal_fake_cluster", HERE / "smoke_local.py")
fake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fake)
from portalapp import app as appmod, auth, siteconf
from portalapp.db import DB

PREFIX = "/api/terminal/v1"


@pytest.fixture
def portal(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(appmod, "DB_PATH", str(tmp_path / "portal.db"))
    monkeypatch.setattr(appmod, "SECRET_FILE", str(tmp_path / "secret"))
    monkeypatch.setitem(siteconf.CONF, "TERMINAL_TOKEN_DAYS", "7")
    monkeypatch.setitem(siteconf.CONF, "TERMINAL_REQUIRE_HTTPS", "1")
    monkeypatch.setitem(siteconf.CONF, "TERMINAL_LOGIN_MAX_ATTEMPTS", "5")
    app = appmod.create_app(testing=True)
    app.config["TESTING"] = True
    db = DB(appmod.DB_PATH)
    for name in ("alice", "bob"):
        uid = db.add_user(name, name.title(), "user", auth.hash_password("TestPass123"), "100G", "existing")
        db.add_key(uid, "test", "ssh-ed25519 AAAA" + "A" * 60)
        fake._fake_assign(name)
        fake._set_fake_quota(name, "100G")
    db.close()
    yield app


def login(app, username="alice", password="TestPass123"):
    return app.test_client(use_cookies=False).post(PREFIX + "/auth/login", json={
        "username": username, "password": password, "device": "pytest"}, base_url="https://portal.test")


def call(app, method, path, token=None, **kw):
    headers = kw.pop("headers", {})
    if token:
        headers["Authorization"] = "Bearer " + token
    return app.test_client(use_cookies=False).open(PREFIX + path, method=method,
        base_url="https://portal.test", headers=headers, **kw)


def test_seven_day_token_no_cookie_or_plaintext_storage(portal):
    r = login(portal)
    assert r.status_code == 200
    assert "Set-Cookie" not in r.headers
    d = r.get_json()["data"]
    assert d["expires_at"] - d["issued_at"] == 7 * 86400
    assert len(d["token"]) >= 43
    assert d["username"] == "alice"
    db = DB(appmod.DB_PATH)
    row = db.q1("SELECT * FROM terminal_tokens")
    assert row["token_hash"] == hashlib.sha256(d["token"].encode()).hexdigest()
    assert d["token"] not in str(dict(row))
    assert "TestPass123" not in str(dict(row))
    assert r.headers["Cache-Control"] == "no-store"
    db.close()


def test_https_required_even_with_spoofed_forwarded_header(portal):
    r = portal.test_client().post(PREFIX + "/auth/login", json={
        "username": "alice", "password": "TestPass123"}, headers={"X-Forwarded-Proto": "https"})
    assert r.status_code == 403
    assert r.get_json()["code"] == "https_required"


def test_no_renewal_and_exact_expiry(portal, monkeypatch):
    now = time.time()
    r = login(portal).get_json()["data"]
    token = r["token"]
    from portalapp import terminal_auth
    monkeypatch.setattr(terminal_auth.time, "time", lambda: r["expires_at"] - 1)
    d = call(portal, "GET", "/auth/me", token).get_json()["data"]
    assert d["expires_at"] == r["expires_at"]
    monkeypatch.setattr(terminal_auth.time, "time", lambda: r["expires_at"])
    assert call(portal, "GET", "/auth/me", token).status_code == 401
    assert now <= r["issued_at"] + 1


def test_configurable_lifetime(portal, monkeypatch):
    monkeypatch.setitem(siteconf.CONF, "TERMINAL_TOKEN_DAYS", "2")
    d = login(portal).get_json()["data"]
    assert d["expires_at"] - d["issued_at"] == 2 * 86400


def test_wrong_password_and_login_throttle(portal):
    for _ in range(5):
        r = login(portal, password="wrong")
        assert r.status_code == 401
        assert "wrong" not in r.get_data(as_text=True)
    r = login(portal)
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) > 0


def test_cookie_cannot_authenticate_terminal_or_token_authenticate_web(portal):
    c = portal.test_client()
    with c.session_transaction() as s:
        db = DB(appmod.DB_PATH)
        s.update(uid=db.user_by_name("alice")["id"], epoch=0, csrf="abc")
        db.close()
    assert c.get(PREFIX + "/auth/me", base_url="https://portal.test").status_code == 401
    token = login(portal).get_json()["data"]["token"]
    r = portal.test_client().get("/api/my/instances", headers={"Authorization": "Bearer " + token})
    assert r.status_code == 302 and "/login" in r.location


def test_each_device_logout_is_independent(portal):
    first = login(portal).get_json()["data"]["token"]
    second = login(portal).get_json()["data"]["token"]
    assert first != second
    assert call(portal, "POST", "/auth/logout", first).status_code == 200
    assert call(portal, "GET", "/auth/me", first).status_code == 401
    assert call(portal, "GET", "/auth/me", second).status_code == 200


@pytest.mark.parametrize("change", ["password", "disable", "delete"])
def test_security_changes_permanently_revoke_tokens(portal, change):
    token = login(portal).get_json()["data"]["token"]
    db = DB(appmod.DB_PATH)
    uid = db.user_by_name("alice")["id"]
    if change == "password":
        db.user_set_password(uid, auth.hash_password("OtherPass123"))
    elif change == "disable":
        db.user_set_active(uid, False)
        db.user_set_active(uid, True)
    else:
        db.exec("DELETE FROM users WHERE id=?", (uid,))
    db.close()
    assert call(portal, "GET", "/auth/me", token).status_code == 401


def test_web_logout_does_not_revoke_terminal(portal):
    token = login(portal).get_json()["data"]["token"]
    db = DB(appmod.DB_PATH)
    uid = db.user_by_name("alice")["id"]
    db.user_bump_session_epoch(uid)
    db.close()
    assert call(portal, "GET", "/auth/me", token).status_code == 200


def test_browser_csrf_still_required(portal):
    c = portal.test_client()
    with c.session_transaction() as s:
        db = DB(appmod.DB_PATH)
        s.update(uid=db.user_by_name("alice")["id"], epoch=0, csrf="abc")
        db.close()
    r = c.post("/profile/name", data={"display_name": "Changed"})
    assert not r.get_json()["ok"]
    assert portal.config["PERMANENT_SESSION_LIFETIME"].total_seconds() == 12 * 3600
