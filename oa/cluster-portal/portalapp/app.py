# -*- coding: utf-8 -*-
"""
OrbitCluster 集群门户 —— Flask 主应用。

运行方式（生产）:
    run.py 使用 waitress 多线程服务（见仓库根 run.py / deploy/install.sh）。

数据目录由环境变量 PORTAL_DATA 指定（默认 ./var），内含 portal.db 与 secret。
"""
import datetime
import json
import os
import re
import sys
import threading
import time
from functools import wraps

from flask import (Flask, abort, flash, g, jsonify, redirect,
                   render_template, request, session, url_for)

from . import auth as authlib
from . import ctl
from . import siteconf
from .db import (ACTIVE_STATES, DB, STATE_CN, init_db)

USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
# 与 deploy/portal-ctl RESERVED 对齐：这些 OS 账号不能设配额/建号
RESERVED_OS = {"root", "portal", "slurm", "nobody", "daemon", "bin", "sys", "games",
               "man", "lp", "mail", "news", "uucp", "proxy", "www-data", "backup",
               "list", "irc", "_apt", "systemd-*"}
QUOTA_SIZE_RE = re.compile(r"^[1-9][0-9]{0,3}[GT]$")
# 个人镜像名：仅英文/数字/下划线（保存路径 /share/images/<user>/<name>.sqsh）
IMG_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")

IMAGES_DIR = os.environ.get("PORTAL_IMAGES_DIR", "/share/images")
# 到期自动保存：提交给 Slurm 的时长 = 用户所选时长 + 该缓冲（分钟），
# 确保“先自动保存镜像、再停机”期间容器不被 Slurm 到点强杀。
EXPIRE_SAVE_BUFFER_MIN = int(os.environ.get("PORTAL_EXPIRE_BUFFER_MIN", "15"))
# 测试/调试开关
SAVE_SYNC = os.environ.get("PORTAL_SAVE_SYNC", "0") == "1"     # 保存同步执行（冒烟测试）
EXPIRY_POLL_S = int(os.environ.get("PORTAL_EXPIRY_POLL", "25"))
EXPIRY_DISABLED = os.environ.get("PORTAL_EXPIRY_DISABLE", "0") == "1"

DATA_DIR = os.environ.get("PORTAL_DATA", os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "var"))
DB_PATH = os.path.join(DATA_DIR, "portal.db")
SECRET_FILE = os.path.join(DATA_DIR, "secret")
CTL_PATH = os.environ.get("PORTAL_CTL", "/usr/local/sbin/portal-ctl")

NODE_IP = {}


def _row_dict(row):
    return {k: row[k] for k in row.keys()}


# ------------------------------------------------------------------ 镜像（公共 / 个人）
def _public_images():
    """/share/images 顶层公共 .sqsh（root 目录 755，portal 可读；路径可被 PORTAL_IMAGES_DIR 覆盖）。"""
    try:
        return sorted(f for f in os.listdir(IMAGES_DIR)
                      if f.lower().endswith(".sqsh"))
    except OSError:
        return []


def _user_images(username):
    """某用户个人镜像（/share/images/<user> 为 700，portal 经 root 助手读取）。"""
    try:
        return ctl.images(username).get("images") or []
    except ctl.CtlError:
        return []


def image_groups(username):
    """返回 {public:[{name,path}], mine:[{name,path}]} —— 用户可申请的镜像分组。"""
    pub = [{"name": f, "path": os.path.join(IMAGES_DIR, f)}
           for f in _public_images()]
    mine = []
    if username:  # username 为空串时只取公共镜像（管理员代申请页初始态）
        mine = [{"name": m["name"], "path": os.path.join(IMAGES_DIR, username, m["name"])}
                for m in _user_images(username)]
    return {"public": pub, "mine": mine}


def allowed_image_paths(username):
    """该用户当前可申请的镜像绝对路径集合（白名单，校验不再依赖 portal 的 stat 权限）。"""
    groups = image_groups(username)
    return {g["path"] for g in groups["public"]} | {g["path"] for g in groups["mine"]}


def _image_total(groups):
    return len(groups.get("public", [])) + len(groups.get("mine", []))


def _fmt_bytes(kb):
    """1K-block -> 人类可读（KiB/MiB/GiB）。"""
    try:
        kb = int(kb or 0)
    except (TypeError, ValueError):
        kb = 0
    if kb <= 0:
        return "0"
    if kb >= 1048576:
        v = kb / 1048576.0
        return ("%d GiB" % v) if v == int(v) else "%.2f GiB" % v
    if kb >= 1024:
        return "%.1f MiB" % (kb / 1024.0)
    return "%d KiB" % kb


def _fmt_limit(kb):
    """限额值：0 = 不限。"""
    return "不限" if not kb else _fmt_bytes(kb)


