# -*- coding: utf-8 -*-
"""portal DB layer (sqlite3, stdlib). 每次请求一个连接（thread-local），teardown 提交关闭。"""
import json
import os
import sqlite3
import threading

from . import siteconf

DEFAULT_COMMON_PORTS = [
    # 常用/高危端口（web 与集群服务等），端口申请必须避开
    21, 22, 23, 25, 53, 80, 110, 111, 123, 143, 443, 465, 587, 873, 993, 995,
    2049, 2180, 3306, 5432, 5672, 6379, 6817, 6818, 6819, 8080, 8085, 8086,
    8443, 8888, 9000, 9090, 9100, 9200, 9835, 10000, 10050, 11211, 15672,
    27017, 30000, 50000,
]
# 本站点的集群 sshd 端口也必须保留，否则用户可能申请到别人登不进来的端口
try:
    _ssh_port = int(siteconf.SSH_PORT)
    if _ssh_port not in DEFAULT_COMMON_PORTS:
        DEFAULT_COMMON_PORTS.append(_ssh_port)
except (TypeError, ValueError):
    pass
PORT_MIN = 10000
PORT_MAX = 65535

ACTIVE_STATES = ("PENDING", "RUNNING", "SUSPENDED", "COMPLETING", "STOPPING")

STATE_CN = {
    "PENDING": "排队中", "RUNNING": "运行中", "SUSPENDED": "已挂起",
    "COMPLETING": "收尾中", "STOPPING": "停止中",
    "COMPLETED": "已结束", "CANCELLED": "已停止", "FAILED": "失败",
    "TIMEOUT": "超时结束", "OUT_OF_MEMORY": "内存溢出", "NODE_FAIL": "节点故障",
    "PREEMPTED": "被抢占", "REQUEUED": "重新排队", "UNKNOWN": "未知",
}


