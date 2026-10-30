#!/usr/bin/env bash
# add-user.sh —— 建集群账号（管理节点：建家目录 + 配额 + sacctmgr 账户关联；计算节点：只建账号）
#
# 用法:
#   [管理节点] add-user.sh <用户名> [配额，默认 500G] [-u UID] [-g GID]
#              [--account <账户>]... [--default-account <账户>] [--accounts a,b]
#   [计算节点] add-user.sh <用户名> [-u UID] [-g GID]
#              （-u/-g 用于把计算节点的 UID/GID 强制对齐到管理节点；
#                两个都要传，只传 -u 时 GID 会在各节点自行取号而漂移）
#
# 多账户（契约 §3.3）:
#   --account 可重复（等价写法 --accounts a,b），环境变量 CLUSTER_ACCOUNTS（逗号分隔）；
#   --default-account 指定用户默认账户，环境变量 CLUSTER_DEFAULT_ACCOUNT；
#   旧环境变量 CLUSTER_ACCOUNT 仍可用（作为唯一账户）。
#   管理节点会为**每个账户**建关联 `sacctmgr add user <u> account=<a> qos=acct-<a>
#   defaultqos=acct-<a>`，再用 modify user set DefaultAccount=<默认账户> 设置默认账户；
#   任何一步失败都会明确报错并以非 0 退出（门户据此判定开通失败）。计算节点分支不碰 Slurm。
#   ⚠️ 账户与 acct-* QoS 由门户/`portal-ctl accounts-init` 维护，本脚本只建关联。
#   ⚠️ portal-ctl 调用本脚本时会**显式**传 --accounts/--default-account（不再依赖默认 lab）。
#
# 站点无关：本机是"管理节点"还是"计算节点"由 /share 是否为本地文件系统自动判定，
# 不写死主机名。因此同一份脚本可原样用于任意命名的集群（管理节点叫什么都行）。
#
# 安全约束：**绝不建重复 UID/GID**（故不再使用 useradd 的 -o）。
#   原因见下方 check_uid_free() 注释：重复 UID 会让 enroot 容器里解析不到真实用户名，
#   用户 ssh 进容器时报 "Permission denied (publickey)"。
set -eu

usage() {
  awk 'NR > 1 { if ($0 ~ /^set -eu/) exit; sub(/^# ?/, ""); print }' "$0"
  exit "${1:-0}"
}

trim() { local s="${1:-}"; s="${s#"${s%%[![:space:]]*}"}"; printf '%s' "${s%"${s##*[![:space:]]}"}"; }

USERNAME=""; QUOTA="500G"; FORCE_UID=""; FORCE_GID=""
ACCOUNT_NAME_RE='^[a-z][a-z0-9_-]{0,31}$'
declare -a ACCOUNTS=()
DEFAULT_ACCOUNT=""
ACCOUNTS_SET=0        # 是否**显式**给过账户参数（显式空列表 = 不建关联）
DEFAULT_SET=0

_add_account() {
  local a="$1" x
  [ -z "$a" ] && return 0
  for x in "${ACCOUNTS[@]}"; do
    [ "$x" = "$a" ] && return 0
  done
  ACCOUNTS+=("$a")
}

_split_accounts() {
  local raw="$1" a
  local -a parts=()
  IFS=',' read -r -a parts <<< "$raw"
  for a in "${parts[@]}"; do _add_account "$(trim "$a")"; done
}

while [ $# -gt 0 ]; do
  case "$1" in
    -u) [ $# -ge 2 ] || usage 1; FORCE_UID="$2"; shift 2 ;;
    -g) [ $# -ge 2 ] || usage 1; FORCE_GID="$2"; shift 2 ;;
    --account)
      [ $# -ge 2 ] || { echo "[!] --account 需要一个账户名" >&2; exit 1; }
      ACCOUNTS_SET=1; _add_account "$(trim "$2")"; shift 2 ;;
    --account=*)
      ACCOUNTS_SET=1; _add_account "$(trim "${1#*=}")"; shift ;;
    --accounts)
      [ $# -ge 2 ] || { echo "[!] --accounts 需要逗号分隔的账户列表" >&2; exit 1; }
      ACCOUNTS_SET=1; _split_accounts "$2"; shift 2 ;;
    --accounts=*)
      ACCOUNTS_SET=1; _split_accounts "${1#*=}"; shift ;;
    --default-account)
      [ $# -ge 2 ] || { echo "[!] --default-account 需要一个账户名" >&2; exit 1; }
      DEFAULT_SET=1; DEFAULT_ACCOUNT="$(trim "$2")"; shift 2 ;;
    --default-account=*)
      DEFAULT_SET=1; DEFAULT_ACCOUNT="$(trim "${1#*=}")"; shift ;;
    -h|--help) usage 0 ;;
    -*) echo "[!] 未知参数: $1" >&2; usage 1 ;;
    *) if [ -z "$USERNAME" ]; then USERNAME="$1"; shift; else QUOTA="$1"; shift; fi ;;
  esac
