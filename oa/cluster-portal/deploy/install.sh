#!/usr/bin/env bash
# cluster-portal 安装脚本 —— 在集群管理/登录节点上以 root 执行
#
# 用法:  install.sh <代码目录> [门户端口] [站点目录]
#   例:  bash install.sh /tmp/cluster-portal-src 80 /tmp/site-3090
#
# 【站点目录】可选，内含 site.conf（及可选的 plans.json）。给了就装到
#   /etc/cluster-portal/ 下，用于适配不同集群的 ssh 端口 / GPU 型号 / 套餐种子；
#   不给则用代码内置默认值（ssh 端口 2180、RTX 3060）。
#   站点目录可用 `make out` 生成的 provision/out/etc/cluster-portal/（含 site.conf 与 plans.json）。
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
PORT="${2:-80}"
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
# 运行期可写目录：数据目录与 var/。
# ⚠️ 应用代码**必须**归 root —— 历史实现把 portalapp/ 改成 portal:portal，
# 而 root 运行 verify-install.sh / bootstrap.py 时会 import 这些模块：
# 门户一旦被攻破，攻击者只要往 portalapp/db.py 里写代码，等管理员跑一次自检就拿到 root。
# portal 只读、不可写。
chown -R root:root "$APP_DIR/portalapp"
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

# Explicit settings are retained. Fresh/missing transport configuration defaults to lab HTTP.
if ! grep -Eq '^[[:space:]]*TERMINAL_REQUIRE_HTTPS[[:space:]]*=' /etc/cluster-portal/site.conf 2>/dev/null; then
  printf '\n# 实验室内网默认允许 HTTP；设为 1 可强制 HTTPS。\nTERMINAL_REQUIRE_HTTPS=0\n' >> /etc/cluster-portal/site.conf
  chown root:root /etc/cluster-portal/site.conf
  chmod 644 /etc/cluster-portal/site.conf
fi
echo "  终端传输：默认 HTTP/HTTPS 均可用；现有显式 HTTPS 限制与环境变量覆盖会保留。"
if [ ! -d "$APP_DIR/terminal-downloads" ]; then
  echo "  尚未附带 CLI 发布包；请将构建生成的 terminal-downloads/ 放到 $APP_DIR/terminal-downloads。"
fi

echo "==> [4d/8] 初始化会计账户（ACCOUNTS，幂等）"
# 账户/每账户 QoS 必须先在 slurmdbd 里存在，门户建号才能给用户加关联；
# 基础集群尚未就绪时这里只告警、不阻断安装（之后可手工重跑）。
if _ACCT_OUT="$("$CTL" accounts-init 2>&1)"; then
  echo "  $_ACCT_OUT"
else
  echo "  [警告] 账户初始化失败（slurmdbd 未就绪？）：$_ACCT_OUT"
  echo "         基础集群就绪后请重跑: $CTL accounts-init"
fi

echo "==> [5/8] systemd 单元"
# 80/443 等特权端口：服务以 portal 用户运行，必须显式授予绑定能力，
# 否则 waitress 在 bind 时会报 Permission denied。
BIND_CAP=""
if [ "$PORT" -lt 1024 ]; then
  BIND_CAP="AmbientCapabilities=CAP_NET_BIND_SERVICE"
fi
# PrivateDevices=yes 后沙箱里的 /dev 只剩最小集合；repquota/setquota 需要
# stat() /share 对应的块设备节点。这里按实际挂载点找设备，不能写死 /dev/sda
# （RAID/云盘/NVMe 环境下会指错，配额列会静默变成「—」）。
SHARE_DEV="$(findmnt -no SOURCE /share 2>/dev/null | head -1)"
if [ -b "$SHARE_DEV" ]; then
  SHARE_DEV="$(readlink -f "$SHARE_DEV")"
else
  SHARE_DEV=/dev/sda
