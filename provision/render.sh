#!/usr/bin/env bash
# ============================================================================
# render.sh —— 从**一个** cluster.conf 生成集群全部配置文件（不碰任何机器）
#
#   用法:
#     ./render.sh -c conf/field-3node.conf            # 输出到 ./out/
#     ./render.sh -c cluster.conf -o /tmp/out --clean
#     ./render.sh -c cluster.conf --print              # 只打印不落盘
#     ./render.sh -c cluster.conf -s SSH_PORT=2222 -s USER=alice   # 临时覆盖个别配置项
#
#   设计目标：部署新集群时**只编辑 cluster.conf 一个文件**，
#   不再做「把手册里的 <IP>/<GPU01> 全文替换成实际值」这种容易出错的操作。
#
#   产物目录结构直接对应目标机路径，便于 rsync/scp 下发：
#     out/etc/hosts                       → 每台 /etc/hosts（内容相同）
#     out/etc/hosts.<节点>                → 同上（按节点分开的副本，便于单发）
#     out/etc/slurm/slurm.conf            → 每台 /etc/slurm/slurm.conf
#     out/etc/slurm/gres.conf             → 每台 /etc/slurm/gres.conf
#     out/etc/enroot/enroot.conf          → 每台 /etc/enroot/enroot.conf
#     out/etc/exports                     → 管理节点 /etc/exports
#     out/etc/fstab.<节点>                → 各节点要追加的数据盘/NFS 行
#     out/etc/ssh/sshd_config.d/10-portal-only.conf → 每台
#     out/etc/chrony/chrony.conf.append   → 管理节点/计算节点各一份
#     out/etc/cluster-portal/site.conf    → 管理节点（门户站点配置）
#     out/etc/cluster-portal/plans.json   → 管理节点（门户套餐种子）
#     out/opt/cluster-admin/*.sh          → 管理节点（从 base-cluster 拷贝）
#     out/MANIFEST.md                     → 每台机器该放哪些文件
#
#   本脚本只做「生成」，不做安装、不 ssh 任何机器（部署执行器见后续的 deploy.sh）。
# ============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
CONF=""
OUT="$HERE/out"
CLEAN=0
PRINT=0
OVERRIDES=()

usage() { sed -n '2,36p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    -c|--config) CONF="$2"; shift 2 ;;
    -o|--out)    OUT="$2";  shift 2 ;;
    -s|--set)    OVERRIDES+=("$2"); shift 2 ;;
    --clean)     CLEAN=1;   shift ;;
    --print)     PRINT=1;   shift ;;
    -h|--help)   usage 0 ;;
    *) echo "未知参数: $1" >&2; usage 1 ;;
  esac
done
[ -n "$CONF" ] || { echo "必须用 -c 指定 cluster.conf（样例见 provision/conf/）" >&2; exit 1; }
[ -f "$CONF" ] || { echo "配置文件不存在: $CONF" >&2; exit 1; }

# 安全检查：绝不往可疑目录里 rm -rf
case "$OUT" in
  /|/etc|/usr|/var|"$HOME"|"") echo "拒绝输出到 $OUT" >&2; exit 1 ;;
esac

# ---------------------------------------------------------------------------
# 1) 解析 cluster.conf
# ---------------------------------------------------------------------------
declare -A CFG=()
declare -a N_NAME=() N_IP=() N_ROLE=() N_GPUS=() N_MODEL=() N_CPUS=() N_MEM=() N_SOCK=() N_THR=()

