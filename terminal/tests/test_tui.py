import asyncio
import time

from textual.widgets import Input, DataTable, TabbedContent, Button

from orbitcluster.client import AuthenticationRequired
from orbitcluster.config import Settings
from orbitcluster.tui import OrbitApp, LoginScreen, MainScreen


class FakeTuiClient:
    def __init__(self, settings, logged_in=True):
        self.settings = settings
        self.logged_in = logged_in
        self.calls = []
        self.rows = [self.row(1), self.row(2)]
        self.expired = False
    def row(self, iid):
        return {"id": iid, "job_id": 100 + iid, "res_name": f"experiment-{iid}", "state": "RUNNING",
                "state_cn": "运行中", "node_cn": "gpu01", "deadline_cn": "2026-11-08 12:00",
                "account": "lab", "last_save": "", "startup_error": "", "saving_now": False,
                "can_stop": True, "plan_name": "gpu-1", "image_name": "cuda.sqsh"}
    def identity(self):
        if not self.logged_in:
            raise AuthenticationRequired()
        return {"username": "alice", "expires_at": int(time.time()) + 604800}
    def login(self, username, password, device=""):
        assert username == "alice" and password == "secret"
        self.logged_in = True
        return self.identity()
    def logout(self, local=False):
        self.logged_in = False
        return {"logged_out": True}
    def close(self):
        pass
    def request(self, method, path, data=None, **kw):
        if self.expired:
            raise AuthenticationRequired("登录已到期")
        self.calls.append((method, path, data))
        if method == "POST":
            return {"instance_id": 3}
        if path == "/instances":
            return self.rows
        if path == "/plans":
            return [{"id": 1, "name": "gpu-1", "gpus": 1, "cpus": 4, "mem_gb": 16, "maxtime_h": 4}]
        if path == "/images":
            return {"public": [{"name": "cuda.sqsh", "path": "/images/cuda.sqsh", "scope": "public"}], "mine": []}
        if path == "/accounts":
            return {"default": "lab", "accounts": [{"name": "lab", "priority": 5, "per_user_gpu_cap": 8}]}
        if path == "/nodes":
            return {"nodes": [{"name": "gpu01", "state": "idle", "cpus_idle": 16, "cpus_total": 16,
                               "mem_free_mb": 32000, "mem_mb": 32000, "gres_free": 4, "gres_total": 4}]}
        if path == "/profile":
            return {"username": "alice", "display_name": "Alice", "os_ok": True, "keys": []}
        if path == "/quota":
            return {"disk": {"used_h": "1 GiB", "hard_h": "100 GiB"}, "accounts": []}
        return {"ready": True, "command": "ssh -p 12345 alice@10.0.0.2", "text": "test log", "events": []}


async def settle(app, pilot):
    for _ in range(3):
        await pilot.pause()
        await app.workers.wait_for_complete()


def make_app(tmp_path, logged_in=True):
    settings = Settings.load({"server": "https://portal.test", "config_dir": tmp_path, "poll_seconds": 3600}, environ={})
    client = FakeTuiClient(settings, logged_in)
    return OrbitApp(settings, client=client), client


def test_terminal_login_and_password_clear(tmp_path):
    async def scenario():
        app, client = make_app(tmp_path, False)
        async with app.run_test(size=(120, 50)) as pilot:
            await settle(app, pilot)
            assert isinstance(app.screen, LoginScreen)
            app.screen.query_one("#username", Input).value = "alice"
            app.screen.query_one("#password", Input).value = "secret"
            await pilot.click("#login")
            await settle(app, pilot)
            assert isinstance(app.screen, MainScreen)
            assert app.screen.query_one("#instances", DataTable).row_count == 2
            assert client.logged_in
    asyncio.run(scenario())


def test_refresh_preserves_selection_and_untrusted_text(tmp_path):
    async def scenario():
        app, client = make_app(tmp_path)
        async with app.run_test(size=(120, 50)) as pilot:
            await settle(app, pilot)
            table = app.screen.query_one("#instances", DataTable)
            table.move_cursor(row=1)
            client.rows = [client.row(3), client.row(2), client.row(1)]
            client.rows[1]["res_name"] = "[bold]literal[/bold]"
            app.screen.refresh_active()
            await settle(app, pilot)
            assert app.screen.selected_instance()["id"] == 2
            assert table.get_cell("2", "task").plain == "[bold]literal[/bold]"
    asyncio.run(scenario())


def test_apply_uses_loaded_plan_image_account(tmp_path):
    async def scenario():
        app, client = make_app(tmp_path)
        async with app.run_test(size=(120, 60)) as pilot:
            await settle(app, pilot)
            app.screen.query_one("#tabs", TabbedContent).active = "apply"
            await settle(app, pilot)
            app.screen.query_one("#task-name", Input).value = "experiment"
            app.screen.query_one("#hours", Input).value = "4"
            app.screen.query_one("#create", Button).scroll_visible(immediate=True)
            await pilot.pause()
            await pilot.click("#create")
            await settle(app, pilot)
            post = [c for c in client.calls if c[0] == "POST"]
            assert post[-1] == ("POST", "/instances", {"task_name": "experiment", "plan_id": "1",
                "image": "/images/cuda.sqsh", "hours": "4", "account": "lab", "node": ""})
    asyncio.run(scenario())


def test_stop_requires_confirmation(tmp_path):
    async def scenario():
        app, client = make_app(tmp_path)
        async with app.run_test(size=(120, 50)) as pilot:
            await settle(app, pilot)
            await pilot.click("#resource-stop")
            await pilot.pause()
            assert not [c for c in client.calls if c[0] == "POST"]
            await pilot.click("#confirm-ok")
            await settle(app, pilot)
            assert ("POST", "/instances/1/stop", None) in client.calls
    asyncio.run(scenario())


def test_expired_auth_returns_to_login(tmp_path):
    async def scenario():
        app, client = make_app(tmp_path)
        async with app.run_test(size=(100, 40)) as pilot:
            await settle(app, pilot)
            client.expired = True
            app.screen.refresh_active()
            await settle(app, pilot)
            assert isinstance(app.screen, LoginScreen)
            assert app.screen.query_one("#password", Input).value == ""
    asyncio.run(scenario())
