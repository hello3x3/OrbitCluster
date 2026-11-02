"""Real shell bootstrap downloads a native executable; no fake Python/pip boundary."""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import tarfile

import pytest
from test_terminal_integration import orbit_binary, portal, serve

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def release(portal, serve, orbit_binary, tmp_path, monkeypatch):
    from portalapp import siteconf
    downloads = tmp_path / 'downloads'; downloads.mkdir()
    system = 'darwin' if platform.system() == 'Darwin' else 'linux'
    arch = 'arm64' if platform.machine().lower() in ('arm64', 'aarch64') else 'amd64'
    from portalapp.terminal_downloads import CLIENT_VERSION
    name = f'orbit-{CLIENT_VERSION}-{system}-{arch}.tar.gz'
    archive = downloads / name
    with tarfile.open(archive, 'w:gz') as tar:
        tar.add(orbit_binary, arcname='orbit')
    digest = downloads / (name + '.sha256')
    digest.write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + '  ' + name + '\n')
    monkeypatch.setitem(siteconf.CONF, 'TERMINAL_DOWNLOAD_DIR', str(downloads))
    monkeypatch.setitem(siteconf.CONF, 'TERMINAL_REQUIRE_HTTPS', '0')
    return serve(), archive, digest


def install(tmp_path, server):
    env = {**os.environ, 'ORBIT_CONFIG_DIR': str(tmp_path / 'config'), 'ORBIT_SERVER': '', 'ORBIT_CA_BUNDLE': '', 'ORBIT_ALLOW_HTTP': '', 'NO_PROXY': '127.0.0.1'}
    result = subprocess.run(['bash', str(ROOT / 'terminal/install.sh'), '--server', server,
        '--install-dir', str(tmp_path / 'install space'), '--bin-dir', str(tmp_path / 'bin space')], env=env, capture_output=True, text=True, timeout=30)
    return result, env


def test_download_install_login_uninstall(portal, release, tmp_path):
    server, archive, digest = release
    result, env = install(tmp_path, server)
    assert result.returncode == 0, result.stdout + result.stderr
    assert all(f'[{n}/5]' in result.stdout for n in range(1, 6))
    orbit = tmp_path / 'bin space/orbit'
    assert orbit.exists()
    def run(*args, password=None):
        p = subprocess.run([str(orbit), *args, '--json'], input=password, env=env, capture_output=True, text=True)
        assert p.returncode == 0, p.stdout + p.stderr
        return json.loads(p.stdout)
    assert run('login', '--username', 'alice', '--password-stdin', password='TestPass123\n')['ok']
    assert run('whoami')['data']['username'] == 'alice'
    assert run('instances', 'list')['ok']
    run('logout')
    run('uninstall', '--yes', '--purge')
    assert not orbit.exists()
    assert not (tmp_path / 'install space').exists()
    assert not (tmp_path / 'config').exists()


def test_corrupt_download_preserves_existing_install(release, tmp_path):
    server, archive, digest = release
    result, env = install(tmp_path, server)
    assert result.returncode == 0, result.stdout + result.stderr
    original = (tmp_path / 'install space/orbit').read_bytes()
    archive.write_bytes(b'tampered')
    result, _ = install(tmp_path, server)
    assert result.returncode != 0
    assert 'SHA256 校验失败' in result.stderr
    assert (tmp_path / 'install space/orbit').read_bytes() == original


def test_archive_traversal_is_rejected_after_valid_checksum(release, tmp_path):
    import io
    server, archive, digest = release
    with tarfile.open(archive, 'w:gz') as tar:
        info = tarfile.TarInfo('../escaped'); info.size = 4
        tar.addfile(info, io.BytesIO(b'evil'))
    digest.write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + '\n')
    result, _ = install(tmp_path, server)
    assert result.returncode != 0 and '安装包内容不正确' in result.stderr
    assert not (tmp_path / 'install space').exists()


def test_installer_missing_artifact_is_clear_and_no_install(portal, serve, tmp_path, monkeypatch):
    from portalapp import siteconf
    downloads = tmp_path / 'empty'; downloads.mkdir()
    monkeypatch.setitem(siteconf.CONF, 'TERMINAL_DOWNLOAD_DIR', str(downloads))
    result, _ = install(tmp_path, serve())
    assert result.returncode != 0
    assert not (tmp_path / 'install space').exists()


def test_interrupt_restores_password_echo(orbit_binary, tmp_path):
    import pty
    import select
    import signal
    import termios
    import time
    master, slave = pty.openpty()
    env = {**os.environ, 'ORBIT_CONFIG_DIR': str(tmp_path / 'config'), 'ORBIT_SERVER': 'http://127.0.0.1:1'}
    process = subprocess.Popen([str(orbit_binary), 'login', '--username', 'alice'], stdin=slave, stdout=slave, stderr=slave, env=env, close_fds=True)
    output = b''
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and '密码：'.encode() not in output:
            ready, _, _ = select.select([master], [], [], 0.1)
            if ready:
                output += os.read(master, 4096)
        assert '密码：'.encode() in output
        assert not (termios.tcgetattr(slave)[3] & termios.ECHO)
        os.kill(process.pid, signal.SIGINT)
        assert process.wait(timeout=5) == 130
        assert termios.tcgetattr(slave)[3] & termios.ECHO
    finally:
        if process.poll() is None:
            process.kill(); process.wait()
        os.close(master); os.close(slave)


def test_default_uninstall_keeps_settings_and_purge_rejects_other_directory(release, tmp_path):
    server, _, _ = release
    result, env = install(tmp_path, server)
    assert result.returncode == 0, result.stdout + result.stderr
    orbit = tmp_path / 'bin space/orbit'
    other = tmp_path / 'other-app'; other.mkdir()
    (other / 'config.json').write_text('important unrelated config')
    refused = subprocess.run([str(orbit), 'uninstall', '--purge', '--yes', '--config-dir', str(other), '--json'], env=env, capture_output=True, text=True)
    assert refused.returncode == 2
    assert (other / 'config.json').read_text() == 'important unrelated config'
    assert orbit.exists()
    removed = subprocess.run([str(orbit), 'uninstall', '--yes', '--json'], env=env, capture_output=True, text=True)
    assert removed.returncode == 0, removed.stdout + removed.stderr
    assert json.loads(removed.stdout)['data']['config_preserved']
    assert (tmp_path / 'config/config.json').exists()
    assert not orbit.exists()
