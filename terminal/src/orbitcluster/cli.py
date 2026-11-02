"""User commands with stable JSON output and no implicit write retries."""
import argparse
from datetime import datetime
import getpass
import json
from pathlib import Path
import sys
from urllib.parse import quote

from . import __version__
from .client import Client, ApiError
from .config import Settings, ConfigError, normalize_server


def parser():
    common = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    for name in ("server", "ca-bundle", "config-dir"):
        common.add_argument("--" + name)
    common.add_argument("--request-timeout", dest="timeout", type=float)
    common.add_argument("--poll-seconds", type=float)
    for flag in ("json", "yes"):
        common.add_argument("--" + flag, action="store_true")
    common.add_argument("--allow-http", action="store_true", help="允许明文 HTTP，仅用于可信内网")
    root = argparse.ArgumentParser(prog="orbit", description="OrbitCluster 用户终端", parents=[common])
    root.add_argument("--version", action="version", version="orbit " + __version__)
    commands = root.add_subparsers(dest="command")
    def command(name, help, parent=commands):
        return parent.add_parser(name, help=help, parents=[common])
    login = command("login", "登录并保存有期限的终端凭证")
    login.add_argument("--username")
    login.add_argument("--password-stdin", action="store_true")
    login.add_argument("--device", default="")
    logout = command("logout", "注销当前终端")
    logout.add_argument("--local", action="store_true", help="仅清除本机凭证，不联系服务器")
    command("whoami", "查看当前账号及服务器认证到期时间")
    command("tui", "打开交互界面")
    config = command("config", "配置服务器地址、CA、超时和刷新间隔")
    config_commands = config.add_subparsers(dest="action", required=True)
    command("show", "显示配置（不含凭证）", config_commands)
    config_set = command("set", "写入配置", config_commands)
    config_set.add_argument("key", choices=["server", "ca_bundle", "timeout", "poll_seconds", "allow_http"])
    config_set.add_argument("value")
    for name, label in [("plans", "资源套餐"), ("nodes", "集群节点"), ("quota", "我的配额"), ("accounts", "我的账户")]:
        command(name, label)
    instances = command("instances", "我的资源")
    actions = instances.add_subparsers(dest="action", required=True)
    command("list", "列出我的资源", actions)
    create = command("create", "按现有门户规则申请资源", actions)
    for flag in ("name", "plan", "image"):
        create.add_argument("--" + flag, required=True)
    create.add_argument("--hours", required=True, type=int)
    create.add_argument("--node", default="")
    create.add_argument("--account", default="")
    for name in ("show", "logs", "connection", "stop", "restart", "delete", "save", "wait"):
        item = command(name, name, actions)
        item.add_argument("id", type=int)
        if name == "logs":
            item.add_argument("--lines", type=int, default=300)
        if name == "save":
            item.add_argument("--name", required=True)
            item.add_argument("--force", action="store_true")
        if name == "wait":
            item.add_argument("--until", choices=["ssh-ready", "saved", "terminal"], default="ssh-ready")
            item.add_argument("--timeout", dest="wait_timeout", type=float, default=300)
    images = command("images", "公共和个人镜像")
    actions = images.add_subparsers(dest="action", required=True)
    command("list", "列出镜像", actions)
    for name in ("rename", "delete", "comment"):
        item = command(name, name, actions)
        item.add_argument("name")
        if name == "rename":
            item.add_argument("new_name")
        if name == "comment":
            item.add_argument("note", help="传空字符串可清空注释")
    save = command("save", "保存运行中的容器为个人镜像", actions)
    save.add_argument("--instance", dest="id", required=True, type=int)
    save.add_argument("--name", required=True)
    save.add_argument("--force", action="store_true")
    profile = command("profile", "个人资料")
    actions = profile.add_subparsers(dest="action", required=True)
    command("show", "查看资料和公钥", actions)
    name = command("name", "设置显示名", actions)
    name.add_argument("display_name")
    command("password", "交互式修改密码", actions)
    keys = command("keys", "SSH 公钥管理", commands)
    actions = keys.add_subparsers(dest="action", required=True)
    command("list", "查看公钥", actions)
    add = command("add", "从 .pub 文件添加公钥", actions)
    add.add_argument("--file", required=True)
    delete = command("delete", "删除公钥", actions)
    delete.add_argument("id", type=int)
    return root


def confirm(args, description):
    if getattr(args, "yes", False):
        return
    if not sys.stdin.isatty():
        raise ConfigError(description + "；非交互执行需要 --yes")
    if input(description + "，确认？[y/N] ").strip().lower() not in ("y", "yes"):
        raise ConfigError("操作已取消")


def resolve_plan(client, value):
    matches = [p for p in client.request("GET", "/plans") if str(p["id"]) == value or p["name"] == value]
    if len(matches) != 1:
        raise ConfigError("套餐不存在或名称不唯一，请运行 orbit plans 并使用套餐 ID")
    return matches[0]["id"]


def resolve_image(client, value):
    groups = client.request("GET", "/images")
    choices = groups.get("public", []) + groups.get("mine", [])
    exact = [p for p in choices if p["path"] == value]
    matches = exact or [p for p in choices if p["name"] == value or p["name"].removesuffix(".sqsh") == value]
    if len(matches) != 1:
        raise ConfigError("镜像不存在或名称不唯一，请运行 orbit images list 并使用完整路径")
    return matches[0]["path"]


