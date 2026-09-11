# 实例环境与变量（换机器复现时按此替换）

> 本目录是 **2026-09 现场（`admin/<GPU02>/<GPU03>` 集群）的配置快照**。
> 新机器上请把下面“应替换”的值换成你自己的；手册中提到这些变量时请对照本表。

## 1. 主机与网络（应替换）

| 变量 | 本实例值 | 说明 |
|---|---|---|
| 管理/登录节点 | `admin` = `<ADMIN_IP>` | NFS 服务端 + slurmctld + 门户服务所在 |
| 计算节点 | `<GPU02>` = `<GPU02_IP>`；`<GPU03>` = `<GPU03_IP>` | 各 1×RTX 3060 |
| 集群 sshd 端口 | `2180`（非标准 22） | 全部节点（portal-ctl 内亦按此连接计算节点） |
| 集群分区 | `gpu` | 3 节点全 idle，各 `gpu:3060:1` |
| 门户服务地址 | `http://admin:8000`（0.0.0.0:8000） | 内网访问 |

`deploy/portal-ctl` 中与节点互连固定使用：`ssh -p 2180 root@<node>`，root 间密钥需先配好
（底层集群手册 4.6① 同步 munge/密钥时一并处理）。

## 2. 版本（见 versions.txt）

slurm 26.05.1（源码，**带 MySQL 会计插件与 nvml**）、enroot 4.2.1、pyxis v0.24.0、
Ubuntu 24.04、Python 3.12、Flask 3.1.3 + waitress。
> 底层集群请严格按 `base-cluster/all-in-one-cluster-manual.md` 部署（含会计/配额/建号脚本），
> 门户的“自动建号”直接调用其中的 `/opt/cluster-admin/add-user.sh`。

## 3. 镜像（现场 /share/images，应替换为你的镜像）

见 `images-list.txt`。当前 4 个：
- `cuda12.8.0-devel-ubuntu24.04.sqsh` / `cuda13.3.1-devel-ubuntu24.04.sqsh`：**内置 sshd + /opt/start_ssh.sh**，交互 SSH 可用；
- `pytorch-2.12.1-cuda13.0-cudnn9-devel.sqsh`、`ubuntu-22.04.sqsh`：未内置 sshd（门户会列出，可提交容器但交互 SSH 需先给镜像补 sshd）。

门户申请页/代申请页自动分组扫描：**公共镜像**=`/share/images/*.sqsh`（无需登记）；
**个人镜像**=`/share/images/<用户名>/*.sqsh`（用户「保存镜像」产生，计入该用户配额，
目录 700 仅本人/root 可读，经 `portal-ctl images` 列表；删除门户代建号用户时该目录一并清理）。
所有作业以命名容器提交（`--container-name=portal --container-writable`），rootfs 位于计算节点
`/scratch/enroot-data/user-<uid>/pyxis_<jobid>_portal`（作业结束 pyxis 自动清理）；
运行中可 `portal-ctl save-image <用户> <jobid> <名称> [force]` 导出；到期自动保存由门户后台线程按
「开始运行时刻+所选时长」触发，Slurm 侧时长含 15 分钟保存余量（`PORTAL_EXPIRE_BUFFER_MIN` 可调）。

## 4. 套餐（plans.json，当前 5 个）

字段：名称、说明、gpus(0/1)、gpu_model、cpus、mem_gb、maxtime_h、enabled。
示例（导出见 plans.json）：
- 基础 CPU：0/2核/4G/最长48h；均衡 CPU：0/4核/8G/48h
- GPU 入门/标准/高配：1×RTX 3060，4/8/16 核，8/16/24G，最长 48h
> GPU 型号下拉选项来自集群 gres（gpu:3060 → RTX 3060），新增型号自动出现。

## 5. 用户与配额（users.json，无密码）

当前平台账号：**`root`（默认管理员 = 平台最高管理员，安装器自动创建；集群有 OS root，可自助申请）**、
`admin`（另一管理员账号：无同名 OS 账号，仅纯平台管理，不能自助申请）、
- `<USERNAME>`（普通用户，existing 绑定 OS uid1001）。
- 门户账号密码的**唯一明文源**在 `/etc/cluster-portal/users.passwd`；
  迁移机器时**直接整份复制该文件**即可原样恢复账号密码（见 03-使用手册/02-管理手册 密码章节）。
- users.json 里的 quota 只是**门户记录的名义值**；磁盘配额的权威是 **OS `/share` 实值**
  （门户用 repquota 实读展示、set-quota.sh 实写，见 02-管理手册 §1b）。
- root 初始密码由安装器随机生成，落盘 `/root/.cluster-portal-admin`（600，仅 OS root 可读）。
- 普通用户“自动建号”流程调用 add-user.sh：家目录 `/share/home/<u>`（NFS）、配额、
  sacctmgr 关联；请先在计算节点复制 `/opt/cluster-admin/*.sh`（install/portal-ctl 会在建号时自动同步 add-user.sh）。

## 6. 文件路径约定（门户）

| 路径 | 用途 | 属主/权限 |
|---|---|---|
| `/opt/cluster-portal` | 门户代码（venv 在 `venv/`） | root，portalapp 属 portal |
| `/var/lib/cluster-portal` | 数据目录：`portal.db`（SQLite）、`secret` | portal |
| `/usr/local/sbin/portal-ctl` | root 特权助手（web 经 sudoers 调用） | root 0755 |
| `/etc/cluster-portal/` | 密码文件目录 | root:portal 770 |
| `/etc/cluster-portal/users.passwd` | 全账号明文密码（双向同步） | root:portal 660 |
| `/etc/sudoers.d/cluster-portal` | 白名单（见 systemd/portal.sudoers） | root 0440 |
| systemd 单元 | systemd/cluster-portal.service | root |
| 用户家目录 `.portal/logs` | 每作业日志 | 用户 |
| `/root/.cluster-portal-admin` | bootstrap 默认管理员(root) 初始密码备份（root 600） | 仅首次部署时用 |

## 7. 需要你动手检查的差异点

1. 节点名/IP/ssh 端口（第 1 节）；2. 镜像清单；3. 分区名（如不是 gpu）；
4. `/etc/cluster-portal/users.passwd`（复制旧机文件或重设密码后等 20s 自动生效）；
5. 计算节点上存在 `/opt/cluster-admin/add-user.sh`（可自动补齐）；6. root 间互信密钥。
