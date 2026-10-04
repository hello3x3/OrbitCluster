#!/bin/bash
# start_ssh.sh —— 在 SSH_PORT 端口启动 sshd；确认监听后打印连接信息；无额外参数时保持任务存活。
# 镜像构建源：Dockerfile 把它 COPY 成 /opt/start_ssh.sh。
#
# 用法（Slurm + pyxis/enroot，显式传入命令，不依赖 ENTRYPOINT）:
#   export SSH_PORT=3333                        # 普通用户用 >=1024；缺省 52300
#   srun --container-image=<镜像> --container-env=SSH_PORT \
#        --container-mounts=$HOME:$HOME /opt/start_ssh.sh
#   追加参数则在 sshd 之外执行它们（如交互 shell）:
#   srun --pty --container-image=<镜像> --container-env=SSH_PORT \
#        --container-mounts=$HOME:$HOME /opt/start_ssh.sh bash -l
#   docker: docker run -d -e SSH_PORT=2222 <镜像> /opt/start_ssh.sh
#
# 认证：任何身份都禁止 root 登录、关闭全部密码认证，只认公钥。
#   公钥默认取 passwd home 的 ~/.ssh/authorized_keys；SSH_AUTHORIZED_KEYS=<容器内路径>
#   可指到单独挂进来的公钥文件（不必挂整个 $HOME）。
# host key：给了 SSH_HOSTKEY_DIR=<挂载盘路径> 就生成/复用到该目录（指纹跨任务稳定）；
#   否则 root 用镜像内置密钥，非 root 用 $HOME/.ssh-hostkeys。
# 拿不到自己的 pidfile（例如端口被占用）时直接 exit 1。
set -u

# ---------- 端口解析 ----------
SSH_PORT="${SSH_PORT:-}"
if [ -z "$SSH_PORT" ]; then
    SSH_PORT=52300
    echo "[start_ssh] 未设置 SSH_PORT，使用默认端口 $SSH_PORT"
fi
case "$SSH_PORT" in
    (*[!0-9]*|'')
        echo "[start_ssh] 错误: SSH_PORT='$SSH_PORT' 不是合法端口号" >&2
        exit 2 ;;
esac
if [ "$SSH_PORT" -lt 1 ] || [ "$SSH_PORT" -gt 65535 ]; then
    echo "[start_ssh] 错误: SSH_PORT=$SSH_PORT 超出范围 (1-65535)" >&2
    exit 2
fi
if [ "$(id -u)" -ne 0 ] && [ "$SSH_PORT" -lt 1024 ]; then
    echo "[start_ssh] 错误: 非 root 用户无法绑定特权端口 $SSH_PORT。" >&2
    echo "[start_ssh] 请改用 >=1024 的端口（不要用 --container-remap-root 去换 root）" >&2
    exit 1
fi

SSHD=/usr/sbin/sshd
PIDFILE=/tmp/sshd.pid
OPTS=(-e -o PidFile="$PIDFILE" -p "$SSH_PORT")

# root 模式下 sshd 要求 /run/sshd 存在且归 root 所有
if [ "$(id -u)" -eq 0 ]; then
    mkdir -p /run/sshd
fi

# ---------- 认证策略（root / 非 root 一致）----------
OPTS+=(-o PermitRootLogin=no \
       -o PasswordAuthentication=no \
       -o PermitEmptyPasswords=no \
       -o KbdInteractiveAuthentication=no \
       -o ChallengeResponseAuthentication=no \
       -o PubkeyAuthentication=yes \
       -o AuthenticationMethods=publickey)
if [ "$(id -u)" -ne 0 ]; then
    OPTS+=(-o UsePAM=no)
    echo "[start_ssh] 非 root 模式: 仅公钥认证"
else
    echo "[start_ssh] root 模式: 已禁止 root 登录 + 只认公钥（口令/空口令全关）"
fi

# ---------- host keys ----------
# 默认：root 用镜像内置密钥；非 root 生成到 $HOME/.ssh-hostkeys。
# 设置了 SSH_HOSTKEY_DIR 则一律生成/复用该目录（指纹跨任务稳定）。
USE_PERSISTENT=0
if [ -n "${SSH_HOSTKEY_DIR:-}" ]; then
    USE_PERSISTENT=1
else
    for f in /etc/ssh/ssh_host_rsa_key /etc/ssh/ssh_host_ed25519_key; do
        [ -r "$f" ] || { USE_PERSISTENT=1; break; }
    done
