#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""e2e / 验证脚本的测试账号守卫：号段隔离 + 保证清理 + 断言清理干净。

为什么要守卫（改这个文件前请先读）：
    在计算节点上用**裸 useradd -u <某 UID>** 建测试账号、事后又不清理，就会与日后分到
    同一 UID 的真实用户形成"两个账号共用一个 UID"。enroot 的 passwd hook
    （/etc/enroot/hooks.d/10-shadow.sh）只按 UID 执行 `getent passwd <uid>` 取**一条**记录
    写进容器 /etc/passwd，取到的是先注册的那个 —— 容器里根本没有真实用户，
    用户 ssh 进去报 "Permission denied (publickey)"，看起来像密钥问题，排查代价很高。

本模块给测试脚本三层保障：

  ① 号段隔离
     测试自建 OS 账号只用 RESERVED_UID_MIN..RESERVED_UID_MAX（默认 59000-59999）。
     该区间在 login.defs 的 UID_MAX(60000) 之内，而 useradd 是从 1000 往上找空号，
     正常业务**永远够不到**这一段；即使测试账号残留，也不会撞到真实用户。
     （不要用 60000+：那是 nobody/overflow 区，且超出 UID_MAX，useradd 会拒绝。）

  ② 保证清理
     注册 atexit 与 SIGINT/SIGTERM 钩子，正常结束/抛异常/Ctrl-C 都会走 cleanup()。
     优先调用门户自己的 `portal-ctl unprovision-user`（覆盖各节点 OS 账号 + 家目录 +
     个人镜像 + Slurm 会计关联），再幂等兜底补删，并清掉 userdel 不会清的
     setquota 记录与主组。

  ③ 断言干净
     verify_clean() 逐项核对 OS 账号（两节点）/家目录/配额记录/Slurm 关联/门户记录/
     个人镜像目录是否真的消失；有残留就列清单并抛 ResidueError，让脚本以非 0 退出，
     残留无法被静默忽略。

用法：
    import e2e_accounts as ea
    guard = ea.TestAccountGuard(run_local=sh, run_peer=peer, peer_name=PEER)
    with guard:
        guard.reserve("e2etest")                      # 门户建号的场景：只登记待清理
        # 或： uid = guard.create_os_account("e2etest")   # 需要"OS 账号已存在"的场景
        ...测试...
        assert not guard.verify_clean()               # 想提前检查也行
