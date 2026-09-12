# 站点档案：3090-2node

第二套已落地的 OrbitCluster 实例。本目录是**站点无关的门户代码**在换集群时唯一需要准备的东西。

> ## ⚠️ 本目录是**脱敏模板**，不可直接拷到机器上使用
>
> 为与仓库「可交付、不含内网标识」的定位一致，本目录里所有**主机名与 IP 都已替换为占位符**
> （真实值只存在于目标机上）。用之前必须先把占位符换成本集群的真实值，**否则 Slurm/NFS 直接起不来**。
>
> | 占位符 | 含义 | 本集群实值 |
> |---|---|---|
> | `<ADMIN>` | 管理节点主机名（本集群它同时是计算节点） | 见目标机 `hostname` |
> | `<GPU02>` | 计算节点主机名 | 同上 |
> | `<ADMIN_IP>` / `<GPU02_IP>` | 两个节点的静态 IP | 见目标机 `ip -br addr` |
> | `<LAN_CIDR>` | 内网网段（NFS 导出与 chrony 放行用） | 见目标机 `ip route` |
> | `<OLD_ADMIN_IP>` | 迁镜像时用到的旧集群管理节点 | —— |
>
> 反过来说：**生产机上的这些文件必须保留真实值**，仓库里的这份永远只是模板。
> 两者内容不同是**设计如此**，不是失同步；核对时请先把占位符还原再逐字节比对。

| 角色 | 主机 | IP | hardware |
|---|---|---|---|
| 管理 + 计算 + NFS 服务端 + 门户 | `<ADMIN>` | `<ADMIN_IP>` | 2×Xeon Silver 4210 / 40 线程 / 125G / 3×RTX 3090 |
| 计算 | `<GPU02>` | `<GPU02_IP>` | i9-10980XE / 36 线程 / 125G / 3×RTX 3090 |

- 系统：Ubuntu 24.04.5 LTS（kernel 6.8.0-139），驱动 595.71.05（open），无 CUDA 工具链（仅 NVML 头用于编译）
- **sshd 端口 2022**（非标准 22）
- 集群名 `lab`，分区 `gpu`，每节点 `Gres=gpu:3090:3`（共 6 张卡）
- `/share` = `<ADMIN>` 的 `sdb`（3.6T HDD，家目录/数据集/缓存）
- **`/share/images` = `<ADMIN>` 的 `nvme0n1`（1.8T NVMe，独立文件系统 + 独立 NFS 导出）**

## 本目录文件

| 文件 | 目标位置 | 说明 |
|---|---|---|
| `site.conf` | `/etc/cluster-portal/site.conf` | ssh 端口 2022、GPU 型号映射、套餐种子路径 |
| `plans.json` | `/etc/cluster-portal/plans.json` | 6 个套餐（2 CPU + 4 GPU，含双卡/三卡） |
| `slurm.conf` | `/etc/slurm/slurm.conf` | 节点行取自各机 `slurmd -C` 实测（含占位符） |
| `gres.conf` | `/etc/slurm/gres.conf` | **显式** 3 卡，而非 `AutoDetect=nvml`（原因见下） |
| `e2e_verify.py` | 在管理节点上执行（不必安装） | 端到端验收脚本，需用 `E2E_MGT`/`E2E_PEER` 传真实节点名 |
| `host-config/` | 见该目录的 `README.md` | 现场系统配置文件（fstab/exports/enroot/plugstack… 已脱敏） |

安装时一次带入（站点目录里的占位符需先换成实值）：

```bash
bash /tmp/cluster-portal-src/deploy/install.sh /tmp/cluster-portal-src 8000 /tmp/site-3090
```

### 端到端验收

`e2e_verify.py` 走**真实门户 HTTP 流程**（只用标准库），在管理节点以 root 执行：

```bash
E2E_MGT=<真实管理节点名> E2E_PEER=<真实计算节点名> python3 e2e_verify.py
```

覆盖 27 项断言：节点 IP 解析 → 管理员登录 → 自动建号（两节点 UID 一致 / 家目录 /
`/share` 配额 / sacctmgr 关联）→ 用户登录与公钥+端口登记 → 申请单卡 GPU 容器 →
**ssh 进容器见 RTX 3090 且 `/share/home` 已挂载** → 三卡套餐容器内见 3 张卡 →
停机 → 删号并验证两节点账号、家目录均已清理。

