# -*- coding: utf-8 -*-
"""portal-ctl（root 助手）安全回归测试。

不需要 root、不碰集群、不动真实家目录 —— 全部在临时目录里跑。

覆盖四类历史漏洞（每一条都对应一个真实可利用的攻击）：
  1) cmd_set_keys 在**用户可写目录**里用固定可猜的临时文件名（authorized_keys.portal-tmp）
     且 open(...,"w") 跟随软链 → 用户在自己的容器里建个软链，就能让 root 把公钥
     写进 /root/.ssh/authorized_keys。
  2) cmd_log / cmd_get_keys 用 os.path.isfile()+open()，同样跟随软链 →
     ln -s /etc/cluster-portal/users.passwd ~/.portal/logs/<jobid>.out
     再点网页「日志」，即可读走全站明文口令（以及 /etc/shadow、Flask secret）。
  3) install -d 跟随软链 → root 把 /etc/cron.d 之类目录的属主/权限改成该用户。
  4) 12 个命令用 name_ok（只验格式）放行 root/portal/slurm 等保留账号。

用法: python3 tests/test_ctl_security.py     （成功打印 CTL_SECURITY_OK）
"""
import importlib.machinery
import importlib.util
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CTL = os.path.join(os.path.dirname(HERE), "deploy", "portal-ctl")

FAIL = []


def check(name, cond, detail=""):
    if cond:
        print("  [OK] %s" % name)
    else:
        print("  [FAIL] %s  %s" % (name, detail))
        FAIL.append(name)


