# -*- coding: utf-8 -*-
"""集群门户真机验收脚本（从外部机器通过 HTTP + 真实 slurm/容器 SSH）。

用法:
  E2E_BASE=http://<ADMIN_IP>:8000 \
  E2E_ADMIN_USER=root \
  E2E_ADMIN_PWD=<root 当前门户密码> \
  python e2e_live.py
（E2E_ADMIN_USER 缺省为 root；不传 E2E_ADMIN_PWD 时自动从 admin 节点
  /etc/cluster-portal/users.passwd 读取该用户的当前密码）

流程: 默认管理员 root 开通用户(真实三节点建号) → 登录 → 资料(密钥/端口) → 冲突校验
      → GPU 容器申请 → SSH 进容器验证 → 并发多资源 → 日志 → 停机 → 停用账号。
"""
import argparse
import os
import re
import subprocess
import sys
import time
import uuid

import requests

BASE = os.environ.get("E2E_BASE", "http://<ADMIN_IP>:8000")
ADMIN_USER = os.environ.get("E2E_ADMIN_USER", "root")     # 默认管理员账号
ADMIN_PWD = os.environ.get("E2E_ADMIN_PWD", "")
SSH_PORT_HOST = 2180
ADMIN_HOST = os.environ.get("E2E_ADMIN_HOST", "<ADMIN_IP>")
NODE_IP = {"admin": "<ADMIN_IP>", "<GPU02>": "<GPU02_IP>", "<GPU03>": "<GPU03_IP>"}

OK = []


def log(msg):
    print("[e2e] " + msg, flush=True)


def check(name, cond, detail=""):
    assert cond, "FAIL %s %s" % (name, detail)
    OK.append(name)
    log("  ✓ " + name)


class Portal:
    def __init__(self, base):
        self.base = base
        self.s = requests.Session()

    def get(self, url, **kw):
        r = self.s.get(self.base + url, timeout=30, **kw)
        return r

    def csrf(self, url=None):
        r = self.get(url or "/login")
        m = re.search(r'name="_csrf" value="([a-f0-9]+)"', r.text)
        if not m:
            m = re.search(r'<meta name="csrf-token" content="([a-f0-9]+)"', r.text)
        assert m, "csrf not found on %s" % url
        return m.group(1)

    def login(self, user, pwd):
        tok = self.csrf("/login")
        r = self.s.post(self.base + "/login",
                        data={"_csrf": tok, "username": user, "password": pwd},
                        allow_redirects=True, timeout=30)
        return r

    def post_json(self, url, data=None, **kw):
        return self.s.post(self.base + url,
                           headers={"X-CSRF-Token": self.csrf()},
                           data=data, timeout=90, **kw)

    def post_form(self, url, data, follow=True):
        tok = self.csrf()
        data = dict(data)
        data["_csrf"] = tok
        return self.s.post(self.base + url, data=data,
                           allow_redirects=follow, timeout=120)

    def json(self, r):
        return r.json()


def ssh_run(key, user, host, port, cmd, timeout=20):
    return subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
         "-o", "StrictHostKeyChecking=accept-new",
         "-o", "UserKnownHostsFile=/tmp/e2e_known_%s" % user,
         "-i", key, "-p", str(port), "%s@%s" % (user, host), cmd],
        capture_output=True, text=True, timeout=timeout)


def ssh_run_retry(key, user, host, port, cmd, tries=80, pause=3):
    """容器 sshd 需要数秒~1-2 分钟才完成绑定（提交作业先要在节点本地展开镜像，
    再启动容器内 sshd），故用较宽裕的重试窗口探测。"""
    last = None
    for _ in range(tries):
        last = ssh_run(key, user, host, port, cmd)
        if last.returncode == 0:
            return last
        time.sleep(pause)
    return last


def wait_instance_state(p, iid, state, tries=30, pause=4):
    """轮询单个实例直至进入指定状态。"""
    last = None
    for _ in range(tries):
        j = p.json(p.get("/api/my/instances"))
        last = j
        for i in j or []:
            if i["id"] == iid:
                if i["state"] == state:
                    return i
                break
        time.sleep(pause)
    raise AssertionError("instance %s state timeout, want %s: %r" % (iid, state, last))


