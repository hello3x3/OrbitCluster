from pathlib import Path
import subprocess
import sys

INSTALLER = Path(__file__).resolve().parents[1] / "install.sh"


def test_installer_help_documents_local_source_and_configuration():
    p = subprocess.run(["bash", str(INSTALLER), "--help"], capture_output=True, text=True)
    assert p.returncode == 0
    for option in ("--source", "--server", "--ca-bundle", "--install-dir", "--bin-dir", "--ref"):
        assert option in p.stdout


def test_installer_bad_argument_fails_without_installing():
    p = subprocess.run(["bash", str(INSTALLER), "--unknown"], capture_output=True, text=True)
    assert p.returncode == 2
    assert "unknown" in p.stderr


def test_bare_local_source_is_installed_as_directory_not_package_name(tmp_path):
    # Exercise the real shell installer with a Python/pip boundary fake. Bare
    # pip requirements like `terminal` must never be resolved from an index.
    fake = tmp_path / "fake-python"
    fake.write_text("#!" + sys.executable + "\n" + '''
import os, pathlib, sys
args = sys.argv[1:]
if args[:2] == ['-m', 'venv']:
    exe = pathlib.Path(args[2]) / 'bin' / 'python'
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text(pathlib.Path(__file__).read_text())
    exe.chmod(0o755)
elif args[:2] == ['-m', 'pip']:
    source = pathlib.Path(args[-1])
    if not source.is_absolute() or not (source / 'pyproject.toml').is_file():
        raise SystemExit(22)
elif args[:2] == ['-m', 'orbitcluster']:
    print('orbit 0.1.1')
else:
    os.execv(sys.executable, [sys.executable] + args)
''')
    fake.chmod(0o755)
    p = subprocess.run(['bash', str(INSTALLER), '--source', 'terminal', '--python', str(fake),
        '--install-dir', str(tmp_path / 'install with spaces'), '--bin-dir', str(tmp_path / 'bin')],
        cwd=INSTALLER.parent.parent, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    assert (tmp_path / 'bin' / 'orbit').is_file()


def test_http_installer_requires_explicit_flag_before_installing(tmp_path):
    target = tmp_path / 'install'
    result = subprocess.run(['bash', str(INSTALLER), '--server', 'http://10.71.106.251',
                             '--install-dir', str(target)], capture_output=True, text=True)
    assert result.returncode == 2
    assert '--allow-http' in result.stderr
    assert not target.exists()


def test_http_installer_persists_permission_before_server(tmp_path):
    import os
    fake = tmp_path / 'fake-python'
    fake.write_text('#!' + sys.executable + '\n' + '''
import os, pathlib, sys
args = sys.argv[1:]
if args[:2] == ['-m', 'venv']:
    target = pathlib.Path(args[2]) / 'bin/python'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(pathlib.Path(__file__).read_text()); target.chmod(0o755)
elif args[:2] == ['-m', 'pip']:
    pass
elif args[:2] == ['-m', 'orbitcluster']:
    sys.path.insert(0, os.environ['ORBIT_TEST_SRC'])
    from orbitcluster.cli import main
    raise SystemExit(main(args[2:]))
else:
    os.execv(sys.executable, [sys.executable] + args)
''')
    fake.chmod(0o755)
    config = tmp_path / 'config'
    env = {**os.environ, 'ORBIT_CONFIG_DIR': str(config), 'ORBIT_TEST_SRC': str(INSTALLER.parent / 'src')}
    for key in ['ORBIT_SERVER', 'ORBIT_ALLOW_HTTP', 'ORBIT_CA_BUNDLE']:
        env.pop(key, None)
    result = subprocess.run(['bash', str(INSTALLER), '--source', str(INSTALLER.parent),
        '--server', 'http://10.71.106.251', '--allow-http', '--python', str(fake),
        '--install-dir', str(tmp_path / 'install'), '--bin-dir', str(tmp_path / 'bin')],
        env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    from orbitcluster.config import Settings
    loaded = Settings.load({'config_dir': config}, environ={})
    assert loaded.allow_http is True and loaded.server == 'http://10.71.106.251'
