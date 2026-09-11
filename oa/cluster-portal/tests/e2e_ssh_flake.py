# -*- coding: utf-8 -*-
"""复现/诊断容器 ssh 公钥登录偶发失败：连建 3 个用户各申请一次 GPU 容器并 ssh。
失败时抓取作业日志与 authorized_keys 指纹。
用法:  E2E_BASE=... E2E_ADMIN_USER=root E2E_ADMIN_PWD=... python e2e_ssh_flake.py
"""
import os
import subprocess
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from e2e_live import (Portal, ssh_run_retry, make_key, check, OK, log,  # noqa
                      ADMIN_HOST, ADMIN_USER, SSH_PORT_HOST, NODE_IP)

BASE = os.environ.get("E2E_BASE", "http://<ADMIN_IP>:8000")
ADMIN_PWD = os.environ.get("E2E_ADMIN_PWD", "")


def ssh_exec(cmd):
    return subprocess.run(["ssh", "-o", "BatchMode=yes", "-p", str(SSH_PORT_HOST),
                           "root@" + ADMIN_HOST, cmd],
                          capture_output=True, text=True, timeout=60)


def main():
    global ADMIN_PWD
    if not ADMIN_PWD:
        r = ssh_exec("grep '^%s:' /etc/cluster-portal/users.passwd | cut -d: -f2-" % ADMIN_USER)
        ADMIN_PWD = (r.stdout or "").strip()
    assert ADMIN_PWD, "无法读取管理员 %s 的密码" % ADMIN_USER

    admin = Portal(BASE)
    admin.login(ADMIN_USER, ADMIN_PWD)
    port = 28850
    for i in range(3):
        suffix = uuid.uuid4().hex[:4]
        user = "ptssh" + suffix
        log("== 轮次 %d 用户 %s ==" % (i, user))
        try:
            r = admin.post_form("/admin/users/create", {
                "username": user, "display_name": "ssh-flake", "role": "user",
                "os_mode": "provision", "quota": "100G", "password": "SshFlake123"})
            assert "创建成功" in r.text
            p = Portal(BASE)
            p.login(user, "SshFlake123")
            kpath, pub = make_key("flk" + suffix)
            j = p.json(p.post_json("/profile/keys/add",
                                   data={"label": "k", "pubkey": pub}))
            assert j["ok"], j
            j = p.json(p.post_json("/profile/ports/add", data={"port": str(port)}))
            assert j["ok"], j
            pls = p.json(p.get("/api/plans"))
            gpu_plan = next((x for x in pls if x["gpus"] == 1), None)
            assert gpu_plan, "无可用 GPU 套餐"
            imgs = (p.json(p.get("/api/images")) or {}).get("public") or []
            img = next((x["path"] for x in imgs if "cuda" in x["name"]),
                       imgs[0]["path"] if imgs else None)
            assert img, "无可用公共镜像"
            j = p.json(p.post_json("/apply", data={
                "image": img, "plan_id": str(gpu_plan["id"]),
                "task_name": "sshflk", "hours": "1",
                "node": "<GPU02>", "port": str(port)}))
            assert j["ok"], j
            iid = j["instance_id"]
            # 轮询到 RUNNING
            inst = None
            for _ in range(30):
                for x in p.json(p.get("/api/my/instances")):
                    if x["id"] == iid and x["state"] == "RUNNING":
                        inst = x
                if inst:
                    break
                time.sleep(4)
            assert inst, "not running"
            rr = ssh_run_retry(kpath, user, NODE_IP["<GPU02>"], port,
                               "echo SSHOK-$(id -un)")
            if rr.returncode == 0:
                check("轮次%d ssh OK" % i, "SSHOK-" + user in rr.stdout)
            else:
                log("!! ssh 失败 rc=%d out=%r err=%r" % (rr.returncode,
                                                         rr.stdout[:200], rr.stderr[:200]))
                lj = p.json(p.get("/instances/%d/log?lines=200" % iid))
                log("---- 作业日志 ----\n" + lj.get("text", lj.get("error", ""))[-1500:])
                # 节点侧 authorized_keys 指纹
                r2 = ssh_exec("ssh -o BatchMode=yes -p 2180 root@<GPU02> '"
                              "ls -l /share/home/%s/.ssh/authorized_keys; "
                              "head -c 120 /share/home/%s/.ssh/authorized_keys; echo'"
                              % (user, user))
                log("---- 节点侧 authorized_keys ----\n" + r2.stdout + r2.stderr)
                raise SystemExit("ssh flake reproduced")
            # 停机收尾
            p.post_json("/instances/%d/stop" % iid)
        finally:
            from e2e_live import E2E_USERS
            E2E_USERS.append(user)
            if "E2E_USERS" not in globals():
                globals()["E2E_USERS"] = E2E_USERS
        port += 1
    log("flake 测试结束，%d 项通过" % len(OK))


if __name__ == "__main__":
    try:
        main()
    finally:
        from e2e_live import cleanup_users
        cleanup_users()