done
[ -n "$USERNAME" ] || usage 1

# ---- 账户解析（显式参数 > 环境变量 > 旧默认 lab）---------------------------
if [ "$ACCOUNTS_SET" = 0 ] && [ -n "${CLUSTER_ACCOUNTS:-}" ]; then
  ACCOUNTS_SET=1; _split_accounts "$CLUSTER_ACCOUNTS"
fi
if [ "$ACCOUNTS_SET" = 0 ] && [ -n "${CLUSTER_ACCOUNT:-}" ]; then
  ACCOUNTS_SET=1; _add_account "$(trim "$CLUSTER_ACCOUNT")"
fi
# 门户已安装时，site.conf 是站点账户的权威来源；没显式传账户就按它走
# （基础集群阶段 site.conf 还不存在，仍落到下面的历史默认 lab）。
_SITE_CONF="${PORTAL_SITE_CONF:-/etc/cluster-portal/site.conf}"
if [ "$ACCOUNTS_SET" = 0 ] && [ -r "$_SITE_CONF" ]; then
  _SITE_ACCTS="$(sed -n 's/^ACCOUNTS=//p' "$_SITE_CONF" | tr -d ' \r' | head -1)"
  if [ -n "$_SITE_ACCTS" ]; then
    ACCOUNTS_SET=1; _split_accounts "$_SITE_ACCTS"
  fi
  if [ "$DEFAULT_SET" = 0 ]; then
    _SITE_DEFAULT="$(sed -n 's/^DEFAULT_ACCOUNT=//p' "$_SITE_CONF" | tr -d ' \r' | head -1)"
    if [ -n "$_SITE_DEFAULT" ]; then
      DEFAULT_SET=1; DEFAULT_ACCOUNT="$(trim "$_SITE_DEFAULT")"
    fi
  fi
fi
if [ "$ACCOUNTS_SET" = 0 ]; then
  # 没给任何账户信息：保持历史行为（lab），方便单独手工执行本脚本
  ACCOUNTS_SET=1; _add_account lab
fi
if [ "$DEFAULT_SET" = 0 ] && [ -n "${CLUSTER_DEFAULT_ACCOUNT:-}" ]; then
  DEFAULT_ACCOUNT="$(trim "$CLUSTER_DEFAULT_ACCOUNT")"
fi
for a in "${ACCOUNTS[@]}"; do
  if ! [[ "$a" =~ $ACCOUNT_NAME_RE ]]; then
    echo "[!] 非法账户名 '$a'：须匹配 ^[a-z][a-z0-9_-]{0,31}$" >&2
    exit 1
  fi
done
if [ -n "$DEFAULT_ACCOUNT" ] && [ "${#ACCOUNTS[@]}" -eq 0 ]; then
  echo "[!] 指定了默认账户 $DEFAULT_ACCOUNT，但账户列表为空" >&2
  exit 1
