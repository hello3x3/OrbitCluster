"""Chinese user interface. All network work runs outside the UI event loop."""
import asyncio
from dataclasses import replace
from datetime import datetime
import json
from urllib.parse import quote

from rich.markup import escape
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen, ModalScreen
from textual.widgets import (Button, Checkbox, DataTable, Footer, Header, Input,
                             Label, Select, Static, TabbedContent, TabPane, TextArea)

from .client import Client, ApiError, AuthenticationRequired
from .config import ConfigError, normalize_server


class ConfirmDialog(ModalScreen):
    BINDINGS = [("escape", "cancel", "取消")]

    def __init__(self, text):
        super().__init__()
        self.text = text

    def compose(self):
        with Vertical(classes="dialog"):
            yield Label("确认操作", classes="dialog-title")
            yield Static(self.text, markup=False)
            with Horizontal(classes="buttons"):
                yield Button("取消", id="confirm-cancel")
                yield Button("确认", variant="error", id="confirm-ok")

    @on(Button.Pressed)
    def clicked(self, event):
        self.dismiss(event.button.id == "confirm-ok")

    def action_cancel(self):
        self.dismiss(False)


class FormDialog(ModalScreen):
    BINDINGS = [("escape", "cancel", "取消")]

    def __init__(self, title, fields, note="", force=False):
        super().__init__()
        self.heading, self.fields, self.note, self.force = title, fields, note, force

    def compose(self):
        with Vertical(classes="dialog"):
            yield Label(self.heading, classes="dialog-title", markup=False)
            if self.note:
                yield Static(self.note, markup=False)
            for key, label, password in self.fields:
                yield Label(label, markup=False)
                yield Input(id="field-" + key, password=password)
            if self.force:
                yield Checkbox("覆盖同名镜像（会替换已有文件）", id="force")
            with Horizontal(classes="buttons"):
                yield Button("取消", id="form-cancel")
                yield Button("提交", variant="primary", id="form-ok")

    @on(Button.Pressed)
    def clicked(self, event):
        if event.button.id == "form-cancel":
            self.dismiss(None)
        elif event.button.id == "form-ok":
            data = {key: self.query_one("#field-" + key, Input).value for key, _, _ in self.fields}
            if self.force:
                data["force"] = "1" if self.query_one("#force", Checkbox).value else "0"
            self.dismiss(data)

    def action_cancel(self):
        self.dismiss(None)


class TextDialog(ModalScreen):
    BINDINGS = [("escape", "close", "关闭")]

    def __init__(self, title, text):
        super().__init__()
        self.heading, self.text = title, text

    def compose(self):
        with Vertical(classes="text-dialog"):
            yield Label(self.heading, classes="dialog-title", markup=False)
            yield TextArea(self.text, read_only=True, id="dialog-text")
            yield Button("关闭", id="text-close", variant="primary")

    @on(Button.Pressed, "#text-close")
    def action_close(self):
        self.dismiss(None)


class LoginScreen(Screen):
    def __init__(self, server="", message="登录凭证保存在当前用户目录；密码不落盘。"):
        super().__init__()
        self.server, self.message = server, message

    def compose(self):
        yield Header()
        with Vertical(id="login-panel"):
            yield Label("OrbitCluster", id="login-brand")
            yield Static("实验室计算资源 · 用户终端", classes="muted")
            yield Label("服务器 HTTPS 地址")
            yield Input(self.server, placeholder="https://集群地址", id="server")
            yield Label("用户名")
            yield Input(id="username")
            yield Label("密码")
            yield Input(password=True, id="password")
            yield Button("登录", id="login", variant="primary")
            yield Static(self.message, id="login-status", markup=False)
        yield Footer()

    @on(Button.Pressed, "#login")
    @on(Input.Submitted, "#password")
    def submit_login(self):
        server = self.query_one("#server", Input).value
        username = self.query_one("#username", Input).value.strip()
        password = self.query_one("#password", Input).value
        if not server or not username or not password:
            self.query_one("#login-status", Static).update("请填写服务器地址、用户名和密码")
            return
        self.query_one("#login", Button).disabled = True
        self.query_one("#password", Input).value = ""
        self.query_one("#login-status", Static).update("正在验证账号…")
        self.app.perform_login(server, username, password)


