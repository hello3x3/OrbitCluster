#!/usr/bin/env bash
# set-quota.sh —— 设置/扩容某用户在 /share 上的磁盘配额（只能在 NFS 服务端执行）
#
# 用法: set-quota.sh <用户名> <大小 如 500G|1T|200M> [挂载点...，默认 /share]
#
# ext4 按 UID 计配额，覆盖该用户在该文件系统上的全部文件。
# soft = hard 相等 => 无宽限期、到顶即拒写；setquota 对已挂载文件系统立即生效。
# 站点无关：只校验"是不是本地文件系统 + 有没有开 usrquota"，不写死主机名。
set -eu

USAGE="用法: set-quota.sh <用户名> <大小如 500G|1T> [挂载点...，默认 /share]"
[ -n "${1:-}" ] || { echo "$USAGE"; exit 1; }
[ -n "${2:-}" ] || { echo "$USAGE"; exit 1; }
U="$1"
SZ="$2"

case "$SZ" in
  *[Gg]) N="${SZ%[Gg]}"; BLK="$(awk "BEGIN{printf \"%.0f\", $N*1024*1024}")" ;;
  *[Tt]) N="${SZ%[Tt]}"; BLK="$(awk "BEGIN{printf \"%.0f\", $N*1024*1024*1024}")" ;;
  *[Mm]) N="${SZ%[Mm]}"; BLK="$(awk "BEGIN{printf \"%.0f\", $N*1024}")" ;;
  *) echo "大小需带单位 M/G/T, 如 500G"; exit 1 ;;
esac

id "$U" >/dev/null 2>&1 || { echo "用户 $U 不存在"; exit 1; }

if [ "$#" -ge 3 ]; then MOUNTS="${*:3}"; else MOUNTS="/share"; fi

rc=0
for M in $MOUNTS; do
  FT="$(findmnt -no FSTYPE "$M" 2>/dev/null || echo none)"
  case "$FT" in
    nfs|nfs4|none)
      echo "[!] 跳过 $M（$FT）：配额只能在 NFS 服务端的本地文件系统上设置"
      rc=1; continue ;;
  esac
  if ! findmnt -no OPTIONS "$M" | tr ',' '\n' | grep -qx usrquota; then
    echo "[!] 跳过 $M：未启用 usrquota（先在 /etc/fstab 该项加 usrquota 并 remount）"
    rc=1; continue
  fi
  setquota -u "$U" "$BLK" "$BLK" 0 0 "$M"
  echo "== 已设置: $U 软/硬上限 = $SZ (blocks=$BLK) @ $M =="
  quota -u "$U" 2>/dev/null | tail -4 || true
done
exit "$rc"
