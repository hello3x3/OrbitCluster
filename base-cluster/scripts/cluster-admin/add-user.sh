#!/usr/bin/env bash
# add-user.sh —— 建集群账号（管理节点：建家目录 + 配额 + sacctmgr 关联；计算节点：只建账号）
#
# 用法:
#   [管理节点] add-user.sh <用户名> [配额，默认 500G] [-u UID]
#   [计算节点] add-user.sh <用户名> [-u UID]
#              （-u 用于管理节点与本机自动分配的 UID 不一致时强制对齐）
#
# 站点无关：本机是"管理节点"还是"计算节点"由 /share 是否为本地文件系统自动判定，
# 不写死主机名。因此同一份脚本可原样用于任意命名的集群（管理节点叫什么都行）。
set -eu

USERNAME=""; QUOTA="500G"; FORCE_UID=""
while [ $# -gt 0 ]; do
  case "$1" in
    -u) FORCE_UID="$2"; shift 2 ;;
    *) if [ -z "$USERNAME" ]; then USERNAME="$1"; shift; else QUOTA="$1"; shift; fi ;;
  esac
done
[ -n "$USERNAME" ] || { echo "用法: add-user.sh <用户名> [配额] [-u UID]"; exit 1; }

HOST="$(hostname -s)"
ACCOUNT="${CLUSTER_ACCOUNT:-lab}"

# ---- 角色判定：/share 是 NFS 挂载 => 计算节点；其它（ext4 等本地盘）=> 管理节点 ----
SHARE_FSTYPE="$(findmnt -no FSTYPE /share 2>/dev/null || echo none)"
case "$SHARE_FSTYPE" in
  nfs|nfs4) ROLE="compute" ;;
  none|"")  echo "[!] /share 未挂载，无法判定本机角色，已中止"; exit 1 ;;
  *)        ROLE="server" ;;
esac

if id "$USERNAME" >/dev/null 2>&1; then
  echo "[$HOST] $USERNAME 已存在，跳过（角色=$ROLE）"
  exit 0
fi

if [ "$ROLE" = "server" ]; then
  echo "[$HOST/管理节点] 创建用户与家目录 ..."
  if [ -n "$FORCE_UID" ]; then
    useradd -m -s /bin/bash -d "/share/home/$USERNAME" -u "$FORCE_UID" -o "$USERNAME"
  else
    useradd -m -s /bin/bash -d "/share/home/$USERNAME" "$USERNAME"
  fi
  UID_NOW="$(id -u "$USERNAME")"
  if [ -x /opt/cluster-admin/set-quota.sh ]; then
    /opt/cluster-admin/set-quota.sh "$USERNAME" "$QUOTA" || true
  fi
  sacctmgr -i add user "$USERNAME" account="$ACCOUNT" qos=normal >/dev/null 2>&1 \
    || echo "[!] sacctmgr 关联失败，请手动: sacctmgr add user $USERNAME account=$ACCOUNT qos=normal"
  echo "[$HOST] 完成: $USERNAME uid=$UID_NOW 家目录=/share/home/$USERNAME 磁盘配额=$QUOTA 作业QoS=normal(中等/不限)"
  echo "[$HOST] 下一步: 1) passwd $USERNAME   2) 各计算节点执行 add-user.sh $USERNAME -u $UID_NOW"
else
  echo "[$HOST/计算节点] 创建账号(不建家目录，家目录经 NFS 自动可见) ..."
  if [ -n "$FORCE_UID" ]; then
    useradd -M -s /bin/bash -d "/share/home/$USERNAME" -u "$FORCE_UID" -o "$USERNAME"
  else
    useradd -M -s /bin/bash -d "/share/home/$USERNAME" "$USERNAME"
  fi
  echo "[$HOST] 完成: $USERNAME uid=$(id -u "$USERNAME")"
fi
