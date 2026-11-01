"""Private local credentials, separated by exact server base URL."""
import json
import os
from pathlib import Path
import stat
import tempfile

from .config import ConfigError


def private_directory(root):
    root = Path(root)
    if root.is_symlink():
        raise ConfigError("凭证目录不能是符号链接")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = root.stat()
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        raise ConfigError("凭证目录不属于当前用户")
    os.chmod(root, 0o700)
    return root


def private_read(root, name):
    root = private_directory(root)
    path = root / name
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return {}
    except OSError:
        raise ConfigError("凭证文件不可读取或为符号链接：%s" % path) from None
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or (hasattr(os, "getuid") and st.st_uid != os.getuid())
                or stat.S_IMODE(st.st_mode) & 0o077):
            raise ConfigError("凭证文件需属于当前用户且权限为 600：%s" % path)
        with os.fdopen(fd, encoding="utf-8") as fh:
            fd = None
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError
        return data
    except (ValueError, OSError):
        raise ConfigError("凭证文件损坏或无法读取，请重新登录") from None
    finally:
        if fd is not None:
            os.close(fd)


def private_write(root, name, data):
    root = private_directory(root)
    target = root / name
    if target.is_symlink():
        raise ConfigError("拒绝写入符号链接凭证文件")
    fd, tmp = tempfile.mkstemp(prefix=".orbit-", suffix=".tmp", dir=root)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class CredentialStore:
    def __init__(self, root):
        self.root = Path(root)

    def get(self, server):
        data = private_read(self.root, "auth.json")
        sessions = data.get("sessions", {})
        if not isinstance(sessions, dict):
            raise ConfigError("凭证文件格式错误，请重新登录")
        result = sessions.get(server)
        if result is not None and not isinstance(result, dict):
            raise ConfigError("凭证记录格式错误，请重新登录")
        return result

    def save(self, server, credential):
        data = private_read(self.root, "auth.json")
        sessions = data.setdefault("sessions", {})
        sessions[server] = {k: credential[k] for k in ("token", "username", "issued_at", "expires_at")}
        private_write(self.root, "auth.json", data)

    def delete(self, server):
        data = private_read(self.root, "auth.json")
        data.setdefault("sessions", {}).pop(server, None)
        private_write(self.root, "auth.json", data)
