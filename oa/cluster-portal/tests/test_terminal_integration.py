"""Native CLI -> real Flask API -> original fake-cluster business handlers."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading

import pytest
from werkzeug.serving import make_server
from test_terminal_api import portal

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope='session')
def orbit_binary(tmp_path_factory):
    target = tmp_path_factory.mktemp('native-cli') / ('orbit.exe' if os.name == 'nt' else 'orbit')
    subprocess.run(['go', 'build', '-o', str(target), './cmd/orbit'], cwd=ROOT / 'terminal', check=True)
    return target


@pytest.fixture
def serve(portal):
    servers = []
    def start(ssl_context=None):
        server = make_server('127.0.0.1', 0, portal, threaded=True, ssl_context=ssl_context)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
        return ('https' if ssl_context else 'http') + '://127.0.0.1:' + str(server.server_port)
    yield start
    for server, thread in servers:
        server.shutdown(); server.server_close(); thread.join(timeout=5)


def cli(orbit_binary, tmp_path, server, *args, password=None, success=0):
    env = {**os.environ, 'ORBIT_CONFIG_DIR': str(tmp_path / 'client'), 'ORBIT_SERVER': server,
           'ORBIT_CA_BUNDLE': '', 'ORBIT_ALLOW_HTTP': '', 'NO_PROXY': '127.0.0.1'}
    result = subprocess.run([str(orbit_binary), *args, '--json'], input=password, capture_output=True, text=True, env=env, timeout=20)
    assert result.returncode == success, result.stdout + result.stderr
    assert 'TestPass123' not in result.stdout + result.stderr
    return json.loads(result.stdout if success == 0 else result.stderr)


def test_native_cli_http_full_flow_visible_to_web(portal, serve, orbit_binary, tmp_path, monkeypatch):
    from portalapp import app as appmod, siteconf
    from portalapp.db import DB
    monkeypatch.delitem(siteconf.CONF, 'TERMINAL_REQUIRE_HTTPS', raising=False)  # New default is HTTP allowed.
    server = serve()
    login = cli(orbit_binary, tmp_path, server, 'login', '--username', 'alice', '--password-stdin', password='TestPass123\n')['data']
    assert 'token' not in login
    assert login['expires_at'] - login['issued_at'] == 7 * 86400
    for command in [('whoami',), ('plans',), ('accounts',), ('quota',), ('nodes',), ('images','list'), ('keys','list'), ('profile','show')]:
        assert cli(orbit_binary, tmp_path, server, *command)['ok']
    created = cli(orbit_binary, tmp_path, server, 'instances', 'create', '--name', 'cli-test', '--plan', '1', '--image', 'cuda12.8.0-devel-ubuntu24.04', '--hours', '1')['data']
    iid = str(created['instance_id'])
    assert cli(orbit_binary, tmp_path, server, 'instances', 'list')['data'][0]['id'] == int(iid)
    for action in ['show', 'logs', 'connection']:
        assert cli(orbit_binary, tmp_path, server, 'instances', action, iid)['ok']
    assert cli(orbit_binary, tmp_path, server, 'instances', 'wait', iid, '--until', 'ssh-ready', '--timeout', '5')['ok']
    browser = portal.test_client(); db = DB(appmod.DB_PATH)
    with browser.session_transaction() as session:
        session.update(uid=db.user_by_name('alice')['id'], epoch=0, csrf='browser')
    db.close()
    assert 'cli-test' in browser.get('/my').get_data(as_text=True)
    assert cli(orbit_binary, tmp_path, server, 'instances', 'stop', iid, success=2)['code'] == 'configuration_error'
    assert cli(orbit_binary, tmp_path, server, 'instances', 'stop', iid, '--yes')['ok']
    cli(orbit_binary, tmp_path, server, 'logout')
    assert cli(orbit_binary, tmp_path, server, 'whoami', success=3)['code'] == 'authentication_required'
    assert browser.get('/profile').status_code == 200


def test_native_cli_https_verification_and_explicit_http_restriction(portal, serve, orbit_binary, tmp_path, monkeypatch):
    from portalapp import siteconf
    server = serve()
    result = cli(orbit_binary, tmp_path, server, 'login', '--username', 'alice', '--password-stdin', password='TestPass123\n', success=1)
    assert result['code'] == 'https_required'
    openssl = shutil.which('openssl')
    if not openssl:
        pytest.skip('openssl unavailable')
    cert, key = tmp_path / 'test.crt', tmp_path / 'test.key'
    subprocess.run([openssl, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key), '-out', str(cert), '-days', '1', '-subj', '/CN=localhost', '-addext', 'subjectAltName=IP:127.0.0.1,DNS:localhost'], capture_output=True, check=True)
    tls = serve((str(cert), str(key)))
    result = cli(orbit_binary, tmp_path, tls, 'login', '--username', 'alice', '--password-stdin', password='TestPass123\n', success=4)
    assert result['code'] == 'network_error'
    assert cli(orbit_binary, tmp_path, tls, 'login', '--username', 'alice', '--password-stdin', '--ca-bundle', str(cert), password='TestPass123\n')['ok']


def test_existing_web_smoke_with_fully_isolated_readiness_stub():
    script = Path(__file__).with_name('smoke_local.py')
    code = ("import runpy; m=runpy.run_path(" + repr(str(script)) + "); "
            "m['ctlmod'].ssh_ready=lambda user,jobid:{'ok':True,'ready':True}; "
            "raise SystemExit(m['main']())")
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout[-1000:] + result.stderr[-2000:]
    assert 'SMOKE_OK' in result.stdout
