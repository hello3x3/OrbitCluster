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
# 注意：这里**不能**用 root 去 import 应用代码（portalapp/*.py）。历史上 verify-install.sh
# 以 root 导入 portalapp.db，而该目录当时归 portal 所有 —— 门户被攻破即可借此拿 root。
# 现在改为直接用标准库 sqlite3 读库，不加载应用代码。
chk "套餐>=1"                "python3 -c 'import sqlite3;c=sqlite3.connect(\"/var/lib/cluster-portal/portal.db\");print(c.execute(\"SELECT COUNT(*) FROM plans\").fetchone()[0])' | grep -qv '^0$'"
# 「用户管理」页的配额列要在**服务的沙箱里**读得到 /share 配额。
# PrivateDevices=yes 会把 /dev/sda 从沙箱的 /dev 里摘掉，而 repquota 必须先 stat() 这个设备节点；
# 少了 BindReadOnlyPaths=/dev/sda，配额整列会变成「—」，额度漂移（库里 500G / OS 实际 5G）
# 肉眼完全看不出来 —— 线上真实事故：某用户 5G 配额去存 ~10G 镜像，报 quota exceeded 才发现。
SVC_PID="$(systemctl show cluster-portal -p MainPID --value 2>/dev/null || true)"
if [ -n "$SVC_PID" ] && [ -d "/proc/$SVC_PID" ]; then
  _q_n="$(nsenter -t "$SVC_PID" -m -- runuser -u portal -- sudo -n /usr/local/sbin/portal-ctl quota-all 2>/dev/null \
          | python3 -c 'import json,sys; print(len(json.load(sys.stdin).get("quotas") or {}))' 2>/dev/null || echo 0)"
  if [ "${_q_n:-0}" -gt 0 ]; then
    echo "  [OK] 门户能读到 /share 配额（$_q_n 条）"
  else
    echo "  [FAIL] 门户在沙箱里读不到 /share 配额 —— 用户管理页的配额列会整列显示「—」"
    echo "         修法：systemd 单元加 BindReadOnlyPaths=/dev/sda（PrivateDevices=yes 摘掉了设备节点）"
    fail=1
  fi
else
  echo "  [i] 服务未运行，跳过「配额可读」检查"
fi


echo "== 安全边界（门户用户不得登录宿主机 / 不得篡改门户代码）=="
chk "应用代码不属于 portal"  "bash -c 'st=\$(stat -c %U /opt/cluster-portal/portalapp); test \"\$st\" = root'"
chk "portal 不能写应用代码"  "! runuser -u portal -- test -w /opt/cluster-portal/portalapp"
# 沙箱的反面：能给 portal-ctl 留下写 /share 的口子吗？
# ProtectSystem=strict 会把整个层级（含 /share 这个挂载点）挂成只读，只有 ReadWritePaths
# 里列了的目录才可写；漏了 /share，门户就"只能看不能动"——提交作业 / 保存镜像 / 登记公钥 /
# 开通用户 / 注销用户全部报 EROFS（线上真实事故）。sudo→portal-ctl 的 root 子进程继承
# 同一挂载命名空间，所以必须在**服务的命名空间里**试写，在宿主机上试写是测不出来的。
SVC_PID="$(systemctl show cluster-portal -p MainPID --value 2>/dev/null || true)"
if [ -n "$SVC_PID" ] && [ -d "/proc/$SVC_PID" ]; then
  if nsenter -t "$SVC_PID" -m -- bash -c 'touch /share/.verify-share-wtest 2>/dev/null && rm -f /share/.verify-share-wtest' 2>/dev/null; then
    echo "  [OK] 门户沙箱内 /share 可写（ReadWritePaths 没漏掉 /share）"
  else
    echo "  [FAIL] 门户沙箱内 /share 是只读的 —— portal-ctl 写任何用户数据都会失败"
    echo "         表现：提交资源报 OSError: [Errno 30] Read-only file system: '/share/home/<用户>/.portal/logs'"
    echo "         修法：systemd 单元里 ReadWritePaths=... 补上 /share，再 daemon-reload + restart cluster-portal"
    fail=1
  fi
else
  echo "  [i] 服务未运行，跳过「沙箱内 /share 可写」检查"
fi
ALLOW="$(sshd -T 2>/dev/null | awk '/^allowusers /{print}')"
if [ -n "$ALLOW" ]; then
  echo "  [OK] sshd 白名单生效：$ALLOW"
  case "$ALLOW" in
    *root*) : ;;
    *) echo "  [FAIL] 白名单里没有 root，管理员将无法登录"; fail=1 ;;
  esac
else
  echo "  [FAIL] sshd 未配置 AllowUsers —— 门户用户可拿自己的容器密钥直接 ssh 登录宿主机！"
  echo "         修法见 base-cluster 手册 1.7：/etc/ssh/sshd_config.d/10-portal-only.conf"
  fail=1
fi
if [ "$(sshd -T 2>/dev/null | awk '/^permitrootlogin /{print $2}')" = "yes" ]; then
  echo "  [i] PermitRootLogin=yes（root 仍可用口令登录）；建议改为 prohibit-password（见手册 1.7）"
fi

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

echo "== 集群账号一致性 =="
# 重复 UID 是本项目踩过的真实故障根因：
# enroot 的 /etc/enroot/hooks.d/10-shadow.sh 会执行 `getent passwd <uid>` 取**一条**
# 记录写进容器 /etc/passwd；UID 重复时取到的是先注册的那个账号名，容器里就没有
# 真实用户名条目 → sshd 解析不到 → 用户 ssh 进容器报 Permission denied (publickey)。
DUP_UID="$(awk -F: '$3>=1000 && $3<60000{print $3}' /etc/passwd | sort -n | uniq -d | tr '\n' ' ')"
if [ -z "$DUP_UID" ]; then
  echo "  [OK] 无重复 UID"
