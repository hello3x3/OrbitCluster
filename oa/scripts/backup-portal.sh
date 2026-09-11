#!/usr/bin/env bash
# backup-portal.sh —— 在 admin 节点以 root 执行：打包门户完整状态用于备份/迁移
# 用法: bash backup-portal.sh [输出目录(默认 /var/backups/cluster-portal)]
set -eu
OUT="${1:-/var/backups/cluster-portal}"
STAMP="$(date +%Y%m%d-%H%M%S)"
DEST="$OUT/portal-$STAMP"
mkdir -p "$DEST"

echo "==> 门户代码与助手"
cp -a /opt/cluster-portal/. "$DEST/app/" 2>/dev/null || rsync -a /opt/cluster-portal/ "$DEST/app/"
[ -f /usr/local/sbin/portal-ctl ] && cp -a /usr/local/sbin/portal-ctl "$DEST/portal-ctl"

echo "==> 数据（DB/secret）"
mkdir -p "$DEST/data"
[ -f /var/lib/cluster-portal/portal.db ] && cp -a /var/lib/cluster-portal/portal.db "$DEST/data/"
[ -f /var/lib/cluster-portal/portal.db-wal ] && cp -a /var/lib/cluster-portal/portal.db-wal "$DEST/data/" || true
[ -f /var/lib/cluster-portal/portal.db-shm ] && cp -a /var/lib/cluster-portal/portal.db-shm "$DEST/data/" || true
[ -f /var/lib/cluster-portal/secret ] && cp -a /var/lib/cluster-portal/secret "$DEST/data/"

echo "==> 密码文件（明文，务必妥善保管）与系统配置"
mkdir -p "$DEST/etc"
cp -a /etc/cluster-portal "$DEST/etc/"
systemctl cat cluster-portal > "$DEST/etc/cluster-portal.service" 2>/dev/null || true
[ -f /etc/sudoers.d/cluster-portal ] && cp -a /etc/sudoers.d/cluster-portal "$DEST/etc/portal.sudoers"

echo "==> 打包"
cd "$OUT" && tar czf "portal-$STAMP.tgz" "portal-$STAMP"
rm -rf "$DEST"
echo "完成: $OUT/portal-$STAMP.tgz"
ls -lh "$OUT/portal-$STAMP.tgz"