def wait_job_state(p, user, ports=None, expect_running=True, tries=30, pause=4):
    """轮询 /api/my/instances，等指定端口(默认全部)的实例达到期望状态。"""
    last = None
    for _ in range(tries):
        j = p.json(p.get("/api/my/instances"))
        if j:
            last = j
            target = [i for i in j if (ports is None or i["port"] in ports)]
            if not target:
                time.sleep(pause)
                continue
            if expect_running:
                if all(i["state"] == "RUNNING" for i in target):
                    return target
            else:
                if all(i["state"] in ("CANCELLED", "COMPLETED", "FAILED", "TIMEOUT")
                       for i in target):
                    return target
        time.sleep(pause)
    raise AssertionError("job state timeout: %r" % (last or []))


def make_key(tag):
    path = "/tmp/e2e_key_%s" % tag
    for f in (path, path + ".pub"):
        if os.path.exists(f):
            os.remove(f)
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", path],
                   check=True)
    pub = open(path + ".pub").read().strip()
    return path, pub


E2E_USERS = []


def cleanup_users():
    if not E2E_USERS:
        return
    log("清理测试用户: %s" % ",".join(E2E_USERS))
    for u in E2E_USERS:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-p", str(SSH_PORT_HOST),
                            "root@" + ADMIN_HOST,
                            "/usr/local/sbin/portal-ctl unprovision-user %s" % u],
                           capture_output=True, text=True, timeout=240)
        if r.returncode != 0:
            log("  unprovision %s 失败: %s" % (u, (r.stdout + r.stderr)[-300:]))
        time.sleep(1)
    py = ("import sys; sys.path.insert(0,'.');\n"
          "from portalapp.db import DB;\n"
          "d=DB('/var/lib/cluster-portal/portal.db');\n"
          "d.exec(\"DELETE FROM users WHERE username LIKE 'pt%'\");\n"
          "print('portal users left:', [x[0] for x in d.q('SELECT username FROM users')])")
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-p", str(SSH_PORT_HOST),
                        "root@" + ADMIN_HOST,
                        "cd /opt/cluster-portal && PORTAL_DATA=/var/lib/cluster-portal "
                        "./venv/bin/python - "],
                       input=py, capture_output=True, text=True, timeout=120)
    log("  " + (r.stdout.strip() or r.stderr.strip()))


