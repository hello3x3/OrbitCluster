# -*- coding: utf-8 -*-
"""本地冒烟测试（不依赖集群/root 助手）：用假 ctl 层跑主要页面与流程。

用法:  ../.venv-test/bin/python smoke_local.py
"""
import os
import re
import sys
import tempfile
import time

TMP = tempfile.mkdtemp(prefix="portal-smoke-")
os.environ["PORTAL_DATA"] = TMP
os.environ["PORTAL_PASSWD_SYNC"] = "0"          # 测试里手动触发同步，不启监视线程
os.environ["PORTAL_PASSWD_FILE"] = os.path.join(TMP, "users.passwd")
os.environ["PORTAL_SAVE_SYNC"] = "1"            # 保存镜像同步执行（便于断言结果）
os.environ["PORTAL_EXPIRY_DISABLE"] = "1"       # 到期扫描由测试显式触发
IMGDIR = os.path.join(TMP, "images")
os.makedirs(IMGDIR, exist_ok=True)
open(os.path.join(IMGDIR, "cuda12.8.0-devel-ubuntu24.04.sqsh"), "w").close()
PUBIMG = os.path.join(IMGDIR, "cuda12.8.0-devel-ubuntu24.04.sqsh")
os.environ["PORTAL_IMAGES_DIR"] = IMGDIR
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import portalapp.app as appmod  # noqa: E402
import portalapp.ctl as ctlmod  # noqa: E402
import portalapp.pwfile as pwfile  # noqa: E402

# ---------- fake ctl ----------
ctlmod.sinfo = lambda: {"ok": True, "partition": "gpu", "nodes": [
    {"name": "<GPU01>", "state": "idle", "avail": "up", "cpus_alloc": 0,
     "cpus_idle": 16, "cpus_total": 16, "mem_mb": 31933,
     # 「空闲/总」要用的字段：已分配内存、GRES 型号/总量/已用量
     "mem_alloc_mb": 8192, "mem_free_mb": 23741,
     "gres": "gpu:3060:1(S:0)", "gres_model": "3060",
     "gres_total": 1, "gres_used": 1, "gres_free": 0,
     "ip": "<GPU01_IP>"}]}
ctlmod.job_state = lambda u, j: {"ok": True, "active": True, "state": "RUNNING",
                                 "node": "<GPU01>", "elapsed": "00:01:00", "name": "x"}
ctlmod.kill = lambda u, j: {"ok": True, "job_id": j, "cancelled": True}
ctlmod.log = lambda u, j, l=200: {"ok": True, "text": "[start_ssh] fake log line", "job_id": j}
ctlmod.rm_log = lambda u, j: {"ok": True, "job_id": j, "removed": True}
ctlmod.set_keys = lambda u, keys: {"ok": True, "user": u, "keys": len(keys)}
ctlmod.get_keys = lambda u: {"ok": True, "user": u,
                             "keys": ["ssh-ed25519 AAAA" + "C" * 60 + " eve@x"]}
# 页面热路径：_os_ok() 用的是轻量的 user-exists（只查管理节点）。
# 语义必须与 user_status 的 exists 一致，否则这里会掩盖真实的回归。
def _fake_os_exists(u):
    return u != "admin"


ctlmod.user_exists = lambda u: {"ok": True, "username": u,
                                "exists": _fake_os_exists(u),
                                "uid": "2000" if _fake_os_exists(u) else None}
ctlmod.user_status = lambda u: {"ok": True, "username": u, "exists": _fake_os_exists(u),
                                "uid_admin": "2000" if _fake_os_exists(u) else None,
                                "nodes": {}}
ctlmod.provision_user = lambda u, q, skip_os=False: (
    _set_fake_quota(u, (q or "500G")),  # 建号同时写假配额记录
    {"ok": True, "mode": "new", "username": u, "uid": 2000})[1]
ctlmod.unprovision_user = lambda u: (FAKE_QUOTA.pop(u, None),
                                     {"ok": True, "username": u, "uid": 2000})[1]
ctlmod.init_user = lambda u: {"ok": True, "username": u, "uid": 2000}
LAST_SUBMIT_SPEC = {}


def _fake_submit(spec):
    """记下最近一次提交的 spec：额外挂载只能由 root 助手从站点配置读，
    请求里绝不允许出现挂载字段（否则攻破门户就能把 DB/secret 挂进用户容器）。"""
    LAST_SUBMIT_SPEC.clear()
    LAST_SUBMIT_SPEC.update(spec)
    return {"ok": True, "job_id": 424242,
            "log_path": "/share/home/%s/.portal/logs/424242.out" % spec["user"],
            "command": "sbatch --fake " + spec["user"]}


ctlmod.submit = _fake_submit

# ---------- fake 镜像保存 / 个人镜像列表（写真实临时目录模拟 root 助手） ----------
def _fake_user_img_dir(user):
    d = os.path.join(IMGDIR, user)
    os.makedirs(d, exist_ok=True)
    return d

ctlmod.images = lambda user: {"ok": True, "user": user, "images": [
    {"name": f, "path": os.path.join(IMGDIR, user, f), "size": os.path.getsize(
        os.path.join(IMGDIR, user, f)), "mtime": int(os.path.getmtime(os.path.join(IMGDIR, user, f)))}
    for f in sorted(os.listdir(_fake_user_img_dir(user)))
    if f.lower().endswith(".sqsh")]}

def _fake_save_image(user, job_id, name, force):
    p = os.path.join(_fake_user_img_dir(user), name + ".sqsh")
    if os.path.exists(p) and not force:
        raise ctlmod.CtlError("个人镜像已存在: %s" % p)
    with open(p, "w") as fh:
        fh.write("fake-squashfs\n")
    return {"ok": True, "user": user, "job_id": int(job_id), "name": name,
            "path": p, "size": os.path.getsize(p), "node": "<GPU01>"}

ctlmod.save_image = _fake_save_image

# ---------- fake 配额 / slurm 关联（quota 用可变字典模拟 OS 实际值） ----------
FAKE_QUOTA = {
    # username: dict(used_kb=…, soft_kb=…, hard_kb=…)
}
GIB = 1024 * 1024

def _quota_row(u):
    if u not in FAKE_QUOTA:
        raise ctlmod.CtlError("fake 无配额记录: %s" % u)
    return {"ok": True, "username": u, "exists": True, **FAKE_QUOTA[u],
            "files_used": 12, "files_soft": 0, "files_hard": 0}

ctlmod.quota = _quota_row
ctlmod.quota_all = lambda: {"ok": True, "quotas": {
    u: {k: v for k, v in d.items()} for u, d in FAKE_QUOTA.items()}}
ctlmod.set_quota = lambda u, size: (_set_fake_quota(u, size), _quota_row(u))[1]
ctlmod.slurm_info = lambda u: {"ok": True, "username": u, "assocs": [
    {"account": "lab", "partition": "", "qos": "normal", "priority": "",
     "grp_tres": "", "grp_tres_mins": "", "max_tres": "", "max_tres_mins": "",
     "grp_jobs": "", "grp_submit": "", "max_jobs": "", "max_submit": "",
     "max_wall": "", "grp_wall": ""}],
    "qos": {"normal": {"priority": "5", "max_tres": "", "grp_tres": "",
                       "max_tres_mins": "", "grp_tres_mins": "", "grp_jobs": "",
                       "grp_submit": "", "max_jobs": "", "max_submit": "",
                       "max_wall": "", "grp_wall": ""}}}

