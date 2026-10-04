# base-cluster/images —— 容器镜像构建源

本目录存放集群 `<IMAGES_MOUNT>/*.sqsh` 的**构建源码**。`.sqsh` 本体每个 7–12 GB，只存在于集群上、
不进仓库（根 `.gitignore` 已屏蔽 `*.sqsh`）；这里保存的是「怎么造出来的」。

## 目录约定

```
base-cluster/images/
├── README.md                          # 本文件：目录约定 / enroot 启动 / 构建部署 / 用法 / 加固要求
└── cuda12.8.0-devel-ubuntu24.04/      # 一个镜像 = 一个目录（= docker build 的构建上下文）
    ├── Dockerfile
    └── start_ssh.sh                   # ★ 容器入口脚本：Dockerfile 会 COPY 成 /opt/start_ssh.sh
```

- 目录名就是镜像名；新增镜像照抄这个目录，改 `FROM` 与镜像名即可。
- `start_ssh.sh` 必须放在**镜像目录里**（`COPY start_ssh.sh` 取的是构建上下文），不要放到上一层。

## 镜像清单

| 镜像（=`<IMAGES_MOUNT>/*.sqsh`） | 基座 | 说明 |
|---|---|---|
| `cuda12.8.0-devel-ubuntu24.04` | `nvidia/cuda:12.8.0-devel-ubuntu24.04` | CUDA 12.8 devel（无系统级 cuDNN）+ Ubuntu 24.04，带 sshd / miniconda / USTC 源。要求宿主驱动 >= 570 |

## enroot 如何启动

### enroot 只做「导入 / 运行」，不做构建

- **不能从 Dockerfile 构建**：Dockerfile 里的 `RUN` 必须由 docker / buildah / podman 执行；
  enroot 只把**已经构建好**的镜像转成 `.sqsh`（`enroot import`）。
- 导入来源：
  `docker://[USER@][REGISTRY#]IMAGE[:TAG]`、`dockerd://IMAGE[:TAG]`、`podman://IMAGE[:TAG]`。
  本版本**没有** `docker-archive://`；`dockerd://` 是直接读本机 docker daemon，不是读 stdin。
- 子命令：`import / create / start / exec / list / export / remove`；
  `enroot start` 可以吃 `.sqsh` 文件本身，也可以吃 `enroot create` 出来的名字。

### 集群里真正跑的路径：Slurm + pyxis

门户与用户都不直接调 enroot，而是提交 `srun`/`sbatch`，由 pyxis 插件翻译成 enroot 调用：

```bash
srun --container-image=<IMAGES_MOUNT>/<镜像>.sqsh \
     --container-env=SSH_PORT \
     --container-mounts=/share/home/<用户>:/share/home/<用户> \
     --wrap /opt/start_ssh.sh
```

节点上 `/etc/slurm/plugstack.conf.d/pyxis.conf`：
`runtime_path=/run/pyxis execute_entrypoint=0 container_scope=job sbatch_support=1 use_enroot_load=1 use_squashfuse=1`

pyxis/enroot 的实测行为（写镜像时都要考虑）：

| 行为 | 说明 |
|---|---|
| 镜像 ENTRYPOINT | **不执行**（`execute_entrypoint=0`），容器里跑什么由 `--wrap` 决定 —— 所以镜像不需要 ENTRYPOINT |
| 镜像 ENV | 会注入容器（`PATH` / `CONDA_PKGS_DIRS` 等生效）；但镜像里**不能**设 `ENV SSH_PORT`，否则会盖住 host 传入值 |
| 网络 | **共享宿主机网络命名空间**（实测容器进程 `ns/net` 与宿主 PID1 相同）→ 容器里的服务就是宿主机端口；<1024 需要 root。`enroot --net` 会变成只有 loopback，别用 |
| 身份 | `ENROOT_REMAP_ROOT=n`：容器内 uid = 提交者；enroot 的 `10-shadow.sh` hook 把该 uid 的 passwd 条目注入容器（sshd 据此识别用户名） |
| GPU | `98-nvidia.sh` hook 调 `nvidia-container-cli` 注入宿主驱动库与设备节点；镜像里**不带** `nvidia-smi` |
| 挂载 | `--container-mounts` 的宿主路径必须在**每台节点**上真实存在，否则容器起不来（门户提交前逐节点核对） |
| 运行时目录 | `ENROOT_RUNTIME_PATH=/run/enroot/user-$(id -u)`、`ENROOT_DATA_PATH=/scratch/enroot-data/user-$(id -u)`（本地盘，每作业一份可写 rootfs）、`ENROOT_CACHE_PATH=/share/enroot-cache/group-$(id -g)`（镜像导入缓存） |