def _run():
    global ADMIN_PWD
    if not ADMIN_PWD:
        # 读取默认管理员(root)在当前 admin 节点上的门户密码（vault 为准）
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-p", str(SSH_PORT_HOST),
                            "root@" + ADMIN_HOST,
                            "grep '^%s:' /etc/cluster-portal/users.passwd | cut -d: -f2-" % ADMIN_USER],
                           capture_output=True, text=True, timeout=60)
        ADMIN_PWD = (r.stdout or "").strip()
    assert ADMIN_PWD, "无法读取管理员 %s 的密码" % ADMIN_USER

    admin = Portal(BASE)
    r = admin.login(ADMIN_USER, ADMIN_PWD)
    check("%s 登录" % ADMIN_USER, r.status_code == 200 and "用户管理" in r.text)

    # ---------- 管理员开通两个真实用户 ----------
    suffix = uuid.uuid4().hex[:4]
    alice = "ptalice" + suffix
    bob = "ptbob" + suffix
    log("测试用户: %s / %s" % (alice, bob))
    E2E_USERS.extend([alice, bob])
    for u, pwd, disp in [(alice, "AlicePass88", "验收-Alice"),
                         (bob, "BobPass6699", "验收-Bob")]:
        r = admin.post_form("/admin/users/create", {
            "username": u, "display_name": disp, "role": "user",
            "os_mode": "provision", "quota": "100G", "password": pwd})
        check("开通用户 %s" % u, r.status_code == 200 and "创建成功" in r.text)

    # ---------- Alice：登录 → 资料门槛 → 密钥/端口 ----------
    a = Portal(BASE)
    r = a.login(alice, "AlicePass88")
    check("alice 登录", "我的资源" in r.text)
    r = a.get("/apply", allow_redirects=True)
    check("未完善资料被拦截", "完善个人资料" in r.text)

    akey, apub = make_key("alice" + suffix)
    r = a.post_json("/profile/keys/add", data={"label": "验收机", "pubkey": apub})
    check("alice 添加公钥", a.json(r)["ok"])
    for port in ("28771", "28772"):
        r = a.post_json("/profile/ports/add", data={"port": port, "label": "验收端口"})
        check("alice 登记端口 %s" % port, a.json(r)["ok"])
    r = a.post_json("/profile/ports/add", data={"port": "22"})
    check("常用端口 22 被拒", not a.json(r)["ok"])
    r = a.post_json("/profile/ports/add", data={"port": "9999"})
    check("小于10000 被拒", not a.json(r)["ok"])

    # ---------- Bob：申请需先有公钥（有端口无密钥被拦）----------
    b = Portal(BASE)
    r = b.login(bob, "BobPass6699")
    check("bob 登录", "我的资源" in r.text)
    r = b.post_json("/profile/ports/add", data={"port": "28771"})
    check("bob 抢注 alice 端口被拒", not b.json(r)["ok"], str(b.json(r)))
    r = b.post_json("/profile/ports/add", data={"port": "28781", "label": "bob"})
    check("bob 登记端口", b.json(r)["ok"])

    # ---------- 读取镜像/套餐（新申请表单：公共 + 个人分组）----------
    pls = a.json(a.get("/api/plans"))
    imgdata = a.json(a.get("/api/images"))
    gpu_plan = next((x for x in pls if x["gpus"] == 1), None)
    cpu_plan = next((x for x in pls if x["gpus"] == 0), None)
    pub_imgs = imgdata.get("public") or []
    pub_img = next((x for x in pub_imgs if "cuda12.8" in x["name"]), pub_imgs[0])
    img = pub_img["path"]
    check("镜像 API 分组(公共/我的)且来自 /share/images",
          pub_imgs and img.endswith(".sqsh") and "mine" in imgdata, str(imgdata)[:200])
    check("套餐预置 GPU/CPU", gpu_plan and cpu_plan, str(pls))
    gpu_plan_id, cpu_plan_id = gpu_plan["id"], cpu_plan["id"]

    def ap(p, plan_id, task, port, node, hours="1"):
        r = p.post_json("/apply", data={"image": img, "plan_id": str(plan_id),
                                        "task_name": task, "hours": hours,
                                        "node": node, "port": str(port)})
        return r, p.json(r)

    r = b.post_json("/apply", data={"image": img, "plan_id": str(gpu_plan_id),
                                    "task_name": "bobkey", "hours": "1",
                                    "node": "admin", "port": "28781"})
    check("bob 无公钥申请被拦", not b.json(r)["ok"])

    bkey, bpub = make_key("bob" + suffix)
    r = b.post_json("/profile/keys/add", data={"label": "验收机", "pubkey": bpub})
    check("bob 添加公钥", b.json(r)["ok"])

    # ---------- 配额 / Slurm 额度（OS + sacctmgr 实读）----------
    q = a.json(a.get("/api/my/quota"))
    check("alice 读到自己磁盘配额(OS)", q["ok"] and q["os_ok"] and
          q["disk"]["hard_kb"] == 100 * 1024 * 1024, str(q)[:300])
    check("alice 读到 Slurm 关联/QoS/总额度",
          q["slurm"]["accounts"] == "lab" and "normal" in q["slurm"]["qos"]
          and q["slurm"]["total"] == "不限", str(q)[:300])
    r = a.get("/my")
    check("我的资源页展示配额区块",
          r.status_code == 200 and "我的配额" in r.text and "总额度" in r.text)
    # 管理员把 alice 配额从 100G 改成 200G（真实写 OS），再改回
    html = admin.get("/admin/users").text
    m = None
    for mm in re.finditer(r"<tr>.*?</tr>", html, re.S):
        if ">%s<" % alice in mm.group(0):
            m = re.search(r"/admin/users/(\d+)/quota", mm.group(0))
            break
    assert m, "用户管理页找不到 alice 的改配额按钮"
    uid_alice_q = m.group(1)
    r = admin.post_json("/admin/users/%s/quota" % uid_alice_q, data={"quota": "200G"})
    check("管理员改 alice 配额为 200G(OS 实写)", admin.json(r)["ok"], str(admin.json(r)))
    q2 = a.json(a.get("/api/my/quota"))
    check("改后 OS 实读 200 GiB", q2["disk"]["hard_kb"] == 200 * 1024 * 1024, str(q2)[:200])
    r = admin.post_json("/admin/users/%s/quota" % uid_alice_q, data={"quota": "100G"})
    check("改回 100G", admin.json(r)["ok"], str(admin.json(r)))
    q3 = a.json(a.get("/api/my/quota"))
    check("改回后 OS 实读 100 GiB", q3["disk"]["hard_kb"] == 100 * 1024 * 1024, str(q3)[:200])

    # ---------- Alice：GPU 容器申请（同时两个节点两个资源）----------
    _, j = ap(a, gpu_plan_id, "alice-g1", 28771, "<GPU02>")
    check("alice 提交 GPU#1", j["ok"], str(j))
    _, j = ap(a, gpu_plan_id, "alice-g2", 28772, "<GPU03>")
    check("alice 提交 GPU#2(并发多资源)", j["ok"], str(j))
    # 同一端口（使用中，任意节点）重复申请被拒
    _, j = ap(a, gpu_plan_id, "alice-dup", 28771, "<GPU03>")
    check("端口使用中重复申请被拒", not j["ok"], str(j))

    log("等待 alice 两个实例运行 ...")
    insts = wait_job_state(a, alice, ports=[28771, 28772])
    check("alice 两个实例 RUNNING", len(insts) == 2 and
          all(i["state"] == "RUNNING" for i in insts), str(insts))
    byport = {i["port"]: i for i in insts}

    # ---------- SSH 进容器验证（真实登录，经节点 IP）----------
    for port, node in ((28771, "<GPU02>"), (28772, "<GPU03>")):
        rr = ssh_run_retry(akey, alice, NODE_IP[node], port,
                           'echo OK-$(hostname)-uid$(id -u); nvidia-smi -L 2>/dev/null | head -1; '
                           'test -d /share/home && echo HOME_OK')
        check("ssh 进 alice 容器 %s:%s" % (node, port),
              rr.returncode == 0 and "OK-" in rr.stdout and "HOME_OK" in rr.stdout,
              rr.stdout[:200] + rr.stderr[:200])
        check("容器内可见 GPU(%s)" % node, "RTX 3060" in rr.stdout)

    # ---------- 日志 ----------
    iid = byport[28771]["id"]
    r = a.get("/instances/%d/log?lines=300" % iid)
    j = a.json(r)
    check("查看日志", j["ok"] and "实际监听端口" in j["text"], str(j)[:200])

    # ---------- Bob GPU 资源 + CPU 同节点并存（用空闲的 admin 节点）----------
    _, j = ap(b, gpu_plan_id, "bob-g1", 28781, "admin")
    check("bob 提交 GPU", j["ok"], str(j))
    r = b.post_json("/profile/ports/add", data={"port": "28782"})
    check("bob 登记第二端口", b.json(r)["ok"])
    _, j = ap(b, cpu_plan_id, "bob-c1", 28782, "admin")
    check("bob 提交 CPU 套餐(同节点并发)", j["ok"], str(j))
    insts_b = wait_job_state(b, bob, ports=[28781, 28782])
    check("bob 两个实例 RUNNING(GPU+CPU 同节点)", len(insts_b) == 2, str(insts_b))
    rb = ssh_run_retry(bkey, bob, NODE_IP["admin"], 28782, "echo CPUCT-$(hostname)")
    check("ssh 进 bob CPU 容器", rb.returncode == 0 and "CPUCT-admin" in rb.stdout)
    rg = ssh_run_retry(bkey, bob, NODE_IP["admin"], 28781,
                       "nvidia-smi -L 2>/dev/null | head -1")
    check("ssh 进 bob GPU 容器且见卡", rg.returncode == 0 and "RTX 3060" in rg.stdout)

    # ---------- 详情页 SSH 连接给真实 IP（非节点名）----------
    for inst in a.json(a.get("/api/my/instances")):
        if inst["port"] in (28771, 28772) and inst["state"] == "RUNNING":
            check("实例 ssh_ip 已回填(详情用真实 IP)",
                  bool(inst.get("ssh_ip")) and inst["ssh_ip"] != inst.get("node"),
                  str(inst)[:220])
            break

    # ---------- 停机 ----------
    for who, p, portlist in ((alice, a, [28771, 28772]), (bob, b, [28781, 28782])):
        for port in portlist:
            inst = next(i for i in p.json(p.get("/api/my/instances")) if i["port"] == port)
            r = p.post_json("/instances/%d/stop" % inst["id"])
            check("%s 停机端口 %s" % (who, port), p.json(r)["ok"], str(p.json(r)))
    time.sleep(4)
    for who, p, pl in ((alice, a, [28771, 28772]), (bob, b, [28781, 28782])):
        insts = wait_job_state(p, who, ports=pl, expect_running=False)
        check("%s 全部已停止" % who,
              all(i["state"] == "CANCELLED" for i in insts), str(insts))

    # ---------- 端口释放后可复用 ----------
    _, j2 = ap(a, gpu_plan_id, "alice-auto", 28771, "", hours="1")
    check("停止后同端口可再次申请(节点留空自动调度)", j2["ok"], str(j2))
    iid = j2["instance_id"]
    wait_instance_state(a, iid, "RUNNING")
    # 自动调度实例：等待真实节点与 IP 回填（一般紧随 RUNNING）
    running = None
    for _ in range(12):
        resp = a.get("/api/my/instances")
        cand = next((x for x in a.json(resp) if x["id"] == iid), None)
        if cand and cand.get("node") and cand.get("ssh_ip"):
            running = cand
            break
        time.sleep(3)
    assert running, "自动调度后未回填真实节点/IP"
    check("自动调度后回填真实节点", running["node"] in ("admin", "<GPU02>", "<GPU03>"),
          str(running))
    # 停机收尾该实例
    r = a.post_json("/instances/%d/stop" % iid)
    check("自动调度实例停机", a.json(r)["ok"], str(a.json(r)))
    wait_instance_state(a, iid, "CANCELLED")

    # ---------- 已停止实例：重新启动 / 删除（复用同一行）----------
    html = a.get("/my").text
    check("停机后行出现重新启动/删除按钮",
          "act-restart" in html and "act-delinst" in html)
    r = a.post_json("/instances/%d/restart" % iid)
    j = a.json(r)
    check("重新启动(自动调度)入队", j["ok"] and j.get("job_id"), str(j))
    wait_instance_state(a, iid, "RUNNING")
    # 重启后行回到活跃，自动调度应再次回填真实节点
    inst = wait_instance_state(a, iid, "RUNNING")
    ok_n = None
    for _ in range(12):
        resp = a.get("/api/my/instances")
        cand = next((x for x in a.json(resp) if x["id"] == iid), None)
        if cand and cand.get("node") and cand.get("ssh_ip"):
            ok_n = cand
            break
        time.sleep(3)
    assert ok_n, "重启后未回填节点/IP"
    check("重启后节点/IP 回填", ok_n["node"] in ("admin", "<GPU02>", "<GPU03>"), str(ok_n)[:200])
    # 再停机 → 删除（记录 + 日志）
    r = a.post_json("/instances/%d/stop" % iid)
    check("重启后停机", a.json(r)["ok"], str(a.json(r)))
    wait_instance_state(a, iid, "CANCELLED")
    r = a.post_json("/instances/%d/delete" % iid)
    check("删除已停止实例(记录+日志)", a.json(r)["ok"] and "日志" in a.json(r)["msg"],
          str(a.json(r)))
    left = a.json(a.get("/api/my/instances"))
    check("删除后列表无该行", all(x["id"] != iid for x in left))

    # ---------- 管理员：停用/启用 Bob ----------
    r = admin.get("/admin/users")
    m = re.search(r'data-url="([^"]*admin/users/\d+/toggle)"[^>]*>\s*停用', r.text)
    # 找到 bob 那行的 toggle
    m = None
    for mm in re.finditer(r'<tr>.*?</tr>', r.text, re.S):
        tr = mm.group(0)
        if bob in tr:
            m = re.search(r'/admin/users/(\d+)/toggle', tr)
            break
    assert m, "admin users 页找不到 bob"
    uid_bob = m.group(1)
    r = admin.post_json("/admin/users/%s/toggle" % uid_bob)
    check("管理员停用 bob", admin.json(r)["ok"])
    rr = b.get("/my", allow_redirects=False)  # bob 的会话仍持有
    check("停用后 bob 无法访问资源",
          rr.status_code in (301, 302) and "/login" in rr.headers.get("Location", ""),
          "%s %s" % (rr.status_code, rr.headers.get("Location", "")))
    r = admin.post_json("/admin/users/%s/toggle" % uid_bob)
    check("管理员重新启用 bob", admin.json(r)["ok"])
    rr = b.get("/my", allow_redirects=False)
    check("启用后 bob 恢复访问", rr.status_code == 200)

    # ---------- 管理员：删除用户（门户 + 集群三节点注销）----------
    r = admin.post_json("/admin/users/%s/delete" % uid_bob)
    check("管理员删除 bob（门户+集群注销）", admin.json(r)["ok"], str(admin.json(r)))
    rr = b.get("/my", allow_redirects=False)
    check("删除后 bob 门户访问被拒", rr.status_code in (301, 302))
    sh = ("id %s >/dev/null 2>&1; echo ADMIN_RC=$?; "
          "for n in '<GPU02>' '<GPU03>'; do ssh -o BatchMode=yes -p 2180 root@$n "
          "'id %s >/dev/null 2>&1'; echo ${n}_RC=$?; done" % (bob, bob))
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-p", str(SSH_PORT_HOST),
                        "root@" + ADMIN_HOST, sh],
                       capture_output=True, text=True, timeout=120)
    check("bob OS 账号三节点均已删除",
          "ADMIN_RC=1" in r.stdout and "<GPU02>_RC=1" in r.stdout and "<GPU03>_RC=1" in r.stdout,
          r.stdout[:300] + r.stderr[:200])

    # ---------- 保存镜像：真实 enroot export（个人镜像 + 到期自动保存）----------
    # 用最小镜像跑通整条链路：管理员把 ubuntu 基座打成带 /opt/start_ssh.sh 的 tiny.sqsh，
    # 放入 alice 的个人镜像目录（验证「我的镜像」分组/白名单申请）；tiny 只有 ~58MB，保存秒级完成。
    tiny = "/share/images/%s/tiny.sqsh" % alice
    build = ("set -e; rm -rf /tmp/tinybuild && mkdir -p /tmp/tinybuild/rootfs && "
             "unsquashfs -q -f -d /tmp/tinybuild/rootfs /share/images/ubuntu-22.04.sqsh && "
             "mkdir -p /tmp/tinybuild/rootfs/opt && "
             "printf '#!/bin/sh\\necho [start_ssh] tiny-image-keepalive\\nwhile true; do sleep 3600; done\\n' "
             "> /tmp/tinybuild/rootfs/opt/start_ssh.sh && chmod +x /tmp/tinybuild/rootfs/opt/start_ssh.sh && "
             "mkdir -p /share/images/%s && chown %s:%s /share/images/%s && "
             "mksquashfs /tmp/tinybuild/rootfs %s -noappend && chown %s:%s %s && "
             "rm -rf /tmp/tinybuild && echo TINY_OK" %
             (alice, alice, alice, alice, tiny, alice, alice, tiny))
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-p", str(SSH_PORT_HOST),
                        "root@" + ADMIN_HOST, build], capture_output=True, text=True, timeout=300)
    check("制作 alice 个人 tiny 镜像", r.returncode == 0 and "TINY_OK" in r.stdout,
          (r.stdout + r.stderr)[-300:])

    # alice 在自己的镜像列表里看到 tiny.sqsh；申请页含“我的镜像”分组
    d2 = a.json(a.get("/api/images"))
    check("个人镜像出现在 API mine 分组", any(x["name"] == "tiny.sqsh"
                                             for x in d2.get("mine", [])), str(d2)[:200])
    r = a.get("/apply")
    check("申请页含公共/我的镜像分组",
          "公共镜像".encode("utf-8") in r.content and "我的镜像".encode("utf-8") in r.content)
    # 用个人镜像 tiny 申请（CPU 套餐，节点 <GPU02>）→ RUNNING
    tiny_path = next(x["path"] for x in d2["mine"] if x["name"] == "tiny.sqsh")
    r2 = a.post_json("/apply", data={"image": tiny_path, "plan_id": str(cpu_plan_id),
                                     "task_name": "snaptiny", "hours": "1",
                                     "node": "<GPU02>", "port": "28771"})
    j2 = a.json(r2)
    check("用个人镜像申请资源", j2["ok"], str(j2)[:200])
    siid = j2["instance_id"]
    wait_instance_state(a, siid, "RUNNING")

    # 手动保存镜像：非法名拒绝 → 合法名开始 → 完成(轮询 saving/last_save) → 同名提示覆盖
    r = a.post_json("/instances/%d/save-image" % siid, data={"name": "has space"})
    check("非法镜像名被拒", not a.json(r)["ok"])
    r = a.post_json("/instances/%d/save-image" % siid, data={"name": "tinyenv"})
    j3 = a.json(r)
    check("保存镜像已开始(异步)", j3["ok"], str(j3)[:200])
    row = None
    for _ in range(180):
        cand = next((x for x in a.json(a.get("/api/my/instances")) if x["id"] == siid), None)
        if cand and cand.get("saving") == 0 and (cand.get("last_save") or "").startswith("已保存"):
            row = cand
            break
        time.sleep(5)
    assert row, "手动保存镜像超时未完成"
    check("手动保存完成并记录结果", "tinyenv.sqsh" in row["last_save"], row["last_save"])
    sh = "ls -l /share/images/%s/tinyenv.sqsh && stat -c 'OWNER=%%U SIZE=%%s' /share/images/%s/tinyenv.sqsh" % (alice, alice)
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-p", str(SSH_PORT_HOST),
                        "root@" + ADMIN_HOST, sh], capture_output=True, text=True, timeout=60)
    check("个人镜像文件已落盘且归属用户", r.returncode == 0 and "OWNER=%s" % alice in r.stdout
          and int(re.search(r"SIZE=(\d+)", r.stdout).group(1)) > 0, r.stdout[:200])
    d3 = a.json(a.get("/api/images"))
    check("手动保存的镜像出现在 mine 列表", any(x["name"] == "tinyenv.sqsh"
                                              for x in d3.get("mine", [])), str(d3)[:200])
    r = a.post_json("/instances/%d/save-image" % siid, data={"name": "tinyenv"})
    jd = a.json(r)
    check("同名保存提示覆盖(need_force)", not jd["ok"] and jd.get("need_force"), str(jd)[:200])

    # 到期自动保存：把该实例开始时刻改到过去，等门户到期扫描自动保存(带时间戳名)并自动停机
    py = ("import sys; sys.path.insert(0,'/opt/cluster-portal');\n"
          "from portalapp.db import DB;\n"
          "d=DB('/var/lib/cluster-portal/portal.db');\n"
          "d.exec(\"UPDATE instances SET started_at='2020-01-01T00:00:00' WHERE id=%d\");\n"
          "print('backdated')" % siid)
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-p", str(SSH_PORT_HOST),
                        "root@" + ADMIN_HOST,
                        "cd /opt/cluster-portal && ./venv/bin/python - "],
                       input=py, capture_output=True, text=True, timeout=120)
    check("回拨实例开始时刻", "backdated" in r.stdout, r.stderr[-200:])
    row = None
    for _ in range(40):   # 到期扫描 25s 一轮，等至多 ~100s
        cand = next((x for x in a.json(a.get("/api/my/instances")) if x["id"] == siid), None)
        if cand and cand["state"] == "CANCELLED" and cand.get("auto_saved_path"):
            row = cand
            break
        time.sleep(3)
    assert row, "到期自动保存未触发（60s+ 未见 CANCELLED/auto_saved_path）"
    check("到期自动保存生成带时间戳镜像", re.match(r"auto_[A-Za-z0-9_]+\.sqsh$",
          os.path.basename(row["auto_saved_path"])), row["auto_saved_path"])
    check("到期后实例已自动停机且记录结果", "到期" in (row["last_save"] or ""), row["last_save"])
    sh = "ls /share/images/%s/auto_*.sqsh" % alice
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-p", str(SSH_PORT_HOST),
                        "root@" + ADMIN_HOST, sh], capture_output=True, text=True, timeout=60)
    check("到期自动保存文件在个人目录", r.returncode == 0 and "auto_" in r.stdout, r.stdout[:200])
    # 清理该实例记录（自动保存已停机 → 终端态可删）
    r = a.post_json("/instances/%d/delete" % siid)
    check("删除保存测试实例", a.json(r)["ok"], str(a.json(r)))

    # ---------- 集群状态页 ----------
    r = admin.get("/status")
    check("集群状态页", r.status_code == 200 and "<GPU02>" in r.text)
    r = admin.get("/api/nodes")
    nodes = admin.json(r)
    check("节点 API 正常", nodes.get("ok") and len(nodes.get("nodes", [])) >= 3)

    log("验收完成 ✓ (%d 项) 测试用户: %s %s" % (len(OK), alice, bob))


def main():
    try:
        _run()
    finally:
        cleanup_users()


if __name__ == "__main__":
    main()