else
  echo "  [FAIL] 存在重复 UID：$DUP_UID"
  for u in $DUP_UID; do grep -E "^[^:]*:[^:]*:$u:" /etc/passwd | sed 's/^/         /'; done
  echo "         容器内会解析不到真实用户名（ssh 报 Permission denied (publickey)）"
  echo "         修正：保留真正的用户，删掉多余账号 userdel <多余账号>"
  fail=1
fi
DUP_GID="$(awk -F: '$3>=1000 && $3<60000{print $3}' /etc/group | sort -n | uniq -d | tr '\n' ' ')"
if [ -z "$DUP_GID" ]; then
  echo "  [OK] 无重复 GID"
else
  echo "  [FAIL] 存在重复 GID：$DUP_GID"; fail=1
fi

# 跨节点比对：只针对**门户里登记过的**用户（os_mode=provision/existing），
# 避免把纯本机账号（如安装系统时的 zju）拿来误判。
# 用 ssh 的退出码区分"节点不可达"与"用户不存在/号码不一致"——后者才是要抓的漂移。
SSH_P="$(sed -n 's/^SSH_PORT=//p' /etc/cluster-portal/site.conf 2>/dev/null | tr -d ' \r' | head -1)"
SSH_P="${SSH_P:-2180}"   # 与 siteconf.py 的内置默认值保持一致（曾误写成 2022，会静默跳过跨节点比对）
NODES="$(/usr/local/sbin/portal-ctl sinfo 2>/dev/null | grep -o '"name": "[^"]*"' | cut -d'"' -f4)"
PORTAL_USERS="$(cd /opt/cluster-portal && PORTAL_DATA=/var/lib/cluster-portal ./venv/bin/python -c 'import sqlite3;c=sqlite3.connect("/var/lib/cluster-portal/portal.db");print("\n".join(r[0] for r in c.execute("SELECT username, os_mode FROM users") if r[1] in ("provision","existing")))' 2>/dev/null)"
if [ -z "$NODES" ]; then
  echo "  [i] 取不到节点列表，跳过跨节点 UID/GID 比对"
elif [ -z "$PORTAL_USERS" ]; then
  echo "  [i] 门户里暂无 OS 账号用户，跳过跨节点比对"
else
  DRIFT=""; UNREACH=""
  mark_unreach() { case " $UNREACH " in *" $1 "*) ;; *) UNREACH="$UNREACH $1" ;; esac; }
  for u in $PORTAL_USERS; do
    LU="$(id -u "$u" 2>/dev/null)"; LG="$(id -g "$u" 2>/dev/null)"
    if [ -z "$LU" ]; then
      echo "  [FAIL] 门户用户 $u 在本机 $(hostname -s) 没有同名 OS 账号"; fail=1
      continue
    fi
    for n in $NODES; do
      [ "$n" = "$(hostname -s)" ] && continue
      RR="$(ssh -n -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
            -o ConnectTimeout=5 -o LogLevel=ERROR -p "$SSH_P" "$n" \
            "printf '%s:%s' \"\$(id -u $u 2>/dev/null)\" \"\$(id -g $u 2>/dev/null)\"" 2>/dev/null)"
      RC=$?
      if [ "$RC" -ne 0 ]; then mark_unreach "$n"; continue; fi
      if [ "$RR" != "$LU:$LG" ]; then
        DRIFT="$DRIFT $u@$n(本机 $LU:$LG vs 远端 ${RR:-无该账号})"
      fi
    done
  done
  if [ -n "$UNREACH" ]; then
    echo "  [i] 以下节点 ssh 不可达，未比对：$UNREACH"
  fi
  if [ -z "$DRIFT" ]; then
    echo "  [OK] 门户用户在计算节点上的 UID/GID 与管理节点一致"
  else
    echo "  [FAIL] UID/GID 漂移（或计算节点缺号）：$DRIFT"
    echo "         修正：add-user.sh <用户> -u <主节点UID> -g <主节点GID>（已存在会提示不符）"
    fail=1
  fi
fi

echo "== 容器运行时（决定容器是否 root：root 模式下镜像内置固定口令会暴露）=="
REMAP_BAD=""
for n in $(/usr/local/sbin/portal-ctl sinfo 2>/dev/null | grep -o '"name": "[^"]*"' | cut -d'"' -f4); do
  if [ "$n" = "$(hostname -s)" ]; then
    V="$(grep -E '^[[:space:]]*ENROOT_REMAP_ROOT' /etc/enroot/enroot.conf 2>/dev/null | awk '{print $2}')"
  else
    V="$(ssh -n -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
         -o ConnectTimeout=5 -o LogLevel=ERROR -p "$SSH_P" "$n" \
         "grep -E '^[[:space:]]*ENROOT_REMAP_ROOT' /etc/enroot/enroot.conf | awk '{print \$2}'" 2>/dev/null)"
  fi
  case "$V" in
    n|no) echo "  [OK] $n ENROOT_REMAP_ROOT=$V（容器以提交者身份运行）" ;;
    "")   echo "  [i] $n 读不到 enroot.conf，跳过" ;;
    *)    echo "  [FAIL] $n ENROOT_REMAP_ROOT=$V —— 容器会以 root 运行，镜像内置口令 sshd 会被暴露"
          echo "         修法：改回 n（base-cluster 手册 5.2），并确认提交参数没有 --container-remap-root"
          REMAP_BAD=1 ;;
  esac
done
[ -n "$REMAP_BAD" ] && fail=1

echo "== 结果 =="
if [ "$fail" = 0 ]; then echo "全部检查通过 ✓"; else echo "存在失败项，请对照 01-部署手册 排查"; fi
exit "$fail"
