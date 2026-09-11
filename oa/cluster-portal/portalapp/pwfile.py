# -*- coding: utf-8 -*-
"""
平台(门户)密码文件机制 —— OS root 在 admin 上编辑一个文件即可批量改所有人密码。

文件（默认 /etc/cluster-portal/users.passwd，root 编辑）：
    每行一条：  用户名:明文密码
    # 开头为注释；空行忽略。
示例：
    # 给 alice 单独设密码
    alice:ChangeMe_2026
    给 root 等管理员也按此改（root 无需经门户管理员）

门户后台线程会定期(默认 20s)检查文件内容；发生变化时对每个“门户里存在且
用户名匹配”的账号重新哈希并更新。未知用户/格式非法行会被跳过并在服务日志里提示。
"""
import hashlib
import logging
import os
import re
import threading
import time

log = logging.getLogger("portal.pwfile")

USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")

DEFAULT_PATH = "/etc/cluster-portal/users.passwd"


def file_path():
    return os.environ.get("PORTAL_PASSWD_FILE", DEFAULT_PATH)


HEADER = """# 平台(门户)登录密码（全部账号的明文密码统一存放于此）
# 格式：每行一条 “用户名:密码”（密码非空且不超过128字符，不能含冒号/首尾空格；# 开头为注释）
# 门户里发生的建号/改密/删除会自动同步本文件；OS root 编辑本文件也会在 ~20s 内生效。
"""


def validate_password(pwd):
    """与解析规则一致的口令校验（长度不限，但需非空且 ≤128，无冒号/首尾空格）。"""
    if not pwd:
        return False, "密码不能为空"
    if len(pwd) > 128:
        return False, "密码过长（最多 128 字符）"
    if ":" in pwd:
        return False, "密码不能包含冒号 ':'（明文密码文件按行存储）"
    if pwd != pwd.strip():
        return False, "密码首尾不能有空格"
    return True, ""


def load_map(path=None):
    """读取文件，返回 {用户名: 明文密码}（注释/空行忽略，同名后者覆盖）。"""
    path = path or file_path()
    out = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return out
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if ":" not in s:
            continue
        u, p = s.split(":", 1)
        u = u.strip()
        if USER_RE.match(u) and p and p == p.strip() and ":" not in p and len(p) <= 128:
            out[u] = p
    return out


def _atomic_write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)
    # 明文密码文件必须保持 660 root:portal（或 portal 属主），不可开放给其它用户读
    try:
        os.chmod(path, 0o660)
        if os.geteuid() == 0:
            import grp
            try:
                gid = grp.getgrnam("portal").gr_gid
            except KeyError:
                gid = -1
            os.chown(path, 0, gid)
    except OSError:
        pass


def upsert(username, password, path=None):
    """把 (用户名:密码) 写入文件：已有则替换该行，没有则追加；保留注释行。"""
    path = path or file_path()
    ok, err = validate_password(password)
    if not ok:
        raise ValueError(err)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = []
    replaced = False
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        pass
    if not any(s.strip() and not s.lstrip().startswith("#") for s in lines):
        lines = [ln for ln in HEADER.splitlines()] + lines
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("#") or ":" not in s:
            continue
        u = s.split(":", 1)[0].strip()
        if u == username:
            lines[i] = "%s:%s" % (username, password)
            replaced = True
            break
    if not replaced:
        lines.append("%s:%s" % (username, password))
    _atomic_write(path, "\n".join(lines) + "\n")


def remove(username, path=None):
    """删除文件中某用户的行（保留其它内容）。"""
    path = path or file_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return
    out = [ln for ln in lines
           if not (ln.strip() and not ln.lstrip().startswith("#")
                   and ln.split(":", 1)[0].strip() == username)]
    _atomic_write(path, "\n".join(out) + "\n")


def parse(text):
    """返回 (entries, malformed)。entries=[(user,password)]，跳过注释/空行。"""
    entries = []
    malformed = []
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if ":" not in s:
            malformed.append((i, line[:60], "缺少 ':'"))
            continue
        user, pwd = s.split(":", 1)
        user, pwd = user.strip(), pwd
        if not USER_RE.match(user):
            malformed.append((i, line[:60], "用户名非法"))
            continue
        if not pwd or len(pwd) > 128 or ":" in pwd or pwd != pwd.strip():
            malformed.append((i, line[:60], "密码须非空、≤128 字符且不含冒号/首尾空格"))
            continue
        entries.append((user, pwd))
    return entries, malformed


def _content_signature(data):
    return hashlib.sha256(data).hexdigest()


def sync_once(db):
    """读取密码文件并应用。返回统计 dict。"""
    path = file_path()
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as e:
        return {"changed": 0, "unknown": [], "malformed": [], "reason": str(e)}
    text = data.decode("utf-8", "replace")
    entries, malformed = parse(text)
    changed = 0
    unknown = []
    for user, pwd in entries:
        row = db.user_by_name(user)
        if row is None:
            unknown.append(user)
            continue
        db.user_set_password(row["id"], _hash_pwd(pwd))
        changed += 1
    return {"changed": changed, "unknown": unknown, "malformed": malformed,
            "signature": _content_signature(data)}


def _hash_pwd(pwd):
    from . import auth
    return auth.hash_password(pwd)


class PwFileWatcher:
    """后台线程：检测文件内容变化后应用，只在实际变化时刷一次。"""

    def __init__(self, db_path, interval):
        self.db_path = db_path
        self.interval = interval
        self._sig = None
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def _tick(self):
        path = file_path()
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            return
        sig = _content_signature(data)
        if sig == self._sig:
            return
        self._sig = sig
        from .db import DB
        db = DB(self.db_path)
        try:
            st = sync_once(db)
        finally:
            db.close()
        if st.get("changed"):
            log.info("密码文件已应用: 更新 %d 个账号%s", st["changed"],
                     "，跳过未知用户: %s" % ",".join(st["unknown"]) if st["unknown"] else "")
        if st.get("malformed"):
            log.warning("密码文件存在非法行 %d 条(已忽略)", len(st["malformed"]))

    def run(self):
        # 首次启动也读一次（文件若已存在并含配置则直接生效）
        self._tick()
        while not self._stop.wait(self.interval):
            try:
                self._tick()
            except Exception as e:  # noqa
                log.exception("密码文件同步失败: %s", e)


def start(db_path):
    """在 create_app 时启动监视线程；PORTAL_PASSWD_SYNC=0 可禁用。"""
    if os.environ.get("PORTAL_PASSWD_SYNC", "1") == "0":
        return None
    try:
        interval = max(5, int(os.environ.get("PORTAL_PASSWD_SYNC_SECONDS", "20")))
    except ValueError:
        interval = 20
    w = PwFileWatcher(db_path, interval)
    t = threading.Thread(target=w.run, name="portal-pwfile", daemon=True)
    t.start()
    return w