trim() { local s="${1:-}"; s="${s#"${s%%[![:space:]]*}"}"; printf '%s' "${s%"${s##*[![:space:]]}"}"; }

lineno=0
while IFS= read -r raw || [ -n "$raw" ]; do
  lineno=$((lineno+1))
  line="${raw%%#*}"
  line="$(trim "$line")"
  [ -z "$line" ] && continue

  if [[ "$line" =~ ^NODE[[:space:]]+(.+)$ ]]; then
    spec="$(trim "${BASH_REMATCH[1]}")"
    name=""; ip=""; role="gpu"; gpus=0; model=""; cpus=""; mem=""; sock=""; thr=""
    first=1
    for tok in $spec; do
      if [ "$first" = 1 ] && [[ "$tok" == *=* ]]; then
        name="$(trim "${tok%%=*}")"; ip="$(trim "${tok#*=}")"; first=0; continue
      fi
      k="$(trim "${tok%%=*}")"; v="$(trim "${tok#*=}")"
      case "$k" in
        role)      role="$v" ;;
        gpus)      gpus="$v" ;;
        gpu_model) model="$v" ;;
        cpus)      cpus="$v" ;;
        mem_mb)    mem="$v" ;;
        sockets)   sock="$v" ;;
        threads)   thr="$v" ;;
        *) echo "  [警告] $CONF:$lineno 未知节点字段 '$k'，已忽略" >&2 ;;
      esac
    done
    [ -n "$name" ] && [ -n "$ip" ] || { echo "  [错误] $CONF:$lineno NODE 行缺少 <主机名>=<IP>" >&2; exit 1; }
    N_NAME+=("$name"); N_IP+=("$ip"); N_ROLE+=("$role")
    N_GPUS+=("$gpus"); N_MODEL+=("$model"); N_CPUS+=("$cpus"); N_MEM+=("$mem")
    N_SOCK+=("$sock"); N_THR+=("$thr")
    continue
  fi

  if [[ "$line" == *=* ]]; then
    k="$(trim "${line%%=*}")"; v="$(trim "${line#*=}")"
    CFG["$(printf '%s' "$k" | tr 'a-z' 'A-Z')"]="$v"
    continue
  fi
  echo "  [警告] $CONF:$lineno 无法解析: $line" >&2
done < "$CONF"

# ---- -s/--set 命令行覆盖（优先级最高，不写回 cluster.conf）----
if [ "${#OVERRIDES[@]}" -gt 0 ]; then
  for kv in "${OVERRIDES[@]}"; do
    [[ "$kv" == *=* ]] || { echo "  [错误] -s 需要 KEY=VALUE 形式，收到: $kv" >&2; exit 1; }
    k="$(printf '%s' "${kv%%=*}" | tr 'a-z' 'A-Z')"; v="${kv#*=}"
    CFG["$k"]="$v"
    echo "  [覆盖] $k=$v"
  done
fi

cfg() { printf '%s' "${CFG[${1:-}]:-${2:-}}"; }

[ "${#N_NAME[@]}" -ge 1 ] || { echo "cluster.conf 里至少要有一个 NODE 行" >&2; exit 1; }

MGR_NAME="${N_NAME[0]}"
MGR_IP="${N_IP[0]}"
CLUSTER_NAME="$(cfg CLUSTER_NAME lab)"
PARTITION="$(cfg PARTITION gpu)"
SSH_PORT="$(cfg SSH_PORT 2180)"
LAN_CIDR="$(cfg LAN_CIDR)"
TIMEZONE="$(cfg TIMEZONE Asia/Shanghai)"
# 文档示例里出现的那个"集群用户"（有 OS 账号、会提交容器的人）。
# 仓库里不写任何真实姓名，一律用 <USER> 占位，渲染时填成这里配的值。
# ⚠️ shell 变量故意叫 USER_VAL —— 不要把 CFG 里的 USER 直接赋给 $USER 去遮蔽环境变量。
USER_VAL="$(cfg USER lab)"
ACCOUNT="$(cfg ACCOUNT lab)"
SHARE_MOUNT="$(cfg SHARE_MOUNT /share)"
SHARE_DEVICE="$(cfg SHARE_DEVICE)"
IMAGES_DEVICE="$(cfg IMAGES_DEVICE)"
IMAGES_MOUNT="$(cfg IMAGES_MOUNT /share/images)"
GRES_MODE="$(cfg GRES_MODE auto)"
GPU_PREFIX="$(cfg GPU_PREFIX RTX)"
PORTAL_PORT="$(cfg PORTAL_PORT 8000)"
SLURM_VERSION="$(cfg SLURM_VERSION 26.05.1)"

# 管理节点是否同时是计算节点（决定它要不要 NFS 客户端行、要不要 slurmd）
MGR_IS_COMPUTE=0
[[ ",${N_ROLE[0]}," == *",gpu,"* ]] && MGR_IS_COMPUTE=1

# ---------------------------------------------------------------------------
# 2) 派生内容
# ---------------------------------------------------------------------------
# /etc/hosts 的节点行 + slurm.conf 的 NodeName 行 + /etc/exports 的客户端列表
NODE_HOSTS=""; NODE_LINES=""; EXPORT_CLIENTS=""; NEED_MEASURE=""
for i in "${!N_NAME[@]}"; do
  n="${N_NAME[$i]}"; ip="${N_IP[$i]}"; g="${N_GPUS[$i]}"; m="${N_MODEL[$i]}"
  cpus="${N_CPUS[$i]}"; mem="${N_MEM[$i]}"

  NODE_HOSTS+="${ip} ${n}"$'\n'

  # 真实两套集群的 NodeName 都**显式写了** Gres=，所以这里无条件写（与 gres.conf
  # 用 explicit 还是 auto 无关）。
  # ⚠️ 若 `slurmd -C` 报出来的 Gres 类型名与 gpu_model 不一致（典型：AutoDetect=nvml
  #    把 RTX 3090 推成 nvidia_geforce_rtx_3090），节点会变成 IDLE+DRAIN+INVALID_REG。
  #    解决：把 gpu_model 改成实测的类型名，或改用 GRES_MODE=explicit 固定成干净名字。
  gres_field=""
  if [ "$g" -gt 0 ] 2>/dev/null && [ -n "$m" ]; then
    gres_field=" Gres=gpu:${m}:${g}"
  fi
  sock="${N_SOCK[$i]:-}"; thr="${N_THR[$i]:-}"
  if [ -n "$cpus" ] && [ -n "$mem" ]; then
    [ -n "$sock" ] || sock=1
    [ -n "$thr" ]  || thr=2
    cores=$(( cpus / (sock * thr) ))
    NODE_LINES+="NodeName=${n} CPUs=${cpus} Boards=1 SocketsPerBoard=${sock} CoresPerSocket=${cores} ThreadsPerCore=${thr} RealMemory=${mem}${gres_field}"$'\n'
  else
    NODE_LINES+="NodeName=${n} CPUs=@@待实测@@ Boards=1 SocketsPerBoard=@@待实测@@ CoresPerSocket=@@待实测@@ ThreadsPerCore=@@待实测@@ RealMemory=@@待实测@@${gres_field}"$'\n'
    NEED_MEASURE+=" ${n}"
  fi
  EXPORT_CLIENTS+=" ${ip}"
done

# exports：镜像与 /share 同盘时只导一行；独立盘时导两行
EXPORTS_LINES="${SHARE_MOUNT}  ${LAN_CIDR}(rw,sync,no_subtree_check)"
if [ -n "$IMAGES_DEVICE" ]; then
  EXPORTS_LINES+=$'\n'"${IMAGES_MOUNT}  ${LAN_CIDR}(rw,sync,no_subtree_check)"
fi

# fstab：管理节点挂本地盘；计算节点挂 NFS（镜像独立盘时多一行）
FSTAB_MGR_LINES="${SHARE_DEVICE:-<数据盘>} ${SHARE_MOUNT} ext4 defaults,noatime,usrquota 0 2"
if [ -n "$IMAGES_DEVICE" ]; then
  FSTAB_MGR_LINES+=$'\n'"${IMAGES_DEVICE} ${IMAGES_MOUNT} ext4 defaults,noatime,x-systemd.requires-mounts-for=${SHARE_MOUNT} 0 2"
fi
FSTAB_GPU_LINES="${MGR_NAME}:${SHARE_MOUNT}  ${SHARE_MOUNT}  nfs _netdev,rw,hard,intr,noatime,actimeo=60 0 0"
if [ -n "$IMAGES_DEVICE" ]; then
  FSTAB_GPU_LINES+=$'\n'"${MGR_NAME}:${IMAGES_MOUNT} ${IMAGES_MOUNT} nfs _netdev,rw,hard,intr,noatime,actimeo=60,x-systemd.requires-mounts-for=${SHARE_MOUNT} 0 0"
fi

# 门户 site.conf 的 GPU 型号映射（去重）
GPU_TOKENS=""; GPU_MAP=""
for m in "${N_MODEL[@]}"; do
  [ -z "$m" ] && continue
  case " $GPU_TOKENS " in *" $m "*) ;; *) GPU_TOKENS+="$m "; GPU_MAP+="${m}:${GPU_PREFIX} ${m}," ;; esac