fi
if [ -z "$DEFAULT_ACCOUNT" ] && [ "${#ACCOUNTS[@]}" -gt 0 ]; then
  DEFAULT_ACCOUNT="${ACCOUNTS[0]}"
fi
if [ -n "$DEFAULT_ACCOUNT" ] && ! [[ "$DEFAULT_ACCOUNT" =~ $ACCOUNT_NAME_RE ]]; then
  echo "[!] 非法默认账户名 '$DEFAULT_ACCOUNT'" >&2
  exit 1
fi
if [ -n "$DEFAULT_ACCOUNT" ]; then
  _found=0
  for a in "${ACCOUNTS[@]}"; do [ "$a" = "$DEFAULT_ACCOUNT" ] && _found=1; done
  if [ "$_found" != 1 ]; then
    echo "[!] 默认账户 $DEFAULT_ACCOUNT 不在账户列表（${ACCOUNTS[*]:-空}）中" >&2
    exit 1
  fi
fi
ACCOUNT_STR="$(IFS=,; printf '%s' "${ACCOUNTS[*]:-}")"

HOST="$(hostname -s)"

# ---- UID/GID 预检 -----------------------------------------------------------
# 为什么必须拦重复 UID：
#   enroot 的 passwd hook（/etc/enroot/hooks.d/10-shadow.sh）会执行
#   `getent passwd <uid>` 取出**唯一一条**记录写进容器的 /etc/passwd。
#   UID 重复时这条取到的是 /etc/passwd 里先出现的那个账号名，于是容器内
#   **根本没有真实用户名的条目**，sshd 解析不到 → 用户登录报
#   "Permission denied (publickey)"（容器启动日志里的"连接示例"还会显示错误的用户名）。
#   所以这里宁可显式失败，也不接受 -o 建出的重复号。
check_uid_free() {
  local uid="${1:-}" owner
  [ -n "$uid" ] || return 0
  owner="$(getent passwd "$uid" | cut -d: -f1 | head -1)"
  if [ -n "$owner" ] && [ "$owner" != "$USERNAME" ]; then
    echo "[!] $HOST: UID $uid 已被账号 $owner 占用，拒绝创建重复 UID" >&2
    echo "[!] 重复 UID 会让 enroot 容器内解析不到 $USERNAME（ssh 报 Permission denied (publickey)）" >&2
    echo "[!] 本机不该有两个账号共用同一 UID。请先确认哪个是多余的，不要删错：" >&2
    echo "[!]   家目录 /share/home/$owner 是否存在？是否还有作业(squeue -u $owner)与登录记录？" >&2
    echo "[!]   确认 $owner 是遗留/已注销账号后再删：userdel $owner" >&2
    echo "[!]   若 $owner 才是有效用户，则不要动它 —— 请改用其它未占用的 UID" >&2
    exit 1
  fi
}

check_gid_free() {
  local gid="${1:-}" owner
  [ -n "$gid" ] || return 0
  owner="$(getent group "$gid" | cut -d: -f1 | head -1)"
  if [ -n "$owner" ] && [ "$owner" != "$USERNAME" ]; then
    echo "[!] $HOST: GID $gid 已被组 $owner 占用，拒绝创建重复 GID" >&2
    echo "[!] 请先清理占用者：groupdel $owner" >&2
    exit 1
  fi
}

# 建同名主组（已存在则复用）；给了 -g 就用该 GID，保证各节点 GID 一致
ensure_group() {
  getent group "$USERNAME" >/dev/null && return 0
  if [ -n "$FORCE_GID" ]; then
    check_gid_free "$FORCE_GID"
    groupadd -g "$FORCE_GID" "$USERNAME"
  else
    groupadd "$USERNAME"
  fi
}

if [ -n "$FORCE_UID" ] && [ -z "$FORCE_GID" ]; then
  echo "[!] 只传了 -u 未传 -g：本机 GID 将自行取号，可能与其它节点不一致（建议一并传 -g）" >&2
