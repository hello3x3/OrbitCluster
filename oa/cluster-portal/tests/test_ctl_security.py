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
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import pwd
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
    # 没有 -n 的话，ssh 会把调用者的 stdin 整个读走：`bash -s < 脚本` 这种用法会在
    # 第一次 ssh 之后静默截断（后半段不执行、退出码还是 0）。
    check("ssh_node 带 -n（不吞调用者的 stdin）", '"ssh", "-n"' in src)

    print("== 6) EXTRA_MOUNTS（NAS/数据集透传）白名单 ==")
    # 这一项一旦松掉，就是"把门户 DB 挂进用户容器"级别的漏洞：额外挂载只能来自
    # 服务端站点配置，解析必须 fail closed，且必须挡住门户自己的数据/系统目录。
    check("额外挂载只从站点配置/环境读，不从请求读",
          "PORTAL_EXTRA_MOUNTS" in src and "SITE.get(\"EXTRA_MOUNTS\")" in src
          and 'spec.get("mounts"' not in src and 'spec["mounts"]' not in src)
    _pm = mod._parse_extra_mounts
    check("空配置 → 无挂载", _pm("") == [] and _pm("   ") == [])
    # EXTRA_MOUNTS 要能分多行写（每行一项），否则十几个 NAS 路径挤一行没法维护
    _d = tempfile.mkdtemp(prefix="ctlsec-site-")
    _sp = os.path.join(_d, "site.conf")
    with open(_sp, "w", encoding="utf-8") as _fh:
        _fh.write("# c\nEXTRA_MOUNTS=/a:/a:ro\nEXTRA_MOUNTS=/b:/b:rw\nSSH_PORT=2222\n")
    _cfg = mod._load_site_conf(_sp)
    check("site.conf 多行 EXTRA_MOUNTS 累加成一项",
          _cfg.get("EXTRA_MOUNTS") == "/a:/a:ro;/b:/b:rw", _cfg)
    check("其它键仍然后写覆盖前写", _cfg.get("SSH_PORT") == "2222", _cfg)
    check("默认 ro、缺省 dst=src",
          _pm("/data") == [{"src": "/data", "dst": "/data", "flags": "ro"}])
    check("支持多项 / rw / 自定义 dst",
          _pm("/data:ro;/mnt/n1:/mnt/n1:ro;/mnt/n2/x:/data/x:rw".replace("/data:ro", "/data:/data:ro"))
          == [{"src": "/data", "dst": "/data", "flags": "ro"},
              {"src": "/mnt/n1", "dst": "/mnt/n1", "flags": "ro"},
              {"src": "/mnt/n2/x", "dst": "/data/x", "flags": "rw"}])
    for _bad, _why in (("/var/lib/cluster-portal", "门户数据目录"),                       ("/etc/cluster-portal:/x:ro", "门户配置目录"),
                       ("/etc:/etc:ro", "系统 /etc"),
                       ("/root:/root:ro", "root 家目录"),
                       ("/proc:/p:ro", "proc"),
                       ("relative:/x:ro", "相对路径"),
                       ("/data:../x:ro", "含 .."),
                       ("/data:d:e:f", "段数过多"),
                       ("/data:/data:rwx", "非法 flag"),
                       ("/data:/data:ro", None)):
        try:
            _pm(_bad)
            got = "passed"
        except SystemExit:
            got = "rejected"
        if _why is None:
            check("合法项被接受: %s" % _bad, got == "passed", got)
        else:
            check("拒绝 %s（%s）" % (_bad, _why), got == "rejected", got)

    # 最终交给 sbatch 的那个字符串：家目录 + 站点配置里的额外挂载
    _old_raw = mod.EXTRA_MOUNTS_RAW
    mod.EXTRA_MOUNTS_RAW = ""
    check("未配置时 --container-mounts 只有家目录",
          mod._container_mounts_arg("/home/u") == "/home/u:/home/u",
          mod._container_mounts_arg("/home/u"))
    mod.EXTRA_MOUNTS_RAW = "/data:/data:ro;/mnt/n1:/d1:rw;/mnt/n2"
    check("配置后 --container-mounts = 家目录 + 各额外挂载（含默认 ro）",
          mod._container_mounts_arg("/home/u")
          == "/home/u:/home/u,/data:/data:ro,/mnt/n1:/d1:rw,/mnt/n2:/mnt/n2:ro",
          mod._container_mounts_arg("/home/u"))
    mod.EXTRA_MOUNTS_RAW = _old_raw

        # ---- 镜像目录来自站点配置（不要写死 /share/images）----
    _tmp = tempfile.mkdtemp(prefix="ctlsec-site-")
    _sc = os.path.join(_tmp, "site.conf")
    with open(_sc, "w", encoding="utf-8") as fh:
        fh.write("SSH_PORT=2180\nIMAGES_MOUNT=%s\n" % os.path.join(_tmp, "imgs"))
    _old_sc = os.environ.get("PORTAL_SITE_CONF")
    _old_id = os.environ.pop("PORTAL_IMAGES_DIR", None)
    os.environ["PORTAL_SITE_CONF"] = _sc
    try:
        _m2 = load_mod()
        check("IMAGES_ROOT 取自 site.conf 的 IMAGES_MOUNT",
              _m2.IMAGES_ROOT == os.path.join(_tmp, "imgs"), _m2.IMAGES_ROOT)
        os.environ["PORTAL_IMAGES_DIR"] = os.path.join(_tmp, "envdir")
        _m3 = load_mod()
        check("PORTAL_IMAGES_DIR 环境变量优先于 site.conf",
              _m3.IMAGES_ROOT == os.path.join(_tmp, "envdir"), _m3.IMAGES_ROOT)
    finally:
        os.environ.pop("PORTAL_IMAGES_DIR", None)
        if _old_id is not None:
            os.environ["PORTAL_IMAGES_DIR"] = _old_id
        if _old_sc is None:
            os.environ.pop("PORTAL_SITE_CONF", None)
        else:
            os.environ["PORTAL_SITE_CONF"] = _old_sc
    check("默认值仍是 /share/images", mod.__dict__["IMAGES_ROOT"] != "", mod.IMAGES_ROOT)

    # ---- 个人镜像改名/删除（rename-image / delete-image）----
    print("  -- 个人镜像改名/删除 --")
    _src = open(CTL, encoding="utf-8").read()
    check("rename-image / delete-image 已注册",
          '"rename-image"' in _src and '"delete-image"' in _src)
    check("镜像名不收路径分隔符/空白（新建个人镜像 ≤32 位，公共/既有 ≤128 位）",
          'IMG_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._+-]{0,31}$")' in _src
          and 'PUBLIC_IMG_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._+-]{0,127}$")' in _src)
    check("两个命令都走普通文件+属主校验（拒软链/越界）",
          _src.count("_check_regular_outside_home(path, {uid, 0}") == 1)
    _me = pwd.getpwuid(os.getuid()).pw_name
    if mod.is_reserved(_me):
        print("  [skip] 当前身份 %s 是保留账号，跳过个人镜像功能用例" % _me)
    else:
        _imgs = os.path.join(_tmp, "imgs")
        _mine = os.path.join(_imgs, _me)
        os.makedirs(_mine, exist_ok=True)
        with open(os.path.join(_mine, "origin.sqsh"), "w") as fh:
            fh.write("fake-squashfs\n")
        with open(os.path.join(_mine, "victim.sqsh"), "w") as fh:
            fh.write("fake\n")
        _old_mount = os.environ.get("PORTAL_IMAGES_DIR")
        os.environ["PORTAL_IMAGES_DIR"] = _imgs
        try:
            # 当子进程跑（命令成功时 out() 会 sys.exit(0)，进程内直接调会带走测试进程）
            rc, so = run_ctl("rename-image", _me, "origin", "renamed")
            check("改名：磁盘文件真的改名了",
                  rc == 0 and os.path.isfile(os.path.join(_mine, "renamed.sqsh"))
                  and not os.path.exists(os.path.join(_mine, "origin.sqsh")), so[:120])
            check("改名：输出新名字与大小", rc == 0 and '"renamed.sqsh"' in so and '"size"' in so,
                  so[:120])
            rc, so = run_ctl("rename-image", _me, "renamed", "victim")
            check("目标已存在 → 拒绝且不覆盖",
                  rc != 0 and "已存在" in so
                  and os.path.isfile(os.path.join(_mine, "victim.sqsh")), so[:120])
            for bad, why in (("../etc/passwd", "路径分隔符"), ("a.b", "点号"), ("nosuch", "不存在")):
                rc, so = run_ctl("delete-image", _me, bad)
                check("delete-image 拒绝%s: %s" % (why, bad), rc != 0 and "ok" in so, so[:120])
            os.symlink("victim.sqsh", os.path.join(_mine, "evil.sqsh"))
            rc, so = run_ctl("delete-image", _me, "evil")
            check("软链镜像 → 拒绝删除且不跟随",
                  rc != 0 and os.path.islink(os.path.join(_mine, "evil.sqsh"))
                  and os.path.isfile(os.path.join(_mine, "victim.sqsh")), so[:120])
            rc, so = run_ctl("delete-image", _me, "renamed")
            check("删除：磁盘文件真的没了",
                  rc == 0 and not os.path.exists(os.path.join(_mine, "renamed.sqsh")), so[:120])
            rc, so = run_ctl("rename-image", _me, "victim", "bad name")
            check("新名非法（空格）→ 拒绝改名",
                  rc != 0 and os.path.isfile(os.path.join(_mine, "victim.sqsh")), so[:120])

            # ---- 全局列表 + 管理员操作（root 视图用；命令本身不做调用者判定，由门户把关）----
            with open(os.path.join(_imgs, "pub.sqsh"), "w") as fh:
                fh.write("pub\n")
            with open(os.path.join(_imgs, "half.sqsh.part"), "w") as fh:
                fh.write("part\n")
            rc, so = run_ctl("images-all")
            check("images-all 列出公共与个人镜像",
                  rc == 0 and '"pub.sqsh"' in so and '"victim.sqsh"' in so
                  and '"scope": "public"' in so, so[:200])
            check("images-all 不列 .part（正在打包的镜像不出现）",
                  rc == 0 and "half.sqsh" not in so, so[:200])
            # 公共镜像的真实命名（含点/横线）必须可以管理；个人镜像仍只许英文/数字/下划线
            with open(os.path.join(_imgs, "cuda12.8.0-devel-ubuntu24.04.sqsh"), "w") as fh:
                fh.write("pub\n")
            rc, so = run_ctl("set-image-note", "public", "cuda12.8.0-devel-ubuntu24.04", "公共基础镜像")
            check("公共镜像（名字带点）可写注释",
                  rc == 0 and os.path.isfile(
                      os.path.join(_imgs, "cuda12.8.0-devel-ubuntu24.04.json")), so[:140])
            rc, so = run_ctl("admin-image-rename", "public",
                             "cuda12.8.0-devel-ubuntu24.04", "cuda12.9.0-devel-ubuntu24.04")
            check("公共镜像（名字带点）可改名",
                  rc == 0 and os.path.isfile(
                      os.path.join(_imgs, "cuda12.9.0-devel-ubuntu24.04.sqsh")), so[:140])
            rc, so = run_ctl("admin-image-delete", "public", "cuda12.9.0-devel-ubuntu24.04")
            check("公共镜像（名字带点）可删除",
                  rc == 0 and not os.path.exists(
                      os.path.join(_imgs, "cuda12.9.0-devel-ubuntu24.04.sqsh")), so[:140])
            for bad_public in (".hidden", "a b", "../x", "x/y", "-lead", "名字"):
                rc, so = run_ctl("set-image-note", "public", bad_public, "x")
                check("公共名非法被拒: %s" % bad_public, rc != 0, so[:120])
            # 个人镜像名与公共镜像同字符集；新建 ≤32 位，既有（含改动前保存的长名）仍可操作
            with open(os.path.join(_mine, "torch.base-2.4.sqsh"), "w") as fh:
                fh.write("x\n")
            rc, so = run_ctl("set-image-note", _me, "torch.base-2.4", "带点名字")
            check("个人镜像名允许点/横线", rc == 0, so[:140])
            rc, so = run_ctl("rename-image", _me, "torch.base-2.4", "a" * 32)
            check("改名目标 32 位 → 允许",
                  rc == 0 and os.path.isfile(os.path.join(_mine, ("a" * 32) + ".sqsh")), so[:140])
            rc, so = run_ctl("rename-image", _me, ("a" * 32), "a" * 33)
            check("改名目标 33 位 → 拒绝", rc != 0 and "32" in so, so[:140])
            _legacy = "legacy_" + "x" * 30                       # 改动前可能存下的长名字
            with open(os.path.join(_mine, _legacy + ".sqsh"), "w") as fh:
                fh.write("x\n")
            rc, so = run_ctl("set-image-note", _me, _legacy, "老镜像")
            check("既有长名（37 位）仍可写注释", rc == 0, so[:140])
            rc, so = run_ctl("delete-image", _me, _legacy)
            check("既有长名仍可删除", rc == 0, so[:140])
            rc, so = run_ctl("set-image-note", _me, ".hidden", "x")
            check("个人名仍不以点开头", rc != 0, so[:140])
            with open(os.path.join(_mine, "_legacy_under.sqsh"), "w") as fh:
                fh.write("x\n")
            rc, so = run_ctl("set-image-note", _me, "_legacy_under", "下划线开头")
            check("下划线开头的既有名字仍可操作", rc == 0, so[:140])
            rc, so = run_ctl("rename-image", _me, "_legacy_under", "_new_under")
            check("新建名也允许下划线开头", rc == 0, so[:140])
            rc, so = run_ctl("rename-image", _me, "_new_under", "-lead")
            check("横线开头仍被拒", rc != 0, so[:140])

            rc, so = run_ctl("admin-image-rename", "public", "pub", "pub2")
            check("改公共镜像名",
                  rc == 0 and os.path.isfile(os.path.join(_imgs, "pub2.sqsh"))
                  and not os.path.exists(os.path.join(_imgs, "pub.sqsh")), so[:140])
            rc, so = run_ctl("admin-image-rename", _me, "victim", "victim2")
            check("改个人镜像名（管理员视角）",
                  rc == 0 and os.path.isfile(os.path.join(_mine, "victim2.sqsh")), so[:140])
            with open(os.path.join(_mine, "taken.sqsh"), "w") as fh:
                fh.write("x\n")
            rc, so = run_ctl("admin-image-rename", _me, "victim2", "taken")
            check("目标已存在 → 拒绝", rc != 0 and "已存在" in so, so[:140])
            rc, so = run_ctl("admin-image-delete", "public", "pub2")
            check("删公共镜像",
                  rc == 0 and not os.path.exists(os.path.join(_imgs, "pub2.sqsh")), so[:140])
            rc, so = run_ctl("admin-image-delete", _me, "../x")
            check("越界名 → 拒绝", rc != 0, so[:140])
            rc, so = run_ctl("admin-image-delete", _me, "evil")
            check("软链 → 拒绝删除", rc != 0 and os.path.islink(os.path.join(_mine, "evil.sqsh")),
                  so[:140])
            rc, so = run_ctl("admin-image-delete", _me, "nosuch")
            check("不存在 → 拒绝", rc != 0, so[:140])

            # ---- 注释：存镜像同目录的同名 .json，随改名/删除同步 ----
            import json as _json
            rc, so = run_ctl("set-image-note", _me, "victim2", "训练用镜像")
            _jp = os.path.join(_mine, "victim2.json")
            check("写注释 → 生成同名 .json",
                  rc == 0 and os.path.isfile(_jp) and '"note": "训练用镜像"' in so, so[:160])
            _meta = _json.load(open(_jp, encoding="utf-8"))
            check("同名 .json 里带 name/note/updated_at",
                  _meta.get("name") == "victim2" and _meta.get("note") == "训练用镜像"
                  and _meta.get("updated_at"), _meta)
            rc, so = run_ctl("images", _me)
            check("列表里带出注释", rc == 0 and '"note": "训练用镜像"' in so, so[:200])
            rc, so = run_ctl("rename-image", _me, "victim2", "victim3")
            check("改名时同名 .json 一起改名",
                  rc == 0 and os.path.isfile(os.path.join(_mine, "victim3.json"))
                  and not os.path.exists(_jp), so[:140])
            rc, so = run_ctl("delete-image", _me, "victim3")
            check("删除时同名 .json 一起删",
                  rc == 0 and not os.path.exists(os.path.join(_mine, "victim3.json")), so[:140])
            rc, so = run_ctl("set-image-note", _me, "nosuch", "x")
            check("给不存在的镜像写注释 → 拒绝", rc != 0, so[:140])
            rc, so = run_ctl("set-image-note", "public", "../x", "x")
            check("注释同样拒绝越界名", rc != 0, so[:140])
            # 规则：允许表情/空格，上限 64 个"看得见的字符"
            rc, so = run_ctl("set-image-note", _me, "taken", "🚀 训练 环境")
            check("注释允许表情与空格", rc == 0 and '"len": 7' in so, so[:140])
            rc, so = run_ctl("set-image-note", _me, "taken", "🎉" * 64)
            check("64 个表情 = 64 个字符 → 允许", rc == 0 and '"len": 64' in so, so[:140])
            rc, so = run_ctl("set-image-note", _me, "taken", "🎉" * 65)
            check("65 个字符 → 拒绝", rc != 0 and "64" in so, so[:140])
            rc, so = run_ctl("set-image-note", _me, "taken", "一" * 65)
            check("65 个汉字 → 拒绝", rc != 0 and "64" in so, so[:140])
            _j2 = _json.load(open(os.path.join(_mine, "taken.json"), encoding="utf-8"))
            check("被拒后原注释不变", mod.note_len(_j2.get("note", "")) == 64, _j2)
            rc, so = run_ctl("set-image-note", _me, "taken", "  ")
            check("全空白 = 清空注释",
                  rc == 0 and _json.load(open(os.path.join(_mine, "taken.json"),
                                              encoding="utf-8")).get("note") == "", so[:140])
        finally:
            if _old_mount is None:
                os.environ.pop("PORTAL_IMAGES_DIR", None)
            else:
                os.environ["PORTAL_IMAGES_DIR"] = _old_mount
    # ---- 就绪判定：只认带非空 PID 的横幅（PID 空 = 端口被别的进程占着）----
    print("  -- ssh-ready 判定 --")
    _v = mod._ssh_ready_verdict
    check("正常就绪：横幅带非空 PID",
          _v(" [start_ssh] sshd 实际监听端口: 52301 (PID=2368559)") == (True, False, "ok"))
    check("端口冲突：横幅在但 PID 为空（线上签名）",
          _v(" [start_ssh] sshd 实际监听端口: 52301 (PID=)") == (False, True, "port-conflict"))
    check("还没到时间：日志里没有横幅",
          _v("[start_ssh] 非 root 模式: 仅公钥认证\n") == (False, False, "starting"))
    check("新脚本的失败标记",
          _v("[start_ssh] 启动失败: sshd 没能监听端口 52301（多半已被别的进程占用）")
          == (False, True, "port-conflict"))
    check("sshd 自己打的绑定失败",
          _v("Bind to port 52301 on 0.0.0.0 failed: Address already in use")
          == (False, True, "port-conflict"))
    check("空日志算未就绪", _v("") == (False, False, "starting"))
    check("PID 不是数字时不误判成就绪",
          _v(" [start_ssh] sshd 实际监听端口: 52301 (PID=x1)") == (False, False, "starting"),
          _v(" [start_ssh] sshd 实际监听端口: 52301 (PID=x1)"))

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