done
GPU_MAP="${GPU_MAP%,}"
DEFAULT_GPU_MODEL=""
[ -n "$GPU_MAP" ] && DEFAULT_GPU_MODEL="${GPU_MAP##*:}"

# 单节点最大卡数（决定要不要生成双卡/三卡套餐）
MAX_GPUS_VAL=0
for g in "${N_GPUS[@]}"; do [ "$g" -gt "$MAX_GPUS_VAL" ] 2>/dev/null && MAX_GPUS_VAL="$g"; done

# gres.conf 内容
if [ "$GRES_MODE" = "explicit" ]; then
  MAXG=0
  for g in "${N_GPUS[@]}"; do [ "$g" -gt "$MAXG" ] 2>/dev/null && MAXG="$g"; done
  GRES_BODY=""
  for ((k=0;k<MAXG;k++)); do
    GRES_BODY+="Name=gpu Type=$(printf '%s' "${N_MODEL[0]}") File=/dev/nvidia${k}"$'\n'
  done
  GRES_BODY="${GRES_BODY%$'\n'}"
else
  GRES_BODY="AutoDetect=nvml"
fi

# ---------------------------------------------------------------------------
# 3) 模板替换
# ---------------------------------------------------------------------------
declare -A SUBST=()
SUBST[MGR_NAME]="$MGR_NAME";           SUBST[MGR_IP]="$MGR_IP"
SUBST[CLUSTER_NAME]="$CLUSTER_NAME";   SUBST[PARTITION]="$PARTITION"
SUBST[SSH_PORT]="$SSH_PORT";           SUBST[LAN_CIDR]="$LAN_CIDR"
SUBST[TIMEZONE]="$TIMEZONE";           SUBST[USER]="$USER_VAL"
SUBST[ACCOUNT]="$ACCOUNT";             SUBST[SHARE_MOUNT]="$SHARE_MOUNT"
SUBST[IMAGES_MOUNT]="$IMAGES_MOUNT";   SUBST[PORTAL_PORT]="$PORTAL_PORT"
SUBST[SLURM_VERSION]="$SLURM_VERSION"; SUBST[GRES_BODY]="$GRES_BODY"
SUBST[NODE_HOSTS]="${NODE_HOSTS%$'\n'}"; SUBST[NODE_LINES]="${NODE_LINES%$'\n'}"
SUBST[EXPORTS_LINES]="$EXPORTS_LINES"; SUBST[GPU_MAP]="$GPU_MAP"
SUBST[FSTAB_MGR_LINES]="$FSTAB_MGR_LINES"; SUBST[FSTAB_GPU_LINES]="$FSTAB_GPU_LINES"
SUBST[SHARE_DEVICE]="$SHARE_DEVICE";   SUBST[IMAGES_DEVICE]="$IMAGES_DEVICE"
SUBST[DEFAULT_GPU_MODEL]="$DEFAULT_GPU_MODEL"
SUBST[MAX_GPUS]="$MAX_GPUS_VAL"
NODE_LIST="$(printf '%s ' "${N_NAME[@]}")"; NODE_LIST="${NODE_LIST% }"
SUBST[NODE_LIST]="$NODE_LIST"

