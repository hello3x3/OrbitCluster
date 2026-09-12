# host-config —— 现场系统配置文件（**已脱敏**，仅供比对与重建参考）

这里存放 3090 集群上**实际生效**的系统级配置。它们不在门户代码里，也不由 `install.sh` 安装，
而是底层集群部署（见 `base-cluster/all-in-one-cluster-manual.md`）过程中落到各机的。

> ⚠️ **全部已脱敏**：主机名 → `<ADMIN>`/`<GPU02>`，IP → `<ADMIN_IP>`/`<GPU02_IP>`，
> 网段 → `<LAN_CIDR>`。**不能直接拷到机器上**，必须先替换成真实值。
> 反过来，目标机上的对应文件**必须保留真实值**——仓库这份与生产内容不同是设计如此。

| 本目录文件 | 目标路径 | 所在节点 | 说明 |
|---|---|---|---|
| `etc-hosts` | `/etc/hosts` | 两台 | **`127.0.1.1` 必须是本机名**，否则 Slurm 解析错（本次踩过的坑） |
| `etc-fstab` | `/etc/fstab` | 管理节点 | `/share`(sdb) + `/share/images`(nvme0n1)；`usrquota` 与 `x-systemd.requires-mounts-for` 都在这里 |
| `etc-fstab.compute-node` | `/etc/fstab` | 计算节点 | 两条 NFS 挂载：`/share` 与 `/share/images`（后者依赖前者先挂） |
| `etc-exports` | `/etc/exports` | 管理节点 | `/share` 与 `/share/images` **各自独立导出**（嵌套挂载点默认隐藏，必须分开） |
| `etc-chrony-chrony.conf` | `/etc/chrony/chrony.conf` | 管理节点 | 管理节点作为内网时钟源（含 `allow <LAN_CIDR>`） |
| `etc-enroot-enroot.conf` | `/etc/enroot/enroot.conf` | 两台 | 运行时/缓存/数据三处路径；`ENROOT_DATA_PATH` 在本地盘 `/scratch` |
| `etc-enroot-hooks.d-95-slurm-gpus.sh` | `/etc/enroot/hooks.d/95-slurm-gpus.sh` | 两台 | **修复版**：把 `CUDA_VISIBLE_DEVICES` 转成 `NVIDIA_VISIBLE_DEVICES`，CPU 作业必须返回 0 |
| `etc-slurm-cgroup.conf` | `/etc/slurm/cgroup.conf` | 两台 | 作业级 cgroup 约束 |
| `etc-slurm-plugstack.conf` | `/etc/slurm/plugstack.conf` | 两台 | 只有一行 `include` |
| `etc-slurm-plugstack.conf.d-pyxis.conf` | `/etc/slurm/plugstack.conf.d/pyxis.conf` | 两台 | pyxis SPANK 插件那一行（`use_squashfuse=1` 等参数） |
| `etc-slurm-slurmdbd.conf.template` | `/etc/slurm/slurmdbd.conf` | 管理节点 | **模板**：口令与 `DbdHost` 已占位（真实口令在目标机 `/root/.slurmdb.pass`，600） |
| `etc-tmpfiles.d-enroot.conf` | `/etc/tmpfiles.d/enroot.conf` | 两台 | `/run/enroot` 开机重建 |
| `etc-tmpfiles.d-pyxis.conf` | `/etc/tmpfiles.d/pyxis.conf` | 两台 | `/run/pyxis` 开机重建 |
| `etc-mysql-mariadb.conf.d-99-slurm.cnf` | `/etc/mysql/mariadb.conf.d/99-slurm.cnf` | 管理节点 | 消除 slurmdbd 启动告警的 MariaDB 调优 |
| `etc-systemd-system-slurmd.service.d-20-wait-remote-fs.conf` | `/etc/systemd/system/slurmd.service.d/20-wait-remote-fs.conf` | 两台 | 让 slurmd 等 `network-online` + `remote-fs` 就绪再起（作业要读写 `/share`） |

## 两个说明

1. **`slurm.conf` / `gres.conf` 不在本目录**：它们是站点档案的一等文件，放在上一级
   （`../slurm.conf`、`../gres.conf`），因为门户/复制流程会直接用它们。
2. **slurmd 的开机加固最终只有一个文件**。部署过程中先在计算节点放过一个
   `override.conf`（只写 `network-online.target`），后来统一成
   `20-wait-remote-fs.conf`（同时覆盖 `network-online` 与 `remote-fs`），
   前者的内容被完全覆盖，已删除——**最终两节点都只有 `20-wait-remote-fs.conf`**。

## 怎么核对「仓库这份 vs 机器上那份」

先还原占位符再比对，例如：

```bash
# 在管理节点上，把真实值代回去后与仓库文件比对
sed -e 's/<ADMIN>/'"$(hostname -s)"'/g' \
    -e 's/<ADMIN_IP>/'"$(hostname -I | awk '{print $1}')"'/g' \
    host-config/etc-hosts | diff - /etc/hosts
```
