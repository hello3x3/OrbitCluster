import json
import sys
from io import StringIO

from orbitcluster import cli
from orbitcluster.client import AuthenticationRequired


class FakeClient:
    calls = []
    def __init__(self, settings):
        self.settings = settings
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def identity(self):
        return {"username": "alice", "expires_at": 2000000000}
    def login(self, username, password, device=""):
        assert password == "TestPass123"
        return self.identity()
    def request(self, method, path, data=None, **kwargs):
        self.calls.append((method, path, data))
        if path == "/plans":
            return [{"id": 1, "name": "gpu-1"}]
        if path == "/images":
            return {"public": [{"name": "cuda.sqsh", "path": "/images/cuda.sqsh"}], "mine": []}
        return {"instance_id": 123} if method == "POST" else [{"id": 123}]


def setup(monkeypatch, tmp_path):
    monkeypatch.setenv("ORBIT_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("ORBIT_SERVER", "https://portal.test")
    FakeClient.calls = []


def test_cli_json_and_global_flags_after_subcommand(monkeypatch, tmp_path, capsys):
    setup(monkeypatch, tmp_path)
    assert cli.main(["instances", "list", "--json"], client_factory=FakeClient) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["data"] == [{"id": 123}]
    assert output.err == ""


def test_login_json_never_contains_credentials(monkeypatch, tmp_path, capsys):
    setup(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "stdin", StringIO("TestPass123\n"))
    assert cli.main(["login", "--username", "alice", "--password-stdin", "--json"], client_factory=FakeClient) == 0
    output = capsys.readouterr()
    assert "TestPass123" not in output.out + output.err
    assert "token" not in json.loads(output.out)["data"]


def test_create_resolves_names_without_changing_resource_rules(monkeypatch, tmp_path, capsys):
    setup(monkeypatch, tmp_path)
    assert cli.main(["instances", "create", "--name", "experiment", "--plan", "gpu-1",
        "--image", "cuda.sqsh", "--hours", "4", "--json"], client_factory=FakeClient) == 0
    assert FakeClient.calls[-1] == ("POST", "/instances", {"task_name": "experiment", "plan_id": 1,
        "image": "/images/cuda.sqsh", "hours": 4, "node": "", "account": ""})


def test_noninteractive_destructive_operation_needs_yes(monkeypatch, tmp_path, capsys):
    setup(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "stdin", StringIO())
    assert cli.main(["instances", "stop", "123", "--json"], client_factory=FakeClient) == 2
    assert not FakeClient.calls
    assert cli.main(["instances", "stop", "123", "--yes", "--json"], client_factory=FakeClient) == 0


def test_auth_failure_has_machine_readable_stderr_and_exit_code(monkeypatch, tmp_path, capsys):
    setup(monkeypatch, tmp_path)
    class Expired(FakeClient):
        def identity(self):
            raise AuthenticationRequired("请重新登录")
    assert cli.main(["whoami", "--json"], client_factory=Expired) == 3
    output = capsys.readouterr()
    assert output.out == ""
    assert json.loads(output.err)["code"] == "authentication_required"


def test_interactive_http_server_prompt_persists_opt_in(monkeypatch, tmp_path, capsys):
    from orbitcluster.config import Settings
    setup(monkeypatch, tmp_path)
    monkeypatch.delenv('ORBIT_SERVER')
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr('builtins.input', lambda prompt: 'http://10.71.106.251')
    monkeypatch.setattr(cli.getpass, 'getpass', lambda prompt: 'TestPass123')
    assert cli.main(['login', '--username', 'alice', '--allow-http'], client_factory=FakeClient) == 0
    loaded = Settings.load({'config_dir': tmp_path}, environ={})
    assert loaded.allow_http and loaded.server == 'http://10.71.106.251'
