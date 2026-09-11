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
    {"name": "<GPU02>", "state": "idle", "avail": "up", "cpus_alloc": 0,
     "cpus_idle": 16, "cpus_total": 16, "mem_mb": 31933,
     "gres": "gpu:3060:1", "ip": "<GPU02_IP>"}]}
ctlmod.job_state = lambda u, j: {"ok": True, "active": True, "state": "RUNNING",
                                 "node": "<GPU02>", "elapsed": "00:01:00", "name": "x"}
ctlmod.kill = lambda u, j: {"ok": True, "job_id": j, "cancelled": True}
ctlmod.log = lambda u, j, l=200: {"ok": True, "text": "[start_ssh] fake log line", "job_id": j}
ctlmod.rm_log = lambda u, j: {"ok": True, "job_id": j, "removed": True}
ctlmod.set_keys = lambda u, keys: {"ok": True, "user": u, "keys": len(keys)}
ctlmod.get_keys = lambda u: {"ok": True, "user": u,
                             "keys": ["ssh-ed25519 AAAA" + "C" * 60 + " eve@x"]}
ctlmod.user_status = lambda u: {"ok": True, "username": u, "exists": u != "admin",
                                "uid_admin": "2000", "nodes": {}}
ctlmod.provision_user = lambda u, q, skip_os=False: (
    _set_fake_quota(u, (q or "500G")),  # 建号同时写假配额记录
    {"ok": True, "mode": "new", "username": u, "uid": 2000})[1]
ctlmod.unprovision_user = lambda u: (FAKE_QUOTA.pop(u, None),
                                     {"ok": True, "username": u, "uid": 2000})[1]
ctlmod.init_user = lambda u: {"ok": True, "username": u, "uid": 2000}
ctlmod.submit = lambda spec: {"ok": True, "job_id": 424242,
                              "log_path": "/share/home/%s/.portal/logs/424242.out" % spec["user"],
                              "command": "sbatch --fake " + spec["user"]}

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
            "path": p, "size": os.path.getsize(p), "node": "<GPU02>"}

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
    # 用户自己改密 → 明文同步回写文件
    tok = csrf_of(c, "/profile")
    r = c.post("/profile/password", headers={"X-CSRF-Token": tok},
               data={"old": "NewAlice77", "new": "AliceNew88x", "new2": "AliceNew88x"})
    assert r.get_json()["ok"], r.get_json()
    assert pwfile.load_map().get("alice") == "AliceNew88x", "自改密码应回写明文文件"

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
            "plan_id": "3", "task_name": "训练 resnet-50", "hours": "12",
            "node": "<GPU02>", "port": "28766"})
        j = r.get_json()
        assert j["ok"], j
        pl = c.get("/api/plans").get_json()
        assert all("maxtime_h" in x and "gpu_model" in x for x in pl), "套餐应含 gpu_model/maxtime_h"
        # 超过套餐 maxtime 被拒并提示需管理员协助
        r = c.post("/apply", headers={"X-CSRF-Token": tok2}, data={
            "image": PUBIMG,
            "plan_id": "3", "task_name": "超长任务", "hours": "200",
            "node": "<GPU02>", "port": "28767"})
        err = r.get_json()["error"]
        assert not r.get_json()["ok"] and ("maxtime" in err or "管理员协助" in err), err
        # 同一端口（用户级任意节点）再次申请被拒
        r = c.post("/apply", headers={"X-CSRF-Token": tok2}, data={
            "image": PUBIMG,
            "plan_id": "4", "task_name": "另一个任务", "hours": "12",
            "node": "<GPU03>", "port": "28766"})
        assert not r.get_json()["ok"], "同一端口在使用中应被拒"
        # 任务名为空被拒
        r = c.post("/apply", headers={"X-CSRF-Token": tok2}, data={
            "image": PUBIMG,
            "plan_id": "3", "task_name": "  ", "hours": "12",
            "node": "<GPU02>", "port": "28767"})
        assert not r.get_json()["ok"] and "任务名称" in r.get_json()["error"]
    finally:
        appmod.os.path.isfile = orig_isfile

    # 我的资源 + API
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

    # 日志
    iid = 1
    r = c.get("/instances/%d/log" % iid)
    assert r.get_json()["ok"]
    # 停机
    r = c.post("/instances/%d/stop" % iid, headers={"X-CSRF-Token": tok2})
    assert r.get_json()["ok"]
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
            "node": "<GPU02>", "port": port})

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
    r = c.post("/instances/%d/delete" % s2, headers={"X-CSRF-Token": tok2})
    assert r.get_json()["ok"], r.get_json()

    # 我的资源页：弹窗遮罩默认隐藏
    r = c.get("/my")
    assert 'id="log-modal" hidden'.encode() in r.data and 'id="detail-modal" hidden'.encode() in r.data

    # 集群状态页
    r = c.get("/status")
    assert r.status_code == 200

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
    # root 行（保留账号）不能直接改配额
    dbxr = _DB(appmod.DB_PATH)
    uid_root = dbxr.q1("SELECT id FROM users WHERE username='root'")["id"]
    dbxr.close()
    r = c.post("/admin/users/%s/quota" % uid_root,
               headers={"X-CSRF-Token": tokq}, data={"quota": "1T"})
    assert not r.get_json()["ok"] and "保留账号" in r.get_json()["error"], r.get_json()

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
                         "node": "<GPU02>", "port": "28766"})
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

    print("SMOKE_OK (db at %s)" % TMP)


if __name__ == "__main__":
    main()
