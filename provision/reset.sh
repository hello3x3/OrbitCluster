#!/usr/bin/env bash
# ============================================================================
# reset.sh —— 撤销 `render.sh --in-place` 的改动，把仓库还原成模板状态。
#
# 只动 .render-state/files.txt 里记录过的文件：
#   · 有备份的（原本就存在、被覆盖的文档）→ 从备份恢复成**最初**的模板
#   · 没有备份的（本次新生成的 deploy/etc/、deploy/MANIFEST.md…）→ 删除
#
# 因此**不会误伤**你任何未提交的其它改动 —— 它不跑 git checkout。
# 备份只在首次渲染时快照，所以连跑多次 make 之后还原，拿到的仍是仓库最初的模板。
# ============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
STATE="$HERE/.render-state"

if [ ! -d "$STATE" ]; then
  echo "==> 没有就地渲染记录（$STATE 不存在）"
  echo "    仓库应当已经是模板状态，无需还原。"
  exit 0
fi
if [ ! -f "$STATE/files.txt" ]; then
  echo "==> 状态文件 $STATE/files.txt 缺失，拒绝删除任何东西" >&2
  exit 1
fi

echo "==> 还原仓库模板"
[ -f "$STATE/meta" ] && sed 's/^/    /' "$STATE/meta"
echo

restored=0; removed=0; skipped=0
while IFS= read -r rel; do
  [ -n "$rel" ] || continue
  # 只接受仓库内的相对路径，防手滑
  case "$rel" in
    /*|..|../*|*/../*) echo "  [跳过] 可疑路径: $rel" >&2; skipped=$((skipped + 1)); continue ;;
  esac
  if [ -f "$STATE/backup/$rel" ]; then
    cp -p "$STATE/backup/$rel" "$REPO/$rel"
    restored=$((restored + 1))
  elif [ -f "$REPO/$rel" ]; then
    rm -f "$REPO/$rel"
    removed=$((removed + 1))
  fi
done < "$STATE/files.txt"

# 清掉因此变空的目录。
# deploy/ 是当前布局；etc/ 与 opt/ 是更早的布局名，若存在也一并扫掉。
for d in "$REPO/deploy" "$REPO/etc" "$REPO/opt"; do
  [ -d "$d" ] || continue
  find "$d" -type d -empty -delete 2>/dev/null || true
  rmdir "$d" 2>/dev/null || true
done

rm -rf "$STATE"

echo "    ✅ 恢复 $restored 个文件，删除 $removed 个生成物$([ "$skipped" -gt 0 ] && echo "，跳过 $skipped 个可疑路径")"
echo
echo "    仓库已回到模板（文档里是 <ADMIN>/<GPU01>/… 这类占位符）。"
echo "    建议用 git status 确认：不应有其它无关改动被动过。"