def _set_fake_quota(u, size):
    n = int(size[:-1]) if size[:-1].isdigit() else 0
    kb = n * GIB if size.endswith("G") else n * GIB * 1024 if size.endswith("T") else n
    FAKE_QUOTA[u] = {"used_kb": FAKE_QUOTA.get(u, {}).get("used_kb", 2048),
                     "soft_kb": kb, "hard_kb": kb}
    return {"ok": True}

app = appmod.create_app(testing=True)
app.config["TESTING"] = True


def csrf_of(client, url):
    r = client.get(url)
    m = re.search(rb'name="_csrf" value="([a-f0-9]+)"', r.data)
    if not m:
        m = re.search(rb'<meta name="csrf-token" content="([a-f0-9]+)"', r.data)
    assert m, "no csrf on " + url
    return m.group(1).decode()


def login(client, user, pwd):
    tok = csrf_of(client, "/login")
    return client.post("/login", data={"_csrf": tok, "username": user, "password": pwd},
                       follow_redirects=True)


def main():
    appmod.main_bootstrap("admin", "AdminPass123", "admin")
    appmod.main_bootstrap("mgr", "MgrPass1234", "admin")  # 第二个管理员，用于权限测试
    appmod.main_bootstrap("root", "RootPass1234", "admin")  # 最高管理员（root 名）
    c = app.test_client()

    # 登录页 & 管理员登录
    r = c.get("/login")
    assert r.status_code == 200 and "用户登录".encode() in r.data
    r = login(c, "admin", "AdminPass123")
    assert "用户管理".encode() in r.data, "admin 应跳转到用户管理"

    # 创建普通用户（走假 provision）
    tok = csrf_of(c, "/admin/users")
    r = c.post("/admin/users/create", data={
        "_csrf": tok, "username": "alice", "display_name": "Alice 测试",
        "role": "user", "os_mode": "provision", "quota": "100G",
        "password": "AliceInit99"}, follow_redirects=True)
    assert "创建成功".encode() in r.data or "alice".encode() in r.data
    # existing 模式：自动导入 OS 上现有公钥
    r = c.post("/admin/users/create", data={
        "_csrf": tok, "username": "eve", "display_name": "Eve 既有账号",
        "role": "user", "os_mode": "existing", "quota": "100G",
        "password": "EvePass1122"}, follow_redirects=True)
    assert "已导入".encode() in r.data, (r.data[:600], r.data[-400:])
    # existing 路径必须把额度**真正落到 OS**
    # （历史 bug：只写门户库不落盘 → 页面按 OS 实读显示，明明设了额度却显示"不限"）
    assert FAKE_QUOTA.get("eve", {}).get("hard_kb") == 100 * 1024 * 1024, \
        "existing 开通没有落地配额: %s" % FAKE_QUOTA.get("eve")
    # ---- 安全：非 root 管理员**不能**创建管理员账号（否则 root 的授权边界失效）----
    r = c.post("/admin/users/create", data={
        "_csrf": tok, "username": "backdoor", "display_name": "越权管理员",
        "role": "admin", "os_mode": "existing", "quota": "100G",
        "password": "Backdoor123"}, follow_redirects=True)
    assert "只有超级管理员 root".encode() in r.data, r.data[:600]
    assert appmod.DB(appmod.DB_PATH).user_by_name("backdoor") is None, "越权管理员被创建了"
    # ---- 安全：系统/保留账号不允许建门户账号（否则可借 portal 的家目录挂载门户数据目录）----
    r = c.post("/admin/users/create", data={
        "_csrf": tok, "username": "portal", "display_name": "保留账号",
        "role": "user", "os_mode": "existing", "quota": "100G",
        "password": "PortalTest99"}, follow_redirects=True)
    assert "系统/保留账号".encode() in r.data, r.data[:600]
    assert appmod.DB(appmod.DB_PATH).user_by_name("portal") is None, "保留账号被建成了门户账号"
    # ---- 安全：新建用户的初始口令不能进客户端 Cookie（历史实现走 flash()，口令随 Set-Cookie 明文外发）----
    r = c.post("/admin/users/create", data={
        "_csrf": tok, "username": "carol", "display_name": "Carol",
        "role": "user", "os_mode": "provision", "quota": "100G",
        "password": "CarolInit99"}, follow_redirects=True)
    _cookies = " ".join(r.headers.getlist("Set-Cookie"))
    assert "CarolInit99" not in _cookies, "初始口令出现在 Set-Cookie 里: %s" % _cookies[:200]
    assert "CarolInit99".encode() in r.data, "初始口令应在响应体里一次性显示"

    # ---- 普通用户「留空密码」应当被随机生成，而不是被拒 ----
    # 界面写的是「留空=随机生成，只显示一次」（README 同），但服务端曾硬校验
    # `role == "user" and not password` → 报「普通用户初始密码不能为空」，用户建不出号。
    r = c.post("/admin/users/create", data={
        "_csrf": tok, "username": "blankpwd", "display_name": "留空密码",
        "role": "user", "os_mode": "provision", "quota": "100G",
        "password": ""}, follow_redirects=True)
    assert "初始密码不能为空".encode() not in r.data, \
        "留空密码被拒了（界面承诺「留空=随机生成」）: %s" % r.data[:600]
    assert "已随机生成初始密码".encode() in r.data, r.data[:600]
    _db = appmod.DB(appmod.DB_PATH)
    try:
        assert _db.user_by_name("blankpwd") is not None, "留空密码应能建出普通用户"
    finally:
        _db.close()

    # ---- 用户名只允许「小写字母/数字/下划线」，且以小写字母或下划线开头 ----
    # 客户端 pattern 曾写成 `v` 模式下的非法正则被浏览器整条忽略（等于没拦），
    # 所以服务端这条 USERNAME_RE 是真正的那道闸，必须自己挡住 `-` 和其它字符。
    for _bad in ("bad-name", "bad.name", "bad name", "9bad", "bad@x"):
        r = c.post("/admin/users/create", data={
            "_csrf": tok, "username": _bad, "display_name": "非法名",
            "role": "user", "os_mode": "provision", "quota": "100G",
            "password": "BadName1234"}, follow_redirects=True)
        assert "用户名不合法".encode() in r.data, \
            "非法用户名 %r 被放过了: %s" % (_bad, r.data[:400])
    _db = appmod.DB(appmod.DB_PATH)
    try:
        for _bad in ("bad-name", "bad.name", "bad name", "9bad", "bad@x"):
            assert _db.user_by_name(_bad) is None, "非法用户名 %r 被建出来了" % _bad
        # 正例：数字/下划线可以出现在首字符之后
        _ok = c.post("/admin/users/create", data={
            "_csrf": tok, "username": "good_name01", "display_name": "合法名",
            "role": "user", "os_mode": "provision", "quota": "100G",
            "password": "GoodName1234"}, follow_redirects=True)
        assert "用户名不合法".encode() not in _ok.data, _ok.data[:400]
        assert _db.user_by_name("good_name01") is not None, "合法用户名被拒了"
    finally:
        _db.close()

    # 管理员 + 「OS 已存在」同样要落地配额（线上事故：某运维账号设了 1T 却显示不限）
    # —— 管理员账号只能由 root 创建，所以这里切换到 root 会话
    c.get("/logout")
    login(c, "root", "RootPass1234")
    tok = csrf_of(c, "/admin/users")
    r = c.post("/admin/users/create", data={
        "_csrf": tok, "username": "adminexist", "display_name": "管理员既有账号",
        "role": "admin", "os_mode": "existing", "quota": "1T",
        "password": "AdminExist99"}, follow_redirects=True)
    assert "adminexist".encode() in r.data, r.data[:600]
    assert FAKE_QUOTA.get("adminexist", {}).get("hard_kb") == 1024 * 1024 * 1024, \
        "管理员 existing 开通没有落地配额: %s" % FAKE_QUOTA.get("adminexist")
    # 切回普通管理员会话继续后面的用例
    c.get("/logout")
    login(c, "admin", "AdminPass123")
    tok = csrf_of(c, "/admin/users")
    # 非 root 管理员的页面上不应出现「管理员」角色选项
    r = c.get("/admin/users")
    assert b'value="admin"' not in r.data, "非 root 管理员的建号表单不该提供管理员选项"
    # 「门户记录 ≠ OS」告警不应误报（以上账号都是刚对齐的）
    r = c.get("/admin/users")
    assert b"badge-warn" not in r.data, "配额一致性告警误报"
    # 反向用例：只改库里额度、不落 OS → 必须出现告警（这正是线上那次的形态）
    dbx = appmod.DB(appmod.DB_PATH)
    dbx.exec("UPDATE users SET quota='2T' WHERE username='eve'")
    dbx.close()
    r = c.get("/admin/users")
    assert b"badge-warn" in r.data, "门户库额度与 OS 硬限不一致时应给出告警"
    dbx = appmod.DB(appmod.DB_PATH)
    dbx.exec("UPDATE users SET quota='100G' WHERE username='eve'")
    dbx.close()
    # 建号即写入明文密码文件
    m = pwfile.load_map()
    assert m.get("alice") == "AliceInit99" and m.get("eve") == "EvePass1122", m

    # 登出 → 普通用户登录 → 资料页
    c.get("/logout")
    r = login(c, "alice", "AliceInit99")
    assert "我的资源".encode() in r.data
    # 普通用户导航栏不应出现管理员入口（用户管理/资源套餐/代申请资源仅管理员可见）
    assert "用户管理".encode() not in r.data and "资源套餐".encode() not in r.data
    assert "代申请资源".encode() not in r.data

    # 未完善资料时 apply 会被重定向
    r = c.get("/apply", follow_redirects=True)
    assert "完善个人资料".encode() in r.data

    # 修改显示名
    tok = csrf_of(c, "/profile")
    r = c.post("/profile/name", headers={"X-CSRF-Token": tok},
               data={"display_name": "小爱同学"})
    assert r.get_json()["ok"], r.get_json()
    r = c.get("/profile")
    assert "小爱同学".encode() in r.data
    assert 'name="display_name" value="小爱同学"'.encode() in r.data

    # 密码文件机制：明文密码统一存文件；root 编辑/门户回写都走同一条
    pwfile.upsert("alice", "NewAlice77")
    pwfile.upsert("nobodyx", "UnknownUser99")     # 未知用户（保留行，测试跳过）
    with open(os.environ["PORTAL_PASSWD_FILE"], "a", encoding="utf-8") as fh:
        fh.write("# 注释\n坏行没有冒号\n")          # 非法行应被忽略
    from portalapp.db import DB as _DB
    d = _DB(appmod.DB_PATH)
    st = pwfile.sync_once(d)
    d.close()
    assert st["changed"] >= 1 and "nobodyx" in st["unknown"] and len(st["malformed"]) >= 1, st
    c.get("/logout")
    r = login(c, "alice", "NewAlice77")
    assert "我的资源".encode() in r.data, "文件改密后应能用新密码登录"
    # 另开一个 alice 会话，用于验证"改密即吊销其它会话"
    c2 = app.test_client()
    login(c2, "alice", "NewAlice77")
    assert "我的资源".encode() in c2.get("/my").data
    # 用户自己改密 → 明文同步回写文件
    tok = csrf_of(c, "/profile")
    r = c.post("/profile/password", headers={"X-CSRF-Token": tok},
               data={"old": "NewAlice77", "new": "AliceNew88x", "new2": "AliceNew88x"})
    assert r.get_json()["ok"], r.get_json()
    assert pwfile.load_map().get("alice") == "AliceNew88x", "自改密码应回写明文文件"
    # 会话吊销：Flask 的 session 是无状态签名 Cookie，不靠 users.session_epoch 的话
    # 被偷走的 Cookie 会**永久有效**（登出/改密都吊销不掉）。
    assert c2.get("/my").status_code == 302, "改密后旧会话仍然有效（会话吊销失效）"
    assert c.get("/my").status_code == 200, "改密的当前会话不该被吊销"

    # 添加密钥与端口
    tok = csrf_of(c, "/profile")
    pub = "ssh-ed25519 AAAA" + "A" * 60 + " test@local"
    r = c.post("/profile/keys/add", headers={"X-CSRF-Token": tok},
               data={"pubkey": pub})
    assert r.get_json()["ok"]
    # 个人资料页应展示完整公钥内容
    r = c.get("/profile")
    assert pub.encode() in r.data, "个人资料页应显示完整公钥内容"
    r = c.post("/profile/ports/add", headers={"X-CSRF-Token": tok},
               data={"port": "28766"})
    assert r.get_json()["ok"]
    r = c.post("/profile/ports/add", headers={"X-CSRF-Token": tok},
               data={"port": "28767"})
    assert r.get_json()["ok"]
    # 重复端口被拒
    r = c.post("/profile/ports/add", headers={"X-CSRF-Token": tok},
               data={"port": "28766"})
    assert not r.get_json()["ok"]
    # 常用端口被拒
    r = c.post("/profile/ports/add", headers={"X-CSRF-Token": tok}, data={"port": "80"})
    assert not r.get_json()["ok"]

    # 申请页：镜像下拉/套餐/任务名表单
    r = c.get("/apply")
    assert r.status_code == 200
    assert "cuda12.8.0-devel-ubuntu24.04.sqsh" in r.text          # 镜像来自 /share/images 扫描
    assert 'name="task_name"' in r.text and 'name="plan_id"' in r.text
    assert "提交命令" not in r.text and "cmd-preview" not in r.text  # 不展示命令
    assert 'value="12"' in r.text                                   # 时长默认 12 小时
    assert 'value=""' in r.text or "自动分配" in r.text              # 节点默认不填
    tok2 = csrf_of(c, "/my")

    # 申请资源（POST）—— 镜像/套餐/任务名；本地 isfile stub
    orig_isfile = appmod.os.path.isfile
    appmod.os.path.isfile = lambda p: p.startswith("/share/images/") or orig_isfile(p)
    try:
        r = c.post("/apply", headers={"X-CSRF-Token": tok2}, data={
            "image": PUBIMG,
            "plan_id": "3", "task_name": "训练resnet", "hours": "12",
            "node": "<GPU01>", "port": "28766"})
        j = r.get_json()
        assert j["ok"], j
        # 提交 spec 的字段是**固定白名单**：额外挂载（NAS/数据集）只能由 root 助手从
        # site.conf 的 EXTRA_MOUNTS 读，请求里出现任何挂载字段就意味着"门户能自己指定
        # 挂载什么"——攻破门户即可挂走 /var/lib/cluster-portal 拿到 DB 与 Flask secret。
        assert set(LAST_SUBMIT_SPEC) == {"user", "node", "image", "gpus", "cpus",
                                         "mem_gb", "walltime", "port", "job_name"}, LAST_SUBMIT_SPEC
        assert not [k for k in LAST_SUBMIT_SPEC if "mount" in k.lower()], LAST_SUBMIT_SPEC
        pl = c.get("/api/plans").get_json()
        assert all("maxtime_h" in x and "gpu_model" in x for x in pl), "套餐应含 gpu_model/maxtime_h"
        # 超过套餐 maxtime 被拒并提示需管理员协助
        r = c.post("/apply", headers={"X-CSRF-Token": tok2}, data={
            "image": PUBIMG,
            "plan_id": "3", "task_name": "超长任务", "hours": "200",
            "node": "<GPU01>", "port": "28767"})
        err = r.get_json()["error"]
        assert not r.get_json()["ok"] and ("maxtime" in err or "管理员协助" in err), err
        # 同一端口（用户级任意节点）再次申请被拒
        r = c.post("/apply", headers={"X-CSRF-Token": tok2}, data={
            "image": PUBIMG,
            "plan_id": "4", "task_name": "另一个任务", "hours": "12",
            "node": "<GPU02>", "port": "28766"})
        assert not r.get_json()["ok"], "同一端口在使用中应被拒"
        # 任务名为空被拒
        r = c.post("/apply", headers={"X-CSRF-Token": tok2}, data={
            "image": PUBIMG,
            "plan_id": "3", "task_name": "  ", "hours": "12",
            "node": "<GPU01>", "port": "28767"})
        assert not r.get_json()["ok"] and "任务名称" in r.get_json()["error"]
        # 任务名长度 3-10（与前端 JS、模板、文档同一口径）
        r = c.post("/apply", headers={"X-CSRF-Token": tok2}, data={
            "image": PUBIMG,
            "plan_id": "3", "task_name": "ab", "hours": "12",
            "node": "<GPU01>", "port": "28767"})
        j = r.get_json()
        assert not j["ok"] and "3-10" in j["error"], j
        # 同用户重名被拒（不区分大小写；停机不释放名称，需删除记录）
        r = c.post("/apply", headers={"X-CSRF-Token": tok2}, data={
            "image": PUBIMG,
            "plan_id": "3", "task_name": "训练RESNET", "hours": "12",
            "node": "<GPU01>", "port": "28767"})
        j = r.get_json()
        assert not j["ok"] and "已被占用" in j["error"], j
    finally:
        appmod.os.path.isfile = orig_isfile

    # 任务名唯一性：DB 层查重语义（不区分大小写 / 排除自身 / 删除记录才释放名称）
    from portalapp.db import DB as _DB2
    dbx = _DB2(appmod.DB_PATH)
    aid = dbx.user_by_name("alice")["id"]
    assert dbx.instance_name_taken(aid, "训练resnet") is not None, "已有同名实例应命中"
    pid = dbx.add_instance(aid, 3, "CaseProbe", "", 0, 1, 1, 29999, "12:00:00",
                           PUBIMG, 999999, "COMPLETED", "", "", "")
    assert dbx.instance_name_taken(aid, "caseprobe") is not None, "重名判定应不区分大小写"
    assert dbx.instance_name_taken(aid, "caseprobe", exclude_iid=pid) is None, "排除自身后不冲突"
    dbx.del_instance(pid)
    assert dbx.instance_name_taken(aid, "CaseProbe") is None, "删除记录后名称应释放"
    dbx.close()

    # 我的资源 + API
    # 「运行中 vs 启动中」由日志里的就绪横幅决定（root 助手 ssh-ready → app._ssh_ready）。
    # 先固定成"已就绪"，
    # 让下面的常规断言按「运行中」跑；「启动中」的专门用例紧跟着单独测。
    _real_ssh_ready = appmod.ctl.ssh_ready
    appmod.ctl.ssh_ready = lambda user, jobid: {'ok': True, 'ready': True}
    appmod._ssh_ready_cache.clear()
    r = c.get("/my")
    assert r.status_code == 200 and "运行中".encode() in r.data
    assert "我的配额".encode() in r.data and "Slurm".encode() in r.data, "我的资源应展示配额/Slurm 额度"
    assert "100 GiB".encode() in r.data, "alice 配额 100G 应显示为硬上限 100 GiB"
    r = c.get("/api/my/quota")
    qj = r.get_json()
    assert qj["ok"] and qj["os_ok"] and qj["disk"]["hard_kb"] == 100 * 1024 * 1024, qj
    assert qj["slurm"]["accounts"] == "lab" and qj["slurm"]["total"] == "不限", qj
    r = c.get("/api/my/instances")
    assert r.get_json()[0]["job_id"] == 424242

    # ---- 「启动中」：Slurm 报 RUNNING，但容器里的 sshd 还没在监听 ----
    # 光有 RUNNING 不等于用户能 ssh 进去（镜像导入 + sshd 起来还要几秒到几十秒）。
    appmod.ctl.ssh_ready = lambda user, jobid: {'ok': True, 'ready': False}
    appmod._ssh_ready_cache.clear()
    r = c.get("/my")
    _row = re.search(r"<tr data-id=\"\d+\">.*?</tr>", r.text, re.S)
    assert _row, "我的资源里应有实例行"
    assert "启动中" in _row.group(0), "sshd 还没监听时应显示「启动中」"
    assert "运行中" not in _row.group(0), "sshd 还没监听时那行不该显示「运行中」"
    assert b"st-starting" in r.data, "「启动中」应有独立的徽章样式类"
    assert "act-stop".encode() in r.data, "「启动中」仍是活跃实例，停机按钮不能消失"
    j = c.get("/api/my/instances").get_json()[0]
    assert j["state_cn"] == "启动中" and j["state_key"] == "starting" and j["starting"] is True, j
    # 就绪之后必须变回「运行中」，不能卡在启动中
    appmod.ctl.ssh_ready = lambda user, jobid: {'ok': True, 'ready': True}
    appmod._ssh_ready_cache.clear()
    r = c.get("/my")
    _row = re.search(r"<tr data-id=\"\d+\">.*?</tr>", r.text, re.S)
    assert _row and "运行中" in _row.group(0) and "启动中" not in _row.group(0), \
        "sshd 起来后那行应变回「运行中」"
    j = c.get("/api/my/instances").get_json()[0]
    assert j["state_cn"] == "运行中" and j["state_key"] == "running" and j["starting"] is False, j
    appmod.ctl.ssh_ready = _real_ssh_ready
    appmod._ssh_ready_cache.clear()

    # 日志：容器日志 + 门户操作日志（提交/停机/保存镜像等）
    iid = 1
    r = c.get("/instances/%d/log" % iid)
    j = r.get_json()
    assert j["ok"] and "text" in j, j
    evs = j.get("events") or []
    assert any(e["kind"] == "submit" for e in evs), "申请成功后应有『提交』门户事件: %s" % evs
    assert all({"ts", "kind", "label", "message"} <= set(e) for e in evs), evs
    assert j.get("log_error") == "", "日志文件存在时 log_error 应为空: %s" % j.get("log_error")
    # 停机
    r = c.post("/instances/%d/stop" % iid, headers={"X-CSRF-Token": tok2})
    assert r.get_json()["ok"]
    evs = c.get("/instances/%d/log" % iid).get_json()["events"]
    assert any(e["kind"] == "stop" for e in evs), "停机后应有『停机』门户事件: %s" % evs
    # 停机后的实例：显示“重新启动/删除”，重启后回到排队中(同一行)，可再次删除
    r = c.get("/my")
    assert "已停止".encode() in r.data and "重新启动".encode() in r.data \
        and "删除".encode() in r.data, "停机后的资源应出现重新启动/删除操作"
    orig_isfile_rs = appmod.os.path.isfile
    appmod.os.path.isfile = lambda p: p.startswith("/share/images/") or orig_isfile_rs(p)
    try:
        r = c.post("/instances/%d/restart" % iid, headers={"X-CSRF-Token": tok2})
        j = r.get_json()
        assert j["ok"] and j["job_id"] == 424242, j
    finally:
        appmod.os.path.isfile = orig_isfile_rs
    r = c.get("/api/my/instances")
    row = r.get_json()[0]
    assert row["id"] == iid and row["state"] in ("PENDING", "RUNNING"), \
        "重启应复用同一行并回到活跃状态(假 ctl 会立即 RUNNING)"
    # 重启后不再显示“重新启动”（活跃态），停机后再删除
    r = c.get("/my")
    assert "重新启动".encode() not in r.data
    r = c.post("/instances/%d/stop" % iid, headers={"X-CSRF-Token": tok2})
    assert r.get_json()["ok"]
    r = c.post("/instances/%d/delete" % iid, headers={"X-CSRF-Token": tok2})
    j = r.get_json()
    assert j["ok"] and "日志" in j["msg"], j
    r = c.get("/api/my/instances")
    assert all(x["id"] != iid for x in r.get_json()), "删除后实例应不在列表"

    # ================== 保存镜像（个人镜像 / 分组 / 到期自动保存）==================
    # 重新申请两个实例：S1 用公共镜像(手动保存测试)，S2 用 S1 保存出的个人镜像(个人镜像可用性+到期自动保存)
    def apply_img(p_img, task, port):
        return c.post("/apply", headers={"X-CSRF-Token": tok2}, data={
            "image": p_img, "plan_id": "3", "task_name": task, "hours": "12",
            "node": "<GPU01>", "port": port})

    r = apply_img(PUBIMG, "快照测试", 28766)
    j = r.get_json()
    assert j["ok"], j
    s1 = j["instance_id"]
    r = c.get("/api/my/instances")
    assert any(x["id"] == s1 and x["state"] == "RUNNING" for x in r.get_json())
    # 运行中的行应有“保存镜像”按钮
    r = c.get("/my")
    assert "保存镜像".encode() in r.data and "act-save".encode() in r.data, "运行中资源应有保存镜像操作"

    # 非法镜像名（空格/特殊字符）被拒
    r = c.post("/instances/%d/save-image" % s1, headers={"X-CSRF-Token": tok2},
               data={"name": "bad name!"})
    jj = r.get_json()
    assert not jj["ok"] and "下划线" in jj["error"], jj
    # 合法名保存（SAVE_SYNC=1 同步完成）
    r = c.post("/instances/%d/save-image" % s1, headers={"X-CSRF-Token": tok2},
               data={"name": "myfirst"})
    jj = r.get_json()
    assert jj["ok"] and "已保存" in jj["msg"], jj
    # 保存镜像必须留下门户事件（含耗时），供日志弹窗展示
    evs = c.get("/instances/%d/log" % s1).get_json()["events"]
    save_evs = [e for e in evs if e["kind"] == "save"]
    assert any("myfirst" in e["message"] for e in save_evs), \
        "保存镜像后应有『保存镜像』事件: %s" % evs
    assert any("耗时" in e["message"] for e in save_evs), \
        "保存完成的事件应包含耗时: %s" % save_evs
    # 同名不覆盖 → need_force；确认覆盖(force) → 成功
    r = c.post("/instances/%d/save-image" % s1, headers={"X-CSRF-Token": tok2},
               data={"name": "myfirst"})
    jj = r.get_json()
    assert not jj["ok"] and jj.get("need_force"), jj
    r = c.post("/instances/%d/save-image" % s1, headers={"X-CSRF-Token": tok2},
               data={"name": "myfirst", "force": "1"})
    assert r.get_json()["ok"], r.get_json()
    # 行上应有保存结果（saving=0, last_save 含路径）
    row1 = next(x for x in c.get("/api/my/instances").get_json() if x["id"] == s1)
    assert row1["saving"] == 0 and row1["last_save"], row1
    # 个人镜像出现在“我的镜像”分组：/api/images 与 /apply 页面
    imgs = c.get("/api/images").get_json()
    assert imgs["mine"] and any(x["name"] == "myfirst.sqsh" for x in imgs["mine"]), imgs
    r = c.get("/apply")
    assert "公共镜像".encode() in r.data and "我的镜像".encode() in r.data \
        and "myfirst.sqsh".encode() in r.data, "申请页应分组展示公共/个人镜像"
    # 用“个人镜像”再申请（白名单放行，验证个人镜像可被自身使用）
    mine_img = next(x["path"] for x in imgs["mine"] if x["name"] == "myfirst.sqsh")
    r = apply_img(mine_img, "个人镜像跑", 28767)
    j = r.get_json()
    assert j["ok"], j
    s2 = j["instance_id"]
    # 先对账到 RUNNING（并记录 started_at），随后回拨开始时刻触发到期自动保存
    for _ in range(20):
        cand = next((x for x in c.get("/api/my/instances").get_json() if x["id"] == s2), None)
        if cand and cand["state"] == "RUNNING" and cand.get("started_at"):
            break
        time.sleep(0.2)
    dbx = _DB(appmod.DB_PATH)
    dbx.exec("UPDATE instances SET started_at='2020-01-01T00:00:00' WHERE id=?", (s2,))
    dbx.close()
    appmod.expiry_tick()
    last = None
    for _ in range(40):
        cand = next((x for x in c.get("/api/my/instances").get_json() if x["id"] == s2), None)
        last = cand
        if cand and cand["state"] == "CANCELLED" and cand.get("auto_saved_path"):
            break
        time.sleep(0.2)
    assert last and last["state"] == "CANCELLED" and last.get("auto_saved_path"), last
    aname = os.path.basename(last["auto_saved_path"])
    assert aname.startswith("auto_") and aname.endswith(".sqsh") and \
        re.match(r"^auto_[A-Za-z0-9_]+\.sqsh$", aname), aname
    assert os.path.isfile(os.path.join(IMGDIR, "alice", aname)), "到期自动保存文件应存在"
    assert "到期" in (last["last_save"] or ""), last["last_save"]
    # 收尾：停机+删除 s1；删除 s2（终端态）
    r = c.post("/instances/%d/stop" % s1, headers={"X-CSRF-Token": tok2})
    assert r.get_json()["ok"]
    r = c.post("/instances/%d/delete" % s1, headers={"X-CSRF-Token": tok2})
    assert r.get_json()["ok"]
    # 删除实例应连带清掉它的门户事件（instance_events 是 ON DELETE CASCADE）
    dbxe = _DB(appmod.DB_PATH)
    assert not dbxe.events_for(s1), "删除实例后应无残留门户事件"
    assert dbxe.q1("SELECT COUNT(*) n FROM instance_events WHERE instance_id=?", (s1,))["n"] == 0
    dbxe.close()
    r = c.post("/instances/%d/delete" % s2, headers={"X-CSRF-Token": tok2})
    assert r.get_json()["ok"], r.get_json()

    # 我的资源页：弹窗遮罩默认隐藏
    r = c.get("/my")
    assert 'id="log-modal" hidden'.encode() in r.data and 'id="detail-modal" hidden'.encode() in r.data

    # 集群状态页
    r = c.get("/status")
    assert r.status_code == 200
    # 每个资源都要标出「空闲/总」，并且值真的是 空闲/总
    for _h in ("CPU（空闲/总）", "内存（空闲/总）", "GPU（空闲/总）"):
        assert _h.encode() in r.data, "状态页表头缺 %s" % _h
    assert b"16/16" in r.data, "CPU 空闲/总没渲染"
    assert b"23G/31G" in r.data, "内存 空闲/总没渲染（23741/1024≈23G，总量 31933/1024≈31G）"
    assert b"0/1" in r.data, "GPU 空闲/总没渲染（已用 1 / 总 1）"

    # 管理员页回归：管理员公钥门槛 + 用户列表统计列
    c.get("/logout")
    login(c, "admin", "AdminPass123")
    # 管理员（无同名 OS 账号）不能登记公钥
    tok = csrf_of(c, "/profile")
    r = c.post("/profile/keys/add", headers={"X-CSRF-Token": tok},
               data={"pubkey": "ssh-ed25519 AAAA" + "B" * 60 + " admin@x"})
    assert not r.get_json()["ok"] and "集群".encode("utf-8").decode() in r.get_json()["error"]
    # 用户列表：统计列应为数字而非 dict 方法对象（回归检查）
    r = c.get("/admin/users")
    assert b"built-in method" not in r.data and "SSH密钥".encode() in r.data
    assert re.search(r"<td title=\"SSH 公钥数\">\d+</td>", r.data.decode())

    # ---- 配额管理：用户管理页显示 OS 实际配额 + “改配额”按钮，POST 真正写 OS(fake) ----
    dbxq = _DB(appmod.DB_PATH)
    uid_alice = dbxq.q1("SELECT id FROM users WHERE username='alice'")["id"]
    dbxq.close()
    r = c.get("/admin/users")
    alice_tr = next((mm.group(0) for mm in re.finditer(r"<tr>.*?</tr>", r.text, re.S)
                     if ">alice<" in mm.group(0)), None)
    assert alice_tr, "用户列表应有 alice 行"
    assert "100 GiB" in alice_tr and "改配额" in alice_tr, "配额列应显示 OS 实际 100G 且含改配额按钮"
    tokq = csrf_of(c, "/admin/users")
    r = c.post("/admin/users/%s/quota" % uid_alice,
               headers={"X-CSRF-Token": tokq}, data={"quota": "200G"})
    j = r.get_json()
    assert j["ok"] and j.get("hard_kb") == 200 * 1024 * 1024, j
    assert FAKE_QUOTA["alice"]["hard_kb"] == 200 * 1024 * 1024, "改配额必须写 OS(fake) 实际值"
    # 「不限」：改配额要把 OS 软/硬限清成 0，且门户库记录为「不限」（不得再挂不一致告警）
    r = c.post("/admin/users/%s/quota" % uid_alice,
               headers={"X-CSRF-Token": tokq}, data={"quota": "不限"})
    j = r.get_json()
    assert j["ok"] and j.get("hard_kb") == 0, j
    assert FAKE_QUOTA["alice"]["hard_kb"] == 0, "「不限」必须把 OS 硬限清成 0"
    dbxu = _DB(appmod.DB_PATH)
    assert dbxu.q1("SELECT quota FROM users WHERE id=?", (uid_alice,))["quota"] == "不限", \
        "门户库应把「不限」按规范写法存下来"
    dbxu.close()
    r = c.get("/admin/users")
    alice_tr = next((mm.group(0) for mm in re.finditer(r"<tr>.*?</tr>", r.text, re.S)
                     if ">alice<" in mm.group(0)), None)
    assert alice_tr and "badge-warn" not in alice_tr, "「不限」不应触发配额不一致告警"
    # 别名 0 / unlimited 也接受，并归一化成「不限」
    r = c.post("/admin/users/%s/quota" % uid_alice,
               headers={"X-CSRF-Token": tokq}, data={"quota": "unlimited"})
    assert r.get_json()["ok"], r.get_json()
    dbxu = _DB(appmod.DB_PATH)
    assert dbxu.q1("SELECT quota FROM users WHERE id=?", (uid_alice,))["quota"] == "不限"
    dbxu.close()
    # 建号表单里要能选到「不限」
    assert "不限" in c.get("/admin/users").text, "建号表单的配额下拉应包含「不限」"
    # 恢复 200G 供后续断言使用
    r = c.post("/admin/users/%s/quota" % uid_alice,
               headers={"X-CSRF-Token": tokq}, data={"quota": "200G"})
    assert r.get_json()["ok"], r.get_json()
    # root 行（保留账号）不能直接改配额
    dbxr = _DB(appmod.DB_PATH)
    uid_root = dbxr.q1("SELECT id FROM users WHERE username='root'")["id"]
    dbxr.close()
    r = c.post("/admin/users/%s/quota" % uid_root,
               headers={"X-CSRF-Token": tokq}, data={"quota": "1T"})
    assert not r.get_json()["ok"] and "保留账号" in r.get_json()["error"], r.get_json()

    # ---- 保留账号（root）的「改配额」按钮 ----
    # 后端 admin_user_quota 明确允许把保留账号设为「不限」（清除遗留额度，那是唯一的对齐途径），
    # 但前端曾用 `not is_reserved_os(...)` 把按钮藏掉 → root 行挂着「门户记 500G ≠ OS」的告警，
    # 而它建议你去点的那个按钮根本不存在。这里守住：按钮在、带 data-reserved 标记、且不再误报。
    # 用独立 client，避免动到 c 的 admin 会话（后面还有用例复用它的 CSRF token）。
    croot = app.test_client()
    login(croot, "root", "RootPass1234")
    dbxr2 = _DB(appmod.DB_PATH)
    uroot = dbxr2.user_by_name("root")
    dbxr2.close()
    r = croot.get("/admin/users")
    root_tr = next((mm.group(0) for mm in re.finditer(r"<tr>.*?</tr>", r.text, re.S)
                    if ">root<" in mm.group(0)), None)
    assert root_tr, "用户列表应有 root 行"
    assert "改配额" in root_tr, "root 行也该有「改配额」按钮（后端支持设为「不限」）"
    assert 'data-reserved="1"' in root_tr, "root 的改配额按钮应带 data-reserved 标记"
    assert "badge-warn" not in root_tr, "保留账号不该挂「门户记 ≠ OS」告警"
    tokr = csrf_of(croot, "/admin/users")
    r = croot.post("/admin/users/%s/quota" % uroot["id"],
                   headers={"X-CSRF-Token": tokr}, data={"quota": "100G"})
    assert not r.get_json()["ok"] and "保留账号" in r.get_json()["error"], r.get_json()
    r = croot.post("/admin/users/%s/quota" % uroot["id"],
                   headers={"X-CSRF-Token": tokr}, data={"quota": "不限"})
    assert r.get_json()["ok"], r.get_json()
    dbxr2 = _DB(appmod.DB_PATH)
    try:
        assert dbxr2.user_by_name("root")["quota"] == "不限", "root 的库内额度应对齐成「不限」"
    finally:
        dbxr2.close()
    assert "badge-warn" not in next(
        (mm.group(0) for mm in re.finditer(r"<tr>.*?</tr>", croot.get("/admin/users").text, re.S)
         if ">root<" in mm.group(0)), "")

    # ---- 「资源套餐」root-only：普通管理员连入口都不给，后端 6 条路由全部 403 ----
    # 套餐决定**所有人**能选什么资源，与同样是 root-only 的「授予/撤销管理员」同级。
    # 普通管理员的授权边界是"管用户 + 代申请"，不含改全站资源规格。
    assert c.get("/admin/plans").status_code == 403, "普通管理员不应能访问资源套餐"
    assert "资源套餐" not in c.get("/admin/users").text, "普通管理员的导航里不该出现资源套餐"
    r = c.post("/admin/plans/add", data={
        "name": "越权套餐", "gpus": "0", "cpus": "1", "mem_gb": "1", "maxtime_h": "1"})
    assert r.status_code == 403, "普通管理员不应能新增套餐"
    r = c.post("/admin/plans/3/edit", data={"name": "越权改名"})
    assert r.status_code == 403, "普通管理员不应能改套餐"
    assert croot.get("/admin/plans").status_code == 200, "root 应能访问资源套餐"
    assert "资源套餐" in croot.get("/admin/users").text, "root 的导航里应有资源套餐"
    dbp = _DB(appmod.DB_PATH)
    try:
        assert dbp.q1("SELECT COUNT(*) n FROM plans WHERE name IN ('越权套餐','越权改名')")["n"] == 0, \
            "越权请求竟然改到了套餐"
        n0 = dbp.q1("SELECT COUNT(*) n FROM plans")["n"]
    finally:
        dbp.close()
    # root 的正路仍然通：新增 + 删除
    tokp = csrf_of(croot, "/admin/plans")
    r = croot.post("/admin/plans/add", data={
        "_csrf": tokp, "name": "冒烟套餐X", "description": "smoke",
        "gpus": "0", "gpu_model": "", "cpus": "2", "mem_gb": "4", "maxtime_h": "12"})
    assert r.status_code in (200, 302), r.status_code
    dbp = _DB(appmod.DB_PATH)
    try:
        row = dbp.q1("SELECT id FROM plans WHERE name='冒烟套餐X'")
        assert row, "root 新增套餐失败"
        assert dbp.q1("SELECT COUNT(*) n FROM plans")["n"] == n0 + 1, "新增后套餐数应 +1"
        pid = row["id"]
    finally:
        dbp.close()
    tokp = csrf_of(croot, "/admin/plans")
    r = croot.post("/admin/plans/%d/delete" % pid, data={"_csrf": tokp})
    assert r.get_json()["ok"], r.get_json()
    dbp = _DB(appmod.DB_PATH)
    try:
        assert dbp.q1("SELECT COUNT(*) n FROM plans")["n"] == n0, "删除后套餐数应还原"
    finally:
        dbp.close()

    # 管理员代申请资源（帮助 alice 提交，用其端口 28766）
    dbx0 = _DB(appmod.DB_PATH)
    uid_mgr = dbx0.q1("SELECT id FROM users WHERE username=?", ("mgr",))["id"]
    dbx0.close()
    r = c.get("/api/apply-targets")
    tgs = r.get_json()
    alice_t = next(x for x in tgs if x["username"] == "alice")
    assert alice_t["ports"], "代申请列表应含 alice 及其端口"
    orig_isfile2 = appmod.os.path.isfile
    appmod.os.path.isfile = lambda p: p.startswith("/share/images/") or orig_isfile2(p)
    try:
        r = c.post("/admin/apply", headers={"X-CSRF-Token": tok},
                   data={"user_id": str(alice_t["id"]),
                         "image": PUBIMG,
                         "plan_id": "3", "task_name": "帮alice提交", "hours": "1",
                         "node": "<GPU01>", "port": "28766"})
        j = r.get_json()
        assert j["ok"], j
    finally:
        appmod.os.path.isfile = orig_isfile2
    # 非 root 管理员不能调整他人管理员权限
    r = c.post("/admin/users/%s/role" % uid_mgr, headers={"X-CSRF-Token": tok})
    assert not r.get_json()["ok"] and "root" in r.get_json()["error"]
    # root 可授予/撤销管理员（对 eve 往返）
    c.get("/logout")
    login(c, "root", "RootPass1234")
    dbx2 = _DB(appmod.DB_PATH)
    uid_eve = dbx2.q1("SELECT id FROM users WHERE username=?", ("eve",))["id"]
    dbx2.close()
    tokr = csrf_of(c, "/admin/users")
    r = c.post("/admin/users/%s/role" % uid_eve, headers={"X-CSRF-Token": tokr})
    assert r.get_json()["ok"] and "管理员" in r.get_json()["msg"], r.get_json()
    r = c.post("/admin/users/%s/role" % uid_eve, headers={"X-CSRF-Token": tokr})
    assert r.get_json()["ok"] and "普通用户" in r.get_json()["msg"], r.get_json()
    # root 不能改自己的权限
    dbx3 = _DB(appmod.DB_PATH)
    uid_root = dbx3.q1("SELECT id FROM users WHERE username='root'")["id"]
    dbx3.close()
    r = c.post("/admin/users/%s/role" % uid_root, headers={"X-CSRF-Token": tokr})
    assert not r.get_json()["ok"]
    # 保留账号 root：仍禁止设**具体额度**，但允许设「不限」（清除限额），
    # 这是把库里遗留默认值（建号脚本曾写 500G）与 OS 现状对齐的途径
    r = c.post("/admin/users/%s/quota" % uid_root,
               headers={"X-CSRF-Token": tokr}, data={"quota": "500G"})
    assert not r.get_json()["ok"] and "保留账号" in r.get_json()["error"], r.get_json()
    r = c.post("/admin/users/%s/quota" % uid_root,
               headers={"X-CSRF-Token": tokr}, data={"quota": "不限"})
    assert r.get_json()["ok"], r.get_json()
    dbxq2 = _DB(appmod.DB_PATH)
    assert dbxq2.q1("SELECT quota FROM users WHERE id=?", (uid_root,))["quota"] == "不限"
    dbxq2.close()
    root_tr = next((mm.group(0) for mm in re.finditer(r"<tr>.*?</tr>", c.get("/admin/users").text, re.S)
                    if ">root<" in mm.group(0)), None)
    assert root_tr and "badge-warn" not in root_tr, "root 的「不限」不应触发一致性告警"
    c.get("/logout")
    login(c, "admin", "AdminPass123")

    # 管理员不能重置其它管理员的门户密码（只对普通用户开放）
    r = c.get("/admin/users")
    dbx = _DB(appmod.DB_PATH)
    uid_mgr = dbx.q1("SELECT id FROM users WHERE username=?", ("mgr",))["id"]
    dbx.close()
    assert uid_mgr, "找不到 mgr 管理员"
    tok = csrf_of(c, "/admin/users")
    r = c.post("/admin/users/%s/resetpwd" % uid_mgr,
               headers={"X-CSRF-Token": tok})
    j = r.get_json()
    assert not j["ok"] and "管理员账号".encode("utf-8").decode() in j["error"], j
    # 管理员行也不应渲染任何操作按钮（重置/停用/删除 均只对普通用户开放）
    r = c.get("/admin/users")
    for row in re.finditer(r"<tr>.*?</tr>", r.text, re.S):
        if ">mgr<" in row.group(0):
            assert "/resetpwd" not in row.group(0)
            assert "/toggle" not in row.group(0)
            assert "/delete" not in row.group(0)
            assert "由 root 管理" in row.group(0)
            break
    # 接口层同样拒绝：停用/删除 其它管理员
    r = c.post("/admin/users/%s/toggle" % uid_mgr, headers={"X-CSRF-Token": tok})
    assert not r.get_json()["ok"] and "管理员账号".encode("utf-8").decode() in r.get_json()["error"]
    r = c.post("/admin/users/%s/delete" % uid_mgr, headers={"X-CSRF-Token": tok})
    assert not r.get_json()["ok"] and "管理员账号".encode("utf-8").decode() in r.get_json()["error"]

    # 管理员：删除用户（alice 由假 provision 建号 → 会调假 unprovision + 删门户记录）
    r = c.get("/admin/users")
    uid_alice = None
    for row in re.finditer(r"<tr>.*?</tr>", r.text, re.S):
        if ">alice<" in row.group(0):
            uid_alice = re.search(r"/admin/users/(\d+)/delete", row.group(0)).group(1)
            break
    assert uid_alice, "用户列表应有 alice 行的删除按钮"
    # 管理员重置普通用户密码 → 明文同步回写文件，且新密码可登录
    tok = csrf_of(c, "/admin/users")
    r = c.post("/admin/users/%s/resetpwd" % uid_alice, headers={"X-CSRF-Token": tok})
    j = r.get_json()
    assert j["ok"] and pwfile.load_map().get("alice") == j["password"], j
    c.get("/logout")
    r = login(c, "alice", j["password"])
    assert "我的资源".encode() in r.data, "重置后的密码应可登录"
    # 回到管理员会话执行删除
    c.get("/logout")
    login(c, "admin", "AdminPass123")
    tok = csrf_of(c, "/admin/users")
    r = c.post("/admin/users/%s/delete" % uid_alice,
               headers={"X-CSRF-Token": tok})
    j = r.get_json()
    assert j["ok"], j
    r = c.get("/admin/users")
    assert not re.search(rb"<tr>.*?alice.*?</tr>", r.data, re.S), "用户列表应已无 alice 行"
    # 删除后 alice 无法登录，且明文文件中 alice 行被移除
    c.get("/logout")
    r = login(c, "alice", "AliceInit99")
    assert "用户名或密码错误".encode() in r.data
    assert "alice" not in pwfile.load_map(), "删除用户后应同步移除密码文件中的行"

    check_legacy_dup_db()
    check_hot_path_is_light()

    print("SMOKE_OK (db at %s)" % TMP)


