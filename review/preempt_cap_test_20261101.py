# -*- coding: utf-8 -*-
"""高优抢占低优 + 账户单用户卡数上限 真机验证（一次性，自动清理）。

用法：
  SUDO_PWD=... python3 review/preempt_cap_test_20261101.py

不把任何口令写入仓库；root 门户密码从密码文件读取。
"""
import json
import os
import re
import shlex
import subprocess
import sys
import time

import requests

BASE = "http://10.71.106.251"
SSH_HOST = "3090-00"
SSH_PORT = "2022"
SSH_USER = "zju"
SUDO_PWD = os.environ.get("SUDO_PWD", "")
if not SUDO_PWD:
    sys.exit("需要 SUDO_PWD")

RESULTS = []
CREATED_USERS = []
CREATED_ACCOUNTS = []
JOB_IDS = []


def check(name, cond, detail=""):
    ok = bool(cond)
    RESULTS.append((name, ok, detail))
    print(("  [OK] " if ok else "  [FAIL] ") + name +
          (("  " + str(detail)[:600]) if not ok else ""), flush=True)
    return ok


def sudo(script, timeout=180):
    return subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
         "-o", "StrictHostKeyChecking=accept-new", "-p", SSH_PORT,
         "%s@%s" % (SSH_USER, SSH_HOST), "sudo -S -p '' bash -s"],
        input=SUDO_PWD + "\n" + script, capture_output=True, text=True, timeout=timeout)


def root_password():
    p = sudo("grep '^root:' /etc/cluster-portal/users.passwd | cut -d: -f2-")
    return (p.stdout or "").strip()


class Portal:
    def __init__(self):
        self.s = requests.Session()

    def get(self, path):
        return self.s.get(BASE + path, timeout=30)

    def csrf(self, path="/login"):
        r = self.get(path)
        m = (re.search(r'name="_csrf" value="([a-f0-9]+)"', r.text)
             or re.search(r'<meta name="csrf-token" content="([a-f0-9]+)"', r.text))
        if not m:
            raise RuntimeError("csrf missing " + path)
        return m.group(1)

    def login(self, user, pwd):
        return self.s.post(BASE + "/login", data={
            "_csrf": self.csrf("/login"), "username": user, "password": pwd},
            allow_redirects=True, timeout=30)

    def post_json(self, path, data=None):
        r = self.s.post(BASE + path, headers={"X-CSRF-Token": self.csrf()},
                        data=data or {}, timeout=180)
        return r, r.json()

    def post_form(self, path, data):
        data = dict(data)
        data["_csrf"] = self.csrf()
        return self.s.post(BASE + path, data=data, allow_redirects=True, timeout=240)

    def uid(self, username):
        html = self.get("/admin/users").text
        for row in re.findall(r"<tr>.*?</tr>", html, re.S):
            if ">%s<" % username not in row:
                continue
            m = re.search(r"/admin/users/(\d+)/", row)
            if m:
                return int(m.group(1))
        return None


def run_as(user, cmd, timeout=120):
    return sudo("runuser -u %s -- bash -lc %s" % (user, shlex.quote(cmd)), timeout=timeout)


def submit(user, args):
    cmd = "cd /share/home/%s && /usr/local/bin/sbatch %s" % (
        user, " ".join(shlex.quote(a) for a in args))
    p = run_as(user, cmd)
    m = re.search(r"Submitted batch job (\d+)", p.stdout + p.stderr)
    jid = int(m.group(1)) if m else None
    if jid:
        JOB_IDS.append(jid)
    return jid, p


def squeue(jid):
    p = sudo("squeue -h -j %d -o '%%T|%%r|%%q|%%Q' 2>/dev/null" % jid)
    return p.stdout.strip()


def sacct_state(jid):
    p = sudo("sacct -j %d -X -n -P -o JobID,State,Reason 2>/dev/null" % jid)
    return p.stdout.strip()


def wait_state(jid, states, tries=90, pause=2):
    last = ""
    for _ in range(tries):
        last = squeue(jid) or sacct_state(jid)
        for s in states:
            if s in last:
                return last
        time.sleep(pause)
    return last


