#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""render_docs.py —— 把仓库里的 markdown 渲染成本集群的实值版本。

被 provision/render.sh 的「文档」阶段调用：

    python3 render_docs.py --repo <仓库根> --out <out/docs> --map <TOKEN=VALUE 文件>

做什么：
  1. 遍历仓库里的 *.md（跳过 .git/、provision/、oa/sites/、oa/config-snapshot/）
  2. 把 <TOKEN> 占位符替换成 cluster.conf 推出的实值
     （<ADMIN> / <ADMIN_IP> / <GPU01>…<GPU0N> / <LAN_CIDR> / <SSH_PORT> …）
  3. **不改**角色名、账号名、路径名里的 admin：
     `cluster-admin`、`admin_users`、`admin_required`、`.cluster-portal-admin`、
     `reset-admin`（靠词边界天然排除），以及 `bootstrap.py admin … admin`（bootstrap 的
     用户名/角色参数）、"默认角色即 admin" 这类表述 —— 详见 render.sh 里的说明。
  4. 输出 _REPLACEMENT-REPORT.md：每个文件替换了多少处；并把**渲染后仍残留的小写
     `admin`** 逐行列出来 —— 它们应该都是"角色/账号"含义；如果哪一行其实是主机名，
     说明仓库里那个位置漏了占位符，报告会让它暴露出来而不是静默出错。
