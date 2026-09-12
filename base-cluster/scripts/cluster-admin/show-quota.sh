#!/usr/bin/env bash
# show-quota.sh —— 查看全部用户的磁盘配额使用情况（在 NFS 服务端执行）
#
# 用法: show-quota.sh [挂载点...，默认 /share]
set -eu

MOUNTS="${*:-/share}"
for M in $MOUNTS; do
  echo "===== $M ====="
  if [ ! -d "$M" ]; then
    echo "[!] $M 不存在，跳过"
    continue
  fi
  repquota -u "$M"
done
