"""Client configuration: explicit flags > environment > JSON file > defaults."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


class ConfigError(Exception):
    code = "configuration_error"
    exit_code = 2


def normalize_server(value, allow_http=False):
    if not value:
        return ""
    value = str(value).strip()
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError:
        raise ConfigError("服务器地址格式不正确") from None
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or any(c.isspace() for c in value)):
        raise ConfigError("服务器地址需为 HTTP 或 HTTPS，不能包含账号、密码、查询参数或片段")
    if parsed.scheme == "http" and not allow_http:
        raise ConfigError("HTTP 连接需明确开启 allow_http；仅在可信内网使用 --allow-http 或在 TUI 勾选允许 HTTP")
    return urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip("/"), "", ""))


def read_json(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (ValueError, OSError):
        raise ConfigError("无法读取配置文件，请检查 JSON 格式和权限：%s" % path) from None
    if not isinstance(data, dict):
        raise ConfigError("配置文件必须为 JSON 对象：%s" % path)
    return data


def bool_value(value):
    if isinstance(value, bool):
        return value
    if str(value).lower() in ("1", "true", "yes"):
        return True
    if str(value).lower() in ("0", "false", "no"):
        return False
    raise ConfigError("布尔配置只能为 true/false 或 1/0")


@dataclass(frozen=True)
class Settings:
    config_dir: Path
    server: str = ""
    ca_bundle: str | None = None
    timeout: float = 120
    poll_seconds: float = 5
    allow_http: bool = False

    @classmethod
    def load(cls, overrides=None, environ=None):
        env = os.environ if environ is None else environ
        overrides = {k: v for k, v in (overrides or {}).items() if v is not None}
        default_root = Path(env.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "orbitcluster"
        root = Path(overrides.get("config_dir") or env.get("ORBIT_CONFIG_DIR") or default_root).expanduser()
        values = read_json(root / "config.json")
        for name in ("server", "ca_bundle", "timeout", "poll_seconds", "allow_http"):
            value = env.get("ORBIT_" + name.upper())
            if value is not None and value != "":
                values[name] = value
        values.update(overrides)
        allowed = bool_value(values.get("allow_http", False))
        server = normalize_server(values.get("server", ""), allowed)
        try:
            timeout = float(values.get("timeout", 120))
            poll = float(values.get("poll_seconds", 5))
            if not (0 < timeout <= 3600 and 0 < poll <= 3600):
                raise ValueError
        except (ValueError, TypeError):
            raise ConfigError("timeout 和 poll_seconds 必须大于 0 且不超过 3600 秒") from None
        ca = values.get("ca_bundle") or None
        if ca:
            ca = str(Path(ca).expanduser().resolve())
            if not Path(ca).is_file():
                raise ConfigError("CA 文件不存在：%s" % ca)
        return cls(root, server, ca, timeout, poll, allowed)

    def save(self, updates):
        allowed = {"server", "ca_bundle", "timeout", "poll_seconds", "allow_http"}
        if set(updates) - allowed:
            raise ConfigError("未知配置项；可配置：" + ", ".join(sorted(allowed)))
        values = read_json(self.config_dir / "config.json")
        values.update(updates)
        # File settings are validated without ambient overrides hiding mistakes.
        Settings.load({"config_dir": self.config_dir, **values}, environ={})
        from .credentials import private_write
        private_write(self.config_dir, "config.json", values)

    def public(self):
        return {"server": self.server, "ca_bundle": self.ca_bundle, "timeout": self.timeout,
                "poll_seconds": self.poll_seconds, "allow_http": self.allow_http,
                "config_dir": str(self.config_dir)}
