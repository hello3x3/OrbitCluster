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
    monkeypatch.setattr(fake.ctlmod, "ssh_ready", lambda user, jobid: {"ok": True, "ready": True})
    appmod._start_state_cache.clear()
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


def create_instance(portal, token, name="experiment"):
    plans = call(portal, "GET", "/plans", token).get_json()["data"]
    image = call(portal, "GET", "/images", token).get_json()["data"]["public"][0]["path"]
    r = call(portal, "POST", "/instances", token, data={"task_name": name,
        "plan_id": plans[0]["id"], "image": image, "hours": 1, "account": "lab"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert "Set-Cookie" not in r.headers
    return r.get_json()["data"]["instance_id"]


def test_instance_lifecycle_reuses_existing_rules(portal):
    token = login(portal).get_json()["data"]["token"]
    iid = create_instance(portal, token)
    rows = call(portal, "GET", "/instances", token).get_json()["data"]
    assert rows[0]["id"] == iid and rows[0]["state"] == "RUNNING"
    assert "cmd" not in rows[0]
    detail = call(portal, "GET", f"/instances/{iid}", token).get_json()["data"]
    assert detail["id"] == iid
    connection = call(portal, "GET", f"/instances/{iid}/connection", token).get_json()["data"]
    assert connection["ready"] is True
    assert connection["argv"][:2] == ["ssh", "-p"]
    assert call(portal, "GET", f"/instances/{iid}/log", token).get_json()["data"]["events"]
    assert call(portal, "POST", f"/instances/{iid}/save-image", token,
                data={"name": "saved_env", "force": "1"}).get_json()["ok"]
    assert call(portal, "POST", f"/instances/{iid}/delete", token).status_code == 400
    assert call(portal, "POST", f"/instances/{iid}/stop", token).get_json()["ok"]
    assert call(portal, "POST", f"/instances/{iid}/restart", token).get_json()["ok"]
    assert call(portal, "POST", f"/instances/{iid}/stop", token).get_json()["ok"]
    assert call(portal, "POST", f"/instances/{iid}/delete", token).get_json()["ok"]


def test_terminal_ownership_and_json_errors(portal):
    alice = login(portal).get_json()["data"]["token"]
    bob = login(portal, "bob").get_json()["data"]["token"]
    iid = create_instance(portal, alice)
    for method, path in [("GET", f"/instances/{iid}"), ("GET", f"/instances/{iid}/log"),
                         ("POST", f"/instances/{iid}/stop"), ("POST", f"/instances/{iid}/delete")]:
        response = call(portal, method, path, bob)
        assert response.status_code in (400, 404)
        assert response.is_json and not response.get_json()["ok"]
    assert call(portal, "GET", "/instances", bob).get_json()["data"] == []
    assert call(portal, "GET", "/admin/users", alice).status_code == 404


def test_profile_keys_accounts_and_quota(portal):
    token = login(portal).get_json()["data"]["token"]
    p = call(portal, "GET", "/profile", token).get_json()["data"]
    assert p["username"] == "alice" and p["keys"]
    assert "passwd" not in p
    r = call(portal, "POST", "/profile/name", token, data={"display_name": "Researcher"})
    assert r.get_json()["ok"] and "Set-Cookie" not in r.headers
    key = "ssh-ed25519 AAAA" + "B" * 60 + " pytest"
    assert call(portal, "POST", "/profile/keys", token, data={"pubkey": key}).get_json()["ok"]
    keys = call(portal, "GET", "/profile", token).get_json()["data"]["keys"]
    kid = next(k["id"] for k in keys if k["pubkey"] == key)
    assert call(portal, "POST", f"/profile/keys/{kid}/delete", token).get_json()["ok"]
    assert call(portal, "GET", "/accounts", token).get_json()["data"]["default"] == "lab"
    assert call(portal, "GET", "/quota", token).get_json()["data"]["disk"]
    assert call(portal, "GET", "/nodes", token).get_json()["data"]["nodes"]


def test_image_operations_keep_busy_guards(portal):
    token = login(portal).get_json()["data"]["token"]
    iid = create_instance(portal, token)
    call(portal, "GET", "/instances", token)
    assert call(portal, "POST", f"/instances/{iid}/save-image", token,
                data={"name": "personal", "force": "1"}).get_json()["ok"]
    assert call(portal, "POST", "/images/personal/comment", token, data={"note": "My image"}).get_json()["ok"]
    assert call(portal, "POST", "/images/personal/rename", token, data={"new_name": "renamed"}).get_json()["ok"]
    assert call(portal, "POST", "/images/renamed/delete", token).get_json()["ok"]


def test_terminal_password_change_no_browser_session(portal):
    token = login(portal).get_json()["data"]["token"]
    r = call(portal, "POST", "/profile/password", token,
             data={"old": "TestPass123", "new": "ChangedPass123", "new2": "ChangedPass123"})
    assert r.get_json()["ok"] and "Set-Cookie" not in r.headers
    assert call(portal, "GET", "/auth/me", token).status_code == 401


def test_stale_password_snapshot_cannot_issue_token(portal):
    from portalapp.terminal_auth import issue
    db = DB(appmod.DB_PATH)
    old = db.user_by_name("alice")
    db.user_set_password(old["id"], auth.hash_password("ReplacedPass123"))
    assert issue(db, old) is None
    db.close()


def test_browser_cookie_attached_to_terminal_does_not_create_session(portal):
    token = login(portal).get_json()["data"]["token"]
    c = portal.test_client()
    with c.session_transaction() as session:
        db = DB(appmod.DB_PATH)
        session.update(uid=db.user_by_name("bob")["id"], epoch=0, csrf="browser")
        db.close()
    response = c.post(PREFIX + "/profile/name", data={"display_name": "Terminal Alice"},
        base_url="https://localhost", headers={"Authorization": "Bearer " + token})
    assert response.status_code == 200 and "Set-Cookie" not in response.headers
    profile = call(portal, "GET", "/profile", token).get_json()["data"]
    assert profile["display_name"] == "Terminal Alice"


def test_waitress_only_trusts_explicit_tls_proxy(portal, monkeypatch):
    from waitress.proxy_headers import proxy_headers_middleware
    from werkzeug.test import Client
    spec = importlib.util.spec_from_file_location("terminal_run", HERE.parent / "run.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    monkeypatch.setitem(siteconf.CONF, "TRUSTED_PROXY", "127.0.0.1")
    proxy = proxy_headers_middleware(portal, **runner.proxy_settings())
    client = Client(proxy)
    data = {"username": "alice", "password": "TestPass123"}
    trusted = client.post(PREFIX + "/auth/login", json=data,
        environ_overrides={"REMOTE_ADDR": "127.0.0.1"}, headers={"X-Forwarded-Proto": "https"})
    assert trusted.status_code == 200
    spoofed = client.post(PREFIX + "/auth/login", json=data,
        environ_overrides={"REMOTE_ADDR": "10.0.0.5"}, headers={"X-Forwarded-Proto": "https"})
    assert spoofed.status_code == 403
    assert client.get("/login", environ_overrides={"REMOTE_ADDR": "10.0.0.5"}).status_code == 200
    monkeypatch.setitem(siteconf.CONF, "TRUSTED_PROXY", "*")
    with pytest.raises(ValueError):
        runner.proxy_settings()