fi

# ---- Slurm 会计关联（仅管理节点）-------------------------------------------
# 账户 + acct-* QoS 由门户/`portal-ctl accounts-init` 维护；这里只建/核对成员关联。
account_exists() {
  local out
  if out="$(sacctmgr -n -P show account "$1" format=Account 2>/dev/null)"; then
    [ -n "$(printf '%s' "$out" | tr -d '[:space:]')" ]
  else
    return 1
  fi
}

assoc_exists() {
  local out
  if out="$(sacctmgr -n -P show assoc user="$1" account="$2" format=Account 2>/dev/null)"; then
    [ -n "$(printf '%s' "$out" | tr -d '[:space:]')" ]
  else
    return 1
  fi
}

current_default() {
  sacctmgr -n -P show user "$1" format=DefaultAccount 2>/dev/null | head -1 | tr -d '[:space:]'
}

ensure_account_assoc() {
  local acct="$1" qos
  qos="acct-$acct"
  if ! account_exists "$acct"; then
    echo "[!] sacctmgr 账户 $acct 不存在：请先在门户【管理账户】页创建/同步，" >&2
    echo "[!] 或在管理节点执行: portal-ctl accounts-init（或 accounts-sync）" >&2
    return 1
  fi
  if assoc_exists "$USERNAME" "$acct"; then
    # 关联已在：顺手把 qos/defaultqos 校正到 acct-*（历史账号可能是 qos=normal）
    sacctmgr -i modify user "$USERNAME" where account="$acct" \
      set QosLevel="$qos" DefaultQOS="$qos" >/dev/null 2>&1 || true
    return 0
  fi
  # 先用契约里的写法，再用 sacctmgr 帮助里列出的规范字段名（各版本别名接受度不同）
  if sacctmgr -i add user "$USERNAME" account="$acct" qos="$qos" defaultqos="$qos" \
      >/dev/null 2>&1; then
    return 0
  fi
  if sacctmgr -i add user "$USERNAME" Accounts="$acct" QosLevel="$qos" \
      DefaultQOS="$qos" >/dev/null 2>&1; then
    return 0
  fi
  assoc_exists "$USERNAME" "$acct" && return 0
  echo "[!] 为 $USERNAME 建立账户 $acct 的关联失败（账户或 QoS acct-$acct 不存在？）" >&2
  return 1
}

set_default_account() {
  local acct="$1" cur
  cur="$(current_default "$USERNAME")"
  [ "$cur" = "$acct" ] && return 0
  if sacctmgr -i modify user "$USERNAME" set DefaultAccount="$acct" >/dev/null 2>&1; then
    cur="$(current_default "$USERNAME")"
    if [ -z "$cur" ] || [ "$cur" = "$acct" ]; then
      return 0
    fi
  fi
  if sacctmgr -i modify user "$USERNAME" where Accounts="$acct" \
      set DefaultAccount="$acct" >/dev/null 2>&1; then
    cur="$(current_default "$USERNAME")"
    if [ -z "$cur" ] || [ "$cur" = "$acct" ]; then
      return 0
    fi
  fi
  echo "[!] 设置 $USERNAME 的默认账户为 $acct 失败" >&2
  return 1
}

# ---- 角色判定：/share 是 NFS 挂载 => 计算节点；其它（ext4 等本地盘）=> 管理节点 ----
SHARE_FSTYPE="$(findmnt -no FSTYPE /share 2>/dev/null || echo none)"
case "$SHARE_FSTYPE" in
  nfs|nfs4) ROLE="compute" ;;
  none|"")  echo "[!] /share 未挂载，无法判定本机角色，已中止"; exit 1 ;;
  *)        ROLE="server" ;;
esac

