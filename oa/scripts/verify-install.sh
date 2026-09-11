#!/usr/bin/env bash
# verify-install.sh —— 在 admin 节点以 root 执行：门户安装自检
# 用法: bash verify-install.sh [门户端口(默认8000)]
set -eu
PORT="${1:-8000}"
fail=0
chk(){ if eval "$2" >/dev/null 2>&1; then echo "  [OK] $1"; else echo "  [FAIL] $1"; fail=1; fi; }

echo "== 服务与健康 =="
chk "systemd active"         "systemctl is-active --quiet cluster-portal"
chk "HTTP /login 200"        "curl -fsS -o /dev/null http://127.0.0.1:$PORT/login"
chk "venv 依赖"              "/opt/cluster-portal/venv/bin/python -c 'import flask,waitress'"

echo "== 权限助手与 sudoers =="
chk "portal-ctl ping"        "/usr/local/sbin/portal-ctl ping"
chk "sudoers 语法"           "visudo -c -f /etc/sudoers.d/cluster-portal"

echo "== 数据与配置 =="
chk "数据目录可写(portal)"   "runuser -u portal -- test -w /var/lib/cluster-portal"
chk "DB 存在"                "test -f /var/lib/cluster-portal/portal.db"
chk "密码文件 660"           "stat -c '%a' /etc/cluster-portal/users.passwd | grep -q '^660'"
chk "密码文件属主"           "stat -c '%U:%G' /etc/cluster-portal/users.passwd | grep -q 'root:portal'"
chk "密码文件有内容行"       "grep -qv '^#' /etc/cluster-portal/users.passwd"
chk "etc 目录 770(portal可写)" "stat -c '%a' /etc/cluster-portal | grep -q '^770'"

echo "== 集群联动 =="
chk "sinfo 可用(节点>=1)"    "bash -c 'n=\$(sinfo -h -N | wc -l); test \"\$n\" -ge 1'"
chk "镜像目录可读"           "bash -c 'ls /share/images/*.sqsh >/dev/null'"
chk "套餐>=1"                "cd /opt/cluster-portal && PORTAL_DATA=/var/lib/cluster-portal ./venv/bin/python -c 'import sys;sys.path.insert(0,\".\");from portalapp.db import DB;d=DB(\"/var/lib/cluster-portal/portal.db\");print(d.q1(\"SELECT COUNT(*) n FROM plans\")[\"n\"])' | grep -qv '^0$'"

echo "== 结果 =="
if [ "$fail" = 0 ]; then echo "全部检查通过 ✓"; else echo "存在失败项，请对照 01-部署手册 排查"; fi
exit "$fail"