### 手工排障：直接用 enroot

```bash
enroot import -o /tmp/img.sqsh docker://<registry>/<image>:<tag>    # 或 dockerd:// / podman://
enroot start -w -m /share/home/<用户>:/share/home/<用户> dbg bash   # -w = rootfs 可写
```

- **不要用 `-r/--root`**：remap 出的 root 会让 Ubuntu sshd 拒绝启动，也是本仓库的安全红线。
- **用完即毁**：`enroot start <镜像>`（不先 `create`）用的是一次性 rootfs，跑完自动删，等价
  `docker run --rm`；`create` 出来的 rootfs 是持久的（在 `$ENROOT_DATA_PATH` 下），得自己
  `enroot remove -f <名字>`。走 Slurm/pyxis 则无需关心：`container_scope=job`，容器随作业清理。

## 构建 → 导入 → 部署

```bash
cd base-cluster/images/cuda12.8.0-devel-ubuntu24.04

# 1) 构建（enroot 不能构建，必须用 docker / podman / buildah）
docker build -t cuda12.8.0-devel-ubuntu24.04:latest .

# 2) 转成 pyxis 用的 .sqsh（enroot 只做导入，三选一）
enroot import -o /tmp/cuda12.8.sqsh docker://<registry>/cuda12.8.0-devel-ubuntu24.04:latest   # 走 registry
enroot import -o /tmp/cuda12.8.sqsh dockerd://cuda12.8.0-devel-ubuntu24.04:latest             # 本机有 docker daemon
enroot import -o /tmp/cuda12.8.sqsh podman://cuda12.8.0-devel-ubuntu24.04:latest              # 本机有 podman

# 3) 放到管理节点（root:root 644；门户按 *.sqsh 自动扫描，文件名随意）
scp /tmp/cuda12.8.sqsh root@<ADMIN>:<IMAGES_MOUNT>/
ssh root@<ADMIN> 'chown root:root <IMAGES_MOUNT>/cuda12.8.sqsh && chmod 644 <IMAGES_MOUNT>/cuda12.8.sqsh'

# 4) 冒烟：门户「申请资源」选该镜像起一个 GPU 容器 → 用登记的私钥
#    ssh -p <端口> <用户名>@<节点IP> 进去看 nvidia-smi
```

> `start_ssh.sh` 已是加固版，重建时不要换回旧的那份（见下方「加固要求」）。
> 换镜像后记得同步更新本文件「镜像清单」表里对镜像的说明（哪些内置了 sshd、能否交互 SSH）。

## 用法（srun / 公钥 / host key）

要点：

- 环境变量用 `--container-env=SSH_PORT` 传入；镜像内不要设 `ENV SSH_PORT`
- pyxis 不执行镜像 ENTRYPOINT，显式把 `/opt/start_ssh.sh` 作为命令传入最可靠
- 容器始终以**提交者身份**运行；**不要**加 `--container-remap-root`
- sshd 端口 <1024 需要 root —— 不要为此 remap root，直接用 >=1024 的端口（不传 `SSH_PORT` 时统一默认 **52300**）

```bash
# 1) 非 root + 持久化 host key（指纹跨任务稳定）
export SSH_PORT=3333
export SSH_HOSTKEY_DIR=/share/home/<用户>/.ssh-hostkeys
srun --container-image=<镜像> \
  --container-env=SSH_PORT,SSH_HOSTKEY_DIR \
  --container-mounts=/share/home/<用户>:/share/home/<用户> /opt/start_ssh.sh

# 2) 挂整个 home（sshd 读 passwd home 下的 ~/.ssh/authorized_keys）
srun --container-image=<镜像> --container-env=SSH_PORT \
  --container-mounts=/share/home/<用户>:/share/home/<用户> /opt/start_ssh.sh

# 3) 不挂 home：只挂 authorized_keys 单文件
export SSH_PORT=2222
export SSH_AUTHORIZED_KEYS=/etc/ssh/authorized_keys
srun --container-image=<镜像> \
  --container-env=SSH_PORT,SSH_AUTHORIZED_KEYS \
  --container-mounts=/share/home/<用户>/.ssh/authorized_keys:/etc/ssh/authorized_keys:ro /opt/start_ssh.sh

# 4) 起 sshd 之外还要交互 shell：把命令追加在后面
srun --pty --container-image=<镜像> --container-env=SSH_PORT \
  --container-mounts=$HOME:$HOME /opt/start_ssh.sh bash -l
```