def check_hot_path_is_light():
    """回归保护：页面热路径必须走 user-exists（只查管理节点），不许走 user-status。

    背景（真实线上性能问题）：user-status 为了给排障提供逐节点 UID 会对每台计算节点
    各 ssh 一次（实测 ~0.34s/台）。_os_ok() 在 /my、/profile、/apply 的每个请求里
    都会被调用，于是管理员账号打开【我的资源】要 0.89s，其中 0.7s 全花在一份门户
    从不显示的逐节点明细上。改成 user-exists 后 ~0.04s。
    这里用计数假实现把"退回慢命令"钉死在测试里。
    """
    calls = []
    orig_status, orig_exists = ctlmod.user_status, ctlmod.user_exists
    # 专用管理员账号：main() 中途会把 mgr 删掉；root 是保留账号，不走"实时确认"分支
    appmod.main_bootstrap("hotpathadm", "HotPath1234", "admin")
    ctlmod.user_status = lambda u: (calls.append(u), orig_status(u))[1]
    try:
        c = app.test_client()
        # admin 角色 → _os_ok 会走"实时向集群确认"的分支（正是出事的那条）
        login(c, "hotpathadm", "HotPath1234")
        for path in ("/my", "/profile", "/apply"):
            r = c.get(path, follow_redirects=True)
            assert r.status_code == 200, (path, r.status_code)
    finally:
        ctlmod.user_status, ctlmod.user_exists = orig_status, orig_exists
    assert calls == [], \
        "页面路径调用了慢命令 user-status（应改用 user_exists）: %s" % calls
    assert ctlmod.user_exists("hotpathadm")["exists"] is True