> ⚠️ **测容器必须重试**：Slurm 状态变 `RUNNING` ≠ 容器就绪。Pyxis 还要解包/挂载
> `.sqsh`（9.7G 镜像实测数十秒），这期间端口是 `Connection refused`。
> 脚本里 `container_ssh_retry()` 就是为此；写别的测试时别忘了这一点。

## 与旧集群（admin / gpu02 / gpu03）的关键差异

| 项 | 旧集群 | 本站点 | 影响 |
|---|---|---|---|
| 节点 | admin + gpu02 + gpu03 | <ADMIN> + <GPU02> | 节点名由 `sinfo` 探测，代码不用改 |
| 每节点卡数 | 1× RTX 3060 | **3× RTX 3090** | 放开多卡套餐（提交侧原本写死 `gpus==1`） |
| sshd 端口 | 2180 | **2022** | 必须写进 `site.conf` |
| 镜像目录 | `/share/images`（在 sdb 上） | **独立 NVMe 文件系统** | NFS 需**单独导出 + 单独挂载**（见下） |
| 个人镜像配额 | 计入 `/share` 配额 | **不计入**（跨文件系统） | 门户展示的用量不含镜像 |

## 三个必须知道的坑（都已在本次部署中处理）

### 1. NFS 嵌套挂载点默认「隐藏」

`exports(5)` 原文：*"if it just mounts the parent, it will see an empty directory at the place
where the other filesystem is mounted. That filesystem is 'hidden'."*

`/share/images` 是挂在 `/share` 之下的**另一个文件系统**，所以只导出 `/share` 的话，
计算节点上 `/share/images` 会是**空目录** —— 作业找不到镜像，`--container-image` 全挂。

本站点采用**独立导出 + 独立挂载**（比 `crossmnt` 更直白、可预测）：

```bash
# <ADMIN> /etc/exports
/share         <LAN_CIDR>(rw,sync,no_subtree_check)
/share/images  <LAN_CIDR>(rw,sync,no_subtree_check)

# <GPU02> /etc/fstab（第二条是关键，且要保证 /share 先挂）
<ADMIN>:/share         /share         nfs _netdev,rw,hard,intr,noatime,actimeo=60 0 0
<ADMIN>:/share/images  /share/images  nfs _netdev,rw,hard,intr,noatime,actimeo=60,x-systemd.requires-mounts-for=/share 0 0
```

> `x-systemd.requires-mounts-for=/share` 保证开机时先挂 `/share` 再挂它的子路径，
> 否则挂载点还不存在会导致 `/share/images` 挂载失败。

### 2. `AutoDetect=nvml` 推导出的型号名是 `nvidia_geforce_rtx_3090`，不是 `3090`

实测 `slurmd -C` 输出 `Gres=gpu:nvidia_geforce_rtx_3090:3`。它有副作用：
① slurm.conf 的 `NodeName ... Gres=` 必须逐字对齐，否则节点 `IDLE+DRAIN+INVALID_REG`；
② 门户的 GPU 型号下拉会显示成 `nvidia_geforce_rtx_3090`。

所以本站点 `gres.conf` **显式**声明三张卡：

```
Name=gpu Type=3090 File=/dev/nvidia0
Name=gpu Type=3090 File=/dev/nvidia1
Name=gpu Type=3090 File=/dev/nvidia2
```

这样 `sinfo` 显示干净的 `gpu:3090:3`，门户展示 `RTX 3090`。
**换卡/增减卡数时，`gres.conf` 与 `slurm.conf` 的 `NodeName Gres=` 必须同步改。**

### 3. 个人镜像**不计入** `/share` 磁盘配额

配额是**按文件系统**算的。用户的 `/share` 配额在 sdb 上，而个人镜像
（`save-image` 产物）落在 `/share/images`（nvme0n1）。本站点**刻意不给镜像盘开配额**
（接受个人镜像不受限），因此 `repquota -u /share` 的用量不含镜像。
若要改成计入，需要给镜像盘也开 `usrquota` 并让门户的 `_quota_rows()` 聚合两块盘。