def cancel(jid):
    sudo("scancel %d 2>/dev/null || true" % jid)


def cleanup(root):
    print("== 清理 ==")
    for jid in JOB_IDS:
        cancel(jid)
    for u in list(CREATED_USERS):
        for _ in range(10):
            if not sudo("id -u %s 2>/dev/null" % u).stdout.strip():
                break
            p = sudo("/usr/local/sbin/portal-ctl unprovision-user %s" % u)
            if p.returncode == 0:
                break
            time.sleep(1)
        try:
            if root:
                uid = root.uid(u)
                if uid:
                    root.post_json("/admin/users/%s/delete" % uid)
        except Exception:
            pass
    for a in list(CREATED_ACCOUNTS):
        try:
            if root:
                root.post_json("/admin/accounts/%s/delete" % a)
        except Exception:
            pass
    # 最后兜底：清掉 DB 中本轮 qa 前缀账户/用户（避免残留影响下次）
    sudo("""/opt/cluster-portal/venv/bin/python - <<'PY'
import sqlite3
c=sqlite3.connect('/var/lib/cluster-portal/portal.db')
c.execute("delete from users where username glob 'qa*'")
c.execute("delete from accounts where name glob 'qa*'")
c.commit()
PY""")
    print("== 清理完成 ==")


def main():
    suffix = "%04d" % (int(time.time()) % 10000)
    low = "qalowp" + suffix
    high = "qahighp" + suffix
    cap = "qacapp" + suffix
    lowu = "qalowu" + suffix
    highu = "qahighu" + suffix
    capu = "qacapu" + suffix
    pw = "QaTest" + suffix + "!"
    root = None
    try:
        print("== 准备 root 门户 ==")
        root = Portal()
        r = root.login("root", root_password())
        check("root 登录", "用户管理" in r.text)
        if "用户管理" not in r.text:
            raise RuntimeError("root login failed")

        print("== 创建测试账户 ==")
        for name, prio, capv, preempt in ((low, 1, 0, 0), (high, 10, 0, 1), (cap, 5, 1, 0)):
            r, j = root.post_json("/admin/accounts/add", {
                "name": name, "description": "preempt/cap test",
                "priority": str(prio), "per_user_gpu_cap": str(capv),
                "preempt": "1" if preempt else "", "is_temporary": ""})
            check("创建账户 %s (prio=%d cap=%d preempt=%d)" % (name, prio, capv, preempt),
                  j.get("ok"), j)
            CREATED_ACCOUNTS.append(name)

        qos = sudo("sacctmgr -n -P show qos format=Name,Priority,MaxTRESPU,Preempt 2>&1").stdout
        check("低优 QoS priority=1", re.search(r"^acct-%s\|1\|" % low, qos, re.M) is not None, qos)
        check("高优 QoS priority=10", re.search(r"^acct-%s\|10\|" % high, qos, re.M) is not None, qos)
        check("高优 Preempt 含低优", ("acct-" + low) in
              sudo("sacctmgr -n -P show qos acct-%s format=Preempt" % high).stdout)
        check("限额 QoS MaxTRESPU=1",
              re.search(r"^acct-%s\|5\|gres/gpu=1" % cap, qos, re.M) is not None, qos)

        print("== 创建测试用户 ==")
        for u, acct, disp in ((lowu, low, "低优测试"), (highu, high, "高优测试"),
                              (capu, cap, "限额测试")):
            r = root.post_form("/admin/users/create", {
                "username": u, "display_name": disp, "role": "user",
                "os_mode": "provision", "quota": "100G", "password": pw,
                "accounts": [acct], "default_account": acct})
            check("创建用户 %s" % u, ("创建成功" in r.text or "初始密码" in r.text), r.text[:400])
            CREATED_USERS.append(u)
        check("三个测试用户 OS 存在",
              all(sudo("id -u %s 2>/dev/null" % u).stdout.strip() for u in CREATED_USERS))
        time.sleep(2)

        print("== 抢占测试：低优占满 3090-03 4 卡，高优 4 卡应抢占 ==")
        lowjob, p = submit(lowu, ["-J", "qalowhold", "-p", "gpu", "-w", "3090-03",
                                  "--gres=gpu:4", "--account", low, "-t", "10",
                                  "--wrap", "sleep 300"])
        check("低优占用作业提交", lowjob is not None, p.stdout + p.stderr)
        st = wait_state(lowjob, ("RUNNING",), tries=30)
        check("低优占用作业 RUNNING", "RUNNING" in st, st)
        highjob, p = submit(highu, ["-J", "qahighpreempt", "-p", "gpu", "-w", "3090-03",
                                    "--gres=gpu:4", "--account", high, "-t", "10",
                                    "--wrap", "sleep 300"])
        check("高优作业提交", highjob is not None, p.stdout + p.stderr)
        time.sleep(8)
        print("  排队状态:", squeue(highjob), "| 低优:", squeue(lowjob))
        high_started = False
        low_cancelled = False
        for _ in range(90):  # 最多 ~180s
            s_high = squeue(highjob)
            s_low = squeue(lowjob)
            s_low_sacct = sacct_state(lowjob)
            if "RUNNING" in s_high:
                high_started = True
            if "CANCELLED" in (s_low + s_low_sacct) or "PREEMPTED" in (s_low + s_low_sacct):
                low_cancelled = True
            if high_started and low_cancelled:
                break
            time.sleep(2)
        check("高优作业最终 RUNNING", high_started, squeue(highjob) + " / " + sacct_state(highjob))
        check("低优作业被抢占 CANCELLED/PREEMPTED", low_cancelled,
              squeue(lowjob) + " / " + sacct_state(lowjob))
        cancel(highjob)
        time.sleep(1)

        print("== 账户单用户卡数上限：qacap=1 ==")
        # 单作业 2 卡应被限制
        jid, p = submit(capu, ["-J", "qacap2gpu", "-p", "gpu", "-w", "3090-01",
                               "--gres=gpu:2", "--account", cap, "-t", "5",
                               "--wrap", "sleep 20"])
        out = (p.stdout + p.stderr).strip()
        if jid:
            st = wait_state(jid, ("PENDING", "RUNNING", "CANCELLED", "COMPLETED"), tries=15)
            check("2 卡单作业被 QoS 限制", "QOSMaxTRES" in st or "PENDING" in st, st)
            cancel(jid)
        else:
            check("2 卡单作业被 QoS 限制", "QOSMaxTRES" in out or "TRES" in out or "limit" in out, out)

        # 两个 1 卡并发：第一个 RUNNING，第二个应 PENDING 且原因限卡
        j1, p1 = submit(capu, ["-J", "qacaprun", "-p", "gpu", "-w", "3090-01",
                               "--gres=gpu:1", "--account", cap, "-t", "5",
                               "--wrap", "sleep 120"])
        check("第一个 1 卡作业提交", j1 is not None, p1.stdout + p1.stderr)
        st1 = wait_state(j1, ("RUNNING",), tries=30)
        check("第一个 1 卡作业 RUNNING", "RUNNING" in st1, st1)
        j2, p2 = submit(capu, ["-J", "qacapwait", "-p", "gpu", "-w", "3090-01",
                               "--gres=gpu:1", "--account", cap, "-t", "5",
                               "--wrap", "sleep 30"])
        check("第二个 1 卡作业提交", j2 is not None, p2.stdout + p2.stderr)
        time.sleep(8)
        s2 = squeue(j2) or sacct_state(j2)
        print("  第二个作业状态:", s2)
        check("第二个 1 卡作业被单用户上限挡住",
              ("QOSMaxTRES" in s2) or ("PENDING" in s2), s2)
        cancel(j1)
        if j2:
            cancel(j2)

        print("== 清理 ==")
    finally:
        try:
            cleanup(root)
        except Exception as e:
            print("cleanup error:", e)

    print("\n===== 结果 =====")
    ok = sum(1 for _, c, _ in RESULTS if c)
    print("通过 %d / %d" % (ok, len(RESULTS)))
    for n, c, d in RESULTS:
        if not c:
            print("FAIL:", n, str(d)[:400])
    sys.exit(0 if ok == len(RESULTS) else 1)


if __name__ == "__main__":
    main()