def execute(args, client):
    command, action = args.command, getattr(args, "action", "")
    if command == "login":
        username = args.username or (input("用户名：") if sys.stdin.isatty() else "")
        if not username:
            raise ConfigError("请指定 --username")
        password = sys.stdin.read().rstrip("\r\n") if args.password_stdin else getpass.getpass("密码：")
        if not password or "\n" in password or "\r" in password:
            raise ConfigError("请提供一行密码")
        return client.login(username, password, args.device)
    if command == "logout":
        return client.logout(local=args.local)
    if command == "whoami":
        return client.identity()
    if command in ("plans", "nodes", "quota", "accounts"):
        return client.request("GET", "/" + command)
    if command == "instances":
        if action == "list":
            return client.request("GET", "/instances")
        if action == "create":
            return client.request("POST", "/instances", data={"task_name": args.name,
                "plan_id": resolve_plan(client, args.plan), "image": resolve_image(client, args.image),
                "hours": args.hours, "node": args.node, "account": args.account})
        base = f"/instances/{args.id}"
        if action == "show":
            return client.request("GET", base)
        if action == "logs":
            return client.request("GET", base + "/log", params={"lines": args.lines})
        if action == "connection":
            return client.request("GET", base + "/connection")
        if action == "wait":
            return client.wait(args.id, args.until, args.wait_timeout)
        if action in ("stop", "delete"):
            confirm(args, f"{action} 资源记录 #{args.id}")
        if action in ("stop", "restart", "delete"):
            return client.request("POST", base + "/" + action)
    if command == "images" and action == "list":
        return client.request("GET", "/images")
    if (command == "instances" and action == "save") or (command == "images" and action == "save"):
        if args.force:
            confirm(args, "覆盖同名个人镜像")
        return client.request("POST", f"/instances/{args.id}/save-image",
                              data={"name": args.name, "force": "1" if args.force else "0"})
    if command == "images":
        if action == "delete":
            confirm(args, "删除个人镜像 " + args.name)
        data = {"new_name": args.new_name} if action == "rename" else {"note": args.note} if action == "comment" else None
        return client.request("POST", "/images/" + quote(args.name, safe="") + "/" + action, data=data)
    if command == "profile":
        if action == "show":
            return client.request("GET", "/profile")
        if action == "name":
            return client.request("POST", "/profile/name", data={"display_name": args.display_name})
        if not sys.stdin.isatty():
            raise ConfigError("改密请在交互终端或 TUI 中进行")
        old, new, repeated = getpass.getpass("原密码："), getpass.getpass("新密码："), getpass.getpass("再次输入新密码：")
        result = client.request("POST", "/profile/password", data={"old": old, "new": new, "new2": repeated})
        client.logout(local=True)
        return result
    if command == "keys":
        if action == "list":
            return client.request("GET", "/profile")["keys"]
        if action == "add":
            try:
                key = Path(args.file).expanduser().read_text(encoding="utf-8").strip()
            except OSError:
                raise ConfigError("无法读取公钥文件") from None
            return client.request("POST", "/profile/keys", data={"pubkey": key})
        confirm(args, f"删除 SSH 公钥 #{args.id}")
        return client.request("POST", f"/profile/keys/{args.id}/delete")
    raise ConfigError("未知命令，请运行 orbit --help")


def emit(data, as_json):
    if as_json:
        print(json.dumps({"ok": True, "data": data}, ensure_ascii=False))
    elif isinstance(data, dict) and "command" in data:
        print(data["command"] or "SSH 尚未就绪，请等待或查看日志")
    else:
        if isinstance(data, dict) and "expires_at" in data:
            data = {**data, "expires_at_local": datetime.fromtimestamp(data["expires_at"]).astimezone().isoformat(timespec="seconds")}
        print(json.dumps(data, ensure_ascii=False, indent=2))


def main(argv=None, *, client_factory=Client):
    command_parser = parser()
    args = command_parser.parse_args(argv)
    as_json = getattr(args, "json", False)
    try:
        values = {k: getattr(args, k, None) for k in ("server", "ca_bundle", "config_dir", "timeout", "poll_seconds", "allow_http")}
        settings = Settings.load(values)
        if args.command == "config":
            if args.action == "set":
                settings.save({args.key: args.value})
                settings = Settings.load(values)
            emit(settings.public(), as_json)
            return 0
        if not args.command:
            if not sys.stdin.isatty():
                command_parser.print_help()
                return 0
            args.command = "tui"
        if args.command == "tui":
            from .tui import OrbitApp
            OrbitApp(settings).run()
            return 0
        if args.command == "login" and not settings.server and sys.stdin.isatty():
            server = normalize_server(input("服务器地址（HTTP / HTTPS）："), settings.allow_http)
            settings.save({"server": server, "allow_http": settings.allow_http})
            settings = Settings.load({**values, "server": server})
        with client_factory(settings) as client:
            result = execute(args, client)
        emit(result, as_json)
        return 0
    except (ApiError, ConfigError) as exc:
        if as_json:
            print(json.dumps({"ok": False, "code": exc.code, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        else:
            print(str(exc), file=sys.stderr)
        return exc.exit_code
    except (EOFError, KeyboardInterrupt):
        print("操作已取消", file=sys.stderr)
        return 130
