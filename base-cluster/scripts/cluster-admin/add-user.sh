#!/usr/bin/env bash
# add-user.sh —— 建集群账号（管理节点：建家目录 + 配额 + sacctmgr 关联；计算节点：只建账号）
#
# 用法:
#   [管理节点] add-user.sh <用户名> [配额，默认 500G] [-u UID] [-g GID]
#   [计算节点] add-user.sh <用户名> [-u UID] [-g GID]
#              （-u/-g 用于把计算节点的 UID/GID 强制对齐到管理节点；
#                两个都要传，只传 -u 时 GID 会在各节点自行取号而漂移）
#
# 站点无关：本机是"管理节点"还是"计算节点"由 /share 是否为本地文件系统自动判定，
# 不写死主机名。因此同一份脚本可原样用于任意命名的集群（管理节点叫什么都行）。
#
# 安全约束：**绝不建重复 UID/GID**（故不再使用 useradd 的 -o）。
#   原因见下方 check_uid_free() 注释：重复 UID 会让 enroot 容器里解析不到真实用户名，
#   用户 ssh 进容器时报 "Permission denied (publickey)"。
set -eu

USERNAME=""; QUOTA="500G"; FORCE_UID=""; FORCE_GID=""
while [ $# -gt 0 ]; do
  case "$1" in
    -u) FORCE_UID="$2"; shift 2 ;;
    -g) FORCE_GID="$2"; shift 2 ;;
    *) if [ -z "$USERNAME" ]; then USERNAME="$1"; shift; else QUOTA="$1"; shift; fi ;;
  esac
done
[ -n "$USERNAME" ] || { echo "用法: add-user.sh <用户名> [配额] [-u UID] [-g GID]"; exit 1; }

HOST="$(hostname -s)"
ACCOUNT="${CLUSTER_ACCOUNT:-lab}"

# ---- UID/GID 预检 -----------------------------------------------------------
# 为什么必须拦重复 UID（踩过的坑）：
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
  sacctmgr -i add user "$USERNAME" account="$ACCOUNT" qos=normal >/dev/null 2>&1 \
    || echo "[!] sacctmgr 关联失败，请手动: sacctmgr add user $USERNAME account=$ACCOUNT qos=normal"
  echo "[$HOST] 完成: $USERNAME uid=$UID_NOW gid=$GID_NOW 家目录=/share/home/$USERNAME 磁盘配额=$QUOTA 作业QoS=normal(中等/不限)"
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
