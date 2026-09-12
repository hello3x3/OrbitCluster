#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 6 端到端验收 —— 3090-2node 站点（在管理节点本地执行）

覆盖真实门户流程：
  管理员登录 → 自动建号（两节点 UID 一致 + 家目录 + /share 配额 + sacctmgr）
  → 测试用户登录 / 登记公钥与端口 → 申请 GPU 资源 → 等 RUNNING
  → SSH 进容器：nvidia-smi 见卡、/share/home 已挂载、身份为该用户
  → 三卡套餐（单节点 3 卡）→ 停机 → 删号并验证清理

只用标准库；ssh 通过 subprocess 调系统 ssh。
"""
import http.cookiejar
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("E2E_BASE", "http://127.0.0.1:8000")
MGT = os.environ.get("E2E_MGT", "<ADMIN>")
PEER = os.environ.get("E2E_PEER", "<GPU02>")
SSH_PORT = os.environ.get("E2E_SSH_PORT", "2022")
GPU_MODEL = os.environ.get("E2E_GPU_MODEL", "RTX 3090")
IMAGE_SUB = os.environ.get("E2E_IMAGE", "cuda12.8.0")
USER = os.environ.get("E2E_USER", "e2etest")
PWD = os.environ.get("E2E_PWD", "E2eTest_2026x")
KEY = "/tmp/e2e_key"
PORT1, PORT2 = 28771, 28772

PASS, FAIL = [], []


def log(m):
    print("[e2e] " + m, flush=True)


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        log("  [OK] " + name)
    else:
        FAIL.append(name)
        log("  [FAIL] " + name + "  " + str(detail)[:400])


def sh(cmd, timeout=90):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def run_argv(argv, timeout=90):
    """列表式调用，避免本地 shell 展开远端命令里的 $HOME / $(...) 。"""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def peer(cmd, timeout=90):
    return run_argv(["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
                     "-o", "LogLevel=ERROR", "-p", SSH_PORT, "root@" + PEER, cmd], timeout)


def node_ip(name):
    _, out, _ = sh("getent hosts %s | awk '{print $1}' | grep -v '^127\\.' | head -1" % name)
    return out.strip()


class Portal:
    def __init__(self, base):
        self.base = base
        self.cj = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))

    def get(self, path):
        with self.op.open(self.base + path, timeout=30) as r:
            return r.read().decode("utf-8", "replace")

    def csrf(self, path="/login"):
        html = self.get(path)
        m = re.search(r'name="_csrf" value="([^"]+)"', html)
        if not m:
            m = re.search(r'<meta name="csrf-token" content="([^"]+)"', html)
        return m.group(1) if m else ""

    def post(self, path, data, token):
        d = dict(data)
        d["_csrf"] = token
        req = urllib.request.Request(self.base + path, data=urllib.parse.urlencode(d).encode())
        try:
            with self.op.open(req, timeout=300) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    def login(self, user, pwd):
        _, body = self.post("/login", {"username": user, "password": pwd}, self.csrf("/login"))
        return "用户名或密码错误" not in body and "欢迎回来" in body

    def jpost(self, path, data, page=None):
        if page is None:
            if path.startswith("/admin"):
                page = "/admin/users"
            elif path.startswith("/profile"):
                page = "/profile"
            else:
                page = "/apply"
        st, body = self.post(path, data, self.csrf(page))
        try:
            return st, json.loads(body)
        except ValueError:
            return st, {"_raw": body[:300]}


def wait_state(p, ports, want=("RUNNING",), tries=90, pause=5):
    last = []
    for _ in range(tries):
        try:
            insts = json.loads(p.get("/api/my/instances"))
        except Exception:
            insts = []
        last = [i for i in insts if str(i.get("port")) in [str(x) for x in ports]]
        if last and all(i.get("state") in want or i.get("state") in
                        ("FAILED", "CANCELLED", "COMPLETED", "STOPPED") for i in last):
            return last
        time.sleep(pause)
    return last


def container_ssh(node_ip_addr, port, cmd, timeout=120):
    return run_argv(["ssh", "-i", KEY, "-p", str(port),
                     "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
                     "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=10",
                     "-o", "LogLevel=ERROR", "%s@%s" % (USER, node_ip_addr), cmd], timeout)


def container_ssh_retry(node_ip_addr, port, cmd, tries=60, pause=5, timeout=120):
    """Slurm 状态 RUNNING ≠ 容器就绪：pyxis 还要解包/挂载 .sqsh（实测数十秒），
    sshd 起来之前连过去是 Connection refused，所以必须重试。"""
    last = (1, "", "no attempt")
    for i in range(tries):
        rc, out, err = container_ssh(node_ip_addr, port, cmd, timeout)
        if rc == 0 and out.strip():
            return rc, out, err
        last = (rc, out, err)
        if i == 0:
            log("    容器尚未就绪，重试中 ... (%s)" % (err.strip()[:80] or out.strip()[:80]))
        time.sleep(pause)
    return last


def portal_uid():
    code = ("import sqlite3;c=sqlite3.connect('/var/lib/cluster-portal/portal.db');"
            "r=c.execute(\"SELECT id FROM users WHERE username='%s'\").fetchone();"
            "print(r[0] if r else '')" % USER)
    rc, out, _ = sh("python3 -c " + subprocess.list2cmdline([code]))
    return out.strip()


def main():
    log("=== 前置 ===")
    rc, admin_file, _ = sh("cat /root/.cluster-portal-admin")
    m = re.findall(r"[A-Za-z0-9_-]{8,}", admin_file)
    admin_pwd = m[-1] if m else ""
    ip_mgt, ip_peer = node_ip(MGT), node_ip(PEER)
    log("  管理 %s=%s  计算 %s=%s  ssh端口 %s" % (MGT, ip_mgt, PEER, ip_peer, SSH_PORT))
    check("节点 IP 解析为真实地址（非回环）",
          ip_mgt.startswith("10.") and ip_peer.startswith("10."), "%s/%s" % (ip_mgt, ip_peer))

    a = Portal(BASE)
    check("管理员 root 登录门户", a.login("root", admin_pwd))

    # ---------- 清理上一轮残留（保证可重复执行）----------
    old = portal_uid()
    if old:
        log("  清理上一轮残留用户 uid=%s" % old)
        _, body = a.post("/admin/users/%s/delete" % old, {}, a.csrf("/admin/users"))
        log("  清理结果: %s" % body[:120])
        time.sleep(3)
    sh("id %s >/dev/null 2>&1 && userdel -r %s 2>/dev/null; rm -rf /share/home/%s; "
       "setquota -u %s 0 0 0 0 /share 2>/dev/null; true" % (USER, USER, USER, USER))
    peer("id %s >/dev/null 2>&1 && userdel %s 2>/dev/null; true" % (USER, USER))

    log("=== 1. 门户自动建号（100G 配额）===")
    _, body = a.post("/admin/users/create",
                     {"username": USER, "display_name": "E2E 测试", "role": "user",
                      "quota": "100G", "os_mode": "provision", "password": PWD},
                     a.csrf("/admin/users"))
    check("建号请求被接受", ("创建成功" in body) or ("已存在" in body), body[:200])

    time.sleep(3)
    log("=== 2. 校验 OS 侧（UID / 家目录 / 配额 / 会计）===")
    rc1, uid_mgt, _ = sh("id -u %s 2>/dev/null" % USER)
    rc2, uid_peer, _ = peer("id -u %s 2>/dev/null" % USER)
    check("管理节点已建 OS 账号", rc1 == 0 and uid_mgt.isdigit(), uid_mgt or "不存在")
    check("计算节点同名账号且 UID 一致", rc2 == 0 and uid_peer == uid_mgt,
          "mgt=%s peer=%s" % (uid_mgt, uid_peer))
    rc, home, _ = sh("ls -ld /share/home/%s 2>&1" % USER)
    check("家目录已建且属主正确", rc == 0 and (USER + " " + USER) in home, home)
    rc, rq, _ = sh("repquota -u /share | awk '$1==\"%s\"' " % USER)
    check("/share 配额 = 100G", "104857600" in rq, rq or "(未设置)")
    rc, asr, _ = sh("sacctmgr -n show assoc user=%s format=Cluster,Account,User 2>/dev/null" % USER)
    check("sacctmgr 关联已建立", USER in asr, asr)

    log("=== 3. 测试用户登录 + 登记公钥/端口 ===")
    u = Portal(BASE)
    check("测试用户登录门户", u.login(USER, PWD))
    for p in (KEY, KEY + ".pub"):
        if os.path.exists(p):
            os.remove(p)
    sh("ssh-keygen -t ed25519 -N '' -f %s -C e2e@%s >/dev/null" % (KEY, MGT))
    pub = open(KEY + ".pub").read().strip()
    _, j = u.jpost("/profile/keys/add", {"pubkey": pub})
    check("登记 SSH 公钥", j.get("ok"), j)
    _, j = u.jpost("/profile/ports/add", {"port": str(PORT1), "label": "e2e"})
    check("登记端口 %d" % PORT1, j.get("ok"), j)

    log("=== 4. 申请单卡 GPU 资源 ===")
    plans = json.loads(u.get("/api/plans"))
    images = json.loads(u.get("/api/images"))
    gpu1 = next((p for p in plans if p["gpus"] == 1 and GPU_MODEL in (p.get("gpu_model") or "")), None)
    gpu3 = next((p for p in plans if p["gpus"] == 3), None)
    imgs = [i["path"] for i in images.get("public", [])]
    image = next((p for p in imgs if IMAGE_SUB in p), None)
    check("发现单卡 %s 套餐" % GPU_MODEL, gpu1 is not None,
          [(p["name"], p["gpus"], p.get("gpu_model")) for p in plans])
    check("发现镜像 %s*" % IMAGE_SUB, image is not None, imgs)
    if not (gpu1 and image):
        return 1

    _, j = u.jpost("/apply", {"plan_id": gpu1["id"], "port": PORT1, "hours": 1,
                              "image": image, "node": "", "task_name": "e2e-g1"})
    check("提交单卡资源", j.get("ok"), j)

    log("  等待实例 RUNNING（Pyxis 首次挂载 .sqsh 需数十秒）...")
    insts = wait_state(u, [PORT1])
    ok_run = bool(insts) and all(i["state"] == "RUNNING" for i in insts)
    check("单卡实例 RUNNING", ok_run, insts)
    if not ok_run:
        return 1
    inst = insts[0]
    log("  实例 #%s job=%s node=%s" % (inst["id"], inst.get("job_id"), inst.get("node")))
    tgt = inst.get("ip") or node_ip(inst["node"])

    log("=== 5. SSH 进容器（等容器就绪）===")
    rc, out, err = container_ssh_retry(tgt, PORT1,
                                       "echo HOST=$(hostname); echo UID=$(id -u); "
                                       "nvidia-smi -L 2>/dev/null | head -3; "
                                       "test -d /share/home && echo SHARE_OK; "
                                       "test -d $HOME && echo HOME_OK")
    check("能 ssh 进容器", rc == 0 and "HOST=" in out, (out + " | " + err))
    check("容器内身份为该用户(uid=%s)" % uid_mgt, "UID=%s" % uid_mgt in out, out)
    check("容器内可见 %s" % GPU_MODEL, GPU_MODEL in out, out)
    check("容器内挂载了 /share/home", "SHARE_OK" in out, out)

    log("=== 6. 三卡套餐 ===")
    insts3 = []
    if gpu3:
        u.jpost("/profile/ports/add", {"port": str(PORT2), "label": "e2e3"})
        _, j = u.jpost("/apply", {"plan_id": gpu3["id"], "port": PORT2, "hours": 1,
                                  "image": image, "node": "", "task_name": "e2e-g3"})
        check("提交三卡资源", j.get("ok"), j)
        insts3 = wait_state(u, [PORT2])
        ok3 = bool(insts3) and all(i["state"] == "RUNNING" for i in insts3)
        check("三卡实例 RUNNING", ok3, insts3)
        if ok3:
            i3 = insts3[0]
            t3 = i3.get("ip") or node_ip(i3["node"])
            rc, out, err = container_ssh_retry(t3, PORT2, "nvidia-smi -L 2>/dev/null | wc -l")
            check("三卡容器内看到 3 张卡", out.strip() == "3", (out + " | " + err))
    else:
        log("  (无三卡套餐，跳过)")

    log("=== 7. 停机 ===")
    for i in insts + insts3:
        _, j = u.jpost("/instances/%d/stop" % i["id"], {})
        log("  stop #%s -> ok=%s" % (i["id"], j.get("ok")))
    time.sleep(10)
    left = json.loads(u.get("/api/my/instances"))
    check("停机后无 RUNNING 实例",
          not any(i["state"] == "RUNNING" for i in left),
          [(i["id"], i["state"]) for i in left])

    log("=== 8. 删号并校验清理 ===")
    uid_portal = portal_uid()
    log("  门户 uid=%s" % uid_portal)
    _, body = a.post("/admin/users/%s/delete" % uid_portal, {}, a.csrf("/admin/users"))
    try:
        jd = json.loads(body)
    except ValueError:
        jd = {"_raw": body[:200]}
    check("删号请求成功", jd.get("ok"), jd)
    time.sleep(3)
    rc, out, _ = sh("id %s 2>&1" % USER)
    check("管理节点 OS 账号已删除", rc != 0, out)
    rc, out, _ = peer("id %s 2>&1" % USER)
    check("计算节点 OS 账号已删除", rc != 0, out)
    rc, out, _ = sh("ls -d /share/home/%s 2>&1" % USER)
    check("NFS 家目录已删除", rc != 0, out)
    return 0


if __name__ == "__main__":
    rc = 2
    try:
        rc = main()
    except SystemExit:
        raise
    except Exception:
        import traceback
        traceback.print_exc()
    print("\n===== 汇总 =====")
    print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  FAIL: " + f)
    print("E2E_RESULT=%s" % ("OK" if (rc == 0 and not FAIL) else "FAILED"))
    sys.exit(0 if (rc == 0 and not FAIL) else 1)
