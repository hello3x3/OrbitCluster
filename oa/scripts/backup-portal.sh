#!/usr/bin/env bash
# backup-portal.sh —— 在管理节点以 root 执行：打包门户完整状态用于备份/迁移
# 用法: bash backup-portal.sh [输出目录(默认 /var/backups/cluster-portal)]
#
# ⚠️ 产物里含 /etc/cluster-portal/users.passwd —— 那是**全站明文门户口令**。
#    因此这里全程 umask 077，并把最终 tgz 设成 0600：默认 umask(022) 会生成 0644 的
#    存档，任何本机用户（或拿到宿主机 shell 的人）都能直接解开读到所有人的口令。
set -eu
umask 077
OUT="${1:-/var/backups/cluster-portal}"
STAMP="$(date +%Y%m%d-%H%M%S)"
DEST="$OUT/portal-$STAMP"
mkdir -p "$DEST"
chmod 700 "$OUT" "$DEST"

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
chmod 600 "$OUT/portal-$STAMP.tgz"
rm -rf "$DEST"
echo "完成: $OUT/portal-$STAMP.tgz  （0600；内含明文口令，请妥善保管）"
ls -lh "$OUT/portal-$STAMP.tgz"