subst_text() {
  local t; t="$(cat)"
  for k in "${!SUBST[@]}"; do t="${t//@@${k}@@/${SUBST[$k]}}"; done
  printf '%s\n' "$t"
}

render() {   # render <模板> <输出文件>
  local tpl="$HERE/templates/$1" out="$2"
  [ -f "$tpl" ] || { echo "  [错误] 缺模板 $tpl" >&2; exit 1; }
  if [ "$PRINT" = 1 ]; then
    echo "----- $out -----"; subst_text < "$tpl"; echo
  else
    mkdir -p "$(dirname "$out")"
    subst_text < "$tpl" > "$out"
  fi
}

write_text() {  # write_text <内容> <输出文件>
  local content="$1" out="$2"
  if [ "$PRINT" = 1 ]; then
    echo "----- $out -----"; printf '%s\n' "$content"; echo
  else
    mkdir -p "$(dirname "$out")"
    printf '%s\n' "$content" > "$out"
  fi
}

# 门户首次建库的套餐种子（schema 见 portalapp/db.py::_load_seed_plans）：
#   name / desc / gpus / gpu_model / cpus / mem_gb / maxtime_h
# 单卡档固定给三档；节点卡数 ≥2/≥3 时自动追加双卡、三卡档。
gen_plans_json() {
  local model="$DEFAULT_GPU_MODEL" maxg="$MAX_GPUS_VAL" out="" sep=""
  _add() { out="${out}${sep}  $1"; sep=$',\n'; }
  _add '{"name": "基础 CPU", "desc": "纯 CPU 小任务（无 GPU），适合调试/轻量任务。", "gpus": 0, "gpu_model": "", "cpus": 2, "mem_gb": 4, "maxtime_h": 48}'
  _add '{"name": "均衡 CPU", "desc": "纯 CPU（无 GPU），适合编译/中等任务。", "gpus": 0, "gpu_model": "", "cpus": 4, "mem_gb": 8, "maxtime_h": 48}'
  if [ -n "$model" ]; then
    _add "{\"name\": \"GPU 入门\", \"desc\": \"单卡 ${model} + 4 核 8G，训练/推理入门配置。\", \"gpus\": 1, \"gpu_model\": \"${model}\", \"cpus\": 4, \"mem_gb\": 8, \"maxtime_h\": 48}"
    _add "{\"name\": \"GPU 标准\", \"desc\": \"单卡 ${model} + 8 核 16G，日常训练推荐。\", \"gpus\": 1, \"gpu_model\": \"${model}\", \"cpus\": 8, \"mem_gb\": 16, \"maxtime_h\": 48}"
    _add "{\"name\": \"GPU 高配\", \"desc\": \"单卡 ${model} + 16 核 24G，重负载训练。\", \"gpus\": 1, \"gpu_model\": \"${model}\", \"cpus\": 16, \"mem_gb\": 24, \"maxtime_h\": 48}"
    if [ "${maxg:-0}" -ge 2 ] 2>/dev/null; then
      _add "{\"name\": \"GPU 双卡\", \"desc\": \"双卡 ${model} + 24 核 96G，多卡并行 / 大模型微调。\", \"gpus\": 2, \"gpu_model\": \"${model}\", \"cpus\": 24, \"mem_gb\": 96, \"maxtime_h\": 48}"
    fi
    if [ "${maxg:-0}" -ge 3 ] 2>/dev/null; then
      _add "{\"name\": \"GPU 三卡\", \"desc\": \"三卡 ${model} + 32 核 112G，单机满卡训练。\", \"gpus\": 3, \"gpu_model\": \"${model}\", \"cpus\": 32, \"mem_gb\": 112, \"maxtime_h\": 24}"
    fi
  fi
  printf '[\n%s\n]\n' "$out"
}

