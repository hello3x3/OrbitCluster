#!/usr/bin/env bash
# cluster-portal 安装脚本 —— 在集群管理/登录节点上以 root 执行
#
# 用法:  install.sh <代码目录> [门户端口] [站点目录]
#   例:  bash install.sh /tmp/cluster-portal-src 8000 /tmp/site-3090
#
# 【站点目录】可选，内含 site.conf（及可选的 plans.json）。给了就装到
#   /etc/cluster-portal/ 下，用于适配不同集群的 ssh 端口 / GPU 型号 / 套餐种子；
#   不给则用代码内置默认值（ssh 端口 2180、RTX 3060），即旧集群行为。
#   站点目录样例见仓库 oa/sites/3090-2node/。
#
# 功能:
#   * 创建运行账号 portal 与数据目录 /var/lib/cluster-portal
#   * 把代码部署到 /opt/cluster-portal，建立 venv 并安装依赖
#   * 安装 root 助手 /usr/local/sbin/portal-ctl 与 sudoers 白名单
#   * 安装站点配置 /etc/cluster-portal/site.conf（可选）
#   * 生成 systemd 服务 cluster-portal.service（waitress，0.0.0.0:<port>）
#   * 创建默认管理员账号 root（最高管理员，密码随机，落盘 /root/.cluster-portal-admin 600）
#   * 启动并做健康检查
set -eu

SRC="${1:?用法: install.sh <代码目录> [端口] [站点目录]}"
PORT="${2:-8000}"
SITE_DIR="${3:-}"
APP_DIR=/opt/cluster-portal
DATA_DIR=/var/lib/cluster-portal
CTL=/usr/local/sbin/portal-ctl
SERVICE=cluster-portal

[ "$(id -u)" = 0 ] || { echo "必须以 root 执行"; exit 1; }
[ -d "$SRC/portalapp" ] || { echo "代码目录无效: $SRC"; exit 1; }

echo "==> [1/8] 创建运行账号与目录"
id portal >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin -d "$DATA_DIR" portal
install -d -o portal -g portal -m 750 "$DATA_DIR"
install -d -o root -g root -m 755 "$APP_DIR"

echo "==> [2/8] 部署代码到 $APP_DIR"
rsync -a --delete \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='var' --exclude='venv' \
  --exclude='.venv' --exclude='.venv-*' --exclude='.git' \
  "$SRC"/ "$APP_DIR"/
chown -R root:root "$APP_DIR"
chmod -R a+rX "$APP_DIR"          # 门户进程(portal)需能读取代码
chown -R portal:portal "$APP_DIR/portalapp" 2>/dev/null || true
chown portal:portal "$APP_DIR/var" 2>/dev/null || true

echo "==> [3/8] Python venv 与依赖"
mk_venv() {
  rm -rf "$APP_DIR/venv"
  python3 -m venv "$APP_DIR/venv"
}
if ! mk_venv 2>/dev/null; then
  echo "  安装 python3-venv（ensurepip）..."
  apt-get update -qq && apt-get install -y -qq python3-venv
  mk_venv
fi
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
"$APP_DIR/venv/bin/python" -c "import flask, waitress" && echo "  deps OK"

echo "==> [4/8] 安装 root 助手与 sudoers"
install -o root -g root -m 755 "$APP_DIR/deploy/portal-ctl" "$CTL"
"$CTL" ping >/dev/null 2>&1 || { echo "portal-ctl ping 失败"; exit 1; }
cat > /etc/sudoers.d/cluster-portal <<EOF
portal ALL=(root) NOPASSWD: $CTL
Defaults:portal !requiretty
EOF
chmod 440 /etc/sudoers.d/cluster-portal
visudo -c -f /etc/sudoers.d/cluster-portal

echo "==> [4b/8] 门户密码文件（明文密码统一存放，root 可编辑，portal 可自动回写）"
install -d -o root -g portal -m 770 /etc/cluster-portal
if [ ! -f /etc/cluster-portal/users.passwd ]; then
  cat > /etc/cluster-portal/users.passwd <<'EOF'
