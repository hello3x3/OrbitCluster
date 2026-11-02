"""CLI -> real Flask terminal API -> original fake-ctl business handlers."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import httpx

from test_terminal_api import portal  # pytest fixture; no real cluster access

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "terminal" / "src"))
from orbitcluster import cli
from orbitcluster.client import Client


def test_cli_end_to_end_and_web_visibility(portal, tmp_path, monkeypatch, capsys):
    from io import StringIO
    from portalapp.db import DB
    from portalapp import app as appmod
    monkeypatch.setenv("ORBIT_CONFIG_DIR", str(tmp_path / "client"))
    monkeypatch.setenv("ORBIT_SERVER", "https://portal.test")
    monkeypatch.setattr(sys, "stdin", StringIO("TestPass123\n"))
    factory = lambda settings: Client(settings, transport=httpx.WSGITransport(app=portal))
    assert cli.main(["login", "--username", "alice", "--password-stdin", "--json"], client_factory=factory) == 0
    login = json.loads(capsys.readouterr().out)
    assert "token" not in login["data"]
    assert cli.main(["instances", "create", "--name", "cli-test", "--plan", "1",
        "--image", "cuda12.8.0-devel-ubuntu24.04", "--hours", "1", "--json"], client_factory=factory) == 0
    iid = json.loads(capsys.readouterr().out)["data"]["instance_id"]
    assert cli.main(["instances", "list", "--json"], client_factory=factory) == 0
    assert json.loads(capsys.readouterr().out)["data"][0]["id"] == iid
    browser = portal.test_client()
    db = DB(appmod.DB_PATH)
    with browser.session_transaction() as session:
        session.update(uid=db.user_by_name("alice")["id"], epoch=0, csrf="browser")
    db.close()
    assert "cli-test" in browser.get("/my").get_data(as_text=True)
    assert cli.main(["logout", "--json"], client_factory=factory) == 0
    capsys.readouterr()
    assert cli.main(["whoami", "--json"], client_factory=factory) == 3
    assert browser.get("/profile").status_code == 200


def test_existing_web_smoke_with_fully_isolated_readiness_stub():
    script = Path(__file__).with_name("smoke_local.py")
    code = ("import runpy; m=runpy.run_path(" + repr(str(script)) + "); "
            "m['ctlmod'].ssh_ready=lambda user,jobid:{'ok':True,'ready':True}; "
            "raise SystemExit(m['main']())")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout[-1000:] + result.stderr[-2000:]
    assert "SMOKE_OK" in result.stdout


def test_real_https_login_requires_trusted_ca(portal, tmp_path, monkeypatch):
    import shutil
    import threading
    import pytest
    from werkzeug.serving import make_server
    from orbitcluster.config import Settings
    from orbitcluster.client import NetworkError
    openssl = shutil.which("openssl")
    if not openssl:
        pytest.skip("openssl is unavailable")
    cert, key = tmp_path / "test-ca.crt", tmp_path / "test-server.key"
    generated = subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(key), "-out", str(cert), "-days", "1", "-subj", "/CN=localhost",
        "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost"], capture_output=True)
    assert generated.returncode == 0
    try:
        server = make_server("127.0.0.1", 0, portal, ssl_context=(str(cert), str(key)))
    except (PermissionError, SystemExit):
        pytest.skip("sandbox denies local sockets; run with network permission")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    try:
        base = {"server": f"https://127.0.0.1:{server.server_port}", "config_dir": tmp_path / "tls-client", "timeout": 3}
        with Client(Settings.load(base, environ={})) as untrusted:
            with pytest.raises(NetworkError):
                untrusted.login("alice", "TestPass123")
        with Client(Settings.load({**base, "ca_bundle": str(cert)}, environ={})) as trusted:
            result = trusted.login("alice", "TestPass123")
            assert result["username"] == "alice"
            assert trusted.identity()["expires_at"] == result["expires_at"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_explicit_http_cli_auth_lifetime_and_resource_operations(portal, tmp_path, monkeypatch, capsys):
    from io import StringIO
    from portalapp import siteconf
    monkeypatch.setitem(siteconf.CONF, 'TERMINAL_REQUIRE_HTTPS', '0')
    monkeypatch.setenv('ORBIT_CONFIG_DIR', str(tmp_path / 'http-client'))
    monkeypatch.setenv('ORBIT_SERVER', 'http://10.71.106.251')
    monkeypatch.setenv('ORBIT_ALLOW_HTTP', '1')
    monkeypatch.setattr(sys, 'stdin', StringIO('TestPass123\n'))
    factory = lambda settings: Client(settings, transport=httpx.WSGITransport(app=portal))
    assert cli.main(['login', '--username', 'alice', '--password-stdin', '--json'], client_factory=factory) == 0
    result = json.loads(capsys.readouterr().out)['data']
    assert result['expires_at'] - result['issued_at'] == 7 * 86400
    assert 'token' not in result
    assert cli.main(['instances', 'create', '--name', 'http-test', '--plan', '1', '--image', 'cuda12.8.0-devel-ubuntu24.04', '--hours', '1', '--json'], client_factory=factory) == 0
    iid = json.loads(capsys.readouterr().out)['data']['instance_id']
    assert cli.main(['instances', 'list', '--json'], client_factory=factory) == 0
    assert json.loads(capsys.readouterr().out)['data'][0]['id'] == iid
    assert cli.main(['logout', '--json'], client_factory=factory) == 0
    capsys.readouterr()
    assert cli.main(['whoami', '--json'], client_factory=factory) == 3


def test_real_http_requires_server_opt_in_and_never_renews(portal, tmp_path, monkeypatch):
    import threading
    import pytest
    from werkzeug.serving import make_server
    from portalapp import siteconf, terminal_auth
    from orbitcluster.config import Settings
    from orbitcluster.client import ApiError, AuthenticationRequired
    try:
        server = make_server('127.0.0.1', 0, portal)
    except (PermissionError, SystemExit):
        pytest.skip('sandbox denies local sockets; run with network permission')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('NO_PROXY', '127.0.0.1')
    try:
        settings = Settings.load({'server': f'http://127.0.0.1:{server.server_port}',
            'config_dir': tmp_path / 'http-real-client', 'allow_http': True}, environ={})
        with Client(settings) as client:
            with pytest.raises(ApiError) as rejected:
                client.login('alice', 'TestPass123')
            assert rejected.value.code == 'https_required'
            monkeypatch.setitem(siteconf.CONF, 'TERMINAL_REQUIRE_HTTPS', '0')
            result = client.login('alice', 'TestPass123')
            assert result['expires_at'] - result['issued_at'] == 7 * 86400
            assert client.identity()['expires_at'] == result['expires_at']
            monkeypatch.setattr(terminal_auth.time, 'time', lambda: result['expires_at'])
            # Check the server's expiry, independently of the client's local expiry guard.
            response = httpx.get(settings.server + '/api/terminal/v1/auth/me',
                headers={'Authorization': 'Bearer ' + client.store.get(settings.server)['token']}, trust_env=False)
            assert response.status_code == 401
            with pytest.raises(AuthenticationRequired):
                client.identity()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
