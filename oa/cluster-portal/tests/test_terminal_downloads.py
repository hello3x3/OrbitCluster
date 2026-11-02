from pathlib import Path
from test_terminal_api import portal


def test_downloads_are_public_allowlisted_and_no_traversal(portal, tmp_path, monkeypatch):
    from portalapp import siteconf
    from portalapp.terminal_downloads import ARTIFACTS
    root = tmp_path / 'releases'; root.mkdir()
    monkeypatch.setitem(siteconf.CONF, 'TERMINAL_DOWNLOAD_DIR', str(root))
    client = portal.test_client()
    for route in ['/terminal/install.sh', '/terminal/install.ps1', '/terminal/skill.md']:
        r = client.get(route)
        assert r.status_code == 200
        assert 'Set-Cookie' not in r.headers
    name = sorted(ARTIFACTS)[0]
    assert client.get('/terminal/downloads/' + name).get_json()['code'] == 'client_not_published'
    (root / name).write_bytes(b'client fixture')
    (root / (name + '.sha256')).write_text('0' * 64)
    assert client.get('/terminal/downloads/' + name).data == b'client fixture'
    assert any(a['available'] for a in client.get('/terminal/manifest.json').get_json()['artifacts'])
    assert client.get('/terminal/downloads/config.json').status_code == 404
    assert client.get('/terminal/downloads/..%2Fsecret').status_code == 404
    (root / name).unlink(); (root / name).symlink_to(tmp_path / 'private')
    (tmp_path / 'private').write_text('secret')
    assert client.get('/terminal/downloads/' + name).status_code == 404


def test_login_shell_removed_and_docs_remain(portal):
    text = portal.test_client().get('/login').get_data(as_text=True)
    assert 'id="sidebar"' not in text and 'class="topbar"' not in text
    assert '/guide' in text and 'name="username"' in text and 'name="password"' in text