fi
if [ "$USE_PERSISTENT" -eq 1 ]; then
    KEYDIR="${SSH_HOSTKEY_DIR:-$HOME/.ssh-hostkeys}"
    mkdir -p "$KEYDIR" 2>/dev/null || KEYDIR=/tmp/ssh-hostkeys
    mkdir -p "$KEYDIR"
    chmod 700 "$KEYDIR"
    [ -f "$KEYDIR/ssh_host_ed25519_key" ] || \
        ssh-keygen -q -t ed25519 -N '' -f "$KEYDIR/ssh_host_ed25519_key"
    [ -f "$KEYDIR/ssh_host_rsa_key" ] || \
        ssh-keygen -q -t rsa -b 4096 -N '' -f "$KEYDIR/ssh_host_rsa_key"
    chmod 600 "$KEYDIR"/ssh_host_*_key
    OPTS+=(-o HostKey="$KEYDIR/ssh_host_ed25519_key" -o HostKey="$KEYDIR/ssh_host_rsa_key")
    if [ -n "${SSH_HOSTKEY_DIR:-}" ]; then
        echo "[start_ssh] 使用持久化 host key: $KEYDIR/ (跨任务指纹稳定)"
    else
        echo "[start_ssh] 已生成 host key: $KEYDIR/ (此目录持久化则指纹稳定)"
    fi
fi

# ---------- 公钥文件 ----------
# 默认读 passwd home 的 ~/.ssh/authorized_keys；
# SSH_AUTHORIZED_KEYS=<容器内绝对路径> 可指到单独挂进来的公钥文件。
if [ -n "${SSH_AUTHORIZED_KEYS:-}" ]; then
    OPTS+=(-o AuthorizedKeysFile="$SSH_AUTHORIZED_KEYS")
    if [ -r "$SSH_AUTHORIZED_KEYS" ]; then
        echo "[start_ssh] 使用自定义公钥文件: $SSH_AUTHORIZED_KEYS"
    else
        echo "[start_ssh] 警告: SSH_AUTHORIZED_KEYS=$SSH_AUTHORIZED_KEYS 不存在或不可读，认证将全部失败" >&2
    fi
fi

# ---------- 启动 sshd ----------
"$SSHD" "${OPTS[@]}"
RC=$?
if [ "$RC" -ne 0 ]; then
    echo "[start_ssh] sshd 启动失败 (exit=$RC)，请检查上方日志" >&2
    exit 1
fi

# ---------- 确认 sshd 真的起来了 ----------
# 只认 sshd 自己写的 pidfile：它绑定失败时父进程仍返回 0，而 /proc/net/tcp 是整个
# 网络命名空间（与宿主机共享）的全量表 —— 别人占着同端口也会显示在听，两者都不能用。
PID=""
for _ in $(seq 1 100); do
    if [ -s "$PIDFILE" ]; then
        PID=$(cat "$PIDFILE")
        kill -0 "$PID" 2>/dev/null && break
    fi
    sleep 0.1
done

if [ -z "$PID" ] || ! kill -0 "$PID" 2>/dev/null; then
    echo "[start_ssh] 启动失败: sshd 没能监听端口 $SSH_PORT（多半已被别的进程占用）" >&2
    echo "[start_ssh] 启动失败: 本容器与宿主机共享网络，请换一个端口重试" >&2
    exit 1
fi

# 双保险：再从 /proc/net/tcp 确认端口在听
HEX=$(printf '%04X' "$SSH_PORT")
LISTENING=0
for _ in $(seq 1 100); do
    if awk -v p=":$HEX" '$4=="0A" && index($2,p){f=1} END{exit !f}' \
            /proc/net/tcp /proc/net/tcp6 2>/dev/null; then
        LISTENING=1
        break
    fi
    kill -0 "$PID" 2>/dev/null || break
    sleep 0.1
done

if [ "$LISTENING" -eq 1 ]; then
    IP=$(hostname -I 2>/dev/null | awk '{print $1}')
    NODE="${SLURMD_NODENAME:-$(hostname 2>/dev/null)}"
    echo "============================================================"
    echo " [start_ssh] sshd 实际监听端口: $SSH_PORT (PID=$PID)"
    echo " [start_ssh] 连接示例(节点名): ssh -p $SSH_PORT $(id -un)@${NODE:-<节点名>}"
    echo " [start_ssh] 连接示例(节点IP): ssh -p $SSH_PORT $(id -un)@${IP:-<节点IP>}"
    echo "============================================================"
else
    echo "[start_ssh] 启动失败: 端口 $SSH_PORT 未能确认监听（PID=$PID）" >&2
    exit 1
fi

# ---------- 保持任务存活 / 转交额外命令 ----------
if [ "$#" -gt 0 ]; then
    exec "$@"
fi
trap 'kill "$PID" 2>/dev/null' TERM INT EXIT
while kill -0 "$PID" 2>/dev/null; do
    sleep 5
done
exit 0
