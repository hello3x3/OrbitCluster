# -*- coding: utf-8 -*-
"""ZJU-CV 门户/账户/优先级 真机功能测试（一次性脚本，失败自动清理）。

用法（本机可访问门户 + ssh zju@3090-00）：
  SUDO_PWD=... python3 review/live_functional_20261101.py

不把密码写进仓库；root 门户密码从 /etc/cluster-portal/users.passwd 读取。
"""
import json
import os
import random
import re
import shlex
import subprocess
import sys
import time

import requests

BASE = os.environ.get("PORTAL_BASE", "http://10.71.106.251")
SSH_HOST = os.environ.get("PORTAL_SSH_HOST", "3090-00")
SSH_PORT = os.environ.get("PORTAL_SSH_PORT", "2022")
SSH_USER = os.environ.get("PORTAL_SSH_USER", "zju")
SUDO_PWD = os.environ.get("SUDO_PWD", "")
if not SUDO_PWD:
    sys.exit("需要 SUDO_PWD（zju 的 sudo 密码）")

RESULTS = []
FAILS = []
CREATED_USERS = []
CREATED_ACCOUNTS = []
CREATED_JOBS = []
INSTANCE_IDS = []


def check(name, cond, detail=""):
    ok = bool(cond)
    RESULTS.append((name, ok, detail))
    print(("  [OK] " if ok else "  [FAIL] ") + name + (("  " + str(detail)[:500]) if (detail and not ok) else ""), flush=True)
    if not ok:
        FAILS.append((name, detail))
    return ok


def sudo(script, timeout=180):
    p = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
         "-o", "StrictHostKeyChecking=accept-new", "-p", SSH_PORT,
         "%s@%s" % (SSH_USER, SSH_HOST), "sudo -S -p '' bash -s"],
        input=SUDO_PWD + "\n" + script, capture_output=True, text=True, timeout=timeout)
    return p


def root_password():
    p = sudo("grep '^root:' /etc/cluster-portal/users.passwd | cut -d: -f2-")
    return (p.stdout or "").strip()


class Portal:
    def __init__(self, base=BASE):
        self.base = base
        self.s = requests.Session()

    def get(self, path, **kw):
        return self.s.get(self.base + path, timeout=30, **kw)

    def csrf(self, path="/login"):
        r = self.get(path)
        m = (re.search(r'name="_csrf" value="([a-f0-9]+)"', r.text)
             or re.search(r'<meta name="csrf-token" content="([a-f0-9]+)"', r.text))
        if not m:
            raise RuntimeError("csrf not found on " + path)
        return m.group(1)

    def login(self, user, pwd):
        tok = self.csrf("/login")
        return self.s.post(self.base + "/login",
                           data={"_csrf": tok, "username": user, "password": pwd},
                           allow_redirects=True, timeout=30)

    def post_json(self, path, data=None):
        r = self.s.post(self.base + path,
                        headers={"X-CSRF-Token": self.csrf()},
                        data=data or {}, timeout=180)
        try:
            return r, r.json()
        except Exception:
            return r, {"ok": False, "error": "non-json HTTP %s" % r.status_code}

    def post_form(self, path, data):
        data = dict(data)
        data["_csrf"] = self.csrf()
        return self.s.post(self.base + path, data=data,
                           allow_redirects=True, timeout=240)

    def uid_of(self, username):
        html = self.get("/admin/users").text
        for row in re.findall(r"<tr>.*?</tr>", html, re.S):
            if (">%s<" % username) not in row:
                continue
            m = re.search(r"/admin/users/(\d+)/", row)
            if m:
                return int(m.group(1))
        return None


def root_session():
    p = Portal()
    pwd = root_password()
    if not pwd:
        raise RuntimeError("无法读取 root 门户密码")
    r = p.login("root", pwd)
    if "用户管理" not in r.text:
        raise RuntimeError("root 登录失败")
    return p


def wait_user_status(user, exists=True, tries=30):
    for _ in range(tries):
        p = sudo("/usr/local/sbin/portal-ctl user-status %s" % shlex.quote(user))
        try:
            d = json.loads(p.stdout)
        except Exception:
            d = {}
        if bool(d.get("exists")) == exists:
            return d
        time.sleep(2)
    return d


def wait_instance(rp, iid, states, tries=90):
    last = None
    for _ in range(tries):
        j = rp.get("/api/my/instances").json()
        last = j
        for i in (j or []):
            if i.get("id") == iid:
                if i.get("state") in states:
                    return i
        time.sleep(4)
    return last


