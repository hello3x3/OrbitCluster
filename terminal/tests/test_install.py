from pathlib import Path
import subprocess

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
