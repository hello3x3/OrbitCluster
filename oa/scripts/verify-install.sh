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
chk "密码文件 660(others 不可读)" "stat -c '%a' /etc/cluster-portal/users.passwd | grep -q '^660'"
# 属主：install.sh 首次建成 root:portal；门户进程(portal)自己回写后按 pwfile.py 的
# 原子写(临时文件+rename)会变成 portal:portal —— 两种都正常（该代码只在 euid==0 时才 chown）。
# 真正必须守住的是 660 与属主/属组之一为 portal。
chk "密码文件属主(root:portal 或 portal:portal)" \
    "stat -c '%U:%G' /etc/cluster-portal/users.passwd | grep -qE '^(root:portal|portal:portal)$'"
chk "密码文件有内容行"       "grep -qv '^#' /etc/cluster-portal/users.passwd"
chk "etc 目录 770(portal可写)" "stat -c '%a' /etc/cluster-portal | grep -q '^770'"

echo "== 集群联动 =="
chk "sinfo 可用(节点>=1)"    "bash -c 'n=\$(sinfo -h -N | wc -l); test \"\$n\" -ge 1'"
chk "镜像目录可读"           "bash -c 'ls /share/images/*.sqsh >/dev/null'"
chk "套餐>=1"                "cd /opt/cluster-portal && PORTAL_DATA=/var/lib/cluster-portal ./venv/bin/python -c 'import sys;sys.path.insert(0,\".\");from portalapp.db import DB;d=DB(\"/var/lib/cluster-portal/portal.db\");print(d.q1(\"SELECT COUNT(*) n FROM plans\")[\"n\"])' | grep -qv '^0$'"

echo "== 站点配置（换集群时最容易配错的地方）=="
if [ -f /etc/cluster-portal/site.conf ]; then
  echo "  [OK] site.conf 存在"
  grep -vE '^[[:space:]]*(#|$)' /etc/cluster-portal/site.conf | sed 's/^/       /'
  SITE_PORT="$(sed -n 's/^SSH_PORT=//p' /etc/cluster-portal/site.conf | tr -d ' \r' | head -1)"
  if [ -n "$SITE_PORT" ]; then
    if ss -lnt 2>/dev/null | grep -q ":${SITE_PORT} "; then
      echo "  [OK] SSH_PORT=$SITE_PORT 与本机 sshd 实际监听一致"
    else
      echo "  [FAIL] SSH_PORT=$SITE_PORT，但本机没有监听该端口（门户将无法操作计算节点）"; fail=1
    fi
  fi
  if /usr/local/sbin/portal-ctl sinfo 2>/dev/null | grep -q '"ip": "127\.'; then
    echo "  [FAIL] 有节点 IP 解析成了回环地址（检查 /etc/hosts 的 127.0.1.1 行）"; fail=1
  else
    echo "  [OK] 各节点 IP 均解析为真实地址"
  fi
else
  echo "  [i] 未安装 /etc/cluster-portal/site.conf → 使用代码内置默认值（ssh 端口 2180、RTX 3060）"
fi

echo "== 结果 =="
if [ "$fail" = 0 ]; then echo "全部检查通过 ✓"; else echo "存在失败项，请对照 01-部署手册 排查"; fi
exit "$fail"