"""
import argparse
import os
import re
import sys

SKIP_DIRS = ('.git', 'provision', os.path.join('oa', 'sites'), os.path.join('oa', 'config-snapshot'))

SKIP_DIRNAMES = {'.git', '__pycache__', 'node_modules'}
SKIP_PREFIXES = (os.path.join('oa', 'sites'), os.path.join('oa', 'config-snapshot'), 'provision')


def _skip_dir(name):
    return (name in SKIP_DIRNAMES or name == 'venv' or name.startswith('.venv')
            or name.startswith('out'))


def collect_md(repo):
    """仓库里要渲染的 markdown（跳过第三方/生成物/别的站点档案/现场快照/生成器自身）。"""
    out = []
    for root, dirs, files in os.walk(repo):
        rel = os.path.relpath(root, repo)
        rel = '' if rel == '.' else rel
        if rel and any(rel == p or rel.startswith(p + os.sep) for p in SKIP_PREFIXES):
            continue
        dirs[:] = [d for d in dirs if not _skip_dir(d)]
        for f in files:
            if f.endswith('.md'):
                out.append(os.path.join(rel, f) if rel else f)
    return sorted(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--map', required=True, help='TOKEN=VALUE 文件（每行一条）')
    ap.add_argument('--print', action='store_true')
    a = ap.parse_args()

    tokens = {}
    for ln in open(a.map, encoding='utf-8'):
        ln = ln.rstrip('\n')
        if '=' in ln and ln.strip():
            k, v = ln.split('=', 1)
            if k.strip():
                tokens[k.strip()] = v
    # 长 token 先替换（<ADMIN_IP> 要在 <ADMIN> 之前；虽然 <> 结构本身不冲突，稳妥起见）
    ordered = sorted(tokens.items(), key=lambda kv: -len(kv[0]))
    tok_re = [(t, re.compile(re.escape(t))) for t, _ in ordered]

    # 关键：残留检测必须看**替换前**的原文 —— 否则当 MGR_NAME 恰好也叫 "admin" 时，
    # 由 <ADMIN> 替换出来的 admin 会被误报成"漏了占位符"。
    admin_re = re.compile(r'(?<![\w-])admin(?![\w-])')

    total = {}
    leftovers = []
    unmapped = {}
    nodeish = re.compile(r'<((?:ADMIN|GPU\d+)(?:_[A-Z0-9]+)?)>')
    for rel in collect_md(a.repo):
        src = os.path.join(a.repo, rel)
        try:
            text = open(src, encoding='utf-8').read()
        except OSError:
            continue
        # ① 先记录原文里"没有被占位符覆盖"的小写 admin
        for i, ln in enumerate(text.split('\n'), 1):
            if admin_re.search(ln):
                leftovers.append((rel, i, ln.strip()[:110]))
        # ② 再做占位符替换
        n = 0
        for t, rx in tok_re:
            text, k = rx.subn(lambda m, v=tokens[t]: v, text)
            n += k
        total[rel] = n
        # ③ 替换后仍存在的"节点类"占位符 → 说明 cluster.conf 里没有对应节点
        for m in nodeish.finditer(text):
            unmapped.setdefault(m.group(1), set()).add(rel)
        if a.print:
            continue
        dst = os.path.join(a.out, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, 'w', encoding='utf-8') as fh:
            fh.write(text)

    if a.print:
        print("（--print：未落盘）")
        return 0

    changed = {k: v for k, v in total.items() if v}
    with open(os.path.join(a.out, '_REPLACEMENT-REPORT.md'), 'w', encoding='utf-8') as fh:
        fh.write("# 文档渲染报告（由 provision/render.sh 生成）\n\n")
        # 节点占位符 ↔ 本集群节点：新增机器后这里会多出 <GPU02>、<GPU03>…
        # 文档正文里**没有**的节点不会被自动补写（文档是示例，不会凭空长段落）；
        # 但配置文件（hosts/fstab/slurm.conf/exports…）是按 NODE 行循环生成的，会自动齐全。
        fh.write("## 节点占位符 ↔ 本集群节点\n\n| 占位符 | 本集群节点 |\n|---|---|\n")
        for t in sorted(tokens, key=lambda x: (x != '<ADMIN>', x)):
            if re.fullmatch(r'<(?:ADMIN|GPU\d+)>', t):
                fh.write("| `%s` | `%s` |\n" % (t, tokens[t]))
        fh.write("\n> 文档正文只覆盖到上面这些位次。cluster.conf 里**再加机器**时：\n"
                 "> 配置文件（每台的 /etc/hosts、fstab、slurm NodeName、exports、chrony…）会按 NODE 行\n"
                 "> 自动全部生成；而文档正文不会自动多出段落 —— 文档里的示例就是示例。\n\n")
        fh.write("## 占位符替换统计\n\n| 文档 | 替换处数 |\n|---|---|\n")
        for k in sorted(changed):
            fh.write("| `%s` | %d |\n" % (k, changed[k]))
        fh.write("\n合计 %d 处。\n\n" % sum(changed.values()))
        fh.write("## 渲染后仍保留的小写 `admin`\n\n")
        fh.write("下面这些 `admin` **没有**被替换 —— 它们应当是**门户角色名 / 门户账号名 / "
                 "历史引文**：\n\n")
        for rel, i, ln in leftovers:
            fh.write("- `%s:%d` — %s\n" % (rel, i, ln))
        fh.write("\n> ⚠️ 请核对上面每一行：如果哪一行里的 `admin` 其实指的是**管理节点主机名**，"
                 "说明该处漏了 `<ADMIN>` 占位符，请在仓库里补上（而不是改生成物）。\n")
        if unmapped:
            fh.write("\n## 未能替换的节点类占位符\n\n")
            fh.write("文档里引用了下列占位符，但 cluster.conf 里没有对应的节点 —— "
                     "如果本集群确实没有这些节点，可以忽略（文档里的示例比本集群规模大）：\n\n")
            for t in sorted(unmapped):
                fh.write("- `<%s>` —— 见于 %s\n" % (t, "、".join(sorted(unmapped[t]))))
    print("  文档渲染: %d 个文件、%d 处替换；残留小写 admin %d 处（已列入报告待核对）"
          % (len(changed), sum(changed.values()), len(leftovers)))
    if unmapped:
        print("  [i] 有 %d 个节点类占位符未映射（本集群节点更少）：%s"
              % (len(unmapped), " ".join("<%s>" % t for t in sorted(unmapped))))
    return 0


if __name__ == '__main__':
    sys.exit(main())