"""
import atexit
import os
import re
import signal
import sys

USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
# 只有"看起来就是测试账号"的名字才允许被本守卫清理（会删家目录/配额/会计关联）。
# 防止有人 E2E_USER=lnq 跑一把，把真实用户的家目录删掉。
TEST_NAME_RE = re.compile(r"^(e2e|test|pt|qa|ci|tmp)[a-z0-9_-]*$")

# 测试自建账号的保留号段（刻意远离 useradd 的自动分配起点 1000）
RESERVED_UID_MIN = int(os.environ.get("E2E_RESERVED_UID_MIN", "59000"))
RESERVED_UID_MAX = int(os.environ.get("E2E_RESERVED_UID_MAX", "59999"))
# login.defs 的合法范围：UID_MIN/UID_MAX（默认 1000/60000）
UID_HARD_MIN = 1000
UID_HARD_MAX = 60000


class ResidueError(RuntimeError):
    """清理后仍有残留 —— 调用方应视为测试失败（非 0 退出）。"""

    def __init__(self, residues):
        self.residues = list(residues)
        super(ResidueError, self).__init__(
            "测试账号清理不彻底，残留 %d 项：\n  - %s"
            % (len(self.residues), "\n  - ".join(self.residues)))


class UnsafeTestName(ValueError):
    """用户名不像测试账号 —— 拒绝清理，避免误删真实用户的家目录/配额/关联。"""


def valid_username(u):
    return bool(USER_RE.match(u or ""))


class TestAccountGuard(object):
    """登记 → 保证清理 → 校验干净。可当上下文管理器用。

    run_local(cmd, timeout) -> (rc, stdout, stderr)   在管理节点执行
    run_peer (cmd, timeout) -> (rc, stdout, stderr)   在计算节点执行（可省略）
    """

    def __init__(self, run_local, run_peer=None, peer_name="",
                 portal_ctl="/usr/local/sbin/portal-ctl",
                 add_user="/opt/cluster-admin/add-user.sh",
                 share="/share", share_home="/share/home", images="/share/images",
                 portal_db="/var/lib/cluster-portal/portal.db",
                 portal_py="/opt/cluster-portal/venv/bin/python",
                 quota="100G", log=None, auto_hooks=True, allow_any_name=None):
        self._local = run_local
        self._peer = run_peer
        self.peer_name = peer_name or ""
        self.portal_ctl = portal_ctl
        self.add_user = add_user
        self.share = share
        self.share_home = share_home
        self.images = images
        self.portal_db = portal_db
        self.portal_py = portal_py
        self.quota = quota
        self.log = log or (lambda m: print("[guard] " + m, flush=True))
        self.allow_any_name = (os.environ.get("E2E_ALLOW_ANY_NAME") == "1"
                               if allow_any_name is None else allow_any_name)
        self.users = []            # 待清理 / 已登记的用户名（有序、去重）
        self.created = {}          # 用户名 -> 本守卫亲手建的 uid
        self._cleaned = False
        self._hooked = False
        if auto_hooks:
            atexit.register(self._atexit)
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    signal.signal(sig, self._on_signal)
                except (ValueError, OSError):
                    pass          # 非主线程安装信号处理器会失败，忽略

    # ---------------------------------------------------------------- 运行工具
    def _run(self, runner, cmd, timeout=90):
        if runner is None:
            return 1, "", "no runner"
        try:
            rc, out, err = runner(cmd, timeout)
        except Exception as e:                      # 运行器自身出错也不能中断清理
            return 1, "", "%s: %s" % (type(e).__name__, e)
        return rc, (out or "").strip(), (err or "").strip()

    def local(self, cmd, timeout=90):
        return self._run(self._local, cmd, timeout)

    def peer(self, cmd, timeout=90):
        return self._run(self._peer, cmd, timeout)

    # ------------------------------------------------------------ 号段分配
    def _uid_used(self, uid):
        """该 UID 在任一节点上是否已被占用（占用者不是我们要建的账号才算占用）。"""
        rc, out, _ = self.local("getent passwd %d | cut -d: -f1 | head -1" % uid)
        if rc == 0 and out:
            return out
        if self._peer is not None:
            rc, out, _ = self.peer("getent passwd %d | cut -d: -f1 | head -1" % uid)
            if rc == 0 and out:
                return out
        return ""

    def _gid_used(self, gid):
        rc, out, _ = self.local("getent group %d | cut -d: -f1 | head -1" % gid)
        if rc == 0 and out:
            return out
        if self._peer is not None:
            rc, out, _ = self.peer("getent group %d | cut -d: -f1 | head -1" % gid)
            if rc == 0 and out:
                return out
        return ""

    def next_free_reserved_uid(self):
        for uid in range(RESERVED_UID_MIN, RESERVED_UID_MAX + 1):
            if not self._uid_used(uid):
                return uid
        raise RuntimeError(
            "保留号段 %d-%d 已无空闲 UID，请先清理残留测试账号"
            % (RESERVED_UID_MIN, RESERVED_UID_MAX))

    def next_free_reserved_gid(self):
        for gid in range(RESERVED_UID_MIN, RESERVED_UID_MAX + 1):
            if not self._gid_used(gid):
                return gid
        raise RuntimeError(
            "保留号段 %d-%d 已无空闲 GID，请先清理残留测试账号"
            % (RESERVED_UID_MIN, RESERVED_UID_MAX))

    # ---------------------------------------------------------------- 登记
    def _check_name(self, username):
        if not valid_username(username):
            raise ValueError("非法测试用户名: %r" % username)
        if not (self.allow_any_name or TEST_NAME_RE.match(username)):
            raise UnsafeTestName(
                "拒绝把 %r 当测试账号清理（会删家目录/配额/会计关联）。测试账号名需以 "
                "e2e/test/pt/qa/ci/tmp 开头；确实要用其它名字请设 E2E_ALLOW_ANY_NAME=1"
                % username)
        return username

    def reserve(self, username):
        """登记一个将由门户/其它途径创建的账号，只负责清理与校验。"""
        self._check_name(username)
        if username not in self.users:
            self.users.append(username)
        self._cleaned = False
        return username

    def create_os_account(self, username, uid=None, gid=None, quota=None):
        """用**正规脚本** add-user.sh 在（两）节点建 OS 账号，UID/GID 都强制取保留号段。

        用于测试"OS 账号已存在"这条门户开通路径。注意必须用 add-user.sh
        （站点无关、拒绝重复 UID/GID），不要再用裸 useradd —— 那正是当年的事故源。
        """
        if not valid_username(username):
            raise ValueError("非法测试用户名: %r" % username)
        self._check_name(username)
        uid = int(uid) if uid else self.next_free_reserved_uid()
        gid = int(gid) if gid else self.next_free_reserved_gid()
        for label, val in (("UID", uid), ("GID", gid)):
            if not (UID_HARD_MIN <= val < UID_HARD_MAX):
                raise ValueError("%s %d 超出 login.defs 允许范围 [%d, %d)"
                                 % (label, val, UID_HARD_MIN, UID_HARD_MAX))
            if not (RESERVED_UID_MIN <= val <= RESERVED_UID_MAX):
                raise ValueError(
                    "测试账号 %s 必须落在保留号段 %d-%d（避免占用真实用户号段）；收到 %d"
                    % (label, RESERVED_UID_MIN, RESERVED_UID_MAX, val))
        owner = self._uid_used(uid)
        if owner and owner != username:
            raise RuntimeError("UID %d 已被账号 %s 占用" % (uid, owner))
        gowner = self._gid_used(gid)
        if gowner and gowner != username:
            raise RuntimeError("GID %d 已被组 %s 占用" % (gid, gowner))

        self.reserve(username)
        q = quota or self.quota
        rc, out, err = self.local("%s %s %s -u %d -g %d"
                                  % (self.add_user, username, q, uid, gid), 180)
        if rc != 0:
            raise RuntimeError("管理节点建号失败: %s" % (err or out)[:300])
        rc, got, _ = self.local("id -u %s" % username)
        if got != str(uid):
            raise RuntimeError("管理节点建号 UID 不符：期望 %d，实际 %s" % (uid, got or "?"))
        rc, got_gid, _ = self.local("id -g %s" % username)
        if got_gid != str(gid):
            raise RuntimeError("管理节点建号 GID 不符：期望 %d，实际 %s" % (gid, got_gid or "?"))

        if self._peer is not None and self.peer_name:
            rc, out, err = self.peer("%s %s -u %d -g %d"
                                     % (self.add_user, username, uid, gid), 120)
            if rc != 0:
                raise RuntimeError("计算节点 %s 建号失败: %s" % (self.peer_name, err or out)[:300])
            rc, pgid, _ = self.peer("id -g %s" % username)
            if pgid != str(gid):
                raise RuntimeError("计算节点 GID 漂移：管理节点 %d，%s %s"
                                   % (gid, self.peer_name, pgid or "?"))
        self.created[username] = uid
        self.log("已建测试账号 %s uid=%d gid=%d（保留号段 %d-%d）"
                 % (username, uid, gid, RESERVED_UID_MIN, RESERVED_UID_MAX))
        return uid

    # ---------------------------------------------------------------- 清理
    def _cleanup_one(self, u):
        # 删号前先取 UID：删完就查不到了，而 setquota 记录要按 UID 清
        uid = ""
        rc, out, _ = self.local("id -u %s 2>/dev/null" % u)
        if rc == 0 and out.isdigit():
            uid = out
        if not uid and self._peer is not None:
            rc, out, _ = self.peer("id -u %s 2>/dev/null" % u)
            if rc == 0 and out.isdigit():
                uid = out

        # 1) 杀掉该用户还在跑的作业，避免容器继续占着（best effort）
        self.local("runuser -u %s -- scancel -u %s 2>/dev/null; true" % (u, u), 60)

        # 2) 首选门户自己的注销路径（覆盖各节点 OS 账号 + 家目录 + 个人镜像 + Slurm 关联）
        rc, out, err = self.local("test -x %s && %s unprovision-user %s </dev/null"
                                  % (self.portal_ctl, self.portal_ctl, u), 300)
        portal_ok = (rc == 0)
        if not portal_ok:
            self.log("portal-ctl unprovision-user %s 未成功（%s），改用兜底删除"
                     % (u, (err or out)[:120] or "退出码 %s" % rc))

        # 3) 兜底（幂等）：两个节点都用正规目录删干净
        if self._peer is not None and self.peer_name:
            self.peer("id %s >/dev/null 2>&1 && userdel %s 2>/dev/null; true" % (u, u), 120)
            self.peer("getent group %s >/dev/null 2>&1 && groupdel %s 2>/dev/null; true"
                      % (u, u), 60)
        self.local("id %s >/dev/null 2>&1 && userdel -r %s 2>/dev/null; true" % (u, u), 180)
        self.local("getent group %s >/dev/null 2>&1 && groupdel %s 2>/dev/null; true"
                   % (u, u), 60)
        self.local("rm -rf %s/%s 2>/dev/null; true" % (self.share_home, u), 120)
        self.local("rm -rf %s/%s 2>/dev/null; true" % (self.images, u), 120)

        # 4) userdel 不会清的东西：配额记录（按 UID 留）、Slurm 会计关联
        if uid:
            self.local("setquota -u %s 0 0 0 0 %s 2>/dev/null; true" % (uid, self.share), 60)
        self.local("sacctmgr -i delete user name=%s 2>/dev/null; true" % u, 60)
        return portal_ok

    def cleanup(self, force=False):
        """幂等清理所有已登记账号。force=True 时即使已清理过也再清一遍。"""
        if not self.users:
            return True
        if self._cleaned and not force:
            return True
        self.log("清理测试账号: %s" % ", ".join(self.users))
        all_portal = True
        for u in self.users:
            try:
                all_portal = self._cleanup_one(u) and all_portal
            except Exception as e:
                self.log("清理 %s 时异常（继续清理其余）：%s" % (u, e))
        self._cleaned = True
        return all_portal

    # ---------------------------------------------------------------- 校验
    def verify_clean(self):
        """返回残留清单（空列表 = 干净）。只检查本守卫登记过的账号。"""
        res = []
        for u in self.users:
            rc, out, _ = self.local("id -u %s 2>/dev/null" % u)
            if rc == 0 and out:
                res.append("%s：管理节点仍有 OS 账号 (uid=%s)" % (u, out))
            if self._peer is not None and self.peer_name:
                rc, out, _ = self.peer("id -u %s 2>/dev/null" % u)
                if rc == 0 and out:
                    res.append("%s：计算节点 %s 仍有 OS 账号 (uid=%s)"
                               % (u, self.peer_name, out))
            rc, _, _ = self.local("test -e %s/%s" % (self.share_home, u))
            if rc == 0:
                res.append("%s：家目录 %s/%s 仍存在" % (u, self.share_home, u))
            rc, _, _ = self.local("test -e %s/%s" % (self.images, u))
            if rc == 0:
                res.append("%s：个人镜像目录 %s/%s 仍存在" % (u, self.images, u))
            rc, out, _ = self.local(
                "repquota -u %s 2>/dev/null | awk '$1==\"%s\"'" % (self.share, u))
            if out:
                res.append("%s：/share 配额记录仍存在（%s）" % (u, out[:60]))
            rc, out, _ = self.local(
                "sacctmgr -n -P show assoc user=%s format=User 2>/dev/null" % u)
            if out:
                res.append("%s：Slurm 会计关联仍存在" % u)
            # 门户 DB：用 venv python（宿主没装 sqlite3 命令行），SQL 里不出现引号
            code = ('import sqlite3;c=sqlite3.connect("%s");'
                    'print(sum(1 for r in c.execute("SELECT username FROM users") '
                    'if r[0]=="%s"))' % (self.portal_db, u))
            rc, out, _ = self.local("%s -c '%s'" % (self.portal_py, code))
            if out.strip() == "1":
                res.append("%s：门户数据库仍有该用户记录" % u)
        return res

    def assert_clean(self):
        res = self.verify_clean()
        if res:
            raise ResidueError(res)
        return True

    # ---------------------------------------------------------------- 钩子
    def _atexit(self):
        try:
            self.cleanup()
        except Exception as e:
            self.log("atexit 清理异常: %s" % e)

    def _on_signal(self, signum, frame):
        self.log("收到信号 %s，先清理测试账号再退出" % signum)
        try:
            self.cleanup()
        finally:
            os._exit(128 + signum)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.cleanup()
        res = self.verify_clean()
        if res:
            for r in res:
                self.log("  [!!] 残留: " + r)
            if exc_type is None:
                raise ResidueError(res)
            # 已有异常在传播：不要掩盖它，但也必须在 stderr 上留下残留告警
            sys.stderr.write("WARNING: 测试账号清理不彻底（%d 项），见上方列表\n" % len(res))
        return False