class DB:
    def __init__(self, path):
        self.path = path
        self._local = threading.local()

    # ---- connection ----
    def conn(self):
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=30000")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA foreign_keys=ON")
            self._local.conn = c
        return c

    def close(self):
        c = getattr(self._local, "conn", None)
        if c is not None:
            try:
                c.commit()
            except sqlite3.Error:
                pass
            try:
                c.close()
            except sqlite3.Error:
                pass
            self._local.conn = None

    # ---- query helpers ----
    def q(self, sql, args=()):
        return self.conn().execute(sql, args).fetchall()

    def q1(self, sql, args=()):
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def exec(self, sql, args=()):
        cur = self.conn().execute(sql, args)
        self.conn().commit()
        return cur

    # ---- settings ----
    def get_setting(self, key, default=None):
        row = self.q1("SELECT value FROM settings WHERE key=?", (key,))
        if row is None:
            return default
        return json.loads(row["value"])

    def set_setting(self, key, value):
        self.exec("INSERT INTO settings(key,value) VALUES(?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, json.dumps(value, ensure_ascii=False)))

    def common_ports(self):
        return list(self.get_setting("common_ports", DEFAULT_COMMON_PORTS))

    # ---- user ----
    def user_by_name(self, username):
        return self.q1("SELECT * FROM users WHERE username=?", (username,))

    def user_by_id(self, uid):
        return self.q1("SELECT * FROM users WHERE id=?", (uid,))

    def users_all(self):
        return self.q("SELECT * FROM users ORDER BY role DESC, id ASC")

    def add_user(self, username, display_name, role, passwd_hash, quota, os_mode):
        cur = self.exec(
            "INSERT INTO users(username, display_name, role, passwd, quota, os_mode, is_active, created_at)"
            " VALUES(?,?,?,?,?,?,1,?)",
            (username, display_name, role, passwd_hash, quota, os_mode, _now()))
        return cur.lastrowid

    def user_set_active(self, uid, active):
        self.exec("UPDATE users SET is_active=? WHERE id=?", (1 if active else 0, uid))

    def user_set_password(self, uid, passwd_hash):
        self.exec("UPDATE users SET passwd=? WHERE id=?", (passwd_hash, uid))

    def user_touch_login(self, uid):
        import datetime
        self.exec("UPDATE users SET last_login=? WHERE id=?",
                  (datetime.datetime.now().isoformat(timespec="seconds"), uid))

    def user_set_display(self, uid, display_name):
        self.exec("UPDATE users SET display_name=? WHERE id=?", (display_name, uid))

    # ---- keys ----
    def keys_for(self, uid):
        return self.q("SELECT * FROM ssh_keys WHERE user_id=? ORDER BY id", (uid,))

    def add_key(self, uid, label, pubkey):
        cur = self.exec(
            "INSERT INTO ssh_keys(user_id, label, pubkey, added_at) VALUES(?,?,?,?)",
            (uid, label, pubkey, _now()))
        return cur.lastrowid

    def del_key(self, uid, kid):
        self.exec("DELETE FROM ssh_keys WHERE id=? AND user_id=?", (kid, uid))

    def key_count(self, uid):
        return self.q1("SELECT COUNT(*) n FROM ssh_keys WHERE user_id=?", (uid,))["n"]

    # ---- ports ----
    def ports_for(self, uid):
        return self.q("SELECT * FROM user_ports WHERE user_id=? ORDER BY port", (uid,))

    def port_by_number(self, port):
        return self.q1("SELECT * FROM user_ports WHERE port=?", (port,))

    def add_port(self, uid, port, label=""):
        cur = self.exec("INSERT INTO user_ports(user_id, port, label, claimed_at) VALUES(?,?,?,?)",
                        (uid, port, label, _now()))
        return cur.lastrowid

    def del_port(self, uid, pid):
        self.exec("DELETE FROM user_ports WHERE id=? AND user_id=?", (pid, uid))

    def port_count(self, uid):
        return self.q1("SELECT COUNT(*) n FROM user_ports WHERE user_id=?", (uid,))["n"]

    def is_port_in_use(self, port, exclude_uid=None):
        if exclude_uid is None:
            return self.q1("SELECT 1 FROM user_ports WHERE port=?", (port,)) is not None
        return self.q1("SELECT 1 FROM user_ports WHERE port=? AND user_id<>?",
                       (port, exclude_uid)) is not None

    # ---- plans（资源套餐：CPU/内存/GPU 预设）----
    def plans_enabled(self):
        return self.q("SELECT * FROM plans WHERE enabled=1 ORDER BY id")

    def plans_all(self):
        return self.q("SELECT * FROM plans ORDER BY id")

    def plan_by_id(self, pid):
        return self.q1("SELECT * FROM plans WHERE id=?", (pid,))

    def add_plan(self, name, description, gpus, gpu_model, cpus, mem_gb, maxtime_h):
        cur = self.exec(
            "INSERT INTO plans(name,description,gpus,gpu_model,cpus,mem_gb,maxtime_h,"
            "enabled,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,1,?,?)",
            (name, description, gpus, gpu_model, cpus, mem_gb, maxtime_h, _now(), _now()))
        return cur.lastrowid

    def update_plan(self, pid, **kw):
        cols = {"name", "description", "gpus", "gpu_model", "cpus", "mem_gb",
                "maxtime_h", "enabled"}
        sets = []
        args = []
        for k, v in kw.items():
            if k in cols:
                sets.append("%s=?" % k)
                args.append(v)
        if sets:
            args.append(pid)
            args.append(_now())
            self.exec("UPDATE plans SET %s, updated_at=? WHERE id=?" % ",".join(sets), args)

    def plan_instances(self, pid):
        return self.q1("SELECT COUNT(*) n FROM instances WHERE plan_id=?", (pid,))["n"]

    def del_plan(self, pid):
        self.exec("DELETE FROM plans WHERE id=?", (pid,))

    # ---- instances ----
    def add_instance(self, uid, plan_id, task_name, node, gpus, cpus, mem_gb, port,
                     walltime, image, job_id, state, cmd, log_path, ssh_ip):
        cur = self.exec(
            "INSERT INTO instances(user_id,plan_id,task_name,node,req_node,gpus,cpus,mem_gb,port,"
            "walltime,image,job_id,state,cmd,log_path,ssh_ip,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (uid, plan_id, task_name, node, node, gpus, cpus, mem_gb, port, walltime, image,
             job_id, state, cmd, log_path, ssh_ip,
             _now(), _now()))
        return cur.lastrowid

    def instance_by_id(self, iid):
        return self.q1("SELECT * FROM instances WHERE id=?", (iid,))

    def instances_for(self, uid):
        return self.q("SELECT * FROM instances WHERE user_id=? ORDER BY id DESC", (uid,))

    def instances_all_active(self):
        return self.q("SELECT * FROM instances WHERE state IN (%s) ORDER BY id DESC"
                      % ",".join("?" * len(ACTIVE_STATES)), ACTIVE_STATES)

    def instances_all(self, limit=200):
        return self.q("SELECT * FROM instances ORDER BY id DESC LIMIT ?", (limit,))

    def set_instance_state(self, iid, state, slurm_state=None, stopped_at=None):
        if slurm_state is not None:
            self.exec("UPDATE instances SET state=?, slurm_state=?, updated_at=? WHERE id=?",
                      (state, slurm_state, _now(), iid))
        elif stopped_at is not None:
            self.exec("UPDATE instances SET state=?, slurm_state=?, updated_at=?, "
                      "stopped_at=? WHERE id=?",
                      (state, slurm_state or state, _now(), stopped_at, iid))
        else:
            self.exec("UPDATE instances SET state=?, updated_at=? WHERE id=?",
                      (state, _now(), iid))

    def set_instance_meta(self, iid, **kw):
        cols = {"job_id", "node", "ssh_ip", "log_path", "cmd", "slurm_state", "last_error"}
        sets = []
        args = []
        for k, v in kw.items():
            if k in cols:
                sets.append("%s=?" % k)
                args.append(v)
        if sets:
            args.append(_now())
            args.append(iid)
            self.exec("UPDATE instances SET %s, updated_at=? WHERE id=?" % ",".join(sets),
                      args)

    def active_instance_with_port_node(self, port, node, exclude_iid=None):
        if exclude_iid is None:
            return self.q1("SELECT * FROM instances WHERE port=? AND node=? AND state IN (%s)"
                           % ",".join("?" * len(ACTIVE_STATES)),
                           (port, node) + ACTIVE_STATES)
        return self.q1("SELECT * FROM instances WHERE port=? AND node=? AND id<>? "
                       "AND state IN (%s)" % ",".join("?" * len(ACTIVE_STATES)),
                       (port, node, exclude_iid) + ACTIVE_STATES)

    def user_active_by_port(self, uid, port):
        """该用户当前（任意节点）是否已有使用此端口的活跃实例。"""
        return self.q1("SELECT 1 FROM instances WHERE user_id=? AND port=? AND state IN (%s)"
                       % ",".join("?" * len(ACTIVE_STATES)),
                       (uid, port) + ACTIVE_STATES) is not None

    def mark_started(self, iid, ts):
        """作业首次进入 RUNNING 时记录开始时刻（用于到期自动保存的截止计算）。"""
        self.exec("UPDATE instances SET started_at=?, updated_at=? "
                  "WHERE id=? AND (started_at IS NULL OR started_at='')",
                  (ts, _now(), iid))

    def set_saving(self, iid, val):
        self.exec("UPDATE instances SET saving=?, updated_at=? WHERE id=?",
                  (1 if val else 0, _now(), iid))

    def set_last_save(self, iid, msg):
        self.exec("UPDATE instances SET last_save=?, updated_at=? WHERE id=?",
                  (str(msg)[:500], _now(), iid))

    def claim_auto_save(self, iid, ts):
        """原子抢占到期自动保存（只成功一次）；返回是否抢到。"""
        cur = self.exec("UPDATE instances SET auto_saved_at=? WHERE id=? "
                        "AND auto_saved_at IS NULL", (ts, iid))
        return cur.rowcount > 0

    def set_auto_save_path(self, iid, path):
        self.exec("UPDATE instances SET auto_saved_path=?, updated_at=? WHERE id=?",
                  (path, _now(), iid))

    def running_unauto_saved(self):
        """RUNNING、已记录开始时刻、尚未做过到期自动保存、且当前无保存动作的实例。"""
        return self.q("SELECT * FROM instances WHERE state='RUNNING' AND saving=0 "
                      "AND auto_saved_at IS NULL "
                      "AND (started_at IS NOT NULL AND started_at<>'')")

    def del_instance(self, iid, uid=None):
        """删除一条实例记录（uid 传则限本人）。"""
        if uid is not None:
            return self.exec("DELETE FROM instances WHERE id=? AND user_id=?", (iid, uid))
        return self.exec("DELETE FROM instances WHERE id=?", (iid,))

    def user_quota(self, uid):
        row = self.q1("SELECT quota FROM users WHERE id=?", (uid,))
        return row["quota"] if row else "500G"