echo "==> 渲染集群: ${CLUSTER_NAME}  节点: ${NODE_LIST}  管理节点: ${MGR_NAME}"
echo "    配置: $CONF"
echo "    输出: $OUT$( [ "$PRINT" = 1 ] && echo ' (仅打印)')"
[ "$PRINT" = 0 ] && [ "$CLEAN" = 1 ] && { rm -rf "$OUT"; echo "    已清空旧输出"; }

# ---- /etc/hosts ----
# 注意：127.0.1.1 必须是**本机**主机名（手册 1.3 的踩坑点），所以每台一份、内容不同
render_hosts() {
  local out="$1" name="$2" tpl="$HERE/templates/hosts.in"
  if [ "$PRINT" = 1 ]; then
    echo "----- $out -----"; subst_text < "$tpl" | sed "s|@@LOCAL_NAME@@|${name}|g"; echo
  else
    mkdir -p "$(dirname "$out")"
    subst_text < "$tpl" | sed "s|@@LOCAL_NAME@@|${name}|g" > "$out"
  fi
}
for n in "${N_NAME[@]}"; do render_hosts "$OUT/etc/hosts.$n" "$n"; done
render_hosts "$OUT/etc/hosts" "${N_NAME[0]}"     # 通用副本以管理节点名生成（仅作参考）

# ---- Slurm ----
render slurm.conf.in       "$OUT/etc/slurm/slurm.conf"
[ "$GRES_MODE" = explicit ] && render gres.conf.explicit.in "$OUT/etc/slurm/gres.conf" \
                            || render gres.conf.auto.in     "$OUT/etc/slurm/gres.conf"

# ---- enroot ----
render enroot.conf.in      "$OUT/etc/enroot/enroot.conf"

# ---- sshd 加固（门户模式必做）----
render sshd-portal-only.conf "$OUT/etc/ssh/sshd_config.d/10-portal-only.conf"

# ---- NFS 服务端 ----
render exports.in          "$OUT/etc/exports"

