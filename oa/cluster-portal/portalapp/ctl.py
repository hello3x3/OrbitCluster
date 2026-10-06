# -*- coding: utf-8 -*-
"""调用 root 助手 portal-ctl 的客户端（web 以 portal 用户通过 sudo -n 执行）。"""
import json
import os
import subprocess
import time


class CtlError(Exception):
    pass


def _helper_path():
    return os.environ.get("PORTAL_CTL", "/usr/local/sbin/portal-ctl")


def _error_detail(r):
    """助手失败时的可读原因。

    助手把 `{"ok": false, "error": "..."}` 打到 stdout 并返回非 0，所以优先取里面的 error 文案
    （否则页面上的提示会是一整串 JSON）；拿不到就退回 stderr/stdout 的最后一行。
    """
    for stream in (r.stdout, r.stderr):
        txt = (stream or "").strip()
        if not txt.startswith("{"):
            continue
        try:
            data = json.loads(txt.splitlines()[-1])
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("error"):
            return str(data["error"])[:400]
    tail = (r.stderr or r.stdout or "").strip().splitlines()
    return tail[-1][:400] if tail else "rc=%d" % r.returncode


def run(cmd_args, stdin_json=None, timeout=120):
    """执行 portal-ctl 子命令，返回其 JSON 结果字典；失败抛 CtlError。"""
    helper = _helper_path()
    direct = os.environ.get("PORTAL_DIRECT_CTL", "0") == "1"
    if direct or (os.geteuid() == 0):
        argv = [helper] + cmd_args
    else:
        argv = ["sudo", "-n", "--", helper] + cmd_args
    try:
        r = subprocess.run(argv, input=json.dumps(stdin_json) if stdin_json is not None else None,
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise CtlError("助手执行超时(%ss): %s" % (timeout, " ".join(cmd_args)))
    if r.returncode != 0:
        detail = _error_detail(r)
        # sudo 本身失败
        if "sudo" in argv[0] and "permission" in (r.stderr or "").lower():
            raise CtlError("权限助手调用被拒（sudo 配置缺失？）: %s" % detail)
        raise CtlError(detail)
    try:
        data = json.loads(r.stdout)
    except ValueError:
        raise CtlError("助手输出非 JSON: %s" % r.stdout[:300])
    if not data.get("ok"):
        raise CtlError(data.get("error", "未知错误"))
    return data


# ---- 语义化封装 ----
def ping():
    return run(["ping"])


def sinfo():
    return run(["sinfo"], timeout=60)


def provision_user(username, quota, skip_os=False):
    args = ["provision-user", username, quota]
    if skip_os:
        args.append("--skip-os")
    return run(args, timeout=180)


def unprovision_user(username):
    return run(["unprovision-user", username], timeout=180)


def init_user(username):
    return run(["init-user", username], timeout=60)


def user_exists(username):
    """账号是否存在于集群（只查管理节点，**不 ssh 到计算节点**）。

    页面路径（_os_ok）必须用这个：user_status 会逐台计算节点 ssh，实测 ~0.7s。
    """
    return run(["user-exists", username], timeout=30)


def user_status(username):
    """完整账号状态（逐节点 UID / Slurm 关联 / 家目录）。

    仅供排障与验收（verify-install.sh、诊断），**不要放进页面请求路径** —— 见 user_exists。
    """
    return run(["user-status", username], timeout=90)


def ssh_ready(username, jobid):
    """容器里的 sshd 是否已监听（读作业日志头部的就绪横幅）。

    日志在用户家目录（750）下，门户进程读不到，由 root 助手本地读文件返回——
    不联网、不 ssh，比"门户自己对节点端口做 TCP 探测"更可靠（不受网络/防火墙影响）。
    """
    return run(["ssh-ready", username, str(jobid)], timeout=30)


def submit(spec):
    return run(["submit"], stdin_json=spec, timeout=90)


def kill(user, job_id):
    return run(["kill", user, str(job_id)], timeout=90)


def job_state(user, job_id):
    return run(["job-state", user, str(job_id)], timeout=60)


def log(user, job_id, lines=200):
    return run(["log", user, str(job_id), str(lines)], timeout=30)


def rm_log(user, job_id):
    """删除某作业日志文件（root 清理用户家目录残留）。"""
    return run(["rm-log", user, str(job_id)], timeout=60)


def save_image(username, job_id, name, force=False, note=None):
    """把某运行中容器的当前状态保存为 /share/images/<user>/<name>.sqsh（root 助手，可耗时数分钟）。

    note 非空时写进镜像的同名 .json（门户「到期自动保存」用它注明保存时间与清理建议）。
    """
    args = ["save-image", username, str(job_id), name, "1" if force else "0"]
    if note:
        args.append(note)
    return run(args, timeout=3600)


def images(username):
    """列出 /share/images/<username> 下该用户的个人镜像（root 视角读取 700 目录）。"""
    return run(["images", username], timeout=30)


def images_all():
    """公共镜像 + 所有用户的个人镜像（门户 root 的全局视图）。"""
    return run(["images-all"], timeout=60)


def set_image_note(owner, name, note):
    """写镜像注释（owner 为用户名或 "public"；注释存在镜像同目录的同名 .json 里）。"""
    return run(["set-image-note", owner or "public", name, note or ""], timeout=30)


def admin_image_rename(owner, old, new):
    """管理员改任意镜像名（owner 为用户名；公共镜像传 "public"）。"""
    return run(["admin-image-rename", owner or "public", old, new], timeout=60)


def admin_image_delete(owner, name):
    """管理员删任意镜像（owner 为用户名；公共镜像传 "public"）。"""
    return run(["admin-image-delete", owner or "public", name], timeout=60)


def rename_image(username, old, new):
    """个人镜像改名（root 助手；同目录 rename，属主与配额口径不变）。"""
    return run(["rename-image", username, old, new], timeout=60)


def delete_image(username, name):
    """删除个人镜像（root 助手；只删该用户目录下的普通 .sqsh）。"""
    return run(["delete-image", username, name], timeout=60)


def set_keys(user, key_list):
    return run(["set-keys", user], stdin_json=key_list, timeout=60)


def get_keys(user):
    return run(["get-keys", user], timeout=30)


# ---- 配额 / Slurm 关联信息 ----
def quota(username):
    """读取该用户 /share 实际磁盘配额（OS 权威）。"""
    return run(["quota", username], timeout=60)


def quota_all():
    """批量读取 /share 全部用户配额。"""
    return run(["quota-all"], timeout=90)


def set_quota(username, size):
    """设置该用户 /share 磁盘配额（软=硬，实际生效到 OS）。"""
    return run(["set-quota", username, str(size)], timeout=120)


def slurm_info(username):
    """读取 Slurm 关联/QoS/优先级/限额（经 sacctmgr）。"""
    return run(["slurm-info", username], timeout=60)


class NodeCache:
    """sinfo 结果短缓存，避免每个请求都跑 sinfo。"""

    def __init__(self, ttl=5):
        self.ttl = ttl
        self._data = None
        self._at = 0.0

    def get(self):
        now = time.time()
        if self._data is None or now - self._at > self.ttl:
            try:
                self._data = sinfo()
                self._at = now
            except CtlError:
                self._data = {"ok": False, "error": "无法获取节点状态"}
        return self._data
