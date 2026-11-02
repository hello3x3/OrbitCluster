"""Shared HTTP client for TUI and CLI; no Cookies, redirects or write retries."""
import socket
import ssl
import time

import httpx

from .config import ConfigError
from .credentials import CredentialStore

PREFIX = "/api/terminal/v1"


class ApiError(Exception):
    exit_code = 1
    def __init__(self, message, code="operation_rejected", status=None):
        super().__init__(message)
        self.code, self.status = code, status


class AuthenticationRequired(ApiError):
    exit_code = 3
    def __init__(self, message="请运行 orbit login 或在 TUI 中重新登录"):
        super().__init__(message, "authentication_required", 401)


class NetworkError(ApiError):
    exit_code = 4
    def __init__(self, message):
        super().__init__(message, "network_error")


class Client:
    def __init__(self, settings, *, transport=None, store=None):
        self.settings = settings
        if not settings.server:
            raise ConfigError("请设置服务器地址：orbit config set server 服务地址")
        self.store = store or CredentialStore(settings.config_dir)
        try:
            verify = ssl.create_default_context(cafile=settings.ca_bundle if settings.server.startswith("https://") else None)
        except (OSError, ssl.SSLError):
            raise ConfigError("无法读取 CA 证书，请检查 ca_bundle 配置") from None
        self.http = httpx.Client(verify=verify, timeout=settings.timeout, follow_redirects=False,
            transport=transport, event_hooks={"request": [lambda req: req.headers.pop("cookie", None)]})

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self.http.close()

    def request(self, method, path, data=None, *, params=None, json=None, authenticated=True):
        if not path.startswith("/") or path.startswith("//"):
            raise ConfigError("客户端接口路径无效")
        headers = {"Accept": "application/json"}
        if authenticated:
            credential = self.store.get(self.settings.server)
            try:
                valid = credential and float(credential["expires_at"]) > time.time() and credential["token"]
            except (ValueError, TypeError, KeyError):
                valid = False
            if not valid:
                raise AuthenticationRequired()
            headers["Authorization"] = "Bearer " + credential["token"]
        try:
            response = self.http.request(method, self.settings.server + PREFIX + path,
                                         headers=headers, data=data, params=params, json=json)
        except httpx.HTTPError:
            suffix = "；操作结果尚不确定，请先核实资源状态再重试" if method != "GET" else ""
            raise NetworkError("无法连接服务器，请检查地址、网络和 CA 证书" + suffix) from None
        if 300 <= response.status_code < 400:
            raise ApiError("服务器返回重定向，请配置最终服务地址；客户端未转发凭证", "unexpected_redirect")
        try:
            payload = response.json()
        except ValueError:
            raise ApiError("服务器未返回终端 API 数据，请确认该地址已部署终端 API", "invalid_response") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
            raise ApiError("服务器响应格式不符合终端 API 协议", "invalid_response")
        if response.status_code == 401 and authenticated:
            self.store.delete(self.settings.server)
            raise AuthenticationRequired()
        if not response.is_success or not payload["ok"]:
            raise ApiError(payload.get("error") or "服务器拒绝请求",
                           payload.get("code", "operation_rejected"), response.status_code)
        return payload.get("data")

    def login(self, username, password, device=""):
        result = self.request("POST", "/auth/login", authenticated=False,
            json={"username": username, "password": password, "device": device or socket.gethostname()})
        try:
            if (not isinstance(result["token"], str) or len(result["token"]) < 43
                    or not isinstance(result["username"], str)
                    or float(result["expires_at"]) <= time.time()):
                raise ValueError
            self.store.save(self.settings.server, result)
        except (KeyError, TypeError, ValueError):
            raise ApiError("服务器返回的登录凭证无效", "invalid_response") from None
        return {k: v for k, v in result.items() if k not in ("token", "token_type")}

    def identity(self):
        return self.request("GET", "/auth/me")

    def logout(self, local=False):
        if not local:
            try:
                self.request("POST", "/auth/logout")
            except AuthenticationRequired:
                pass
        self.store.delete(self.settings.server)
        return {"logged_out": True, "local_only": local}

    def wait(self, iid, until="ssh-ready", timeout=300):
        if timeout <= 0:
            raise ConfigError("等待超时必须大于 0")
        deadline = time.monotonic() + timeout
        while True:
            path = f"/instances/{iid}" + ("/connection" if until == "ssh-ready" else "")
            result = self.request("GET", path)
            if until == "ssh-ready" and result.get("ready"):
                return result
            active = result.get("state") in ("PENDING", "RUNNING", "SUSPENDED", "COMPLETING", "STOPPING")
            if until == "terminal" and not active:
                return result
            if until == "saved" and not result.get("saving_now") and result.get("last_save"):
                if "失败" in result["last_save"] or "异常" in result["last_save"]:
                    raise ApiError(result["last_save"])
                return result
            if until == "ssh-ready" and (result.get("error") or not active):
                raise ApiError(result.get("error") or "作业已结束，无法等待 SSH 就绪")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ApiError("等待超时；作业继续由服务器管理", "wait_timeout")
            time.sleep(min(self.settings.poll_seconds, remaining))
