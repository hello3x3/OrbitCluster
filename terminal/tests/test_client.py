import json
import os
from pathlib import Path
import stat
import time

import httpx
import pytest

from orbitcluster.config import Settings, ConfigError
from orbitcluster.credentials import CredentialStore
from orbitcluster.client import Client, ApiError, AuthenticationRequired, NetworkError


def settings(tmp_path, **kw):
    return Settings.load({"config_dir": tmp_path, "server": "https://portal.test", **kw}, environ={})


def credential(**kw):
    return {"token": "T" * 43, "username": "alice", "issued_at": int(time.time()),
            "expires_at": int(time.time()) + 604800, **kw}


def test_config_priority_and_no_hardcoded_site(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"server": "https://file.test", "timeout": 30}))
    env = {"ORBIT_CONFIG_DIR": str(tmp_path), "ORBIT_SERVER": "https://env.test", "ORBIT_TIMEOUT": "60"}
    s = Settings.load({"server": "https://flag.test"}, environ=env)
    assert s.server == "https://flag.test" and s.timeout == 60
    assert Settings.load(environ={}).server == ""


@pytest.mark.parametrize("url", ["https://user:password@portal.test", "https://portal.test?token=secret",
                                    "https://portal.test#secret", "file:///tmp/x", "http://10.0.0.1"])
def test_unsafe_server_urls_rejected(tmp_path, url):
    with pytest.raises(ConfigError):
        settings(tmp_path, server=url)


def test_loopback_http_requires_explicit_opt_in(tmp_path):
    with pytest.raises(ConfigError):
        settings(tmp_path, server="http://127.0.0.1:8888")
    assert settings(tmp_path, server="http://127.0.0.1:8888", allow_http=True).allow_http
    with pytest.raises(ConfigError):
        settings(tmp_path, server="http://10.0.0.1", allow_http=True)


def test_credentials_private_atomic_and_scoped(tmp_path):
    root = tmp_path / "private"
    store = CredentialStore(root)
    store.save("https://first.test", credential())
    store.save("https://second.test", credential(username="bob"))
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "auth.json").stat().st_mode) == 0o600
    assert store.get("https://first.test")["username"] == "alice"
    assert store.get("https://unknown.test") is None
    store.delete("https://first.test")
    assert store.get("https://first.test") is None
    assert store.get("https://second.test")["username"] == "bob"
    assert not list(root.glob("*.tmp"))


def test_credentials_reject_symlink_and_insecure_file(tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("original")
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    (root / "auth.json").symlink_to(victim)
    store = CredentialStore(root)
    with pytest.raises(ConfigError):
        store.save("https://portal.test", credential())
    assert victim.read_text() == "original"
    (root / "auth.json").unlink()
    (root / "auth.json").write_text("{}")
    os.chmod(root / "auth.json", 0o644)
    with pytest.raises(ConfigError):
        store.get("https://portal.test")


def test_login_stores_token_but_does_not_return_secret_or_send_cookie(tmp_path):
    seen = []
    def serve(request):
        seen.append(request)
        assert "cookie" not in request.headers
        if request.url.path.endswith("/auth/login"):
            assert json.loads(request.content)["password"] == "secret"
            return httpx.Response(200, json={"ok": True, "data": credential()},
                                  headers={"set-cookie": "unwanted=value"})
        assert request.headers["Authorization"] == "Bearer " + "T" * 43
        return httpx.Response(200, json={"ok": True, "data": {"username": "alice"}})
    client = Client(settings(tmp_path), transport=httpx.MockTransport(serve))
    d = client.login("alice", "secret")
    assert "token" not in d and "secret" not in json.dumps(d)
    assert client.identity()["username"] == "alice"
    assert len(seen) == 2
    client.close()


def test_expired_local_auth_prompts_login_without_network(tmp_path):
    s = settings(tmp_path)
    CredentialStore(s.config_dir).save(s.server, credential(expires_at=1))
    def forbidden(request):
        pytest.fail("expired credential was sent")
    with Client(s, transport=httpx.MockTransport(forbidden)) as client:
        with pytest.raises(AuthenticationRequired):
            client.identity()


def test_unauthorized_clears_only_current_server(tmp_path):
    s = settings(tmp_path)
    store = CredentialStore(s.config_dir)
    store.save(s.server, credential())
    store.save("https://another.test", credential())
    with Client(s, transport=httpx.MockTransport(lambda r: httpx.Response(401,
        json={"ok": False, "code": "authentication_required", "error": "expired"}))) as client:
        with pytest.raises(AuthenticationRequired):
            client.identity()
    assert store.get(s.server) is None and store.get("https://another.test")


@pytest.mark.parametrize("response", [httpx.Response(302, headers={"location": "https://other.test"}),
    httpx.Response(200, text="<html>Login</html>"), httpx.Response(200, json=[1, 2])])
def test_redirects_and_non_api_responses_fail_clearly(tmp_path, response):
    s = settings(tmp_path)
    CredentialStore(s.config_dir).save(s.server, credential())
    requests = []
    def serve(r):
        requests.append(r)
        return response
    with Client(s, transport=httpx.MockTransport(serve)) as client:
        with pytest.raises(ApiError):
            client.identity()
    assert len(requests) == 1


def test_write_timeout_not_retried(tmp_path):
    s = settings(tmp_path)
    CredentialStore(s.config_dir).save(s.server, credential())
    count = []
    def serve(r):
        count.append(r)
        raise httpx.ReadTimeout("timeout")
    with Client(s, transport=httpx.MockTransport(serve)) as client:
        with pytest.raises(NetworkError, match="核实"):
            client.request("POST", "/instances", data={"task_name": "test"})
    assert len(count) == 1


def test_api_rejection_with_http_200_is_failure(tmp_path):
    s = settings(tmp_path)
    CredentialStore(s.config_dir).save(s.server, credential())
    with Client(s, transport=httpx.MockTransport(lambda r: httpx.Response(200,
        json={"ok": False, "error": "Invalid plan", "code": "operation_rejected"}))) as client:
        with pytest.raises(ApiError, match="Invalid plan"):
            client.request("POST", "/instances")