def load_mod():
    loader = importlib.machinery.SourceFileLoader("portal_ctl", CTL)
    spec = importlib.util.spec_from_loader("portal_ctl", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def run_ctl(*args, stdin=None):
    """把助手当子进程跑（与线上完全一致的入口）。返回 (rc, stdout)。"""
    p = subprocess.run([sys.executable, CTL] + list(args),
                       input=stdin, capture_output=True, text=True, timeout=60)
    return p.returncode, (p.stdout or "").strip()


def main():
    mod = load_mod()
    TMP = tempfile.mkdtemp(prefix="ctlsec-")
    HOME = os.path.join(TMP, "home")
    os.makedirs(os.path.join(HOME, ".ssh"), mode=0o700)
    os.makedirs(os.path.join(HOME, ".portal", "logs"))
    # 把"用户家目录"指向临时目录，避免碰到任何真实用户
    mod.user_home = lambda u: HOME

    print("== 1) 保留账号识别（含 systemd-* 前缀，历史上写成字面量永不匹配）==")
    check("is_reserved(root)", mod.is_reserved("root"))
    check("is_reserved(portal)", mod.is_reserved("portal"))
    check("is_reserved(slurm)", mod.is_reserved("slurm"))
    check("is_reserved(systemd-timesync) 前缀命中", mod.is_reserved("systemd-timesync"))
    check("is_reserved(sshd) 前缀命中", mod.is_reserved("sshd"))
    check("is_reserved(alice) 为假", not mod.is_reserved("alice"))
    check("valid_user(root) 为假", not mod.valid_user("root"))
    check("valid_user(portal) 为假", not mod.valid_user("portal"))
    check("valid_user(alice) 为真", mod.valid_user("alice"))
    check("valid_user('../x') 为假", not mod.valid_user("../x"))
    check("valid_user('Alice') 为假（大写）", not mod.valid_user("Alice"))
    check("name_ok(root) 仍为真（只读命令需要）", mod.name_ok("root"))

    print("== 2) 软链拒绝：读路径 ==")
    victim = os.path.join(TMP, "victim-secret")
    with open(victim, "w", encoding="utf-8") as fh:
        fh.write("TOP SECRET")
    link = os.path.join(HOME, ".portal", "logs", "4242.out")
    os.symlink(victim, link)
    check("_link_free_chain 拒绝软链", mod._link_free_chain("alice", link) is not None)
    fd, err = mod._open_user_file("alice", link)
    check("_open_user_file 拒绝软链", fd is None and bool(err), "fd=%s err=%s" % (fd, err))
    # 普通文件正常
    normal = os.path.join(HOME, ".portal", "logs", "1.out")
    with open(normal, "w") as fh:
        fh.write("hello")
    fd2, err2 = mod._open_user_file("alice", normal)
    check("_open_user_file 允许普通文件", fd2 is not None, "err=%s" % err2)
    if fd2 is not None:
        os.close(fd2)
    # 越界（软链指向家目录之外）
    check("_link_free_chain 拒绝越界路径", mod._link_free_chain("alice", "/etc/passwd") is not None)

    print("== 3) 软链拒绝：写路径（set-keys 的漏洞点）==")
    ssh_link = os.path.join(HOME, "bad-ssh")
    os.symlink("/etc", ssh_link)
    check("_ensure_user_dir 拒绝把软链当目录", _raises_die(mod, ssh_link))

    # ---- 直接演练历史攻击：预置 authorized_keys.portal-tmp 软链指向"root 的文件" ----
    import io
    import subprocess as _sp

    def fake_run(argv, timeout=120, input_text=None, env=None, **kw):
        if list(argv)[:2] == ["id", "-u"]:
            return _sp.CompletedProcess(argv, 0, str(os.getuid()), "")
        return _sp.CompletedProcess(argv, 0, "", "")

    mod.run = fake_run
    PUB = "ssh-ed25519 AAAA" + "A" * 60 + " attacker@evil"

    rootfile = os.path.join(TMP, "root-authorized_keys")
    with open(rootfile, "w", encoding="utf-8") as fh:
        fh.write("ROOT-ORIGINAL-KEY\n")
    attack_link = os.path.join(HOME, ".ssh", "authorized_keys.portal-tmp")
    os.symlink(rootfile, attack_link)
    _call_cmd(mod, "cmd_set_keys", "alice", stdin='["%s"]' % PUB)
    with open(rootfile, encoding="utf-8") as fh:
        still = fh.read()
    check("历史攻击：预置 authorized_keys.portal-tmp 软链后，root 的文件未被写入",
          still == "ROOT-ORIGINAL-KEY\n", repr(still))
    ak = os.path.join(HOME, ".ssh", "authorized_keys")
    check("正常写入仍然成功", os.path.isfile(ak) and not os.path.islink(ak)
          and PUB in open(ak, encoding="utf-8").read())

    # authorized_keys 自身是软链 → 必须拒绝
    os.remove(ak)
    os.symlink(rootfile, ak)
    _call_cmd(mod, "cmd_set_keys", "alice", stdin='["%s"]' % PUB)
    with open(rootfile, encoding="utf-8") as fh:
        still2 = fh.read()
    check("authorized_keys 是软链时拒绝写入（root 文件未被改写）",
          still2 == "ROOT-ORIGINAL-KEY\n", repr(still2))
    os.remove(ak)

    print("== 4) 命令入口拒绝保留账号（真实子进程，无需 root）==")
    for name, args in (("set-keys", ["root"]), ("get-keys", ["root"]),
                       ("images", ["root"]), ("log", ["root", "1"]),
                       ("rm-log", ["root", "1"]), ("kill", ["root", "1"]),
                       ("job-state", ["root", "1"]), ("save-image", ["root", "1", "x"]),
                       ("unprovision-user", ["sshd"]), ("ensure-assoc", ["root"]),
                       ("provision-user", ["portal", "100G"]), ("init-user", ["root"])):
        rc, out = run_ctl(*args, stdin="[]" if name == "set-keys" else None)
        ok = rc != 0 and '"ok": false' in out.replace("'", '"')
        check("拒绝 %s %s" % (name, " ".join(args)), ok, "rc=%s out=%s" % (rc, out[:120]))
    # 合法用户名应当通过校验层（这里只验"不是被守卫拦下"，不真的去碰集群）
    rc, out = run_ctl("get-keys", "nosuchuser_zzz")
    check("普通用户名不被守卫拦下（走到账号不存在）", "系统保留账号" not in out, out[:120])

    print("== 5) 关键实现约束（防止回退）==")
    src = open(CTL, encoding="utf-8").read()
    check("不再对用户路径用 install -d", '"install"' not in src)
    check("不再把 --export=ALL 作为参数传给 sbatch", '"--export=ALL' not in src)
    check("存在 --export 白名单", "--export=HOME=" in src)
    check("存在 --container-remap-root 拦截", "--container-remap-root" in src and "禁止" in src)
    check("set-keys 临时文件用 mkstemp 随机名（与目标同文件系统）",
          "tempfile.mkstemp" in src and "dir=sshd_dir" in src)
    check("不再使用可猜的固定临时名（tmp = ak + ...）", "tmp = ak +" not in src)
    check("O_NOFOLLOW 已使用", "O_NOFOLLOW" in src)
    check("按 inode 校验替换结果（防掉包）", "st_now.st_ino != st_created.st_ino" in src)
    check("ssh_node 对每个参数做单引号转义", "_shq" in src)

    if FAIL:
        print("\n有 %d 项失败: %s" % (len(FAIL), ", ".join(FAIL)))
        return 1
    print("\nCTL_SECURITY_OK")
    return 0


def _call_cmd(mod, func_name, *args, stdin=None):
    """在进程内调用助手命令，捕获 stdout / SystemExit（die() 会 sys.exit）。"""
    import io
    old_in, old_out = sys.stdin, sys.stdout
    sys.stdin, sys.stdout = io.StringIO(stdin or ""), io.StringIO()
    try:
        getattr(mod, func_name)(*args)
    except SystemExit:
        pass
    finally:
        out = sys.stdout.getvalue()
        sys.stdin, sys.stdout = old_in, old_out
    return out


def _raises_die(mod, path):
    """_ensure_user_dir 失败时调用 die()（SystemExit），据此判断是否被拒绝。"""
    try:
        mod._ensure_user_dir("alice", 0, path, 0o755)
        return False
    except SystemExit:
        return True
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