def safe_json(obj):
    """JSON 序列化并转义 < > &，可安全嵌入 <script> 块。"""
    return (json.dumps(obj, ensure_ascii=False)
            .replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e"))


def _secret():
    if os.environ.get("PORTAL_SECRET"):
        return os.environ["PORTAL_SECRET"]
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(SECRET_FILE):
        import secrets
        with open(SECRET_FILE, "w", encoding="utf-8") as fh:
            fh.write(secrets.token_hex(32))
        os.chmod(SECRET_FILE, 0o600)
    with open(SECRET_FILE, encoding="utf-8") as fh:
        return fh.read().strip()


def create_app(testing=False):
    app = Flask(__name__, template_folder=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "templates"),
        static_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), "static"))
    app.secret_key = _secret()
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024
    app.node_cache = ctl.NodeCache(ttl=5)

    def get_db() -> DB:
        if "db" not in g:
            g.db = DB(DB_PATH)
        return g.db

    def get_user_row():
        uid = session.get("uid")
        if not uid:
            return None
        row = get_db().user_by_id(uid)
        if row is None or not row["is_active"]:
            return None
        return row

    @app.teardown_appcontext
    def _teardown(exc):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    # ------------------------------------------------------------------ 装饰器
    def login_required(f):
        @wraps(f)
        def w(*a, **kw):
            if get_user_row() is None:
                return redirect(url_for("login", next=request.path))
            return f(*a, **kw)
        return w

    def admin_required(f):
        @wraps(f)
        def w(*a, **kw):
            u = get_user_row()
            if u is None:
                return redirect(url_for("login", next=request.path))
            if u["role"] != "admin":
                abort(403)
            return f(*a, **kw)
        return w

    def csrf_ok():
        tok = session.get("csrf")
        posted = request.form.get("_csrf") or request.headers.get("X-CSRF-Token") or ""
        return authlib.csrf_ok(tok, posted)

    @app.context_processor
    def inject_globals():
        u = get_user_row()
        return {
            "cur_user": u,
            "cur_role": u["role"] if u else "",
            "portal_name": get_db().get_setting("portal_name", "集群门户"),
            "csrf_token": session.get("csrf", ""),
            "state_cn": STATE_CN,
        }

    # ------------------------------------------------------------------ 认证
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if get_user_row() is not None:
            return redirect(url_for("my_instances"))
        if request.method == "POST":
            if not csrf_ok():
                flash("页面已过期，请重试", "error")
                return render_template("login.html")
            username = (request.form.get("username") or "").strip().lower()
            password = request.form.get("password") or ""
            u = get_db().user_by_name(username)
            if u is None or not authlib.verify_password(password, u["passwd"]):
                flash("用户名或密码错误", "error")
                return render_template("login.html")
            if not u["is_active"]:
                flash("该账号已被停用，请联系管理员", "error")
                return render_template("login.html")
            session.clear()
            session["uid"] = u["id"]
            session["csrf"] = authlib.new_csrf()
            get_db().user_touch_login(u["id"])
            flash("欢迎回来，%s！" % (u["display_name"] or u["username"]), "ok")
            nxt = request.args.get("next")
            if nxt and nxt.startswith("/") and not nxt.startswith("//"):
                return redirect(nxt)
            return redirect(url_for("admin_users" if u["role"] == "admin" else "my_instances"))
        session["csrf"] = authlib.new_csrf()
        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.clear()
        flash("已退出登录", "ok")
        return redirect(url_for("login"))

    # ------------------------------------------------------------------ 首页/我的资源
    @app.route("/")
    def home():
        u = get_user_row()
        if u is None:
            return redirect(url_for("login"))
        return redirect(url_for("admin_users" if u["role"] == "admin" else "my_instances"))

    # ---- 工具函数 ----
    def profile_complete(u):
        db = get_db()
        return db.key_count(u["id"]) >= 1 and db.port_count(u["id"]) >= 1

    def _os_ok(u):
        """该门户账号是否有同名集群 OS 账号。
        普通用户由 os_mode 判定；管理员（如 root）实时向集群确认（如纯平台 admin 则无）。"""
        attr = "osok_" + u["username"]
        cached = getattr(g, attr, None)
        if cached is not None:
            return cached
        if u["role"] != "admin":
            res = u["os_mode"] in ("provision", "existing")
        else:
            try:
                res = bool(ctl.user_status(u["username"]).get("exists"))
            except ctl.CtlError:
                res = False
        setattr(g, attr, res)
        return res

    def _sync_os_keys(db, u):
        """把 OS `~/.ssh/authorized_keys` 里已有的公钥并入门户 DB。

        为什么需要：门户「个人资料」页展示的是**门户自己的 ssh_keys 表**，并不直接读
        OS 文件。用户若直接在 `~/.ssh/authorized_keys` 里配了公钥（没走门户的添加流程），
        此前门户会显示"没有公钥"，表现为"明明配了却没读到"。

        这里在打开个人资料页时做一次**单向导入**：
          OS -> 门户 DB，只补缺失项；
          **绝不回写 OS 文件**（回写只发生在用户主动增删公钥时）。
        任何失败都静默处理，不影响页面渲染。
        """
        if not _os_ok(u):
            return 0
        try:
            oskeys = ctl.get_keys(u["username"]).get("keys", [])
        except ctl.CtlError:
            return 0
        added = 0
        for k in oskeys:
            if db.q1("SELECT 1 FROM ssh_keys WHERE user_id=? AND pubkey=?", (u["id"], k)):
                continue
            try:
                db.add_key(u["id"], "", k)
                added += 1
            except Exception:
                pass
        if added:
            app.logger.info("已从集群导入 %d 把公钥到门户账号 %s", added, u["username"])
        return added

    def can_apply(u):
        """能否申请资源：集群有同名 OS 账号 且 已完善公钥+端口。"""
        return _os_ok(u) and profile_complete(u)

    def _reconcile_instance(db, inst, urow):
        """与 Slurm 对账单个实例状态；返回更新后的行。"""
        if inst["state"] not in ACTIVE_STATES:
            return inst
        try:
            st = ctl.job_state(urow["username"], inst["job_id"])
        except ctl.CtlError as e:
            db.set_instance_meta(inst["id"], last_error=str(e)[:300])
            return inst
        if st.get("active"):
            new_state = st["state"] if st["state"] in STATE_CN else "UNKNOWN"
            db.set_instance_state(inst["id"], new_state, slurm_state=st["state"])
            # 记录作业实际开始时刻（到期自动保存按“开始时刻+时长”触发）
            if st["state"] == "RUNNING" and not (inst["started_at"] or ""):
                db.mark_started(inst["id"], _now())
            # 自动调度场景：运行后回填真实节点与 IP
            node = (st.get("node") or "").strip()
            if node and node != inst["node"]:
                ip = ""
                for n in app.node_cache.get().get("nodes", []):
                    if n["name"] == node:
                        ip = n.get("ip", "")
                        break
                db.set_instance_meta(inst["id"], node=node, ssh_ip=ip)
        else:
            sl = st.get("state", "UNKNOWN")
            mapped = sl if sl in STATE_CN else "UNKNOWN"
            db.set_instance_state(inst["id"], mapped, slurm_state=sl,
                                  stopped_at=_now())
        return db.instance_by_id(inst["id"])

    def _now():
        import datetime
        return datetime.datetime.now().isoformat(timespec="seconds")

    def enrich_instance(inst):
        """附加展示字段（dict 化）。"""
        d = dict(inst)
        db = get_db()
        u = db.user_by_id(inst["user_id"])
        d["username"] = u["username"] if u else "?"
        plan = db.plan_by_id(inst["plan_id"]) if inst["plan_id"] else None
        d["plan_name"] = plan["name"] if plan else ""
        d["res_name"] = inst["task_name"] or (plan["name"] if plan else "") or "任务"
        img = inst["image"] or ""
        d["image_name"] = img.rsplit("/", 1)[-1]
        d["state_cn"] = STATE_CN.get(inst["state"], inst["state"])
        d["can_stop"] = inst["state"] in ACTIVE_STATES
        d["is_running"] = inst["state"] == "RUNNING"
        d["created"] = inst["created_at"].replace("T", " ")[:19] if inst["created_at"] else ""
        d["started"] = ""
        d["hours"] = inst["walltime"]
        d["node_cn"] = inst["node"] or "自动调度"
        d["saving"] = 1 if d["saving"] else 0
        d["saving_now"] = bool(d["saving"])
        d["last_save"] = d["last_save"] or ""
        d["auto_saved_path"] = d["auto_saved_path"] or ""
        d["started_at"] = d["started_at"] or ""
        d["can_save"] = d["state"] == "RUNNING" and not d["saving_now"]
        return d

    def reconcile_user_instances(u):
        db = get_db()
        out = []
        for inst in db.instances_for(u["id"]):
            inst = _reconcile_instance(db, inst, u)
            out.append(enrich_instance(inst))
        return out

    def _quota_ctx(u):
        """当前用户配额/额度上下文：磁盘配额(OS 实读) + Slurm 关联/QoS/优先级/总额度。"""
        ctx = {"ok": False, "os_ok": _os_ok(u), "disk": None, "slurm": None,
               "disk_err": "", "slurm_err": ""}
        if not ctx["os_ok"]:
            ctx["note"] = "该门户账号在集群没有同名 OS 账号，无磁盘配额/Slurm 关联"
            return ctx
        try:
            q = ctl.quota(u["username"])
            ctx["disk"] = {
                "used_kb": q.get("used_kb", 0), "soft_kb": q.get("soft_kb", 0),
                "hard_kb": q.get("hard_kb", 0),
                "files_used": q.get("files_used", 0),
                "files_soft": q.get("files_soft", 0),
                "files_hard": q.get("files_hard", 0),
                "used_h": _fmt_bytes(q.get("used_kb", 0)),
                "soft_h": _fmt_limit(q.get("soft_kb", 0)),
                "hard_h": _fmt_limit(q.get("hard_kb", 0)),
                "note": q.get("note", ""),
            }
        except ctl.CtlError as e:
            ctx["disk_err"] = str(e)
        try:
            s = ctl.slurm_info(u["username"])
            assocs = s.get("assocs") or []
            qos = s.get("qos") or {}
            # 汇总：账号 / QoS / 关联优先级 / QoS 优先级
            accounts = sorted({a.get("account", "") for a in assocs if a.get("account")})
            qos_names = sorted({a.get("qos", "") for a in assocs if a.get("qos")})
            assoc_prio = next((a.get("priority") for a in assocs
                               if a.get("priority") not in (None, "")), "默认(未单独设置)")
            qos_brief = []
            for qn in qos_names:
                q = qos.get(qn, {})
                qos_brief.append("%s(优先级%s)" % (qn, q.get("priority") or "未设"))
            # 总额度 = QoS/关联限额字段（全空=不限）
            def nonempty(*vals):
                return [v for v in vals if v not in (None, "", "0")]
            limit_parts = []
            for a in assocs:
                limit_parts += nonempty(a.get("grp_tres"), a.get("grp_tres_mins"),
                                        a.get("max_tres"), a.get("max_tres_mins"),
                                        a.get("grp_jobs"), a.get("grp_submit"),
                                        a.get("max_jobs"), a.get("max_submit"),
                                        a.get("max_wall"), a.get("grp_wall"))
            for q in qos.values():
                limit_parts += nonempty(q.get("grp_tres"), q.get("grp_tres_mins"),
                                        q.get("max_tres"), q.get("max_tres_mins"),
                                        q.get("grp_jobs"), q.get("grp_submit"),
                                        q.get("max_jobs"), q.get("max_submit"),
                                        q.get("max_wall"), q.get("grp_wall"))
            ctx["slurm"] = {
                "accounts": ",".join(accounts) or "-",
                "qos": ",".join(qos_brief) or "-",
                "assoc_priority": assoc_prio,
                "total": "不限" if not limit_parts else "；".join(sorted(set(limit_parts))),
                "raw": {"assocs": assocs, "qos": qos},
            }
            ctx["ok"] = ctx["disk"] is not None
        except ctl.CtlError as e:
            ctx["slurm_err"] = str(e)
        return ctx

    @app.route("/my")
    @login_required
    def my_instances():
        u = get_user_row()
        instances = reconcile_user_instances(u)
        return render_template("my.html", instances=instances,
                               profile_ok=(not _os_ok(u)) or profile_complete(u),
                               my_quota=_quota_ctx(u))

    @app.route("/api/my/quota")
    @login_required
    def api_my_quota():
        u = get_user_row()
        return jsonify(_quota_ctx(u))

    @app.route("/api/my/instances")
    @login_required
    def api_my_instances():
        u = get_user_row()
        instances = reconcile_user_instances(u)
        return jsonify([enrich_instance(i) for i in instances])

    @app.route("/api/nodes")
    @login_required
    def api_nodes():
        return jsonify(app.node_cache.get())

    # ------------------------------------------------------------------ 个人资料
    @app.route("/profile")
    @login_required
    def profile():
        u = get_user_row()
        db = get_db()
        # 打开个人资料页时把 OS 上已有的公钥导入门户 DB（只增不改，不回写 OS 文件），
        # 避免"用户直接在 ~/.ssh/authorized_keys 里配了 key，门户却显示没有"。
        _sync_os_keys(db, u)
        mine = [p["port"] for p in db.ports_for(u["id"])]
        claimed = [r["port"] for r in db.q("SELECT port FROM user_ports WHERE user_id<>?",
                                           (u["id"],))]
        payload = {"common_ports": db.common_ports(), "mine": mine, "claimed": claimed}
        os_ok = _os_ok(u)
        return render_template("profile.html",
                               keys=db.keys_for(u["id"]),
                               ports=db.ports_for(u["id"]),
                               common_ports=db.common_ports(),
                               port_min=10000, port_max=65535,
                               profile_payload=safe_json(payload),
                               profile_ok=(not os_ok) or profile_complete(u),
                               is_admin=u["role"] == "admin",
                               can_os=os_ok)

    @app.route("/profile/name", methods=["POST"])
    @login_required
    def profile_name():
        if not csrf_ok():
            return _json_err("页面已过期，请刷新后重试")
        u = get_user_row()
        name = (request.form.get("display_name") or "").strip()
        if not name:
            return _json_err("显示名不能为空")
        if len(name) > 64:
            return _json_err("显示名过长（最多 64 个字符）")
        get_db().user_set_display(u["id"], name)
        return _json_ok("显示名已更新为「%s」" % name)

    @app.route("/profile/password", methods=["POST"])
    @login_required
    def profile_password():
        if not csrf_ok():
            return _json_err("页面已过期，请刷新后重试")
        u = get_user_row()
        old = request.form.get("old", "")
        new = request.form.get("new", "")
        new2 = request.form.get("new2", "")
        if not authlib.verify_password(old, u["passwd"]):
            return _json_err("原密码错误")
        from . import pwfile
        ok, err = pwfile.validate_password(new)
        if not ok:
            return _json_err(err)
        if new != new2:
            return _json_err("两次输入的新密码不一致")
        get_db().user_set_password(u["id"], authlib.hash_password(new))
        try:
            pwfile.upsert(u["username"], new)   # 同步明文到 /etc/cluster-portal/users.passwd
        except Exception:
            pass
        return _json_ok("密码已修改")

    @app.route("/profile/keys/add", methods=["POST"])
    @login_required
    def profile_key_add():
        if not csrf_ok():
            return _json_err("页面已过期，请刷新后重试")
        u = get_user_row()
        pubkey = (request.form.get("pubkey") or "").strip()
        ok, err = authlib.validate_pubkey(pubkey)
        if not ok:
            return _json_err(err)
        # 公钥写入集群同名 OS 账号的 authorized_keys：OS 上必须存在同名账号
        if not _os_ok(u):
            return _json_err("集群上没有与门户同名的 OS 账号，不能登记 SSH 公钥")
        db = get_db()
        if db.q1("SELECT 1 FROM ssh_keys WHERE user_id=? AND pubkey=?",
                 (u["id"], pubkey)):
            return _json_err("该公钥已存在")
        try:
            # 先并入 OS authorized_keys 里已有的公钥（防止覆盖 root 等账号的既有密钥）
            oskeys = ctl.get_keys(u["username"]).get("keys", [])
            for k in oskeys:
                if not db.q1("SELECT 1 FROM ssh_keys WHERE user_id=? AND pubkey=?",
                             (u["id"], k)):
                    db.add_key(u["id"], "", k)
            existing = [k["pubkey"] for k in db.keys_for(u["id"])]
            ctl.set_keys(u["username"], existing + [pubkey])
        except ctl.CtlError as e:
            return _json_err("写入 authorized_keys 失败：%s" % e)
        db.add_key(u["id"], "", pubkey)
        return _json_ok("公钥已添加（%d 个密钥已生效）" % (len(existing) + 1))

    @app.route("/profile/keys/<int:kid>/delete", methods=["POST"])
    @login_required
    def profile_key_del(kid):
        if not csrf_ok():
            return _json_err("页面已过期，请刷新后重试")
        u = get_user_row()
        db = get_db()
        key = db.q1("SELECT * FROM ssh_keys WHERE id=? AND user_id=?", (kid, u["id"]))
        if not key:
            return _json_err("密钥不存在")
        rest = [k["pubkey"] for k in db.keys_for(u["id"]) if k["id"] != kid]
        try:
            if rest:
                ctl.set_keys(u["username"], rest)
            else:
                # 全部删除：清空 authorized_keys
                ctl.set_keys(u["username"], [])
        except ctl.CtlError as e:
            return _json_err("更新 authorized_keys 失败：%s" % e)
        db.del_key(u["id"], kid)
        return _json_ok("已删除密钥")

    @app.route("/profile/ports/add", methods=["POST"])
    @login_required
    def profile_port_add():
        if not csrf_ok():
            return _json_err("页面已过期，请刷新后重试")
        u = get_user_row()
        raw = (request.form.get("port") or "").strip()
        label = (request.form.get("label") or "").strip()[:40]
        try:
            port = int(raw)
        except ValueError:
            return _json_err("端口必须是整数")
        if port < 10000 or port > 65535:
            return _json_err("端口必须在 10000-65535 之间")
        db = get_db()
        if port in db.common_ports():
            return _json_err("端口 %d 属于常用/保留端口，不能申请" % port)
        if db.is_port_in_use(port):
            return _json_err("端口 %d 已被其他用户申请" % port)
        try:
            db.add_port(u["id"], port, label)
        except Exception:
            return _json_err("端口 %d 已被其他用户申请（并发冲突）" % port)
        return _json_ok("端口 %d 已登记到你的端口池" % port)

    @app.route("/profile/ports/<int:pid>/delete", methods=["POST"])
    @login_required
    def profile_port_del(pid):
        if not csrf_ok():
            return _json_err("页面已过期，请刷新后重试")
        u = get_user_row()
        db = get_db()
        p = db.q1("SELECT * FROM user_ports WHERE id=? AND user_id=?", (pid, u["id"]))
        if not p:
            return _json_err("端口不存在")
        busy = db.q1("SELECT * FROM instances WHERE port=? AND user_id=? AND state IN (%s)"
                     % ",".join("?" * len(ACTIVE_STATES)),
                     (p["port"], u["id"]) + ACTIVE_STATES)
        if busy:
            return _json_err("端口 %d 正被运行中的资源占用（#%d），请先停机再释放" %
                             (p["port"], busy["id"]))
        db.del_port(u["id"], pid)
        return _json_ok("已释放端口 %d" % p["port"])

    # ------------------------------------------------------------------ 资源申请（镜像 + 套餐 + 任务名/时长/节点/端口，不展示命令）
    def _walltime_disp(hours):
        """用户可见时长（入库展示/到期计算）。"""
        return "%02d:00:00" % hours

    def _walltime_submit(hours):
        """实际提交给 Slurm 的时长 = 所选小时 + 到期保存缓冲（EXPIRE_SAVE_BUFFER_MIN）。"""
        m = int(hours) * 60 + EXPIRE_SAVE_BUFFER_MIN
        return "%02d:%02d:00" % (m // 60, m % 60)

    def _sanitize_task_name(name):
        s = re.sub(r"[^A-Za-z0-9_.\-]+", "-", (name or "").strip())
        s = re.sub(r"-{2,}", "-", s).strip("-")
        return (s or "task")[:40]

    def _gpu_models():
        """从集群实际 gres 中发现可选 GPU 型号（如 gpu:3090 → RTX 3090）。

        型号 token → 展示名 的映射由站点配置决定（见 portalapp/siteconf.py）：
        纯数字 token 自动加 "RTX " 前缀，因此换卡型不必改代码；
        需要特殊命名时在 /etc/cluster-portal/site.conf 里写 GPU_MODEL_MAP。
        """
        seen = set()
        try:
            data = app.node_cache.get()
            nodes = data.get("nodes") or [] if data.get("ok") else []
        except Exception:
            nodes = []
        for n in nodes:
            m = re.search(r"(?:^|,)\s*gpu:([^:]+):", n.get("gres") or "")
            if m:
                seen.add(siteconf.pretty_gpu(m.group(1)))
        if not seen:
            seen.add(siteconf.DEFAULT_GPU_MODEL)
        return sorted(seen)

    @app.route("/apply")
    @login_required
    def apply_page():
        u = get_user_row()
        if not _os_ok(u):
            flash("该门户账号在集群上没有同名 Linux 账号，不能申请资源"
                  "（如有 OS 账号的 root 可申请；纯平台管理员需用「代申请」帮用户提交）", "error")
            return redirect(url_for("my_instances"))
        if not profile_complete(u):
            flash("请先完善个人资料（至少添加 1 个 SSH 密钥和 1 个端口）才能申请资源", "error")
            return redirect(url_for("profile"))
        db = get_db()
        ncache = app.node_cache.get()
        nodes = ncache.get("nodes", []) if ncache.get("ok") else []
        groups = image_groups(u["username"])
        if _image_total(groups) == 0:
            flash("未发现可申请镜像：/share/images 下没有 .sqsh 文件，请联系管理员", "error")
        busy = {p["port"] for p in db.ports_for(u["id"])
                if db.user_active_by_port(u["id"], p["port"])}
        return render_template("apply.html",
                               image_groups=groups,
                               plans=db.plans_enabled(),
                               my_ports=db.ports_for(u["id"]),
                               busy_ports=sorted(busy),
                               nodes=nodes,
                               default_hours=12,
                               username=u["username"])

    @app.route("/apply", methods=["POST"])
    @login_required
    def apply_submit():
        if not csrf_ok():
            return _json_err("页面已过期，请刷新后重试")
        u = get_user_row()
        db = get_db()
        if not _os_ok(u):
            return _json_err("该门户账号在集群上没有同名 OS 账号，不能申请资源")
        if not profile_complete(u):
            return _json_err("个人资料未完善：需至少 1 个 SSH 密钥和 1 个端口")
        if _image_total(image_groups(u["username"])) == 0:
            return _json_err("/share/images 下没有可用 .sqsh 镜像")
        try:
            plan_id = int(request.form.get("plan_id") or 0)
            port = int(request.form.get("port") or 0)
            hours = int(request.form.get("hours") or 0)
        except ValueError:
            return _json_err("参数格式错误")
        image = (request.form.get("image") or "").strip()
        node = (request.form.get("node") or "").strip()
        task_name = (request.form.get("task_name") or "").strip()

        if not task_name:
            return _json_err("请填写任务名称")
        if not (1 <= len(task_name) <= 40):
            return _json_err("任务名称须为 3-10 个字符")
        # 镜像白名单：公共镜像 或 该用户自己的个人镜像（个人目录 700，portal 不能直接 stat）
        if image not in allowed_image_paths(u["username"]):
            return _json_err("请从镜像列表中选择（公共镜像或你自己的个人镜像）")
        plan = db.plan_by_id(plan_id)
        if not plan or not plan["enabled"]:
            return _json_err("套餐不存在或已停用")
        gpus, cpus, mem_gb = plan["gpus"], plan["cpus"], plan["mem_gb"]
        maxtime_h = plan["maxtime_h"]
        my_port = db.port_by_number(port)
        if not my_port or my_port["user_id"] != u["id"]:
            return _json_err("请从自己的端口池选择端口")
        if db.user_active_by_port(u["id"], port):
            return _json_err("端口 %d 正在被你的某个资源占用（排队/运行中），"
                             "请先停机或改用其它端口" % port)
        if not (1 <= hours <= maxtime_h):
            return _json_err("时长最多 %d 小时（套餐「%s」上限 maxtime=%d）。"
                             "如需更长，请找平台管理员协助申请" % (maxtime_h, plan["name"], maxtime_h))

        # 指定节点时校验容量；不指定则由 Slurm 自动调度
        nodeinfo = app.node_cache.get().get("nodes", [])
        ip = ""
        if node:
            node_ok = next((n for n in nodeinfo if n["name"] == node and n["avail"] == "up"), None)
            if node_ok is None:
                return _json_err("节点不可用或不在分区内: %s" % node)
            if cpus > node_ok["cpus_total"]:
                return _json_err("节点 %s 只有 %d 核，该套餐需要 %d 核" % (node, node_ok["cpus_total"], cpus))
            if mem_gb * 1024 > node_ok["mem_mb"]:
                return _json_err("节点 %s 内存不足（%d MB），该套餐需要 %dG" % (node, node_ok["mem_mb"], mem_gb))
            ip = node_ok["ip"]

        walltime = _walltime_disp(hours)          # 入库展示时长（到期自动保存按此计算）
        submit_wt = _walltime_submit(hours)       # 实际提交时长（含到期保存缓冲）
        home = "/share/home/%s" % u["username"]
        job_name = _sanitize_task_name(task_name)
        spec = {"user": u["username"], "node": node, "image": image,
                "gpus": gpus, "cpus": cpus, "mem_gb": mem_gb,
                "walltime": submit_wt, "port": port, "job_name": job_name}
        try:
            res = ctl.submit(spec)
        except ctl.CtlError as e:
            return _json_err("提交失败：%s" % e)
        job_id = res["job_id"]
        cmd = res.get("command") or ""
        iid = db.add_instance(u["id"], plan_id, task_name, node, gpus, cpus, mem_gb,
                              port, walltime, image, job_id, "PENDING", cmd,
                              res["log_path"], ip)
        where = "自动调度" if not node else node
        flash("资源已提交：作业 #%d（%s），端口 %d @ %s" % (job_id, task_name, port, where), "ok")
        return _json_ok("ok", {"instance_id": iid, "job_id": job_id})

    @app.route("/api/plans")
    @login_required
    def api_plans():
        return jsonify([_row_dict(t) for t in get_db().plans_enabled()])

    @app.route("/api/images")
    @login_required
    def api_images():
        """当前用户可见镜像分组：{public:[{name,path}], mine:[{name,path}]}。"""
        u = get_user_row()
        return jsonify(image_groups(u["username"]))

    @app.route("/api/user-images/<int:uid>")
    @admin_required
    def api_user_images(uid):
        """管理员代申请：查看指定目标用户的可用镜像（公共 + 该用户个人镜像）。"""
        db = get_db()
        u = db.user_by_id(uid)
        if not u:
            return _json_err("用户不存在")
        return jsonify(image_groups(u["username"]))

    # ------------------------------------------------------------------ 实例操作
    def _saving_guard(inst):
        return "该资源正在保存镜像，请稍候（保存完成后才能执行该操作）" if inst["saving"] else ""

    @app.route("/instances/<int:iid>/stop", methods=["POST"])
    @login_required
    def instance_stop(iid):
        if not csrf_ok():
            return _json_err("页面已过期，请刷新后重试")
        u = get_user_row()
        db = get_db()
        inst = db.instance_by_id(iid)
        if not inst or inst["user_id"] != u["id"]:
            return _json_err("资源不存在")
        if inst["state"] not in ACTIVE_STATES:
            return _json_err("该资源已不在运行/排队状态")
        sg = _saving_guard(inst)
        if sg:
            return _json_err(sg)
        try:
            res = ctl.kill(u["username"], inst["job_id"])
        except ctl.CtlError as e:
            return _json_err("停机失败：%s" % e)
        if res.get("already_ended"):
            sl = res.get("state", "UNKNOWN")
            mapped = sl if sl in STATE_CN else "UNKNOWN"
            db.set_instance_state(iid, mapped, slurm_state=sl, stopped_at=_now())
            return _json_ok("该作业此前已结束（%s）" % STATE_CN.get(mapped, mapped))
        db.set_instance_state(iid, "CANCELLED", slurm_state="CANCELLED",
                              stopped_at=_now())
        return _json_ok("已发送停机指令，作业将被取消")

    @app.route("/instances/<int:iid>/log")
    @login_required
    def instance_log(iid):
        u = get_user_row()
        db = get_db()
        inst = db.instance_by_id(iid)
        if not inst or inst["user_id"] != u["id"]:
            abort(404)
        lines = request.args.get("lines", 200, type=int)
        try:
            res = ctl.log(u["username"], inst["job_id"], lines)
        except ctl.CtlError as e:
            return jsonify({"ok": False, "error": str(e)})
        return jsonify({"ok": True, "text": res["text"], "job_id": inst["job_id"]})

    def _instance_terminal(inst):
        return inst["state"] not in ACTIVE_STATES

    @app.route("/instances/<int:iid>/restart", methods=["POST"])
    @login_required
    def instance_restart(iid):
        """停机/结束后的实例按原参数（镜像/套餐资源/端口/时长/任务名）重新 sbatch 提交，
        复用同一条记录（覆盖 job_id/状态/日志/命令）。"""
        if not csrf_ok():
            return _json_err("页面已过期，请刷新后重试")
        u = get_user_row()
        db = get_db()
        inst = db.instance_by_id(iid)
        if not inst or inst["user_id"] != u["id"]:
            return _json_err("资源不存在")
        if not _instance_terminal(inst):
            return _json_err("该资源仍在排队/运行中，请先停机后再重新启动")
        sg = _saving_guard(inst)
        if sg:
            return _json_err(sg)
        if not _os_ok(u):
            return _json_err("该账号在集群没有同名 OS 账号，无法重新启动")
        image = inst["image"] or ""
        if image not in allowed_image_paths(u["username"]):
            return _json_err("镜像已不存在（%s），无法重新启动" % os.path.basename(image))
        # 端口仍须属于该用户端口池（重启=继续占用同一端口）
        port = inst["port"]
        my_port = db.port_by_number(port)
        if not my_port or my_port["user_id"] != u["id"]:
            return _json_err("端口 %d 已不在你的端口池中（可能已删除）；"
                             "请先删除本条记录或在个人资料重新登记该端口" % port)
        if db.user_active_by_port(u["id"], port):
            return _json_err("端口 %d 已被其它资源占用（排队/运行中），请先处理后再重启" % port)
        # 用申请时选定的节点（req_node，自动调度=''），忽略运行后被回填的实际节点
        node = inst["req_node"] or ""
        ncache = app.node_cache.get()
        nodeinfo = ncache.get("nodes", []) if ncache.get("ok") else []
        if node and node not in [n["name"] for n in nodeinfo]:
            node = ""
        ip = next((n.get("ip", "") for n in nodeinfo if n["name"] == node), "") if node else ""
        task_name = inst["task_name"] or "restart"
        walltime = inst["walltime"] or "24:00:00"
        try:
            hours = int(walltime.split(":")[0])
        except (ValueError, IndexError):
            hours = 24
        if not (1 <= hours <= 720):
            return _json_err("原任务时长非法（%s）" % walltime)
        spec = {"user": u["username"], "node": node, "image": image,
                "gpus": inst["gpus"], "cpus": inst["cpus"], "mem_gb": inst["mem_gb"],
                "walltime": _walltime_submit(hours), "port": port,
                "job_name": _sanitize_task_name(task_name)}
        try:
            res = ctl.submit(spec)
        except ctl.CtlError as e:
            return _json_err("重新提交失败：%s" % e)
        job_id = res["job_id"]
        db.exec("UPDATE instances SET job_id=?, state='PENDING', slurm_state='PENDING', "
                "node=?, ssh_ip=?, cmd=?, log_path=?, stopped_at=NULL, "
                "started_at=NULL, auto_saved_at=NULL, auto_saved_path='', "
                "last_error=NULL, updated_at=? WHERE id=?",
                (job_id, node, ip, res.get("command") or "", res["log_path"], _now(), iid))
        return _json_ok("已重新启动：作业 #%d（%s）" % (job_id, task_name),
                        {"instance_id": iid, "job_id": job_id})

    @app.route("/instances/<int:iid>/save-image", methods=["POST"])
    @login_required
    def instance_save_image(iid):
        """手动保存镜像：把“运行中容器”的当前状态导出为个人镜像 /share/images/<u>/<name>.sqsh。

        底层是分钟级操作（enroot export 整个 rootfs），异步执行：先置 saving 标记，
        后台线程完成后写回 last_save / 解除标记；返回仅表示“已开始”。
        （PORTAL_SAVE_SYNC=1 时同步执行，供冒烟测试直接断言结果。）
        """
        if not csrf_ok():
            return _json_err("页面已过期，请刷新后重试")
        u = get_user_row()
        db = get_db()
        inst = db.instance_by_id(iid)
        if not inst or inst["user_id"] != u["id"]:
            return _json_err("资源不存在")
        if inst["state"] != "RUNNING":
            return _json_err("容器未在运行，无法保存镜像（仅运行中的容器可保存）")
        if inst["saving"]:
            return _json_err("该资源正在保存镜像，请稍候")
        if not _os_ok(u):
            return _json_err("该账号在集群没有同名 OS 账号，无法保存镜像")
        name = (request.form.get("name") or "").strip()
        if not IMG_NAME_RE.match(name):
            return _json_err("镜像名只能包含英文/数字/下划线（1-64 位），不能含空格或特殊字符")
        force = (request.form.get("force") or "") == "1"
        if not force:
            mine_names = {m["name"] for m in _user_images(u["username"])}
            if (name + ".sqsh") in mine_names:
                return jsonify({"ok": False, "need_force": True,
                                "error": "个人镜像 %s.sqsh 已存在；再次保存将覆盖该镜像" % name})
        username = u["username"]
        db.set_saving(iid, 1)
        db.set_last_save(iid, "正在保存个人镜像 %s.sqsh…" % name)
        if SAVE_SYNC:
            ok, msg, path = _perform_save(db, iid, username, inst["job_id"], name, force)
            db.set_saving(iid, 0)
            if ok:
                return _json_ok(msg, {"path": path})
            return _json_err(msg)
        t = threading.Thread(target=_save_worker, daemon=True,
                             args=(iid, username, inst["job_id"], name, force, "manual"))
        t.start()
        return _json_ok("已开始保存个人镜像 %s.sqsh，完成后可在此资源下方及「我的镜像」中看到" % name)

    @app.route("/instances/<int:iid>/delete", methods=["POST"])
    @login_required
    def instance_delete(iid):
        """删除已停止/结束的实例记录，并同步清理该作业日志文件。"""
        if not csrf_ok():
            return _json_err("页面已过期，请刷新后重试")
        u = get_user_row()
        db = get_db()
        inst = db.instance_by_id(iid)
        if not inst or inst["user_id"] != u["id"]:
            return _json_err("资源不存在")
        if not _instance_terminal(inst):
            return _json_err("该资源仍在排队/运行中，请先停机后再删除")
        sg = _saving_guard(inst)
        if sg:
            return _json_err(sg)
        # 清理日志（尽力而为：文件不存在/删除失败不阻塞记录删除）
        log_msg = ""
        try:
            res = ctl.rm_log(u["username"], inst["job_id"])
            log_msg = "；已清理日志文件" if res.get("removed") else ""
        except ctl.CtlError as e:
            log_msg = "；日志清理失败：%s" % str(e)[:80]
        db.del_instance(iid, uid=u["id"])
        return _json_ok("已删除该资源记录%s" % log_msg)

    # ------------------------------------------------------------------ 集群状态
    @app.route("/status")
    @login_required
    def status_page():
        u = get_user_row()
        db = get_db()
        data = app.node_cache.get()
        active = []
        for inst in db.instances_all_active():
            active.append(enrich_instance(inst))
        mine = [enrich_instance(i) for i in db.instances_for(u["id"])]
        return render_template("status.html", nodes=data.get("nodes", []),
                               partition=data.get("partition", "gpu"),
                               active=active, mine=mine,
                               is_admin=u["role"] == "admin")

    # ------------------------------------------------------------------ 管理员：用户
    @app.route("/admin")
    @admin_required
    def admin_index():
        return redirect(url_for("admin_users"))

    @app.route("/admin/users")
    @admin_required
    def admin_users():
        db = get_db()
        actor = get_user_row()
        # 批量读取 OS 实际配额（一次调用；失败则每行显示无）
        qmap = {}
        try:
            qmap = ctl.quota_all().get("quotas") or {}
        except ctl.CtlError:
            qmap = {}
        users = []
        for u in db.users_all():
            d = dict(u)
            # 注意：避免用 keys/ports 等与 dict 内置方法同名的键（Jinja 会优先取方法对象）
            d["key_cnt"] = db.key_count(u["id"])
            d["port_cnt"] = db.port_count(u["id"])
            d["active_cnt"] = db.q1(
                "SELECT COUNT(*) n FROM instances WHERE user_id=? AND state IN (%s)"
                % ",".join("?" * len(ACTIVE_STATES)),
                (u["id"],) + ACTIVE_STATES)["n"]
            d["created"] = (u["created_at"] or "").replace("T", " ")[:16]
            d["last_login"] = (u["last_login"] or "-").replace("T", " ")[:16]
            q = qmap.get(u["username"]) or {}
            d["quota_used"] = _fmt_bytes(q.get("used_kb", 0))
            d["quota_hard"] = _fmt_limit(q.get("hard_kb", 0))
            d["quota_os_present"] = "hard_kb" in q and u["username"] in qmap
            # 是否允许“改配额”：目标须有同名 OS 账号且非系统保留账号；
            # 普通用户行任何管理员可改；管理员行仅 root 或本人可改
            d["quota_can_edit"] = False
            if u["role"] == "user":
                d["quota_can_edit"] = u["os_mode"] in ("provision", "existing") \
                    and u["username"] not in RESERVED_OS
            elif actor["username"] == "root" or actor["id"] == u["id"]:
                d["quota_can_edit"] = u["username"] not in RESERVED_OS
            users.append(d)
        return render_template("admin_users.html", users=users,
                               quota_options=["100G", "300G", "500G", "1T"])

    @app.route("/admin/users/<int:uid>/quota", methods=["POST"])
    @admin_required
    def admin_user_quota(uid):
        """管理员改他人/自己磁盘配额（实际写 OS /share，软=硬即时生效）。"""
        if not csrf_ok():
            return _json_err("页面已过期，请刷新后重试")
        actor = get_user_row()
        db = get_db()
        u = db.user_by_id(uid)
        if not u:
            return _json_err("用户不存在")
        size = (request.form.get("quota") or request.form.get("size") or "").strip().upper()
        if not QUOTA_SIZE_RE.match(size):
            return _json_err("配额格式错误（例 100G / 500G / 1T）")
        if u["username"] in RESERVED_OS:
            return _json_err("系统保留账号不能设置配额")
        # 权限：普通用户行任何管理员可改；管理员行仅 root 或本人
        if u["role"] == "admin" and actor["username"] != "root" and actor["id"] != u["id"]:
            return _json_err("管理员账号的配额由 root 管理")
        # 目标必须真实存在同名 OS 账号（OS 上没有则拒绝）
        if not _os_ok(u):
            return _json_err("该账号在集群没有同名 OS 账号，无法设置配额")
        try:
            res = ctl.set_quota(u["username"], size)
        except ctl.CtlError as e:
            return _json_err("设置配额失败：%s" % e)
        db.exec("UPDATE users SET quota=? WHERE id=?", (size, uid))
        hard = res.get("hard_kb") or 0
        return _json_ok("已将 %s 的 /share 配额设为 %s（OS 实际硬限 %s）"
                        % (u["username"], size, _fmt_limit(hard)),
                        {"hard_kb": hard, "used_kb": res.get("used_kb", 0)})

    def _create_user_inner(db, username, display_name, role, password, quota, os_mode):
        from . import pwfile
        imported_keys = 0
        err = None
        if not USERNAME_RE.match(username):
            return None, "用户名不合法（小写字母/数字/_-，3-32 位）", 0
        if db.user_by_name(username):
            return None, "门户中已存在同名用户", 0
        if role not in ("user", "admin"):
            return None, "角色非法", 0
        if password:
            okp, errp = pwfile.validate_password(password)
            if not okp:
                return None, errp, 0
        if role == "user":
            if os_mode not in ("provision", "existing"):
                return None, "普通用户必须选择账号开通方式", 0
            if not password:
                return None, "普通用户初始密码不能为空", 0
            try:
                if os_mode == "provision":
                    ctl.provision_user(username, quota)
                else:
                    ctl.init_user(username)
            except ctl.CtlError as e:
                return None, "集群建号失败：%s" % e, 0
        # 管理员角色：仅门户账号（可选设密码）
        pwd = password or authlib.random_password()
        uid = db.add_user(username, display_name or username, role,
                          authlib.hash_password(pwd), quota,
                          "provision" if role == "user" else "none")
        try:
            pwfile.upsert(username, pwd)   # 新账号密码明文写入密码文件
        except Exception:
            pass
        if role == "user" and os_mode == "existing":
            db.exec("UPDATE users SET os_mode='existing' WHERE id=?", (uid,))
            # 导入 OS 账号 authorized_keys 中已有的公钥（防止日后门户写回时覆盖丢失）
            try:
                gk = ctl.get_keys(username)
                for k in gk.get("keys", []):
                    if not db.q1("SELECT 1 FROM ssh_keys WHERE user_id=? AND pubkey=?",
                                 (uid, k)):
                        db.add_key(uid, "", k)
                        imported_keys += 1
            except ctl.CtlError:
                pass  # 读取失败不阻塞开户（用户可后续在个人资料里自行补登记）
        return uid, pwd, imported_keys

    @app.route("/admin/users/create", methods=["POST"])
    @admin_required
    def admin_user_create():
        if not csrf_ok():
            flash("页面已过期，请重试", "error")
            return redirect(url_for("admin_users"))
        db = get_db()
        username = (request.form.get("username") or "").strip().lower()
        display_name = (request.form.get("display_name") or "").strip()[:64]
        role = request.form.get("role") or "user"
        quota = (request.form.get("quota") or "500G").strip().upper()
        if not re.match(r"^[1-9][0-9]{0,2}[GT]$", quota):
            flash("配额格式错误（如 100G/500G/1T）", "error")
            return redirect(url_for("admin_users"))
        password = request.form.get("password") or ""
        os_mode = request.form.get("os_mode") or "provision"
        uid, pwd, imported = _create_user_inner(db, username, display_name, role,
                                                password, quota, os_mode)
        if uid is None:
            flash(pwd, "error")
            return redirect(url_for("admin_users"))
        note = "请将初始密码转交给用户：" if role == "user" else "管理员账号初始密码："
        if not password:
            note = "已随机生成初始密码（仅显示一次）："
        extra = "（已导入 OS 上现有 %d 把公钥）" % imported if imported else ""
        flash("用户 %s 创建成功%s。%s %s" % (username, extra, note, pwd), "ok")
        return redirect(url_for("admin_users"))

    @app.route("/admin/users/<int:uid>/toggle", methods=["POST"])
    @admin_required
    def admin_user_toggle(uid):
        if not csrf_ok():
            return _json_err("页面已过期")
        db = get_db()
        u = db.user_by_id(uid)
        if not u:
            return _json_err("用户不存在")
        if u["role"] == "admin":
            return _json_err("不能停用/启用其他管理员账号（管理员账号由集群 root 管理，"
                             "可在 admin 节点直接改库或删除 /root/.cluster-portal-admin 重置）")
        db.user_set_active(uid, not u["is_active"])
        return _json_ok("已%s %s" % ("启用" if not u["is_active"] else "停用", u["username"]))

    @app.route("/admin/users/<int:uid>/resetpwd", methods=["POST"])
    @admin_required
    def admin_user_resetpwd(uid):
        """门户密码重置：仅限普通用户；管理员账号由 OS root 用密码文件统一改。"""
        if not csrf_ok():
            return _json_err("页面已过期")
        db = get_db()
        u = db.user_by_id(uid)
        if not u:
            return _json_err("用户不存在")
        if u["role"] == "admin":
            return _json_err("管理员账号的门户密码不能由门户管理员重置；"
                             "请在 admin 节点以 root 编辑 /etc/cluster-portal/users.passwd")
        pwd = authlib.random_password()
        db.user_set_password(uid, authlib.hash_password(pwd))
        try:
            from . import pwfile
            pwfile.upsert(u["username"], pwd)
        except Exception:
            pass
        return jsonify({"ok": True, "msg": "已重置密码",
                        "username": u["username"], "password": pwd})

    @app.route("/admin/users/<int:uid>/delete", methods=["POST"])
    @admin_required
    def admin_user_delete(uid):
        """删除门户账号。
        - 普通用户(os_mode=provision，门户代建号)：先注销集群 OS 账号（终止全部作业、
          删除管理节点与各计算节点上的账号、sacctmgr 关联与 NFS 家目录数据），再删门户记录；
        - 管理员 / os_mode=existing（OS 账号本来就存在）：只删门户记录，不动集群账号与家目录。
        """
        if not csrf_ok():
            return _json_err("页面已过期")
        db = get_db()
        u = db.user_by_id(uid)
        if not u:
            return _json_err("用户不存在")
        if u["role"] == "admin":
            return _json_err("不能删除其他管理员账号（管理员账号由集群 root 管理）")
        if u["id"] == session.get("uid"):
            return _json_err("不能删除当前登录的管理员账号")
        username = u["username"]
        role, os_mode = u["role"], u["os_mode"]
        removed_os = False
        if role == "user" and os_mode == "provision":
            try:
                ctl.unprovision_user(username)
                removed_os = True
            except ctl.CtlError as e:
                return _json_err("注销集群账号失败：%s（门户账号未删除，可重试或先手动处理）" % e)
        db.exec("DELETE FROM users WHERE id=?", (uid,))
        try:
            from . import pwfile
            pwfile.remove(username)
        except Exception:
            pass
        if removed_os:
            msg = "门户账号 %s 已删除；集群账号、作业与 /share 家目录数据已一并注销" % username
        else:
            msg = ("门户账号 %s 已删除（该账号原本不存在或未由门户开通集群账号，"
                   "已保留其 OS 账号与家目录数据）" % username)
        return _json_ok(msg)

    # ------------------------------------------------------------------ 管理员代申请资源
    @app.route("/api/apply-targets")
    @admin_required
    def api_apply_targets():
        """管理员代申请的候选人：有集群 OS 账号的普通用户及其端口池。"""
        db = get_db()
        out = []
        for u in db.users_all():
            if u["role"] != "user" or u["os_mode"] not in ("provision", "existing"):
                continue
            ports = []
            for p in db.ports_for(u["id"]):
                ports.append({"port": p["port"],
                              "busy": db.user_active_by_port(u["id"], p["port"])})
            out.append({"id": u["id"], "username": u["username"],
                        "display_name": u["display_name"],
                        "keys": db.key_count(u["id"]),
                        "ports": ports})
        return jsonify(out)

    @app.route("/admin/apply")
    @admin_required
    def admin_apply():
        # 先只给公共镜像；选中目标用户后前端经 /api/user-images/<uid> 补其个人镜像
        return render_template("admin_apply.html",
                               image_groups={"public": image_groups("")["public"],
                                             "mine": []},
                               plans=get_db().plans_enabled(),
                               nodes=app.node_cache.get().get("nodes", []) if app.node_cache.get().get("ok") else [],
                               default_hours=12)

    @app.route("/admin/apply", methods=["POST"])
    @admin_required
    def admin_apply_submit():
        if not csrf_ok():
            return _json_err("页面已过期，请刷新后重试")
        actor = get_user_row()
        db = get_db()
        try:
            user_id = int(request.form.get("user_id") or 0)
            plan_id = int(request.form.get("plan_id") or 0)
            port = int(request.form.get("port") or 0)
            hours = int(request.form.get("hours") or 0)
        except ValueError:
            return _json_err("参数格式错误")
        image = (request.form.get("image") or "").strip()
        node = (request.form.get("node") or "").strip()
        task_name = (request.form.get("task_name") or "").strip()
        if not task_name:
            return _json_err("请填写任务名称")
        if not (1 <= len(task_name) <= 40):
            return _json_err("任务名称须为 3-10 个字符")
        target = db.user_by_id(user_id)
        if not target or target["role"] != "user" or target["os_mode"] not in ("provision", "existing"):
            return _json_err("请选择有效的普通用户（有集群 OS 账号）作为申请对象")
        if image not in allowed_image_paths(target["username"]):
            return _json_err("请从镜像列表中选择（公共镜像或该用户的个人镜像）")
        plan = db.plan_by_id(plan_id)
        if not plan or not plan["enabled"]:
            return _json_err("套餐不存在或已停用")
        my_port = db.port_by_number(port)
        if not my_port or my_port["user_id"] != target["id"]:
            return _json_err("端口必须是该用户端口池里的（请选帮谁申请后再选其端口）")
        if db.user_active_by_port(target["id"], port):
            return _json_err("该用户的端口 %d 正在使用中，请换端口" % port)
        if not (1 <= hours <= 720):
            return _json_err("时长须在 1-720 小时之间")
        nodeinfo = app.node_cache.get().get("nodes", [])
        ip = ""
        if node:
            node_ok = next((n for n in nodeinfo if n["name"] == node and n["avail"] == "up"), None)
            if node_ok is None:
                return _json_err("节点不可用或不在分区内: %s" % node)
            if plan["cpus"] > node_ok["cpus_total"]:
                return _json_err("节点 %s 只有 %d 核，该套餐需要 %d 核" % (node, node_ok["cpus_total"], plan["cpus"]))
            if plan["mem_gb"] * 1024 > node_ok["mem_mb"]:
                return _json_err("节点 %s 内存不足，该套餐需要 %dG" % (node, plan["mem_gb"]))
            ip = node_ok["ip"]
        walltime = _walltime_disp(hours)
        home = "/share/home/%s" % target["username"]
        spec = {"user": target["username"], "node": node, "image": image,
                "gpus": plan["gpus"], "cpus": plan["cpus"], "mem_gb": plan["mem_gb"],
                "walltime": _walltime_submit(hours), "port": port,
                "job_name": _sanitize_task_name(task_name)}
        try:
            res = ctl.submit(spec)   # 底层：root 经 runuser(=sudo -u) 以目标用户身份提交
        except ctl.CtlError as e:
            return _json_err("提交失败：%s" % e)
        job_id = res["job_id"]
        iid = db.add_instance(target["id"], plan_id, task_name, node, plan["gpus"],
                              plan["cpus"], plan["mem_gb"], port, walltime, image,
                              job_id, "PENDING", res.get("command") or "", res["log_path"], ip)
        return _json_ok("已代 %s 提交：作业 #%d（%s）" % (target["username"], job_id, task_name),
                        {"instance_id": iid, "job_id": job_id})

    # ------------------------------------------------------------------ root 授予/撤销管理员
    @app.route("/admin/users/<int:uid>/role", methods=["POST"])
    @admin_required
    def admin_user_role(uid):
        if not csrf_ok():
            return _json_err("页面已过期")
        actor = get_user_row()
        if actor["username"] != "root":
            return _json_err("仅平台最高管理员 root 可以授予/撤销管理员权限")
        db = get_db()
        u = db.user_by_id(uid)
        if not u:
            return _json_err("用户不存在")
        if u["username"] == "root":
            return _json_err("不能修改 root 自己的权限")
        new_role = "user" if u["role"] == "admin" else "admin"
        db.exec("UPDATE users SET role=? WHERE id=?", (new_role, u["id"]))
        return _json_ok("已将 %s 的权限调整为：%s" % (u["username"],
                         "管理员" if new_role == "admin" else "普通用户"))

    # ------------------------------------------------------------------ 管理员：资源套餐（CPU/内存/GPU 预设）
    @app.route("/admin/plans")
    @admin_required
    def admin_plans():
        db = get_db()
        return render_template("admin_plans.html", plans=db.plans_all(),
                               gpu_models=_gpu_models())

    def _plan_form():
        name = (request.form.get("name") or "").strip()[:80]
        description = (request.form.get("description") or "").strip()[:400]
        try:
            gpus = int(request.form.get("gpus") or 0)
            cpus = int(request.form.get("cpus") or 1)
            mem_gb = int(request.form.get("mem_gb") or 4)
            maxtime_h = int(request.form.get("maxtime_h") or 48)
        except ValueError:
            return None, "数字字段格式错误"
        gpu_model = (request.form.get("gpu_model") or "").strip()[:40]
        available = _gpu_models()
        if not name:
            return None, "套餐名称必填"
        if not (0 <= gpus <= 8):
            return None, "GPU 卡数须在 0-8（0=纯 CPU）"
        if gpus >= 1:
            if gpu_model not in available:
                return None, "GPU 型号须从集群可用型号中选择：%s" % "、".join(available)
        else:
            gpu_model = ""
        if not (1 <= cpus <= 64):
            return None, "CPU 核数须在 1-64"
        if not (1 <= mem_gb <= 256):
            return None, "内存须在 1-256 GB"
        if not (1 <= maxtime_h <= 720):
            return None, "最大时长须在 1-720 小时"
        return (name, description, gpus, gpu_model, cpus, mem_gb, maxtime_h), None

    @app.route("/admin/plans/add", methods=["POST"])
    @admin_required
    def admin_plan_add():
        if not csrf_ok():
            flash("页面已过期，请重试", "error")
            return redirect(url_for("admin_plans"))
        vals, err = _plan_form()
        if err:
            flash(err, "error")
            return redirect(url_for("admin_plans"))
        get_db().add_plan(*vals)
        flash("套餐已添加", "ok")
        return redirect(url_for("admin_plans"))

    @app.route("/admin/plans/<int:pid>/edit", methods=["POST"])
    @admin_required
    def admin_plan_edit(pid):
        if not csrf_ok():
            return _json_err("页面已过期")
        vals, err = _plan_form()
        if err:
            return _json_err(err)
        keys = ("name", "description", "gpus", "gpu_model", "cpus", "mem_gb", "maxtime_h")
        get_db().update_plan(pid, **dict(zip(keys, vals)))
        return _json_ok("套餐已更新")

    @app.route("/admin/plans/<int:pid>/toggle", methods=["POST"])
    @admin_required
    def admin_plan_toggle(pid):
        if not csrf_ok():
            return _json_err("页面已过期")
        db = get_db()
        p = db.plan_by_id(pid)
        if not p:
            return _json_err("套餐不存在")
        db.update_plan(pid, enabled=0 if p["enabled"] else 1)
        return _json_ok("套餐已%s" % ("停用" if p["enabled"] else "启用"))

    @app.route("/admin/plans/<int:pid>/data")
    @admin_required
    def admin_plan_data(pid):
        p = get_db().plan_by_id(pid)
        if not p:
            return _json_err("套餐不存在")
        return jsonify({"ok": True, "plan": _row_dict(p)})

    @app.route("/admin/plans/<int:pid>/delete", methods=["POST"])
    @admin_required
    def admin_plan_delete(pid):
        if not csrf_ok():
            return _json_err("页面已过期")
        db = get_db()
        if db.plan_instances(pid):
            return _json_err("该套餐已有资源申请记录，不能删除（可停用）")
        db.del_plan(pid)
        return _json_ok("套餐已删除")

    # ------------------------------------------------------------------ JSON 工具
    def _json_ok(msg, extra=None):
        d = {"ok": True, "msg": msg}
        if extra:
            d.update(extra)
        return jsonify(d)

    def _json_err(msg):
        return jsonify({"ok": False, "error": msg})

    # ------------------------------------------------------------------ 错误页
    @app.errorhandler(403)
    def e403(e):
        return render_template("error.html", code=403, msg="没有权限访问该页面"), 403

    @app.errorhandler(404)
    def e404(e):
        return render_template("error.html", code=404, msg="页面不存在"), 404

    @app.errorhandler(500)
    def e500(e):
        return render_template("error.html", code=500, msg="服务器内部错误"), 500

    with app.app_context():
        db = DB(DB_PATH)
        init_db(db)
        db.close()
    from . import pwfile
    pwfile.start(DB_PATH)  # 密码文件监听（PORTAL_PASSWD_SYNC=0 可关闭）
    if not EXPIRY_DISABLED:
        threading.Thread(target=_expiry_loop, daemon=True).start()
    return app


# ----------------------------------------------------------------------------
# 保存镜像（手动 / 到期自动）与到期调度 —— 模块级，独立线程执行
# ----------------------------------------------------------------------------
def _fmt_bytes_kb(kb):
    try:
        kb = int(kb or 0)
    except (TypeError, ValueError):
        kb = 0
    if kb <= 0:
        return "0"
    if kb >= 1048576:
        v = kb / 1048576.0
        return ("%d GiB" % v) if v == int(v) else "%.2f GiB" % v
    if kb >= 1024:
        return "%.1f MiB" % (kb / 1024.0)
    return "%d KiB" % kb


def _now_iso():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _perform_save(db, iid, username, job_id, name, force):
    """执行一次镜像导出并写回结果（不触碰 saving 标记，由调用方负责）。返回 (ok, msg, path)。"""
    try:
        res = ctl.save_image(username, job_id, name, force)
        path = res.get("path") or ""
        size = res.get("size") or 0
        msg = "已保存个人镜像：%s/%s.sqsh%s" % (
            username, name,
            ("（%s）" % _fmt_bytes_kb(int(size) // 1024)) if size else "")
        db.set_last_save(iid, msg)
        return True, msg, path
    except ctl.CtlError as e:
        msg = "镜像保存失败：%s" % str(e)[:300]
        db.set_last_save(iid, msg)
        return False, msg, ""


def _save_worker(iid, username, job_id, name, force, kind):
    """后台保存线程：手动(kind=manual)或到期(kind=auto)。"""
    db = DB(DB_PATH)
    try:
        ok, msg, path = _perform_save(db, iid, username, job_id, name, force)
        db.set_saving(iid, 0)
        if kind == "auto":
            if ok and path:
                db.set_auto_save_path(iid, path)
            _stop_after_expiry(db, iid, username, job_id, ok, msg)
    except Exception as e:  # 兜底：绝不因保存线程异常拖垮服务
        try:
            db.set_saving(iid, 0)
            db.set_last_save(iid, "镜像保存异常：%s" % str(e)[:200])
        except Exception:
            pass
    finally:
        try:
            db.close()
        except Exception:
            pass


def _stop_after_expiry(db, iid, username, job_id, saved_ok, save_msg):
    """到期自动保存完成后停机（先尽力 scancel；作业若已被 Slurm 收走则按实际状态落库）。"""
    note = ""
    state, sl = "CANCELLED", "CANCELLED"
    try:
        res = ctl.kill(username, job_id)
        if res.get("already_ended"):
            sl = res.get("state", "UNKNOWN")
            state = sl if sl in STATE_CN else "UNKNOWN"
            note = "（作业此前已由系统结束：%s）" % STATE_CN.get(state, state)
    except ctl.CtlError as e:
        note = "；自动停机失败：%s" % str(e)[:120]
    db.set_instance_state(iid, state, slurm_state=sl, stopped_at=_now_iso())
    tail = "资源已到期并自动停机%s" % note if saved_ok else \
        "资源已到期，自动保存失败、已停机%s" % note
    db.set_last_save(iid, (save_msg + "；" if save_msg else "") + tail)
    db.set_saving(iid, 0)


def _auto_image_name(task_name, image):
    """到期自动保存的文件名：auto_<任务名(净化)>_<YYYYMMDD_HHMM>，仅 [A-Za-z0-9_]。"""
    clean = re.sub(r"[^A-Za-z0-9_]", "_", task_name or "").strip("_")
    if not clean:
        clean = re.sub(r"[^A-Za-z0-9_]", "_",
                       os.path.basename(image or "").rsplit(".", 1)[0]).strip("_")
    clean = (clean[:40]).strip("_") or "task"
    return "auto_%s_%s" % (clean, datetime.datetime.now().strftime("%Y%m%d_%H%M"))


def _inst_end(inst):
    """实例到期时刻 = 开始运行时刻(started_at) + 时长(walltime 小时数)；无法判定返回 None。"""
    st = (dict(inst).get("started_at") or "").strip().replace("T", " ")
    if not st:
        return None
    try:
        start = datetime.datetime.fromisoformat(st)
    except ValueError:
        return None
    try:
        hours = int((dict(inst).get("walltime") or "0").split(":")[0])
    except (ValueError, IndexError):
        return None
    if hours <= 0:
        return None
    return start + datetime.timedelta(hours=hours)


def expiry_tick(now=None):
    """到期自动保存扫描（每 EXPIRY_POLL_S 秒由后台线程执行；测试可直接调用）。

    命中条件：RUNNING、已记录开始时刻、超过到期时刻、尚未自动保存过、当前无保存动作。
    命中后：原子抢占(auto_saved_at) → 后台线程先导出镜像(带时间戳文件名) → 自动停机。
    """
    db = DB(DB_PATH)
    try:
        now = now or datetime.datetime.now()
        for inst in db.running_unauto_saved():
            end = _inst_end(inst)
            if end is None or now < end:
                continue
            urow = db.user_by_id(inst["user_id"])
            if not urow:
                continue
            if not db.claim_auto_save(inst["id"], _now_iso()):
                continue
            db.set_saving(inst["id"], 1)
            db.set_last_save(inst["id"], "资源已到期：开始自动保存镜像…")
            name = _auto_image_name(inst["task_name"], inst["image"])
            t = threading.Thread(target=_save_worker, daemon=True,
                                 args=(inst["id"], urow["username"], inst["job_id"],
                                       name, False, "auto"))
            t.start()
    except Exception as e:  # 到期扫描异常不影响门户主流程
        sys.stderr.write("expiry_tick error: %s\n" % e)
    finally:
        try:
            db.close()
        except Exception:
            pass


def _expiry_loop():
    while True:
        time.sleep(EXPIRY_POLL_S)
        try:
            expiry_tick()
        except Exception as e:
            sys.stderr.write("expiry loop error: %s\n" % e)


def main_bootstrap(username, password=None, role=None, force=False):
    """创建/重置门户账号（供 deploy 安装脚本调用）。

    默认管理员为 root：role 缺省时 root→admin、其它用户名→user。
    """
    from . import auth as a
    from . import pwfile
    role = role or ("admin" if username == "root" else "user")
    if password:
        okp, errp = pwfile.validate_password(password)
        if not okp:
            print("密码不合法: %s" % errp)
            return
    app = create_app()
    with app.app_context():
        db = DB(DB_PATH)
        exist = db.user_by_name(username)
        pwd = password or a.random_password()
        if exist and not force:
            print("已存在用户 %s，跳过初始化（如需重置密码请加 --force）" % username)
            return
        if exist:
            db.user_set_password(exist["id"], a.hash_password(pwd))
            print("已重置账号密码: %s" % username)
        else:
            db.add_user(username, username, role, a.hash_password(pwd), "500G",
                        "none" if role == "admin" else "provision")
            print("%s账号已创建: %s" % ("管理员" if role == "admin" else "门户", username))
        pwfile.upsert(username, pwd)  # 明文同步到密码文件
        print("初始密码: %s" % pwd)
        db.close()


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "bootstrap":
        pwd = sys.argv[3] if len(sys.argv) > 3 else None
        role = sys.argv[4] if len(sys.argv) > 4 else None
        main_bootstrap(sys.argv[2], pwd, role)
    else:
        print(__doc__)
