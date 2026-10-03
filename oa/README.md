# oa —— 集群门户可复现交付包

本目录把“当前这套集群门户（登录/申请/管理一体化）的真实配置”固化为 **代码 + 快照 + 手册**，
用于在另一台机器上按手册原样复现。

## 目录结构

> **前置依赖（顺序不能颠倒）**：
> ⓪ 先跑 `provision/` 生成器（仓库根目录 `make`）拿到各节点配置与实值版文档；
> ① 再把**并列目录** `base-cluster/` 的集群本体（NFS/Slurm/enroot/监控/多用户）部署并验收通过；
> ② 最后才部署本包的门户 —— 门户依赖 Slurm/NFS/enroot 已就绪。

```
OrbitCluster/
├── Makefile                         # ★ 一条 make 完成变量替换（在根目录执行）
├── provision/                       # ⓪ 部署生成器（动手前先做，本包的前提之一）
│   ├── cluster.conf.example         #   ★ 拷成 cluster.conf 后只改这一个文件
│   ├── render.sh + lib/ + templates/#   生成各节点配置与实值版 markdown
│   └── conf/                        #   两套真实集群参数（与真机对照的回归夹具）
├── base-cluster/                    # ① 底层集群（门户的前提，先部署它）
│   ├── all-in-one-cluster-manual.md # 全量部署手册：NFS/Slurm/enroot/监控/多用户
│   ├── grafana-slurm-dashboard.json # Grafana 仪表盘（= 手册附录 B）
│   ├── important.md                 # 交互容器 SSH 登录的 srun 示例
│   ├── images/                      # 交互容器镜像构建源（Dockerfile + 加固的 start_ssh.sh）
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
    │   └── 3090-2node/              #   另一套实例的档案：2 节点，各 3×RTX3090，sshd 2022
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
        ├── verify-install.sh        # root：安装自检
        ├── e2e_security.sh          # root：容器可达 / 宿主机不可达
        └── e2e_accounts.py          # root：建号全流程（保留 UID 段，测完清理）
```

> **站点无关设计**：门户代码里不再写死集群的 ssh 端口、节点名、GPU 型号。
> 节点列表与分区名由 `sinfo` 运行时探测，节点 IP 从 `/etc/hosts` 解析，
> 其余变量放在 `/etc/cluster-portal/site.conf`（见 `cluster-portal/portalapp/siteconf.py`）。
> 因此换机器**不需要改代码**，只需准备一个 `sites/<站点>/` 目录。

> 路径约定：本包各手册中的 `base-cluster/…`、`oa/…`、`provision/…` 均相对**工作区根目录**（内含
> 这三个目录的那个目录，即 `OrbitCluster/`）；散见的 `/opt/cluster-portal`、
> `/var/lib/cluster-portal` 等则是目标机器上的绝对路径。

## 复现路线（⓪ 生成 → ① 底层 → ② 门户 → ③ 验收）

**⓪ 生成配置与文档**（在任意一台机器上做，不碰集群）
在仓库根目录编辑 `provision/cluster.conf`（节点名/IP/卡型卡数/sshd 端口/账户/存储布局）后执行 `make`，
得到 `provision/out/`：各节点的全部配置文件 + 占位符已填成**实值**的文档。
首次执行会从样例生成 `cluster.conf` 并停下，提示你先改。细节见 `provision/README.md`。

> 下面两步请读 `provision/out/docs/` 下**渲染后**的手册：里面的占位符已全部填成你的真实值，
> 可以照抄。

1. **① 部署底层集群**：按 `provision/out/docs/base-cluster/all-in-one-cluster-manual.md` 逐章执行
   （Slurm 26.05.1 + enroot + pyxis + 会计 + 配额 + 多用户）。要落地的 `/etc/hosts`、`slurm.conf`、
   `/etc/exports`、`/etc/fstab` 直接用 `provision/out/etc/` 生成好的那份；
   `/opt/cluster-admin/` 三个脚本取 `base-cluster/scripts/cluster-admin/`。
2. **② 部署门户**：按 `provision/out/docs/oa/01-部署手册.md` 把 `cluster-portal/` 拷到管理节点一键
   安装（`install.sh <代码目录> <端口> [站点目录]`，幂等；自动创建默认管理员 root，初始密码落盘
   `/root/.cluster-portal-admin`）+ 按需初始化/迁移账号。
3. **账号与套餐**：迁移或重设门户密码（`config-snapshot/README.md`「密码」一节），按需用管理页
   重建套餐/用户。
4. **③ 验收**：`scripts/verify-install.sh` 自检 + 手册「验收」一节做端到端验证；另有
   `scripts/e2e_security.sh`（用户能进自己的容器、不能进宿主机）与 `scripts/e2e_accounts.py`
   （建号全流程，用保留 UID 段并断言无残留）。

> **改参数的正确姿势**：永远改 `provision/cluster.conf` 后重新 `make`。直接改机器上或 `out/` 里的
> 生成物，下次渲染会被覆盖，而且 `slurm.conf` 的 `Gres=` 与 `gres.conf` 很容易不同步。

> 密钥与安全：本包不含任何明文门户密码；备份脚本打包时会包含（请妥善保管备份产物）。
