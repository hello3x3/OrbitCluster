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
