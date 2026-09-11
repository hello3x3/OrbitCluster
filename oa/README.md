# oa —— 集群门户可复现交付包

本目录把“当前这套集群门户（登录/申请/管理一体化）的真实配置”固化为 **代码 + 快照 + 手册**，
用于在另一台机器上按手册原样复现。

## 目录结构

> **前置依赖**：与本目录**并列**的 `base-cluster/` 是门户能跑起来的基础条件
> （集群本体：NFS/Slurm/enroot/监控/多用户）。请先按 `base-cluster/all-in-one-cluster-manual.md`
> 把底层集群部署并验收通过，再来部署本包的门户。

```
OrbitCluster/
├── base-cluster/                    # ① 底层集群（门户的前提，先部署它）
│   ├── all-in-one-cluster-manual.md # 全量部署手册：NFS/Slurm/enroot/监控/多用户
│   ├── grafana-slurm-dashboard.json # Grafana 仪表盘（= 手册附录 B）
│   └── important.md                 # 交互容器 SSH 登录的 srun 示例
└── oa/                              # ② 门户交付包（依赖 base-cluster 已就绪）
    ├── README.md                    # 本文件
    ├── 01-部署手册.md               # 部署（先读）：门户安装/初始化/验收
    ├── 02-管理手册.md               # 管理：用户/套餐/代申请/密码文件/备份恢复/排障
    ├── 03-使用手册.md               # 使用：登录、资料、申请、连接、日志、停机
    ├── cluster-portal/              # 门户完整源码/安装器/助手/测试
    ├── config-snapshot/             # 现场非密钥配置快照（env-notes 变量表请先读）
    │   ├── env-notes.md             # ★ 主机/IP/端口/版本/差异点总表（换机器必看）
    │   ├── plans.json / users.json / settings.json / instances.json
    │   ├── nodes.txt / images-list.txt / versions.txt
    │   ├── systemd/  sudoers、systemd 单元原文
    │   └── etc-cluster-portal/users.passwd.example（密码模板，见快照 README）
    └── scripts/
        ├── backup-portal.sh         # root：整机备份（代码+DB+明文密码文件）
        └── verify-install.sh        # root：安装自检
```

> 路径约定：本包各手册中的 `base-cluster/…`、`oa/…` 均相对**工作区根目录**（内含
> `base-cluster/` 与 `oa/` 的那个目录，即 `OrbitCluster/`）；散见的 `/opt/cluster-portal`、
> `/var/lib/cluster-portal` 等则是目标机器上的绝对路径。

## 复现路线（五步）

1. 读 `config-snapshot/env-notes.md`，记录你要替换的变量（主机名/IP/ssh 端口/分区/镜像）；
2. 按 `base-cluster/all-in-one-cluster-manual.md` 部署底层集群（Slurm26.05.1+enroot+pyxis+
   会计+配额+多用户，含 `/opt/cluster-admin/`）；
3. 按 `01-部署手册.md` 把 `cluster-portal/` 拷到 admin 并一键安装（install.sh，安装器自动创建
   默认管理员 root，初始密码落盘 `/root/.cluster-portal-admin`）+ 按需初始化/迁移账号；
4. 迁移或重设门户密码（`config-snapshot/README.md`“密码”一节），按需用管理页重建套餐/用户；
5. `scripts/verify-install.sh` 自检 + 手册“验收”一节做端到端验证。

> 密钥与安全：本包不含任何明文门户密码；备份脚本打包时会包含（请妥善保管备份产物）。