# 平台(门户)登录密码（全部账号的明文密码统一存放于此文件）
# 格式：每行一条 “用户名:密码”（密码非空、≤128 字符，不含冒号/首尾空格；# 开头为注释）
# 门户里发生的建号/改密/删除会自动同步本文件；OS root 直接编辑本文件也会在 ~20s 内自动生效
# 示例：
# alice:ChangeMe_2026
# root:RootPwd_2026
EOF
fi
chown root:portal /etc/cluster-portal/users.passwd
chmod 660 /etc/cluster-portal/users.passwd   # root 可编辑，portal 进程可回写
ls -l /etc/cluster-portal/users.passwd

echo "==> [4c/8] 站点配置（可选：ssh 端口 / GPU 型号 / 套餐种子）"
if [ -n "$SITE_DIR" ] && [ -d "$SITE_DIR" ]; then
  [ -f "$SITE_DIR/site.conf" ] || { echo "站点目录缺少 site.conf: $SITE_DIR"; exit 1; }
  install -o root -g root -m 644 "$SITE_DIR/site.conf" /etc/cluster-portal/site.conf
  if [ -f "$SITE_DIR/plans.json" ]; then
    install -o root -g root -m 644 "$SITE_DIR/plans.json" /etc/cluster-portal/plans.json
  fi
  echo "  已安装 /etc/cluster-portal/site.conf:"
  grep -vE '^[[:space:]]*(#|$)' /etc/cluster-portal/site.conf | sed 's/^/    /'
else
  echo "  未提供站点目录 → 使用代码内置默认值（ssh 端口 2180、RTX 3060、内置套餐）"
fi

echo "==> [5/8] systemd 单元"
cat > /etc/systemd/system/$SERVICE.service <<EOF
[Unit]
Description=OrbitCluster Cluster Portal (web)
After=network-online.target munge.service slurmctld.service
Wants=network-online.target

[Service]
Type=simple
User=portal
Group=portal
WorkingDirectory=$APP_DIR
Environment=PORTAL_DATA=$DATA_DIR
Environment=PORTAL_CTL=$CTL
Environment=PORTAL_PORT=$PORT
ExecStart=$APP_DIR/venv/bin/python $APP_DIR/run.py
Restart=always
RestartSec=3
# 注意：不要开 NoNewPrivileges —— web 需经 sudoers 白名单调 root 助手 portal-ctl
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload

echo "==> [6/8] 初始化默认管理员账号 root（最高管理员）"
ADMINFILE=/root/.cluster-portal-admin
if [ ! -s "$ADMINFILE" ]; then
  PWD="$(openssl rand -hex 10 2>/dev/null || head -c 18 /dev/urandom | tr -dc 'A-Za-z0-9' | head -c 14)"
  [ -n "${PWD:-}" ] || PWD="ChangeMe9x$(date +%s)"
  PORTAL_DATA=$DATA_DIR "$APP_DIR/venv/bin/python" "$APP_DIR/bootstrap.py" root "$PWD" admin \
    > "$ADMINFILE" 2>&1 || true
  if grep -q '初始密码' "$ADMINFILE"; then
    chmod 600 "$ADMINFILE"
    echo "  root 门户初始密码已写入 $ADMINFILE（600，仅 root 可读）"
  else
    echo "  bootstrap 输出: $(cat "$ADMINFILE")"
  fi
else
  echo "  已存在 $ADMINFILE，跳过（忘记密码可删除后重跑本脚本，或用 bootstrap.py --force）"
fi

echo "==> [7/8] 启动服务"
chown -R portal:portal "$DATA_DIR"        # secret/db 归 portal 可读写
systemctl daemon-reload
systemctl reset-failed $SERVICE || true
systemctl enable --now $SERVICE || true
systemctl restart $SERVICE
for i in $(seq 1 20); do
  if curl -fsS -o /dev/null "http://127.0.0.1:$PORT/login" 2>/dev/null; then
    break
  fi
  sleep 1
done

echo "==> [8/8] 健康检查"
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
if curl -fsS -o /dev/null "http://127.0.0.1:$PORT/login"; then
  echo "服务已就绪:"
  echo "  本机:  http://127.0.0.1:$PORT"
  echo "  内网:  http://$IP:$PORT  (或 http://$(hostname -s):$PORT)"
  echo "  状态:  systemctl status $SERVICE"
else
  echo "服务未就绪，请检查: journalctl -u $SERVICE -n 50"
  exit 1
fi
echo "完成。"
