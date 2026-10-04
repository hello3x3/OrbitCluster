#!/usr/bin/env bash
# e2e_security.sh —— 在管理节点以 root 执行的门户安全端到端回归。
#
# 验证两件事（这正是产品模型的核心）：
#   ✅ 用户能用自己的密钥 ssh 进【自己的容器】
#   ❌ 同一个用户 ssh 登【宿主机】必须被拒绝
# 外加：助手不再被符号链接/保留账号绕过。
#
# 全程使用保留 UID 号段 59000-59999，结束必清理（成功失败都清）。
set -u
ok=0; bad=0
say(){ printf '%s\n' "$*"; }
pass(){ say "  [OK]   $*"; ok=$((ok+1)); }
fail(){ say "  [FAIL] $*"; bad=$((bad+1)); }

CTL=/usr/local/sbin/portal-ctl
TAG="e2e$(date +%H%M%S)"
KEY=/root/.e2e-$TAG.key
PORT=2877$((RANDOM % 9))
UID_T=595$((RANDOM % 90 + 10))

cleanup(){
  say "== 清理 =="
  "$CTL" unprovision-user "$TAG" >/dev/null 2>&1 || true
  id "$TAG" >/dev/null 2>&1 && { userdel -r "$TAG" 2>/dev/null; userdel "$TAG" 2>/dev/null; true; }
  setquota -u "$TAG" 0 0 0 0 /share 2>/dev/null || true
  rm -f "$KEY" "$KEY.pub"
  # 确认清干净
  if id "$TAG" >/dev/null 2>&1; then say "  [FAIL] 测试账号残留：$TAG"; bad=$((bad+1));
  else say "  [OK]   测试账号已清理：$TAG"; ok=$((ok+1)); fi
}
trap cleanup EXIT

say "=== 0) 准备测试账号 $TAG (uid=$UID_T) 与密钥 ==="
ssh-keygen -q -t ed25519 -N '' -f "$KEY" >/dev/null
PUB="$(cat "$KEY.pub")"
"$CTL" provision-user "$TAG" 100G --uid "$UID_T" || { say "provision 失败，终止"; exit 1; }
"$CTL" set-keys "$TAG" <<< "[\"$PUB\"]" || { say "set-keys 失败，终止"; exit 1; }
pass "建号 + 登记公钥完成"

say "=== 1) 【核心】同一把私钥 ssh 宿主机 —— 必须被拒绝 ==="
HOSTSSH=$(ssh -p 2180 -i "$KEY" -o BatchMode=yes -o ConnectTimeout=8 \
          -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
          "$TAG@10.71.106.239" 'echo HOST_SHELL_OBTAINED' 2>&1 || true)
if printf '%s' "$HOSTSSH" | grep -q HOST_SHELL_OBTAINED; then
  fail "用户竟然登上了宿主机！输出: $HOSTSSH"
else
  pass "宿主机登录被拒绝: $(printf '%s' "$HOSTSSH" | tail -1)"
fi

say "=== 2) 助手不再被保留账号/符号链接绕过 ==="
R=$("$CTL" set-keys root <<< "[\"$PUB\"]" 2>&1 || true)
printf '%s' "$R" | grep -q '"ok": false' && pass "set-keys root 被拒: $R" || fail "set-keys root 未被拒: $R"
R=$("$CTL" get-keys root 2>&1 || true)
printf '%s' "$R" | grep -q '"ok": false' && pass "get-keys root 被拒" || fail "get-keys root 未被拒: $R"

HOMEDIR="$(getent passwd "$TAG" | cut -d: -f6)"
ln -sf /etc/cluster-portal/users.passwd "$HOMEDIR/.portal/logs/999.out"
R=$("$CTL" log "$TAG" 999 2>&1 || true)
if printf '%s' "$R" | grep -q '"ok": false'; then pass "软链日志被拒（防任意 root 文件读）: $(printf '%s' "$R" | head -c 120)"
else fail "软链日志未被拒，读到: $(printf '%s' "$R" | head -c 200)"; fi
rm -f "$HOMEDIR/.portal/logs/999.out"

ln -sfn /etc "$HOMEDIR/.ssh-link-test" 2>/dev/null || true
BEFORE=$(md5sum /root/.ssh/authorized_keys 2>/dev/null | cut -d' ' -f1)
"$CTL" set-keys "$TAG" <<< "[\"$PUB\"]" >/dev/null 2>&1 || true
AFTER=$(md5sum /root/.ssh/authorized_keys 2>/dev/null | cut -d' ' -f1)
[ "$BEFORE" = "$AFTER" ] && pass "/root/.ssh/authorized_keys 未被改写" || fail "root 的 authorized_keys 被改写了！"
rm -f "$HOMEDIR/.ssh-link-test"

say "=== 3) 提交容器作业（端口 $PORT）==="
IMG=/share/images/cuda12.8.0-devel-ubuntu24.04.sqsh
SPEC=$(printf '{"user":"%s","node":"","image":"%s","gpus":1,"cpus":4,"mem_gb":8,"walltime":"00:30:00","port":%d,"job_name":"%s"}' \
       "$TAG" "$IMG" "$PORT" "$TAG")
SUB=$(printf '%s' "$SPEC" | "$CTL" submit 2>&1) || true
say "  submit -> $(printf '%s' "$SUB" | head -c 200)"
JOB=$(printf '%s' "$SUB" | sed -n 's/.*"job_id": \([0-9]*\).*/\1/p')
[ -n "$JOB" ] && pass "作业已提交 job_id=$JOB" || { fail "提交失败"; exit 1; }

NODE=""
for i in $(seq 1 60); do
  ST=$(squeue -h -j "$JOB" -o '%T|%N' 2>/dev/null || true)
  case "$ST" in
    RUNNING*) NODE="${ST#*|}"; [ -n "$NODE" ] && break ;;
    "") break ;;
  esac
  sleep 5
done
[ -n "$NODE" ] && pass "作业在 $NODE 上 RUNNING" || { fail "作业未运行 (state=$ST)"; exit 1; }
NODEIP=$(getent hosts "$NODE" | awk '{print $1}' | head -1)

say "=== 4) 【核心】ssh 进自己的容器 —— 必须成功（且确实在容器里、不是 root）==="
IN=""
for i in $(seq 1 40); do
  IN=$(ssh -p "$PORT" -i "$KEY" -o BatchMode=yes -o ConnectTimeout=5 \
       -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "$TAG@$NODEIP" \
       'echo CONTAINER_OK uid=$(id -u) user=$(id -un) img=$(test -f /opt/start_ssh.sh && echo yes || echo no) gpu=$(nvidia-smi -L 2>/dev/null | head -1)' 2>&1 || true)
  printf '%s' "$IN" | grep -q CONTAINER_OK && break
  sleep 5
done
if printf '%s' "$IN" | grep -q CONTAINER_OK; then
  pass "容器登录成功: $IN"
  printf '%s' "$IN" | grep -q "uid=0 " && fail "容器内是 root（不该发生）" || pass "容器内非 root"
  printf '%s' "$IN" | grep -q "img=yes" && pass "确认在容器内（有 /opt/start_ssh.sh）" || say "  [i] 未取到镜像标记"
else
  fail "容器登录失败: $(printf '%s' "$IN" | tail -2)"
fi

say "=== 5) 停机 + 清理 ==="
"$CTL" kill "$TAG" "$JOB" >/dev/null 2>&1 || true
sleep 2
say ""
say "=== 结果: 通过 $ok 项，失败 $bad 项 ==="
exit $((bad > 0))