if id "$USERNAME" >/dev/null 2>&1; then
  echo "[$HOST] $USERNAME 已存在，跳过（角色=$ROLE）"
  # 已存在也要核对 UID/GID：与管理节点漂移会让 NFS 上属主/属组不一致，
  # 而且曾经出现过"本机已有别的账号占着同一个 UID"的重复号（会让容器内解析不到该用户）
  if [ -n "$FORCE_UID" ] && [ "$(id -u "$USERNAME")" != "$FORCE_UID" ]; then
    echo "[!] $USERNAME 现有 UID=$(id -u "$USERNAME")，期望 $FORCE_UID，请手工修正" >&2
  fi
  if [ -n "$FORCE_GID" ] && [ "$(id -g "$USERNAME")" != "$FORCE_GID" ]; then
    echo "[!] $USERNAME 现有 GID=$(id -g "$USERNAME")，期望 $FORCE_GID" >&2
    echo "[!] 修正: groupmod -g $FORCE_GID $USERNAME && usermod -g $FORCE_GID $USERNAME" >&2
  fi
  exit 0
fi

if [ "$ROLE" = "server" ]; then
  echo "[$HOST/管理节点] 创建用户与家目录 ..."
  check_uid_free "$FORCE_UID"
  ensure_group
  if [ -n "$FORCE_UID" ]; then
    useradd -m -s /bin/bash -d "/share/home/$USERNAME" -u "$FORCE_UID" -g "$USERNAME" "$USERNAME"
  else
    useradd -m -s /bin/bash -d "/share/home/$USERNAME" -g "$USERNAME" "$USERNAME"
  fi
  UID_NOW="$(id -u "$USERNAME")"
  GID_NOW="$(id -g "$USERNAME")"
  if [ -x /opt/cluster-admin/set-quota.sh ]; then
    /opt/cluster-admin/set-quota.sh "$USERNAME" "$QUOTA" || true
  fi
  ASSOC_FAIL=0
  if [ "${#ACCOUNTS[@]}" -eq 0 ]; then
    echo "[$HOST] 显式指定了空账户列表：跳过 sacctmgr 关联（管理员角色可不选账户）"
  else
    for a in "${ACCOUNTS[@]}"; do
      ensure_account_assoc "$a" || ASSOC_FAIL=1
    done
    if [ "$ASSOC_FAIL" = 0 ]; then
      set_default_account "$DEFAULT_ACCOUNT" || ASSOC_FAIL=1
    fi
  fi
  if [ "$ASSOC_FAIL" != 0 ]; then
    echo "[!] $USERNAME 的 OS 账号已创建，但 Slurm 会计关联/默认账户设置失败。" >&2
    echo "[!] 修复账户后执行: portal-ctl ensure-assoc $USERNAME --accounts $ACCOUNT_STR --default-account $DEFAULT_ACCOUNT" >&2
    exit 1
  fi
  echo "[$HOST] 完成: $USERNAME uid=$UID_NOW gid=$GID_NOW 家目录=/share/home/$USERNAME 磁盘配额=$QUOTA"
  echo "[$HOST] 作业账户=${ACCOUNT_STR:-无} 默认账户=${DEFAULT_ACCOUNT:-无} QoS=$( [ -n "$ACCOUNT_STR" ] && echo 'acct-<账户>' || echo '无' )"
  echo "[$HOST] 下一步: 1) passwd $USERNAME   2) 各计算节点执行 add-user.sh $USERNAME -u $UID_NOW -g $GID_NOW"
else
  echo "[$HOST/计算节点] 创建账号(不建家目录，家目录经 NFS 自动可见) ..."
  check_uid_free "$FORCE_UID"
  ensure_group
  if [ -n "$FORCE_UID" ]; then
    useradd -M -s /bin/bash -d "/share/home/$USERNAME" -u "$FORCE_UID" -g "$USERNAME" "$USERNAME"
  else
    useradd -M -s /bin/bash -d "/share/home/$USERNAME" -g "$USERNAME" "$USERNAME"
  fi
  echo "[$HOST] 完成: $USERNAME uid=$(id -u "$USERNAME") gid=$(id -g "$USERNAME")"
fi
