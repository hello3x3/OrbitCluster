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
│   ├── important.md                 # 交互容器 SSH 登录的 srun 示例
│   └── scripts/cluster-admin/       # /opt/cluster-admin 脚本（手册 7.5 的正文版）
│       ├── add-user.sh              # 建号（自动判定管理节点/计算节点，不写死主机名）
│       ├── set-quota.sh             # 设/扩容 /share 配额（soft=hard，即时生效）
│       └── show-quota.sh            # 全员配额一览
└── oa/                              # ② 门户交付包（依赖 base-cluster 已就绪）
    ├── README.md                    # 本文件
    ├── 01-部署手册.md               # 部署（先读）：门户安装/初始化/验收
    ├── 02-管理手册.md               # 管理：用户/套餐/代申请/密码文件/备份恢复/排障
    ├── 03-使用手册.md               # 使用：登录、资料、申请、连接、日志、停机
    ├── cluster-portal/              # 门户完整源码/安装器/助手/测试
    ├── sites/                       # ★ 站点档案：同一份代码适配不同集群
    │   └── 3090-2node/              #   <ADMIN>(管理+计算) + <GPU02>(计算)，各 3×RTX3090
    │       ├── README.md            #   本站点与旧集群的差异清单 + 部署要点
    │       ├── site.conf            #   → /etc/cluster-portal/site.conf（ssh 端口/GPU 型号/套餐种子）
    │       ├── plans.json           #   → /etc/cluster-portal/plans.json（首次建库的套餐）
    │       ├── slurm.conf           #   → /etc/slurm/slurm.conf
    │       └── gres.conf            #   → /etc/slurm/gres.conf
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

> **站点无关设计**：门户代码里不再写死集群的 ssh 端口、节点名、GPU 型号。
> 节点列表与分区名由 `sinfo` 运行时探测，节点 IP 从 `/etc/hosts` 解析，
> 其余变量放在 `/etc/cluster-portal/site.conf`（见 `cluster-portal/portalapp/siteconf.py`）。
> 因此换机器**不需要改代码**，只需准备一个 `sites/<站点>/` 目录。

> 路径约定：本包各手册中的 `base-cluster/…`、`oa/…` 均相对**工作区根目录**（内含
> `base-cluster/` 与 `oa/` 的那个目录，即 `OrbitCluster/`）；散见的 `/opt/cluster-portal`、
> `/var/lib/cluster-portal` 等则是目标机器上的绝对路径。

## 复现路线（五步）

1. 读 `config-snapshot/env-notes.md`，记录你要替换的变量（主机名/IP/ssh 端口/分区/镜像）；
   若目标集群与旧集群差异较大（节点数/卡型/ssh 端口不同），先照 `sites/3090-2node/` 做一个
   自己的 `sites/<站点>/`（`site.conf` + `plans.json` + `slurm.conf` + `gres.conf`）；
2. 按 `base-cluster/all-in-one-cluster-manual.md` 部署底层集群（Slurm26.05.1+enroot+pyxis+
   会计+配额+多用户），`/opt/cluster-admin/` 三个脚本直接取 `base-cluster/scripts/cluster-admin/`；
3. 按 `01-部署手册.md` 把 `cluster-portal/` 拷到管理节点并一键安装
   （`install.sh <代码目录> <端口> [站点目录]`，安装器自动创建默认管理员 root，初始密码落盘
   `/root/.cluster-portal-admin`）+ 按需初始化/迁移账号；
4. 迁移或重设门户密码（`config-snapshot/README.md`“密码”一节），按需用管理页重建套餐/用户；
5. `scripts/verify-install.sh` 自检 + 手册“验收”一节做端到端验证。

> 密钥与安全：本包不含任何明文门户密码；备份脚本打包时会包含（请妥善保管备份产物）。