## 部署顺序（本次实际执行顺序）

1. **前置**：`/etc/hosts`（`127.0.1.1` 必须是本机名，否则 Slurm 解析错）、root 双向互信、chrony
2. **共享盘**：`nvme0n1` → `mkfs.ext4` → `/share/images`；`/share` 开 `usrquota` + `quotarpc`；
   目录 `home/datasets/enroot-cache/images`；`/etc/exports` 两行
3. **Slurm**：依赖 + `cuda-nvml-dev` → 源码编译（`--with-nvml`，`mysql_config` 软链）→
   munge key → `slurm.conf`/`gres.conf`/`cgroup.conf` → slurmdbd → slurmctld → slurmd
4. **enroot/pyxis**：`squashfs-tools`/`squashfuse`、`libnvidia-container-tools`（在 CUDA 源里）、
   enroot deb、`enroot.conf`、`95-slurm-gpus.sh` 钩子、pyxis 编译 + `plugstack.conf`
5. **多用户**：MariaDB + slurmdbd + `sacctmgr`（cluster/account/qos normal）+ `/opt/cluster-admin/`
6. **门户**：`install.sh <代码> 8000 <站点目录>`
7. **镜像**：从旧集群 `rsync` 到 `/share/images`

## 运维注意（本次实测踩到）

**1. 管理节点与计算节点「同时」重启后，计算节点可能被标 DOWN。**
实测两台一起 reboot 后，slurmctld 报 `Reason=Node unexpectedly rebooted` 并把 <GPU02> 标成 DOWN
（slurmctld 的"意外重启"判定与 slurmd 的注册发生竞争；正常**单台**重启不会触发）。
一条命令拉回：

```bash
scontrol update nodename=<GPU02> state=resume
```

已加的开机加固（两台的 `slurmd.service.d/20-wait-remote-fs.conf`）：让 slurmd 等
`network-online.target` + `remote-fs.target` 就绪再启动（作业要读写 `/share`，早起没意义），
可缩小竞争窗口，但不保证消除。

**2. 在脚本里调 `srun` 必须显式 `</dev/null`。**
`srun` 默认把 stdin 转发给作业；若脚本本身是从 stdin 读的（heredoc、`ssh host 'bash -s'`），
第一个 `srun` 会把脚本剩余内容吃掉，表现为"脚本执行到一半就没了"（排查时极易误判成系统故障）。
写自动化时用 `srun ... </dev/null` 或 `--input=none`。

**3. 已核实的开机自启链路**（两台同时重启后逐项复验通过）：

| 项 | 结果 |
|---|---|
| `/share`(sdb) 与 `/share/images`(nvme0n1) | 都自动挂上且**顺序正确**（靠 `x-systemd.requires-mounts-for=/share`） |
| `/share` 的 usrquota | **自动恢复**（fstab 带 `usrquota` 时 systemd 让 `share.mount` 自动 `Wants=quotaon.service`） |
| munge / slurmdbd / slurmctld / slurmd / mariadb / nfs-server / quotarpc / cluster-portal | 8/8 `active` 且 `enabled` |
| NFS 导出 | `/share` 与 `/share/images` 都在 |
| 门户 | `/login` HTTP 200；`oa/scripts/verify-install.sh` 全部通过 |
| 镜像 | 4 个 `.sqsh` 经 NFS 在计算节点可见 |

## 网络限制备忘

| 目标 | 可达性 |
|---|---|
| `mirrors.zju.edu.cn`（apt） | ✅ |
| PyPI | ✅ |
| `download.schedmd.com`（Slurm 源码） | ✅ 但 <ADMIN> 实测仅 ~13KB/s，建议从 <GPU02> 内网拷 |
| `developer.download.nvidia.com`（CUDA 源，含 `libnvidia-container`） | ✅ |
| github.com / raw.githubusercontent.com | ❌ 阻断 |
| `gh-proxy.com`（GitHub 代理） | ✅ enroot release、pyxis tag tarball 都能下 |
| 旧集群 `<OLD_ADMIN_IP>` | ✅ RTT 0.5ms，镜像可直推 |