脚本起来后会在 srun 输出里打印实际监听端口与连接示例：

```
 [start_ssh] sshd 实际监听端口: 3333 (PID=xxx)
 [start_ssh] 连接示例: ssh -p 3333 <用户名>@<节点IP>
```

- 需要 GPU 时记得 `--gres=gpu:1`（集群 cgroup 限制下，不分配看不到 `/dev/nvidia*`，`nvidia-smi` 报 No devices 属正常）。
- 报 `nvidia-container-cli: requirement error: unsatisfied condition: cuda>=xx` → 节点驱动支持的 CUDA 上限低于镜像大版本（CUDA 12.8 需要驱动 >= 570）。
- 直接 `docker run`（可选）：`docker run -it --rm -e SSH_PORT=2222 --gpus all <镜像> /opt/start_ssh.sh`。

### host key 与 `.ssh-hostkeys`

- `$HOME/.ssh-hostkeys`（可用 `SSH_HOSTKEY_DIR` 覆盖）是 `start_ssh.sh` 自建的 **sshd host key 目录**，
  既不是 OpenSSH 标准路径，也不是 authorized_keys：
  - root 模式默认不生成它 —— 直接用镜像内置 `/etc/ssh/ssh_host_*`（同镜像指纹恒定）
  - 只有「非 root」或「显式设置了 `SSH_HOSTKEY_DIR`」时才生成/复用它
- 只有容器**实际运行过** `start_ssh.sh`、且写入的是**挂载盘**，才能在宿主机对应路径看到它
  （如 `/share/home/<用户>/.ssh-hostkeys/`，含 `ssh_host_ed25519_key[.pub]`、`ssh_host_rsa_key[.pub]`；
  隐藏目录要 `ls -a` 查看）。

### 改 Dockerfile 时的两个坑

- `/run/sshd` 必须是 **root 0755**：`chmod 1777 /run/sshd` 会让 root 模式的 sshd 直接拒绝启动；镜像里只 `mkdir -p /run/sshd`。
- 非 root 模式读不到 `/etc/shadow`，口令登录本来就不成立：`start_ssh.sh` 会自动只认公钥（并 `UsePAM=no`）。

## ⚠️ 加固要求（重建时务必遵守）

1. `start_ssh.sh` 必须加固：任何身份都强制
   `PermitRootLogin=no`（**禁止 root 登录 ssh，有公钥也不行**）+ `PasswordAuthentication=no`
   + `PermitEmptyPasswords=no` + `KbdInteractiveAuthentication=no`
   + `ChallengeResponseAuthentication=no` + `PubkeyAuthentication=yes`
   + `AuthenticationMethods=publickey`。
   Dockerfile 里另有同一套 `sshd_config.d/00-portal-hardening.conf`（双保险）；
   root 口令保留，但只服务容器内本地场景（控制台 / `su`）。
   原因：上游镜像的 `sshd_config` 是 `PermitRootLogin yes` + `PermitEmptyPasswords yes`，且 root 有固定口令，
   而 enroot **共享宿主机网络命名空间** —— 容器一旦以 root 运行，就等于把一个「口令公开的 sshd」暴露给整个内网。
2. **不要给 sshd 加 `--container-remap-root`** —— 硬规定，不是「建议」：
   本仓库约定容器**始终以提交者身份**运行；门户在 `oa/cluster-portal/deploy/portal-ctl` 里硬拦截该参数，
   提交参数里永远不会出现；上游还提到 userns remap 出来的 root 会让 Ubuntu sshd 直接拒绝服务
   （`/run/sshd must be owned by root` 等），那是另一个症状，同样别用。
3. 节点上 `/etc/enroot/enroot.conf` 的 `ENROOT_REMAP_ROOT` 保持 `n`；
   `oa/scripts/verify-install.sh` 会逐台检查并报 FAIL。
4. 构建时**不要**在镜像里写 `ENV SSH_PORT`（会盖住 pyxis 传入的值）。
5. 镜像里**不要**自动启动 sshd（例如 entrypoint 里 `service ssh start`）：那会在共享网络命名空间上占默认 22 端口。
   本集群的 sshd 一律由 `/opt/start_ssh.sh` 按 `SSH_PORT` 拉起。