class MainScreen(Screen):
    def __init__(self, identity):
        super().__init__()
        self.identity = identity
        self.instances = {}
        self.images = {}
        self.plans = []
        self._refreshing = False
        self._writing = False
        self._inactive = False

    def compose(self):
        yield Header(show_clock=True)
        with Horizontal(id="session-bar"):
            expires = datetime.fromtimestamp(self.identity["expires_at"]).astimezone().strftime("%Y-%m-%d %H:%M %Z")
            yield Static(f'{self.identity["username"]}  ·  登录有效至 {expires}', id="identity", markup=False)
            yield Button("注销此终端", id="logout")
        yield Static(self.app.settings.server, id="server-label", markup=False)
        with TabbedContent(id="tabs"):
            with TabPane("我的资源", id="resources"):
                with Horizontal(classes="buttons resource-buttons"):
                    for key, label in [("refresh", "刷新"), ("connect", "SSH 连接"), ("logs", "日志"),
                                       ("save", "保存镜像"), ("stop", "停机"), ("restart", "重启"), ("delete", "删除记录")]:
                        yield Button(label, id="resource-" + key, variant="error" if key in ("stop", "delete") else "default")
                yield DataTable(id="instances", cursor_type="row", zebra_stripes=True)
                yield Static("选择资源后，可查看连接信息与执行操作。", id="resource-detail", markup=False)
            with TabPane("申请资源", id="apply"):
                with VerticalScroll(classes="form-area"):
                    yield Label("任务名称（沿用门户规则，3–10 个字符）")
                    yield Input(placeholder="experiment", id="task-name")
                    yield Label("镜像")
                    yield Select([], prompt="加载镜像…", id="apply-image")
                    yield Label("资源套餐")
                    yield Select([], prompt="加载套餐…", id="apply-plan")
                    yield Static("", id="plan-description", markup=False)
                    yield Label("计费账户")
                    yield Select([], prompt="加载账户…", id="apply-account")
                    yield Label("时长（小时）")
                    yield Input("4", type="integer", id="hours")
                    yield Label("节点")
                    yield Select([("自动分配（推荐）", "")], value="", id="apply-node")
                    yield Static("SSH 端口由平台分配。申请条件、额度与调度遵循现有门户规则。", classes="muted")
                    yield Button("提交申请", id="create", variant="primary")
            with TabPane("镜像", id="image-tab"):
                with Horizontal(classes="buttons"):
                    yield Button("刷新", id="image-refresh")
                    yield Button("改名", id="image-rename")
                    yield Button("注释", id="image-comment")
                    yield Button("删除个人镜像", id="image-delete", variant="error")
                yield DataTable(id="images", cursor_type="row", zebra_stripes=True)
                yield Static("公共镜像只读；个人镜像的占用保护与网页一致。", classes="muted")
            with TabPane("集群", id="cluster"):
                yield Button("刷新节点", id="node-refresh")
                yield DataTable(id="nodes", cursor_type="row", zebra_stripes=True)
                yield Static("空闲数量来自 Slurm 调度分配，不代表设备即时利用率。", classes="muted")
            with TabPane("配额与账户", id="quota-tab"):
                with VerticalScroll():
                    yield Button("刷新", id="quota-refresh")
                    yield Static("加载中…", id="quota", markup=False)
            with TabPane("个人资料", id="profile-tab"):
                with VerticalScroll():
                    yield Static("加载中…", id="profile", markup=False)
                    with Horizontal(classes="buttons"):
                        yield Button("刷新", id="profile-refresh")
                        yield Button("添加公钥", id="key-add")
                        yield Button("删除选中公钥", id="key-delete", variant="error")
                        yield Button("修改显示名", id="profile-name")
                        yield Button("修改密码", id="profile-password")
                    yield DataTable(id="keys", cursor_type="row", zebra_stripes=True)
        yield Static("准备就绪", id="status", markup=False)
        yield Footer()

    def on_mount(self):
        columns = {
            "instances": [("ID", "id"), ("任务", "task"), ("状态", "state"), ("节点", "node"), ("账户", "account"), ("截止时间", "deadline")],
            "images": [("来源", "scope"), ("名称", "name"), ("大小", "size"), ("占用", "active"), ("注释", "note")],
            "nodes": [("节点", "name"), ("状态", "state"), ("CPU 空闲/总", "cpu"), ("内存 GiB 空闲/总", "memory"), ("GPU 空闲/总", "gpu")],
            "keys": [("ID", "id"), ("标签", "label"), ("SSH 公钥", "pubkey")],
        }
        for name, cols in columns.items():
            for label, key in cols:
                self.query_one("#" + name, DataTable).add_column(label, key=key)
        self._refresh_timer = self.set_interval(self.app.settings.poll_seconds, self.refresh_active)
        self.refresh_active()

    def deactivate(self):
        """Stop authenticated activity before removing screens or their modals."""
        self._inactive = True
        self._refresh_timer.stop()
        self.app.workers.cancel_node(self)

    def notice(self, text, error=False):
        self.app.notify(escape(str(text)), severity="error" if error else "information")

    def selected_key(self, table_id):
        table = self.query_one("#" + table_id, DataTable)
        if not table.row_count:
            return None
        return table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value

    def selected_instance(self):
        return self.instances.get(self.selected_key("instances"))

    def draw_table(self, name, rows):
        table = self.query_one("#" + name, DataTable)
        selected = self.selected_key(name)
        table.clear()
        keys = []
        for key, values in rows:
            keys.append(str(key))
            table.add_row(*(Text(str(v if v is not None else "—")) for v in values), key=str(key))
        if selected in keys:
            table.move_cursor(row=keys.index(selected), animate=False)

    @on(TabbedContent.TabActivated, "#tabs")
    def tab_changed(self):
        self.refresh_active()

    @work(group="refresh", exclusive=False)
    async def refresh_active(self):
        if self._refreshing or self._inactive:
            return
        self._refreshing = True
        pane = self.query_one("#tabs", TabbedContent).active
        status = self.query_one("#status", Static)
        status.update("正在读取服务器…")
        try:
            await self.load_pane(pane)
            status.update("更新于 " + datetime.now().strftime("%H:%M:%S") + "  ·  r 刷新 / Ctrl+Q 退出，作业继续运行")
        except AuthenticationRequired:
            self.app.require_login("登录已到期或撤销，请重新登录。")
        except (ApiError, ConfigError) as exc:
            status.update(str(exc))
            self.notice(exc, True)
        finally:
            self._refreshing = False
            # A tab can change while its predecessor's request is in flight.
            if self.is_mounted and not self._inactive and self.query_one("#tabs", TabbedContent).active != pane:
                self.refresh_active()

    async def fetch(self, path):
        return await asyncio.to_thread(self.app.client.request, "GET", path)

    async def load_pane(self, pane):
        if pane == "resources":
            rows = await self.fetch("/instances")
            self.instances = {str(i["id"]): i for i in rows}
            self.draw_table("instances", [(i["id"], [i["id"], i["res_name"], i["state_cn"],
                i["node_cn"], i.get("account", ""), i.get("deadline_cn", "")]) for i in rows])
            self.resource_details()
        elif pane == "apply":
            self.plans = await self.fetch("/plans")
            groups = await self.fetch("/images")
            accounts = await self.fetch("/accounts")
            nodes = await self.fetch("/nodes")
            self.set_options("apply-plan", [(f'{p["name"]} · 最长 {p["maxtime_h"]} 小时', str(p["id"])) for p in self.plans])
            self.set_options("apply-image", [(f'公共 · {p["name"]}', p["path"]) for p in groups.get("public", [])]
                + [(f'个人 · {p["name"]}', p["path"]) for p in groups.get("mine", [])])
            self.set_options("apply-account", [(p["name"], p["name"]) for p in accounts["accounts"]], accounts.get("default"))
            self.set_options("apply-node", [("自动分配（推荐）", "")] + [(p["name"], p["name"]) for p in nodes.get("nodes", [])], "")
            self.plan_changed()
        elif pane == "image-tab":
            groups = await self.fetch("/images")
            rows = [(scope + ":" + p["name"], {**p, "local_scope": scope})
                    for scope in ("public", "mine") for p in groups.get(scope, [])]
            self.images = dict(rows)
            self.draw_table("images", [(key, ["公共" if p["local_scope"] == "public" else "个人", p["name"],
                p.get("size_cn", "—"), p.get("active", 0), p.get("note", "")]) for key, p in rows])
        elif pane == "cluster":
            result = await self.fetch("/nodes")
            rows = result.get("nodes", [])
            memory = lambda value: "—" if value is None else str(round(value / 1024))
            self.draw_table("nodes", [(n["name"], [n["name"], n["state"],
                f'{n.get("cpus_idle", "—")}/{n.get("cpus_total", "—")}',
                f'{memory(n.get("mem_free_mb"))}/{memory(n.get("mem_mb"))}',
                f'{n.get("gres_free", "—")}/{n.get("gres_total", "—")}']) for n in rows])
        elif pane == "quota-tab":
            q = await self.fetch("/quota")
            disk = q.get("disk") or {}
            lines = ["磁盘配额 /share", f'已用：{disk.get("used_h", "—")}   硬上限：{disk.get("hard_h", "—")}', "", "账户额度"]
            for a in q.get("accounts", []):
                lines.append(f'{a["name"]}  优先级 {a["priority"]}  单用户 GPU 上限 {a["per_user_gpu_cap"] or "不限"}')
            for field in ("disk_err", "slurm_err", "note"):
                if q.get(field):
                    lines.append(q[field])
            self.query_one("#quota", Static).update("\n".join(lines))
        elif pane == "profile-tab":
            p = await self.fetch("/profile")
            self.query_one("#profile", Static).update(f'{p["username"]} · {p["display_name"]}\nSSH 公钥用于登录容器；私钥始终留在用户机器。')
            self.draw_table("keys", [(k["id"], [k["id"], k["label"], k["pubkey"]]) for k in p["keys"]])

    def set_options(self, name, options, default=None):
        widget = self.query_one("#" + name, Select)
        previous = widget.value
        widget.set_options([(Text(str(label)), value) for label, value in options])
        values = [value for _, value in options]
        widget.value = previous if previous in values else default if default in values else values[0] if values else Select.BLANK

    @on(Select.Changed, "#apply-plan")
    def plan_changed(self):
        selected = self.query_one("#apply-plan", Select).value
        plan = next((p for p in self.plans if str(p["id"]) == selected), None)
        if plan:
            self.query_one("#plan-description", Static).update(
                f'{plan["gpus"]} GPU · {plan["cpus"]} CPU · {plan["mem_gb"]} GiB · 最长 {plan["maxtime_h"]} 小时')
            hours = self.query_one("#hours", Input)
            if hours.value.isdigit() and int(hours.value) > plan["maxtime_h"]:
                hours.value = str(plan["maxtime_h"])

    @on(DataTable.RowHighlighted, "#instances")
    def resource_details(self):
        row = self.selected_instance()
        text = "还没有资源，可在“申请资源”中提交。"
        if row:
            text = f'作业 #{row["job_id"]} · {row.get("plan_name", "")} · {row.get("image_name", "")}'
            if row.get("last_save"):
                text += "\n" + row["last_save"]
            if row.get("startup_error"):
                text += "\n" + row["startup_error"]
        self.query_one("#resource-detail", Static).update(text)

    def confirm_write(self, text, path, data=None):
        self.app.push_screen(ConfirmDialog(text), lambda yes: self.start_write(path, data) if yes else None)

    def form_write(self, title, fields, path, *, note="", force=False):
        self.app.push_screen(FormDialog(title, fields, note, force),
            lambda data: self.start_write(path, data) if data is not None else None)

    def start_write(self, path, data=None):
        if self._inactive:
            return
        if self._writing:
            self.notice("上一项操作尚未完成，请稍候", True)
            return
        self._writing = True
        for button in self.query(Button):
            button.disabled = True
        self.write_operation(path, data)

    @work(group="operation")
    async def write_operation(self, path, data):
        try:
            await asyncio.to_thread(self.app.client.request, "POST", path, data=data)
            if path == "/profile/password":
                await asyncio.to_thread(self.app.client.logout, local=True)
                self.app.require_login("密码已修改，请使用新密码重新登录。")
                return
            self.notice("操作已提交；长任务请查看资源状态和日志。")
            if path == "/instances":
                self.query_one("#tabs", TabbedContent).active = "resources"
            self.refresh_active()
        except AuthenticationRequired:
            self.app.require_login("登录已到期或撤销，请重新登录。")
        except (ApiError, ConfigError) as exc:
            self.notice(exc, True)
        finally:
            self._writing = False
            if self.is_mounted:
                for button in self.query(Button):
                    button.disabled = False

    @work(group="details", exclusive=True)
    async def show_resource_text(self, iid, logs):
        if self._inactive:
            return
        try:
            d = await self.fetch(f"/instances/{iid}/" + ("log" if logs else "connection"))
            if logs:
                events = "\n".join(f'{e["ts"]} [{e.get("label", "")}] {e["message"]}' for e in d.get("events", []))
                text = (d.get("text") or d.get("log_error") or "暂无容器日志") + "\n\n门户操作记录\n" + events
            else:
                text = d.get("command") or d.get("error") or "SSH 尚未就绪，请稍后刷新或查看日志。"
            self.app.push_screen(TextDialog("作业日志" if logs else "SSH 连接", text))
        except AuthenticationRequired:
            self.app.require_login("登录已到期，请重新登录。")
        except (ApiError, ConfigError) as exc:
            self.notice(exc, True)

    @on(Button.Pressed)
    def clicked(self, event):
        name = event.button.id or ""
        if name.endswith("-refresh"):
            self.refresh_active()
        elif name == "create":
            values = {key: self.query_one("#apply-" + key, Select).value for key in ("plan", "image", "account", "node")}
            if any(values[key] is Select.BLANK for key in ("plan", "image")):
                self.notice("请先选择镜像和套餐", True)
                return
            self.start_write("/instances", {"task_name": self.query_one("#task-name", Input).value,
                "plan_id": values["plan"], "image": values["image"], "hours": self.query_one("#hours", Input).value,
                "account": "" if values["account"] is Select.BLANK else values["account"],
                "node": "" if values["node"] is Select.BLANK else values["node"]})
        elif name.startswith("resource-"):
            row = self.selected_instance()
            if not row:
                self.notice("请先选择资源", True)
                return
            action = name.removeprefix("resource-")
            base = f'/instances/{row["id"]}'
            if action in ("connect", "logs"):
                self.show_resource_text(row["id"], action == "logs")
            elif action == "save":
                self.form_write("保存个人镜像", [("name", "镜像名称（1–32 位英文、数字、点、横线、下划线、加号）", False)],
                    base + "/save-image", note="导出容器环境，计入磁盘配额；共享家目录独立保存。", force=True)
            elif action in ("stop", "restart", "delete"):
                notes = {"stop": "停机将终止容器；尚未导出的容器环境会丢失。",
                         "restart": "按原参数重新排队，SSH 端口由平台重新分配。",
                         "delete": "永久删除已结束资源的记录及日志。"}
                self.confirm_write(f'作业 #{row["job_id"]}：' + notes[action], base + "/" + action)
        elif name.startswith("image-"):
            row = self.images.get(self.selected_key("images"))
            if not row or row["local_scope"] != "mine":
                self.notice("请选择个人镜像；公共镜像只读", True)
                return
            action = name.removeprefix("image-")
            base = "/images/" + quote(row["name"], safe="")
            if action == "delete":
                self.confirm_write("永久删除个人镜像 " + row["name"], base + "/delete")
            elif action == "rename":
                self.form_write("镜像改名", [("new_name", "新名称", False)], base + "/rename")
            elif action == "comment":
                self.form_write("修改镜像注释", [("note", "注释（留空可清空）", False)], base + "/comment")
        elif name == "key-add":
            self.form_write("添加 SSH 公钥", [("pubkey", "粘贴 .pub 文件中的完整公钥", False)], "/profile/keys")
        elif name == "key-delete":
            key = self.selected_key("keys")
            if key:
                self.confirm_write("删除选中的 SSH 公钥，随后该密钥将无法登录容器。", f"/profile/keys/{key}/delete")
        elif name == "profile-name":
            self.form_write("修改显示名", [("display_name", "显示名", False)], "/profile/name")
        elif name == "profile-password":
            self.form_write("修改密码", [("old", "原密码", True), ("new", "新密码", True), ("new2", "重复新密码", True)],
                "/profile/password", note="修改后所有终端凭证失效，需要重新登录。")
        elif name == "logout":
            self.app.push_screen(ConfirmDialog("注销当前终端凭证；网页和其它终端的登录不受影响。"),
                lambda yes: self.app.perform_logout() if yes else None)