# ---- fstab 追加行 ----
render fstab.mgr.in        "$OUT/etc/fstab.$MGR_NAME"
for i in "${!N_NAME[@]}"; do
  [ "$i" = 0 ] && continue
  render fstab.gpu.in "$OUT/etc/fstab.${N_NAME[$i]}"
done

# ---- chrony 追加行 ----
render chrony.mgr.in "$OUT/etc/chrony/chrony.conf.append.mgr"
render chrony.gpu.in "$OUT/etc/chrony/chrony.conf.append.gpu"

# ---- 门户站点配置 ----
render cluster-portal-site.conf.in "$OUT/etc/cluster-portal/site.conf"
write_text "$(gen_plans_json)" "$OUT/etc/cluster-portal/plans.json"

# ---- 建号脚本（直接从仓库拷贝，不重复维护）----
if [ "$PRINT" = 0 ]; then
  mkdir -p "$OUT/opt/cluster-admin"
  cp -p "$REPO"/base-cluster/scripts/cluster-admin/*.sh "$OUT/opt/cluster-admin/"
fi

# ---- 文档渲染：markdown 里的 <占位符> → 本集群实值 ----
# 仓库里的 md 是**模板**（<ADMIN> / <GPU01> / <LAN_CIDR> …）；这里产出可直接阅读/交付的
# 实值版本到 out/docs/。
# 注意：仓库文档里仍然保留着少量小写 `admin`，它们**不是主机名**而是
#     · 门户角色名（bootstrap.py 的第 3 个参数、"默认角色即 admin"）
#     · 门户账号名（现场实例里有一个叫 admin 的纯平台管理员）
#     · 历史引文（描述"旧代码把主机名硬编码成 admin"那段排障记录）
#   render_docs.py **不会**碰它们，并把渲染后仍残留的 admin 逐行列进报告供核对。
DOCMAP="$(mktemp)"
{
  GPU_TYPE_VAL=""
  for m in "${N_MODEL[@]}"; do [ -n "$m" ] && { GPU_TYPE_VAL="$m"; break; }; done
  echo "<GPU_TYPE>=${GPU_TYPE_VAL}"
  # 编号规则：第一个 NODE 行 = 管理节点 <ADMIN>；其余节点**按顺序从 <GPU01> 起编号**
  # （管理节点即使自己也插卡，也只算 <ADMIN>，卡数由 <ADMIN_GPUS> 表达）
  gi=0
  for i in "${!N_NAME[@]}"; do
    if [ "$i" = 0 ]; then
      pfx=ADMIN
    else
      gi=$((gi + 1)); pfx="$(printf 'GPU%02d' "$gi")"
    fi
    cpus="${N_CPUS[$i]:-}"; mem="${N_MEM[$i]:-}"
    sock="${N_SOCK[$i]:-1}"; thr="${N_THR[$i]:-2}"; gg="${N_GPUS[$i]:-0}"
    [ -n "$cpus" ] || cpus="待实测"; [ -n "$mem" ] || mem="待实测"
    if [ "$cpus" = "待实测" ]; then cores="待实测"; else cores=$(( cpus / (sock * thr) )); fi
    echo "<${pfx}>=${N_NAME[$i]}"
    echo "<${pfx}_IP>=${N_IP[$i]}"
    echo "<${pfx}_CPUS>=${cpus}"
    echo "<${pfx}_SOCKETS>=${sock}"
    echo "<${pfx}_CORES>=${cores}"
    echo "<${pfx}_THREADS>=${thr}"
    echo "<${pfx}_MEM>=${mem}"
    echo "<${pfx}_GPUS>=${gg}"
  done
  echo "<LAN_CIDR>=${LAN_CIDR}"
  echo "<SSH_PORT>=${SSH_PORT}"
  echo "<CLUSTER_NAME>=${CLUSTER_NAME}"
  echo "<ACCOUNT>=${ACCOUNT}"
  echo "<USER>=${USER_VAL}"
  echo "<TIMEZONE>=${TIMEZONE}"
  echo "<PORTAL_PORT>=${PORTAL_PORT}"
  echo "<SHARE_MOUNT>=${SHARE_MOUNT}"
  echo "<IMAGES_MOUNT>=${IMAGES_MOUNT}"
  echo "<PARTITION>=${PARTITION}"
  echo "<SLURM_VERSION>=${SLURM_VERSION}"
} > "$DOCMAP"
if command -v python3 >/dev/null 2>&1; then
  if [ "$PRINT" = 1 ]; then
    python3 "$HERE/lib/render_docs.py" --repo "$REPO" --out "$OUT/docs" --map "$DOCMAP" --print
  else
    python3 "$HERE/lib/render_docs.py" --repo "$REPO" --out "$OUT/docs" --map "$DOCMAP"
  fi
else
  echo "  [警告] 未找到 python3，跳过文档渲染（只生成配置文件）" >&2
fi
rm -f "$DOCMAP"

# ---- MANIFEST ----
if [ "$PRINT" = 0 ]; then
  {
    echo "# 下发清单（由 provision/render.sh 生成，勿手改）"
    echo
    echo "集群: \`${CLUSTER_NAME}\`　管理节点: \`${MGR_NAME}\`（$( [ "$MGR_IS_COMPUTE" = 1 ] && echo '兼计算' || echo '仅管理')）"
    echo
    echo "## 每台节点（全部 ${#N_NAME[@]} 台，内容相同）"
    echo '```'
    echo "/etc/hosts                       ← out/etc/hosts"
    echo "/etc/slurm/slurm.conf            ← out/etc/slurm/slurm.conf"
    echo "/etc/slurm/gres.conf             ← out/etc/slurm/gres.conf"
    echo "/etc/enroot/enroot.conf          ← out/etc/enroot/enroot.conf（先装好 enroot）"
    echo "/etc/ssh/sshd_config.d/10-portal-only.conf ← out/etc/ssh/sshd_config.d/"
    echo '```'
    echo
    echo "## 仅管理节点 \`${MGR_NAME}\`"
    echo '```'
    echo "/etc/exports                     ← out/etc/exports"
    echo "/etc/fstab                       ← 追加 out/etc/fstab.${MGR_NAME} 里的数据盘行"
    echo "/etc/chrony/chrony.conf          ← 追加 out/etc/chrony/chrony.conf.append.mgr"
    echo "/etc/cluster-portal/site.conf    ← out/etc/cluster-portal/site.conf"
    echo "/etc/cluster-portal/plans.json   ← out/etc/cluster-portal/plans.json"
    echo "/opt/cluster-admin/*.sh          ← out/opt/cluster-admin/"
    echo '```'
    echo
    echo "## 仅计算节点"
    echo '```'
    for i in "${!N_NAME[@]}"; do
      [ "$i" = 0 ] && continue
      echo "${N_NAME[$i]}: /etc/fstab ← 追加 out/etc/fstab.${N_NAME[$i]}；/etc/chrony/chrony.conf ← 追加 out/etc/chrony/chrony.conf.append.gpu"
    done
    echo '```'
    echo
    echo "## 文档（已渲染成本集群实值，直接给部署人员看）"
    echo '```'
    echo "out/docs/README.md                                   ← 仓库总览"
    echo "out/docs/base-cluster/all-in-one-cluster-manual.md    ← 底层集群部署手册（1–8 章）"
    echo "out/docs/oa/01-部署手册.md / 02-管理手册.md / 03-使用手册.md"
    echo "out/docs/oa/cluster-portal/README.md                  ← 门户自身文档"
    echo "out/docs/_REPLACEMENT-REPORT.md                       ← 替换统计 + 残留 admin 核对清单"
    echo '```'
    if [ -n "$NEED_MEASURE" ]; then
      echo
      echo "## ⚠️ 待实测回填"
      echo "以下节点未提供 \`cpus\`/\`mem_mb\`，slurm.conf 里是占位符，需在对应机器上执行"
      echo '\`slurmd -C | head -1\` 取值后填回 cluster.conf 再重新渲染：'
      echo '```'
      for n in $NEED_MEASURE; do echo "  ${n}"; done
      echo '```'
    fi
  } > "$OUT/MANIFEST.md"
fi

echo
if [ "$PRINT" = 0 ]; then
  echo "==> 完成，产物："
  (cd "$OUT" && find . -type f | sort | sed 's|^\./|    |')
  echo
  echo "    下发清单: $OUT/MANIFEST.md"
fi
[ -n "$NEED_MEASURE" ] && echo "    ⚠️ 有节点缺 cpus/mem_mb，slurm.conf 中是占位符：$NEED_MEASURE"
exit 0