def main():
    suffix = "%04d" % (int(time.time()) % 10000)
    low = "qalow" + suffix
    high = "qahigh" + suffix
    cap = "qacap" + suffix
    exp = "qaexp" + suffix
    user = "qauser" + suffix
    adm = "qaadm" + suffix
    user_pwd = "QaUser" + suffix + "!"
    adm_pwd = "QaAdm" + suffix + "!"
    state = {"root": None, "user_uid": None, "adm_uid": None, "port": "31" + suffix[-3:]}

    try:
        print("== 基础/权限 ==")
        rp = root_session()
        state["root"] = rp
        check("root 登录", True)
        check("root 用户管理页", rp.get("/admin/users").status_code == 200)
        check("root 资源套餐页", rp.get("/admin/plans").status_code == 200)
        ra = rp.get("/admin/accounts")
        check("root 管理账户页", ra.status_code == 200 and "phd" in ra.text, ra.status_code)
        # 默认账户 phd 不可删
        r, j = rp.post_json("/admin/accounts/phd/delete")
        check("默认账户 phd 删除保护", not j.get("ok") and "默认账户" in j.get("error", ""), j)

        print("== 创建测试账户 ==")
        for name, prio, capv, preempt in ((low, 1, 0, 0), (high, 10, 0, 0), (cap, 5, 1, 0)):
            r, j = rp.post_json("/admin/accounts/add", {
                "name": name, "description": "QA " + name,
                "priority": str(prio), "per_user_gpu_cap": str(capv),
                "preempt": "1" if preempt else "", "is_temporary": ""})
            if not j.get("ok") and "已存在" in j.get("error", ""):
                check("账户 %s 已存在(复用)" % name, True)
            else:
                check("创建账户 %s" % name, j.get("ok"), j)
            CREATED_ACCOUNTS.append(name)
        r, j = rp.post_json("/admin/accounts/add", {
            "name": exp, "description": "QA expired", "priority": "5",
            "per_user_gpu_cap": "0", "is_temporary": "1", "expires_at": "2020-01-01"})
        check("创建过期测试账户", j.get("ok") or "已存在" in j.get("error", ""), j)
        CREATED_ACCOUNTS.append(exp)
        r = rp.get("/admin/accounts")
        check("管理账户页含测试账户", all(x in r.text for x in (low, high, cap, exp)))

        print("== 用户/管理员创建 ==")
        r = rp.post_form("/admin/users/create", {
            "username": user, "display_name": "QA 普通用户", "role": "user",
            "os_mode": "provision", "quota": "100G", "password": user_pwd,
            "accounts": [low, high, cap], "default_account": low})
        check("创建普通用户 %s" % user, "创建成功" in r.text or "初始密码" in r.text, r.text[:300])
        CREATED_USERS.append(user)
        st = wait_user_status(user, True)
        check("OS 账号存在且四节点 UID 一致",
              st.get("exists") and len({v.get("uid") for v in (st.get("nodes") or {}).values()}) == 1,
              st)
        uid = rp.uid_of(user)
        state["user_uid"] = uid
        check("门户取到用户 id", uid is not None, uid)
        # 配额
        st2 = wait_user_status(user, True)
        q = rp.get("/api/my/quota") if False else None  # user session below
        r = rp.post_form("/admin/users/create", {
            "username": adm, "display_name": "QA 管理员", "role": "admin",
            "os_mode": "none", "quota": "500G", "password": adm_pwd})
        check("创建纯门户管理员 %s" % adm, "创建成功" in r.text or "初始密码" in r.text, r.text[:300])
        CREATED_USERS.append(adm)
        state["adm_uid"] = rp.uid_of(adm)
        check("管理员 id", state["adm_uid"] is not None)
        dbuid = state["adm_uid"]
        r = rp.get("/admin/users")
        check("管理员记录在用户列表", adm in r.text)

        print("== 非 root 管理员权限 ==")
        ap = Portal()
        r = ap.login(adm, adm_pwd)
        check("管理员登录", "用户管理" in r.text)
        check("管理员不能进管理账户(403)", ap.get("/admin/accounts").status_code == 403)
        check("管理员不能进资源套餐(403)", ap.get("/admin/plans").status_code == 403)
        check("管理员可进用户管理", ap.get("/admin/users").status_code == 200)
        r = ap.post_form("/admin/users/create", {
            "username": "qatmp" + suffix, "display_name": "x", "role": "admin",
            "os_mode": "none", "quota": "500G", "password": "NoWay12345"})
        check("普通管理员不能创建管理员", "只有超级管理员 root" in r.text, r.text[:300])

        print("== 普通用户登录/资料/配额 ==")
        up = Portal()
        r = up.login(user, user_pwd)
        check("普通用户登录", "我的资源" in r.text)
        check("未完善资料时申请被重定向", "完善个人资料" in up.get("/apply", allow_redirects=True).text)
        # 公钥
        keypath = "/tmp/qa_key_" + suffix
        for f in (keypath, keypath + ".pub"):
            if os.path.exists(f):
                os.remove(f)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", keypath], check=True)
        pub = open(keypath + ".pub").read().strip()
        r, j = up.post_json("/profile/keys/add", {"label": "QA", "pubkey": pub})
        check("用户添加 SSH 公钥", j.get("ok"), j)
        p1 = "31" + suffix[-3:]
        p2 = "32" + suffix[-3:]
        r, j = up.post_json("/profile/ports/add", {"port": p1, "label": "QA1"})
        check("用户登记端口 %s" % p1, j.get("ok"), j)
        r, j = up.post_json("/profile/ports/add", {"port": p2, "label": "QA2"})
        check("用户登记端口 %s" % p2, j.get("ok"), j)
        r = up.get("/api/my/quota")
        try:
            qj = r.json()
        except Exception:
            qj = {}
        check("用户配额 API OS 实读 100G", qj.get("os_ok") and qj.get("disk", {}).get("hard_kb") == 100 * 1024 * 1024, qj)
        check("用户多账户可见", {a["name"] for a in qj.get("accounts", [])} >= {low, high, cap}, qj.get("accounts"))
        check("用户默认账户为 %s" % low, qj.get("default_account") == low, qj.get("default_account"))
        r = up.get("/apply")
        check("申请页账户下拉包含多账户", all(x in r.text for x in (low, high, cap)))

        print("== 账户选择 / 提交 ==")
        imgj = up.get("/api/images").json()
        plj = up.get("/api/plans").json()
        imgs = imgj.get("public") or []
        img = next((x for x in imgs if "cuda12.8" in x.get("name", "")), imgs[0] if imgs else None)
        plan = next((x for x in plj if x.get("gpus") == 1), None)
        check("镜像/1GPU套餐存在", bool(img and plan), (img, plan))
        if img and plan:
            r, j = up.post_json("/apply", {
                "image": img["path"], "plan_id": str(plan["id"]), "task_name": "qat1",
                "hours": "1", "node": "3090-01", "port": p1, "account": high})
            check("指定 qahigh 账户提交", j.get("ok"), j)
            if j.get("ok"):
                iid = j.get("instance_id")
                INSTANCE_IDS.append(iid)
                inst = wait_instance(up, iid, ("RUNNING",), tries=60)
                if isinstance(inst, dict):
                    check("实例 RUNNING", inst.get("state") == "RUNNING", inst)
                    check("实例记录账户=qahigh", inst.get("account") == high, inst.get("account"))
                    check("日志 API", up.get("/instances/%s/log" % iid).status_code == 200)
                    r, jj = up.post_json("/instances/%s/stop" % iid)
                    check("停机 API", jj.get("ok"), jj)
                    wait_instance(up, iid, ("CANCELLED", "COMPLETED", "FAILED", "TIMEOUT"), tries=20)
                    r, jj = up.post_json("/instances/%s/delete" % iid)
                    check("删除实例记录", jj.get("ok"), jj)
                else:
                    check("实例 RUNNING", False, inst)
            # 不属于的账户
            r, j = up.post_json("/apply", {
                "image": img["path"], "plan_id": str(plan["id"]), "task_name": "qat2",
                "hours": "1", "node": "3090-01", "port": p2, "account": "be"})
            check("不属于的账户被拒", not j.get("ok") and "账户" in j.get("error", ""), j)
            # 过期账户
            r, j = up.post_json("/apply", {
                "image": img["path"], "plan_id": str(plan["id"]), "task_name": "qat3",
                "hours": "1", "node": "3090-01", "port": p2, "account": exp})
            check("过期账户提交被拒", not j.get("ok") and "过期" in j.get("error", ""), j)

        print("== 账户优先级/限额/抢占配置 ==")
        sac = sudo("sacctmgr -n -P show qos format=Name,Priority,MaxTRESPU,Preempt 2>&1").stdout
        check("QoS qalow priority=1", re.search(r"^acct-%s\|1\|" % low, sac, re.M) is not None, sac)
        check("QoS qahigh priority=10", re.search(r"^acct-%s\|10\|" % high, sac, re.M) is not None, sac)
        check("QoS qacap MaxTRESPU=1", re.search(r"^acct-%s\|5\|gres/gpu=1" % cap, sac, re.M) is not None, sac)
        # 过期账户加入成员（创建用户时不能选过期账户；成员接口允许加入，提交时会被过滤）
        if uid:
            r, j = rp.post_json("/admin/accounts/%s/members/add" % exp, {"user_id": str(uid)})
            check("过期账户加入成员", j.get("ok"), j)
        # 抢占配置
        r, j = rp.post_json("/admin/accounts/%s/edit" % high, {
            "name": high, "description": "QA " + high, "priority": "10",
            "per_user_gpu_cap": "0", "preempt": "1"})
        check("qahigh 开启抢占", j.get("ok"), j)
        sac2 = sudo("sacctmgr -n -P show qos acct-%s format=Preempt 2>&1" % high).stdout
        check("qahigh Preempt 含 qalow", ("acct-" + low) in sac2, sac2)

        print("== Slurm 优先级/限额真机测试 ==")
        def run_as(u, cmd, timeout=60):
            return sudo("runuser -u %s -- bash -lc %s" % (u, shlex.quote(cmd)), timeout=timeout)
        def submit(args):
            return run_as(user, "cd /share/home/%s && /usr/local/bin/sbatch %s" % (user, " ".join(shlex.quote(a) for a in args)))
        def job_states():
            return sudo("squeue -h -o '%i|%T|%r|%q|%Q' 2>/dev/null").stdout
        def cancel(jid):
            sudo("scancel %s 2>/dev/null || true" % jid)
        # 占满 3090-02 4 卡
        p = submit(["-J", "qahold", "-p", "gpu", "-w", "3090-02", "--gres=gpu:4",
                    "--account", low, "-t", "10", "--wrap", "sleep 120"])
        m = re.search(r"Submitted batch job (\d+)", p.stdout + p.stderr)
        hold = int(m.group(1)) if m else None
        if hold:
            CREATED_JOBS.append(hold)
        check("占用作业提交", hold is not None, p.stdout + p.stderr)
        running = False
        for _ in range(30):
            if hold and re.search(r"^%d\|RUNNING\|" % hold, job_states(), re.M):
                running = True
                break
            time.sleep(2)
        check("占用作业 RUNNING", running, job_states())
        p = submit(["-J", "qalowjob", "-p", "gpu", "-w", "3090-02", "--gres=gpu:4",
                    "--account", low, "-t", "5", "--wrap", "sleep 20"])
        m = re.search(r"Submitted batch job (\d+)", p.stdout + p.stderr)
        lowjob = int(m.group(1)) if m else None
        if lowjob:
            CREATED_JOBS.append(lowjob)
        p = submit(["-J", "qahighjob", "-p", "gpu", "-w", "3090-02", "--gres=gpu:4",
                    "--account", high, "-t", "5", "--wrap", "sleep 20"])
        m = re.search(r"Submitted batch job (\d+)", p.stdout + p.stderr)
        highjob = int(m.group(1)) if m else None
        if highjob:
            CREATED_JOBS.append(highjob)
        check("低/高优先级作业都提交", lowjob is not None and highjob is not None, p.stdout + p.stderr)
        # 释放占用，观察高优先跑
        if hold:
            cancel(hold)
        started_high = started_low = False
        for _ in range(30):
            s = job_states()
            if highjob and re.search(r"^%d\|RUNNING\|" % highjob, s, re.M):
                started_high = True
            if lowjob and re.search(r"^%d\|RUNNING\|" % lowjob, s, re.M):
                started_low = True
            if started_high:
                break
            time.sleep(2)
        check("高优先级作业先启动", started_high, job_states())
        check("低优先级作业未同时启动", not started_low, job_states())
        for jid in (lowjob, highjob):
            if jid:
                cancel(jid)
        # 单用户卡数上限
        p = submit(["-J", "qacapjob", "-p", "gpu", "-w", "3090-03", "--gres=gpu:2",
                    "--account", cap, "-t", "2", "--wrap", "sleep 10"])
        out = (p.stdout + p.stderr).strip()
        # 可能 sbatch 直接拒绝，也可能排队 reason=QOSMaxTRESPerUser
        accepted = re.search(r"Submitted batch job (\d+)", out)
        if accepted:
            jid = int(accepted.group(1))
            CREATED_JOBS.append(jid)
            time.sleep(2)
            reason = ""
            for line in job_states().splitlines():
                if line.startswith("%d|" % jid):
                    reason = line
            check("超过 qacap 单用户上限被限制", "QOSMaxTRES" in reason or "PENDING" in reason, reason)
            cancel(jid)
        else:
            check("超过 qacap 单用户上限被拒绝", "QOSMaxTRES" in out or "TRES" in out or "limit" in out, out)

        print("== 配额真实写盘测试 ==")
        if uid:
            r, j = rp.post_json("/admin/users/%s/quota" % uid, {"quota": "1G"})
            check("root 改测试用户配额为 1G", j.get("ok"), j)
            time.sleep(10)   # 门户配额缓存 8s
            qj = up.get("/api/my/quota").json()
            check("用户读到 1G", qj.get("disk", {}).get("hard_kb") == 1024 * 1024, qj)
            p = run_as(user, "dd if=/dev/zero of=/share/home/%s/qa-big bs=1M count=1100" % user, timeout=90)
            check("写超 1G 被配额拦截", p.returncode != 0, (p.returncode, (p.stderr or "")[-300:]))
            run_as(user, "rm -f /share/home/%s/qa-big" % user)
            r, j = rp.post_json("/admin/users/%s/quota" % uid, {"quota": "100G"})
            check("恢复 100G", j.get("ok"), j)
            time.sleep(10)

        print("== 删除普通用户（应连同 OS/家目录/Slurm 一起删）==")
        if uid:
            # 确保没有活动实例
            sudo("scancel -u %s 2>/dev/null || true" % user)
            r, j = rp.post_json("/admin/users/%s/delete" % uid)
            check("root 删除普通用户", j.get("ok"), j)
            st = wait_user_status(user, False, tries=30)
            check("OS 账号已不存在", not st.get("exists"), st)
            p = sudo("test -e /share/home/%s && echo YES || echo NO" % user)
            check("家目录已删除", "NO" in p.stdout, p.stdout)
            p = sudo("sacctmgr -n -P show assoc user=%s format=User 2>/dev/null" % user)
            check("Slurm 关联已删除", not p.stdout.strip(), p.stdout)
            CREATED_USERS.remove(user)

        print("== 删除测试管理员 ==")
        if state["adm_uid"]:
            r, j = rp.post_json("/admin/users/%s/role" % state["adm_uid"])
            check("取消测试管理员权限", j.get("ok"), j)
            r, j = rp.post_json("/admin/users/%s/delete" % state["adm_uid"])
            check("删除纯门户管理员", j.get("ok"), j)
            if adm in CREATED_USERS:
                CREATED_USERS.remove(adm)

        print("== 清理测试账户 ==")
        for name in [exp, cap, high, low]:
            r, j = rp.post_json("/admin/accounts/%s/delete" % name)
            if j.get("ok"):
                check("删除账户 %s" % name, True)
                if name in CREATED_ACCOUNTS:
                    CREATED_ACCOUNTS.remove(name)
            else:
                check("删除账户 %s" % name, False, j)
    finally:
        print("== 清理 ==")
        try:
            for jid in CREATED_JOBS:
                sudo("scancel %s 2>/dev/null || true" % jid)
            for iid in INSTANCE_IDS:
                try:
                    if state.get("root"):
                        state["root"].post_json("/instances/%s/delete" % iid)
                except Exception:
                    pass
            if state.get("user_uid"):
                try:
                    state["root"].post_json("/admin/users/%s/delete" % state["user_uid"])
                except Exception:
                    pass
            if state.get("adm_uid"):
                try:
                    state["root"].post_json("/admin/users/%s/role" % state["adm_uid"])
                    state["root"].post_json("/admin/users/%s/delete" % state["adm_uid"])
                except Exception:
                    pass
            for name in list(CREATED_ACCOUNTS):
                try:
                    if state.get("root"):
                        state["root"].post_json("/admin/accounts/%s/delete" % name)
                except Exception:
                    pass
        except Exception as e:
            print("cleanup error:", e)

    print("\n===== 结果 =====")
    print("通过 %d / %d" % (len(RESULTS) - len(FAILS), len(RESULTS)))
    for n, d in FAILS:
        print("FAIL:", n, str(d)[:300])
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