def check_legacy_dup_db():
    """老库已有重名任务时：唯一索引建不上，但门户必须照常启动（降级为应用层查重）。"""
    import io
    import contextlib
    from portalapp.db import DB as _DB3, init_db as _init_db
    legacy = os.path.join(TMP, "legacy")
    os.makedirs(legacy, exist_ok=True)
    dbp = os.path.join(legacy, "portal.db")
    db = _DB3(dbp)
    _init_db(db)
    db.exec("DROP INDEX IF EXISTS ux_inst_user_task")   # 模拟升级前的老库
    assert db.q1("SELECT 1 FROM sqlite_master WHERE name='ux_inst_user_task'") is None
    db.add_user("dupuser", "dupuser", "user", "pbkdf2$1$00$00", "10G", "existing")
    uid = db.user_by_name("dupuser")["id"]
    for i in (1, 2):
        db.add_instance(uid, None, "same", "", 0, 1, 1, 30000 + i, "12:00:00",
                        PUBIMG, 900000 + i, "COMPLETED", "", "", "")
    db.close()
    # 再次初始化：不能抛异常，且应打印降级警告
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        db = _DB3(dbp)
        _init_db(db)
    assert db.q1("SELECT 1 FROM sqlite_master WHERE name='ux_inst_user_task'") is None, \
        "有重名时不应建出唯一索引"
    assert "任务名唯一索引创建失败" in err.getvalue(), err.getvalue() or "应打印降级警告"
    # 降级后应用层查重仍然生效
    assert db.instance_name_taken(uid, "same") is not None, "应用层查重应仍能拦住重名"
    db.close()


if __name__ == "__main__":
    main()