fi
cat > /etc/systemd/system/$SERVICE.service <<EOF
[Unit]
Description=OrbitCluster Cluster Portal (web)
After=network-online.target munge.service slurmctld.service
Wants=network-online.target

[Service]
Type=simple
User=portal
Group=portal
$BIND_CAP
WorkingDirectory=$APP_DIR
Environment=PORTAL_DATA=$DATA_DIR
Environment=PORTAL_CTL=$CTL
Environment=PORTAL_PORT=$PORT
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=$APP_DIR/venv/bin/python $APP_DIR/run.py
Restart=always
RestartSec=3
# 注意：不要开 NoNewPrivileges —— web 需经 sudoers 白名单调 root 助手 portal-ctl
PrivateTmp=true
# ---- 沙箱加固（纵深防御）------------------------------------------------
# 门户只应写「数据目录 + 密码文件 + /share 集群数据区 + /etc 账号库」；应用代码只读。
# 这样即使门户被攻破，也无法篡改自己的代码（与 install.sh 里的属主设置互为双保险）。
# /run、/dev、/proc、/sys 以及 PrivateTmp 提供的 /tmp 仍可写，sudo 不受影响。
#
# ⚠ /share 必须列进来：ProtectSystem=strict 会把**整个层级**（含所有挂载点）挂成只读，
#   而 portal-ctl 干的活全在 /share 上 —— 建 ~/.portal/logs 与 ~/.ssh/authorized_keys、
#   userdel -r 删家目录、写 /share/images 个人镜像、quotactl 落地配额。少写这一项，
#   门户就变成"只能看不能动"：提交作业报
#   OSError: [Errno 30] Read-only file system: '/share/home/<u>/.portal/logs'
#   注意 sudo→portal-ctl 的 root 子进程**继承同一挂载命名空间**，所以这里放宽是必须的，
#   不是"反正 root 能写"。SHARE_HOME/IMAGES_ROOT 在 portal-ctl 里固定为 /share/*，
#   若将来改到别处，这里要跟着加。
#
# ⚠ /etc 必须整体可写（不能只列 /etc/cluster-portal）：门户开通普通用户时，
#   portal-ctl 以 root 调 add-user.sh → groupadd/useradd，需要创建/更新
#   /etc/group、/etc/passwd、/etc/shadow、/etc/gshadow 及其 .lock 临时文件。
#   ProtectSystem=strict 下不放开 /etc 会报
#   "groupadd: cannot lock /etc/group; try again later."（这是实测踩过的坑）。
#   portal 进程本身仍是非 root，写不了 /etc；只有 sudo 白名单里的 root 助手能写。
ProtectSystem=strict
ReadWritePaths=$DATA_DIR /etc /share
ProtectHome=read-only
PrivateDevices=yes
# ⚠ PrivateDevices=yes 会把 /share 对应的块设备从沙箱的 /dev 里摘掉，而
#   `repquota /share` 必须先 stat() 这个块设备节点，否则直接空手而归
#   （rc 还是 0，所以是**静默**失败）：用户管理页的「配额（OS 实读）」整列
#   变成「—」，库里的额度与 OS 实际值漂移就完全看不出来 —— 直到写入超过配额、
#   报 "Write failed because Disk quota exceeded" 才暴露。
#   只把这一个设备节点**只读**bind 进沙箱：repquota 只要 stat()，不需要打开设备，
#   所以不必放宽 DevicePolicy；非特权 portal 进程依旧打不开它（brw-rw---- root:disk）。
BindReadOnlyPaths=$SHARE_DEV
RestrictSUIDSGID=yes
ProtectKernelTunables=yes
ProtectControlGroups=yes
RestrictRealtime=yes
UMask=0077

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
chmod 600 "$DATA_DIR"/secret 2>/dev/null || true
chmod 600 "$DATA_DIR"/portal.db 2>/dev/null || true   # 库里有口令哈希与用户信息，不必给他人可读
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