def _now():
    import datetime
    return datetime.datetime.now().isoformat(timespec="seconds")


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('admin','user')),
  passwd TEXT NOT NULL,
  quota TEXT NOT NULL DEFAULT '500G',
  os_mode TEXT NOT NULL DEFAULT 'provision',
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  last_login TEXT
);
CREATE TABLE IF NOT EXISTS ssh_keys(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  label TEXT NOT NULL DEFAULT '',
  pubkey TEXT NOT NULL,
  added_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_keys ON ssh_keys(user_id, pubkey);
CREATE TABLE IF NOT EXISTS user_ports(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  port INTEGER NOT NULL,
  label TEXT NOT NULL DEFAULT '',
  claimed_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_port ON user_ports(port);
CREATE TABLE IF NOT EXISTS instances(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  plan_id INTEGER REFERENCES plans(id) ON DELETE SET NULL,
  task_name TEXT,
  node TEXT NOT NULL DEFAULT '',
  req_node TEXT NOT NULL DEFAULT '',
  gpus INTEGER NOT NULL DEFAULT 1,
  cpus INTEGER NOT NULL DEFAULT 4,
  mem_gb INTEGER NOT NULL DEFAULT 8,
  port INTEGER NOT NULL,
  walltime TEXT NOT NULL DEFAULT '24:00:00',
  image TEXT NOT NULL,
  job_id INTEGER,
  state TEXT NOT NULL DEFAULT 'PENDING',
  slurm_state TEXT,
  cmd TEXT,
  log_path TEXT,
  ssh_ip TEXT NOT NULL DEFAULT '',
  last_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  stopped_at TEXT,
  started_at TEXT,
  saving INTEGER NOT NULL DEFAULT 0,
  last_save TEXT NOT NULL DEFAULT '',
  auto_saved_at TEXT,
  auto_saved_path TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS plans(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  gpus INTEGER NOT NULL DEFAULT 0,
  gpu_model TEXT NOT NULL DEFAULT '',
  cpus INTEGER NOT NULL DEFAULT 4,
  mem_gb INTEGER NOT NULL DEFAULT 8,
  maxtime_h INTEGER NOT NULL DEFAULT 48,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_inst_node_port
  ON instances(node, port) WHERE state IN ('PENDING','RUNNING','SUSPENDED','COMPLETING','STOPPING');
"""


def init_db(db):
    db.conn().executescript(SCHEMA)
    db.conn().commit()
    _migrate(db)
    if db.get_setting("common_ports") is None:
        db.set_setting("common_ports", DEFAULT_COMMON_PORTS)
    if db.get_setting("portal_name") is None:
        db.set_setting("portal_name", "OrbitCluster 集群门户")
    if db.q1("SELECT COUNT(*) n FROM plans")["n"] == 0:
        _seed_plans(db)


def _migrate(db):
    """为已存在的库补加新列（sqlite ALTER TABLE ADD COLUMN）。"""
    icols = {r[1] for r in db.q("PRAGMA table_info(instances)")}
    if "plan_id" not in icols:
        db.exec("ALTER TABLE instances ADD COLUMN plan_id INTEGER")
    if "task_name" not in icols:
        db.exec("ALTER TABLE instances ADD COLUMN task_name TEXT")
    if "req_node" not in icols:
        db.exec("ALTER TABLE instances ADD COLUMN req_node TEXT NOT NULL DEFAULT ''")
        # 老库没有 req_node：用当前 node 作为申请意图（自动调度运行后可能已是实际节点，
        # 无法追溯，只能近似；新提交都会写 req_node）
        db.exec("UPDATE instances SET req_node=node WHERE req_node=''")
    for col, ddl in (("started_at", "ALTER TABLE instances ADD COLUMN started_at TEXT"),
                     ("saving", "ALTER TABLE instances ADD COLUMN saving INTEGER NOT NULL DEFAULT 0"),
                     ("last_save", "ALTER TABLE instances ADD COLUMN last_save TEXT NOT NULL DEFAULT ''"),
                     ("auto_saved_at", "ALTER TABLE instances ADD COLUMN auto_saved_at TEXT"),
                     ("auto_saved_path", "ALTER TABLE instances ADD COLUMN auto_saved_path TEXT NOT NULL DEFAULT ''")):
        if col not in icols:
            db.exec(ddl)
    pcols = {r[1] for r in db.q("PRAGMA table_info(plans)")}
    if "gpu_model" not in pcols:
        db.exec("ALTER TABLE plans ADD COLUMN gpu_model TEXT NOT NULL DEFAULT ''")
    if "maxtime_h" not in pcols:
        db.exec("ALTER TABLE plans ADD COLUMN maxtime_h INTEGER NOT NULL DEFAULT 48")
    # 已有 GPU 套餐补默认型号（站点默认型号，见 portalapp/siteconf.py）
    db.exec("UPDATE plans SET gpu_model=? WHERE gpus=1 AND gpu_model=?",
            (siteconf.DEFAULT_GPU_MODEL, ""))


def _load_seed_plans():
    """首次建库用的套餐定义。

    站点配置 SEED_PLANS 指向 JSON 文件时用该文件（便于按机型定制）；
    否则用与机型无关的通用内置套餐，GPU 型号取站点默认值。
    """
    if siteconf.SEED_PLANS:
        try:
            with open(siteconf.SEED_PLANS, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            seeds = [(str(p["name"]), str(p.get("desc", "")), int(p.get("gpus", 0)),
                      str(p.get("gpu_model", "") or ""), int(p["cpus"]),
                      int(p["mem_gb"]), int(p.get("maxtime_h", 48))) for p in data]
            if seeds:
                return seeds
        except (OSError, ValueError, KeyError, TypeError):
            pass
    g = siteconf.DEFAULT_GPU_MODEL
    return [
        ("基础 CPU", "纯 CPU 小任务（无 GPU），适合调试/轻量任务。", 0, "", 2, 4, 48),
        ("均衡 CPU", "纯 CPU（无 GPU），适合编译/中等任务。", 0, "", 4, 8, 48),
        ("GPU 入门", "单卡 %s + 4 核 8G，训练/推理入门配置。" % g, 1, g, 4, 8, 48),
        ("GPU 标准", "单卡 %s + 8 核 16G，日常训练推荐。" % g, 1, g, 8, 16, 48),
        ("GPU 高配", "单卡 %s + 16 核 24G，重负载训练。" % g, 1, g, 16, 24, 48),
    ]


def _seed_plans(db):
    for name, desc, gpus, model, cpus, mem, mx in _load_seed_plans():
        db.add_plan(name, desc, gpus, model, cpus, mem, mx)