class OrbitApp(App):
    TITLE = "OrbitCluster"
    SUB_TITLE = "用户计算终端"
    BINDINGS = [Binding("ctrl+q", "quit", "退出"), Binding("r", "refresh", "刷新")]
    CSS = """
    Screen { background: #0c1424; color: #dce5f3; }
    Header { background: #17263d; }
    Footer { background: #17263d; }
    #login-panel { width: 66; height: auto; margin: 2 0; padding: 2 3; background: #142238; border: round #31557c; align-horizontal: center; }
    LoginScreen { align: center middle; }
    #login-brand { text-style: bold; color: #69c7ff; margin-bottom: 1; }
    #login-panel Input { margin-bottom: 1; }
    #login-panel Button { width: 100%; margin-top: 1; }
    #login-status { margin-top: 1; color: #9cafc9; }
    #session-bar { height: 3; margin: 0 2; align-vertical: middle; }
    #identity { width: 1fr; padding-top: 1; }
    #server-label { height: 1; margin: 0 2 1 2; color: #8da6c8; }
    #tabs { height: 1fr; margin: 0 1; }
    TabPane { padding: 1; }
    DataTable { height: 1fr; background: #101c2f; }
    DataTable > .datatable--header { background: #20334e; color: #90d4ff; text-style: bold; }
    .buttons { height: auto; min-height: 3; margin-bottom: 1; }
    .buttons Button { min-width: 10; margin-right: 1; }
    .resource-buttons Button { min-width: 8; padding: 0 1; }
    #resource-detail { height: auto; max-height: 6; padding: 1; background: #142238; }
    .form-area { max-width: 90; padding: 0 2; }
    .form-area Label { margin-top: 1; }
    .form-area Button { margin-top: 1; }
    #plan-description, .muted { color: #8da6c8; height: auto; margin: 1 0; }
    #quota, #profile { padding: 1; height: auto; }
    #keys { min-height: 10; }
    #status { height: 1; margin: 0 2; color: #91a6c3; }
    ModalScreen { align: center middle; background: #07101bbc; }
    .dialog { width: 76; max-width: 95%; height: auto; max-height: 95%; padding: 2 3; background: #142238; border: round #3b6a9b; }
    .dialog-title { text-style: bold; color: #90d4ff; margin-bottom: 1; }
    .dialog Input { margin-bottom: 1; }
    .dialog .buttons { margin-top: 1; }
    .text-dialog { width: 95%; height: 90%; padding: 1 2; background: #142238; border: round #3b6a9b; }
    #dialog-text { height: 1fr; margin-bottom: 1; }
    """

    def __init__(self, settings, *, client=None):
        super().__init__()
        self.settings = settings
        self.client = client
        self._external_client = client is not None
        self._auth_transition = False

    async def on_mount(self):
        await self.push_screen(LoginScreen(self.settings.server))
        if self.settings.server:
            if self.client is None:
                self.client = Client(self.settings)
            self.restore_session()

    @work(group="session", exclusive=True)
    async def restore_session(self):
        try:
            identity = await asyncio.to_thread(self.client.identity)
            await self.switch_screen(MainScreen(identity))
        except AuthenticationRequired:
            pass
        except (ApiError, ConfigError) as exc:
            if isinstance(self.screen, LoginScreen):
                self.screen.query_one("#login-status", Static).update(str(exc))

    @work(group="session", exclusive=True)
    async def perform_login(self, server, username, password):
        try:
            server = normalize_server(server, self.settings.allow_http)
            self.settings = replace(self.settings, server=server)
            self.settings.save({"server": server, "allow_http": self.settings.allow_http})
            if not self._external_client:
                if self.client:
                    self.client.close()
                self.client = Client(self.settings)
            identity = await asyncio.to_thread(self.client.login, username, password)
            await self.switch_screen(MainScreen(identity))
        except (ApiError, ConfigError) as exc:
            if isinstance(self.screen, LoginScreen):
                self.screen.query_one("#login-status", Static).update(str(exc))
                self.screen.query_one("#login", Button).disabled = False

    def require_login(self, message):
        if self._auth_transition or isinstance(self.screen, LoginScreen):
            return
        self._auth_transition = True
        for screen in self.screen_stack:
            if isinstance(screen, MainScreen):
                screen.deactivate()
        self.show_login(message)

    @work(group="login-transition", exclusive=True)
    async def show_login(self, message):
        try:
            # Replacing only the top modal leaves its authenticated main
            # screen mounted. Unwind the whole user session instead.
            while len(self.screen_stack) > 1:
                await self.pop_screen()
            await self.push_screen(LoginScreen(self.settings.server, message))
        finally:
            self._auth_transition = False

    @work(group="session", exclusive=True)
    async def perform_logout(self):
        try:
            await asyncio.to_thread(self.client.logout)
            self.require_login("已注销当前终端，请重新登录。")
        except (ApiError, ConfigError) as exc:
            self.notify(escape(str(exc)), severity="error")

    def action_refresh(self):
        if isinstance(self.screen, MainScreen):
            self.screen.refresh_active()

    def on_unmount(self):
        if self.client:
            self.client.close()
