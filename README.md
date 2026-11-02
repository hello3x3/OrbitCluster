# OrbitCluster

面向**实验室小型 GPU 集群**的一体化工程：把「集群怎么搭、用户怎么自助用、换台机器怎么复现」
固化成可照做的文档与可安装的代码。集群本体为 **Slurm + enroot/pyxis** 容器化 GPU 调度。

> **部署从 [`oa/00-生成器部署.md`](oa/00-生成器部署.md) 开始** —— 改一个 `provision/cluster.conf`，
> `make` 出全部机器配置与文档，再按 `MANIFEST.md` 下发。本文档与其余手册是深入参考。
>
> 门户按**多账户**建模：一套集群可配置多个 Slurm 会计账户（示例站点 `ZJU-CV` 为 `phd/ms/be/ra`，
> 默认 `phd`）；root 在「管理账户」页维护每个账户的优先级 / 单用户卡数上限 / 可抢占 / 成员 / 到期，
> 用户申请时选账户，**多账户额度叠加**。账户与 OS 用户是两回事（见「命名约定」）。

仓库分两部分，**有先后依赖**：

| 目录 | 角色 | 说明 |
|---|---|---|
| `base-cluster/` | ① **基础条件**（先做） | 底层集群部署手册与仪表盘：NFS / Slurm / enroot+pyxis / Prometheus+Grafana / 多用户（配额·会计·建号脚本） |
| `oa/` | ② **门户交付包**（后做） | OrbitCluster 自助门户：源码、安装器、特权助手、测试、现场配置快照、三本手册 |

> `oa/` 里的门户**依赖 `base-cluster/` 已经部署并验收通过**：建号要调底层手册产出的
> `/opt/cluster-admin/add-user.sh`，提交作业依赖 Slurm 与 pyxis。顺序不能反。

## 目录结构

```
OrbitCluster/
├── README.md                        # 本文件
├── Makefile                         # ★ 一条 make 完成变量替换（见「部署顺序与使用方法」）
├── .gitignore / .gitattributes
├── provision/                       # ⓪ 部署生成器（动手前先做：一个 cluster.conf → 全部配置与文档）
│   ├── README.md                    #   变量表 / 渲染规则 / 与真机对照的自检结果
│   ├── cluster.conf.example         #   ★ 拷成 cluster.conf 后**只改这一个文件**
│   │                                #     含 CLUSTER_NAME / ACCOUNTS / DEFAULT_ACCOUNT / USER 等
│   ├── render.sh                    #   生成器（只生成文件，不 ssh、不改任何机器）
│   ├── lib/render_docs.py           #   markdown 占位符替换 + 待人工核对的报告
│   ├── templates/                   #   目标文件模板（hosts/slurm/gres/exports/fstab/sshd/site.conf…）
│   ├── conf/                        #   两套**真实**集群参数（回归夹具，与真机对照用）
│   └── out/                         #   生成物（已 gitignore）
├── base-cluster/                    # ① 底层集群（门户的前提）
│   ├── all-in-one-cluster-manual.md # 全量部署手册（逐章带验收命令；含多账户/QoS/抢占/配额）
│   ├── grafana-slurm-dashboard.json # Grafana 仪表盘（= 手册附录 B）
│   ├── important.md                 # 交互容器 SSH 登录的 srun 示例
│   ├── images/                      # 交互容器镜像的构建源（Dockerfile + 加固的 start_ssh.sh）
│   └── scripts/cluster-admin/       # /opt/cluster-admin 三个脚本（手册 7.5 的正文版）
│       └── add-user.sh / set-quota.sh / show-quota.sh
│                                    #   ★ 不写死主机名：靠 /share 是否本地盘自动判定
│                                    #     自己是管理节点还是计算节点，两套集群通用
└── oa/                              # ② 门户交付包
    ├── README.md                    # ★ 交付包总览 + 复现路线（先读这个）
    ├── 00-生成器部署.md             # ★ 从这开始：改参数 → make → 下发（含账户初始化与验收命令）
    ├── 01-部署手册.md               # 门户安装/初始化（含 accounts-init）/验收（在 base-cluster 之后读）
    ├── 02-管理手册.md               # 用户/套餐/代申请/**管理账户**/密码文件/备份恢复/排障
    ├── 03-使用手册.md               # 面向最终用户：登录、资料、申请（选账户）、连接、日志、停机
    ├── cluster-portal/              # 门户完整源码（Flask + SQLite + waitress）
    │   ├── portalapp/               #   应用、鉴权、DB、站点配置、特权助手客户端、模板
    │   ├── deploy/                  #   install.sh 一键安装 + portal-ctl（root 特权助手）
    │   ├── tests/                   #   smoke_local（免集群）/ e2e_live（真机）/ test_ctl_security
    │   ├── run.py / bootstrap.py    #   生产入口 / 账号初始化与重置
    │   └── README.md                #   门户自身文档（功能、安全模型、已知边界）
    └── scripts/                     # backup-portal.sh / verify-install.sh / e2e_security.sh
                                     # e2e_accounts.py（测试账号守卫）/ e2e_verify.py（端到端验收）
```

代码与文档中出现的 `base-cluster/…`、`oa/…` 均相对**本仓库根目录**；`/opt/cluster-portal`、
`/var/lib/cluster-portal`、`/etc/cluster-portal` 等则是目标机器上的绝对路径。

## 部署顺序与使用方法

整条链路是 **⓪ 生成配置 → ① 底层集群 → ② 门户 → ③ 验收**，前一步的产物是后一步的输入。
**顺序不能颠倒**：门户依赖 Slurm / NFS / enroot 已就绪。

> 可照做的完整流程（每台节点的下发命令、权限、让配置生效、验收命令）见 **`oa/00-生成器部署.md`**；
> 下面是同一件事的概览。

### ⓪ 生成配置与文档（不碰集群，在任意一台能编辑仓库的机器上做）

只编辑 **一个文件** `provision/cluster.conf`（节点名 / IP / 卡型卡数 / sshd 端口 / 账户 /
存储布局），然后在仓库根目录执行 `make`：

> 其中 `CLUSTER_NAME` / `ACCOUNTS` / `DEFAULT_ACCOUNT` 决定集群名与站点账户清单
> （示例站点：`ZJU-CV`、`phd,ms,be,ra`、默认 `phd`；`ACCOUNT` 是兼容旧单账户键）。
> 它们会被渲染进 `site.conf` 与文档；**Slurm 里的账户由门户安装时的 `accounts-init` 创建**。

```bash
make                                   # ★ 就地替换：文档 + 机器配置都写回**当前仓库**
make SET="SSH_PORT=2222 USER=alice"    # 临时覆盖个别配置项（不写回 cluster.conf）
make check                             # 打印「需要人工核对」清单
make reset                             # 还原仓库模板（撤销就地替换）
make print                             # 只打印，不落盘（先看效果）
make out                               # 不改仓库，另存到 provision/out/
make help                              # 全部目标
```

`make` 做两件事，**都只落在当前仓库里**：

1. **文档就地替换** —— `README.md`、`base-cluster/**/*.md`、`oa/**/*.md` 里的
   `<ADMIN>` / `<GPU01>` / `<LAN_CIDR>` / `<CLUSTER_NAME>` / `<ACCOUNTS>` / `<DEFAULT_ACCOUNT>` /
   `<USER>` 等占位符被填成本集群实值，直接可读、可交付。
   （`provision/**` 是生成器自身文档，同样不参与。）
2. **机器配置写进 `deploy/`** —— `deploy/etc/` 的目录结构与目标机一一对应
   （`deploy/etc/hosts` → `/etc/hosts`），`deploy/MANIFEST.md` 是下发清单。
   下发就是 `rsync -a deploy/etc/ root@<节点>:/etc/`。

首次执行会从 `cluster.conf.example` 生成 `cluster.conf` 并停下，提示你先改 ——
不会拿一份没看过的模板渲染出"看着能部署"的文件。

> ⚠️ **就地替换只做一次**：占位符被用掉之后再跑 `make` 会被明确拒绝，并提示先 `make reset`。
> 这是刻意的 —— 否则你会以为重新渲染过了，实际文档一个字都没变。
>
> ⚠️ **不要把这些改动 commit 进 git**：就地替换后的文档装的是你这套集群的实值，不是通用模板。
> `make reset` 会把它们还原成模板 —— 它从 `provision/.render-state/` 里**首次渲染前**的快照恢复，
> 不跑 `git checkout`，所以不会误伤你其它未提交的改动。

`deploy/MANIFEST.md` 写明哪台机器该放哪些文件：

| 生成物（仓库内路径） | 目标位置 | 哪台机器 |
|---|---|---|
| `deploy/etc/hosts.<节点>` | `/etc/hosts`（**每台只有 `127.0.1.1` 那行不同**） | 每台 |
| `deploy/etc/slurm/slurm.conf`、`gres.conf` | `/etc/slurm/` | 每台 |
| `deploy/etc/enroot/enroot.conf` | `/etc/enroot/` | 每台 |
| `deploy/etc/ssh/sshd_config.d/10-portal-only.conf` | 同路径（**只允许 root 登录宿主机**） | 每台 |
| `deploy/etc/fstab.<节点>` | 追加到 `/etc/fstab`（只给数据盘 / NFS 行，系统盘行别动） | 管理 + 各计算 |
| `deploy/etc/exports` | `/etc/exports` | 管理节点 |
| `deploy/etc/chrony/*`、`deploy/etc/cluster-portal/*` | 追加 / 同路径（`site.conf` 含 `ACCOUNTS`/`DEFAULT_ACCOUNT`） | 管理节点 |
| `base-cluster/scripts/cluster-admin/*.sh` | `/opt/cluster-admin/` | 管理节点（就地模式不复制副本，直接 rsync 源目录）|
| `README.md`、`base-cluster/**/*.md`、`oa/**/*.md` | 就地替换，直接阅读 | — |
| `deploy/MANIFEST.md` | 下发清单本身 | — |

### ① 部署底层集群（管理节点 + 各计算节点）

按 **`base-cluster/all-in-one-cluster-manual.md`** 逐章执行 —— 认准**渲染后**的
这一份：里面的占位符已全部填成你的真实值，每章都带验收命令。

- 手册里凡是要落地 `/etc/hosts`、`slurm.conf`、`/etc/exports`、`/etc/fstab` 的地方，
  **直接用 `deploy/etc/` 那份**，不要手写。
- **账户**：`cluster.conf` 的 `ACCOUNTS`/`DEFAULT_ACCOUNT` 只写进 `site.conf`；Slurm 里的会计账户与
  `acct-<账户名>` QoS 在门户安装时由 `portal-ctl accounts-init` 创建（也可按底层手册 §7.4 手工建）。
  每账户的“单用户同时最多 N 卡”用 QoS `MaxTRESPerUser`，**不用 account 级 `GrpTRES`**（见手册 §7.6）。
- `/opt/cluster-admin/` 三个脚本取 `base-cluster/scripts/cluster-admin/`（或 `out/opt/cluster-admin/`）。
- 交互容器镜像的构建源在 `base-cluster/images/`，含加固过的 `start_ssh.sh`。

> **换集群 = 改参数 + 重新生成**：节点数 / 卡型 / ssh 端口 / 存储布局 / 套餐 / **账户清单**都写在
> `provision/cluster.conf`，改完 `make`，站点配置（site.conf、plans.json）与全部 `/etc` 配置
> 一起生成到 `deploy/`。门户代码本就不写死 ssh 端口与 GPU 型号，节点列表、分区名运行时探测，
> 因此**换机器不需要改代码**（新增账户后记得重跑 `portal-ctl accounts-init`）。

### ② 部署门户（管理节点）

按 **`oa/01-部署手册.md`** 把 `oa/cluster-portal/` 拷到管理节点一键安装：

```bash
deploy/install.sh <代码目录> <端口> [站点目录]   # 幂等
                                                # 自动创建默认管理员 root
                                                # 初始口令落盘 /root/.cluster-portal-admin
```

安装器会把应用代码设为 **root 属主**（`portal` 进程不可写代码），并对 systemd 单元做沙箱加固
（`ProtectSystem=strict` + `ReadWritePaths` + `UMask=0077`）。
安装过程中还会按 `site.conf` 的 `ACCOUNTS` 自动执行 `portal-ctl accounts-init`（幂等建账户 + `acct-*` QoS；
Slurm 未就绪时只告警），装完可用 `portal-ctl accounts` 核对。之后 root 可在门户「管理账户」页
维护账户的优先级 / 单用户卡数上限 / 可抢占 / 成员 / 到期；用户开通与申请资源时都要选账户。

### ③ 验收

```bash
oa/scripts/verify-install.sh     # 安装自检：含 sshd 白名单 / 代码属主 / enroot / ACCOUNTS 初始化断言
oa/scripts/e2e_security.sh       # 端到端安全验收：用户能进自己的容器、不能进宿主机
oa/scripts/e2e_accounts.py       # 建号全流程（用保留 UID 段，测完自动清理并断言无残留）
```

再按 `01-部署手册.md` 的「验收」一节做一遍人工确认（含账户：`portal-ctl accounts`、
`portal-ctl user-accounts <用户>`、申请时选账户）。

### ④ 日常使用

| 我要做什么 | 看哪本 / 敲什么 |
|---|---|
| 开通用户、改配额、代申请、备份恢复、排障 | `oa/02-管理手册.md` |
| **管理账户 / 临时高优项目 / 多账户额度** | `oa/02-管理手册.md` §1b、§1d；门户 root 的「管理账户」页 |
| 登录门户、申请资源（选计费账户）、连容器、看日志、停机 | `oa/03-使用手册.md` |
| 加机器 / 换卡型 / 换端口 / 增减账户等改参数 | 编辑 `provision/cluster.conf` → `make` → 下发变化的文件 |
| 整机备份、安装自检 | `oa/scripts/backup-portal.sh`、`oa/scripts/verify-install.sh` |
| 本地跑测试（不需要集群） | `make test` |

> **改参数的正确姿势**：永远改 `provision/cluster.conf` 后重新 `make`。
> 直接改机器上的生成物，下次渲染会被覆盖，而且 `slurm.conf` 的 `Gres=` 与 `gres.conf`
> 很容易不同步（3090 站点 README 记过这个坑）。

## 站点无关设计（为什么换机器不用改代码）

| 项 | 来源 | 换集群要做什么 |
|---|---|---|
| 节点列表 / 分区名 | `sinfo` 运行时探测 | 不用改 |
| 节点 IP | `/etc/hosts` 解析（取最后一条非回环地址） | 写好 hosts 即可 |
| 节点间 ssh 端口 | `/etc/cluster-portal/site.conf` 的 `SSH_PORT` | **要改** |
| **账户列表 / 默认账户** | `site.conf` 的 `ACCOUNTS` / `DEFAULT_ACCOUNT`（由 `cluster.conf` 生成） | **要改**：增减账户、换默认账户；重启门户并重跑 `portal-ctl accounts-init` |
| GPU 型号展示名 | site.conf 的 `GPU_MODEL_MAP`（纯数字 token 自动补 `RTX `） | 一般不用改 |
| 套餐种子 | site.conf 的 `SEED_PLANS` 指向的 JSON | 按机型改 |

`site.conf` 由门户进程（`portalapp/siteconf.py`）与 root 助手（`deploy/portal-ctl`）**同时**读取，
优先级：环境变量 `PORTAL_<KEY>` > site.conf > 代码内置默认值。

## 命名约定（集群名 / 账户 / OS 用户）

| 名字 | 含义 | 配置键 | 示例（现场站点） |
|---|---|---|---|
| **集群名** | `slurm.conf` 的 `ClusterName`，也是 sacctmgr 的 cluster 名 | `CLUSTER_NAME` | `ZJU-CV` |
| **会计账户** | Slurm 的计费/优先级/额度单位，**可多个**，一个用户可同时属于多个 | `ACCOUNTS`（逗号分隔） | `phd,ms,be,ra` |
| **默认账户** | 新用户默认挂的账户，必须在 `ACCOUNTS` 内 | `DEFAULT_ACCOUNT` | `phd` |
| **OS 用户** | Linux 账号（运维/示例用户），与 Slurm 账户无关 | `USER` | `zju` |

> 账户由 root 在门户「管理账户」页维护：优先级（1~10）/ 单用户卡数上限（QoS `MaxTRESPerUser`，
> 0=不限）/ 可抢占 / 成员 / 到期。**账户不设总额度**（不用 account 级 `GrpTRES`）；
> 多账户额度**叠加**（如 `phd` 8 + 临时项目 16 = 同时最多 24 卡，受物理卡数限制）。
> 文档模板里这些都写成 `<CLUSTER_NAME>` / `<ACCOUNTS>` / `<DEFAULT_ACCOUNT>` / `<USER>` 占位符，
> `ZJU-CV`/`phd` 只是现场站点的示例值，不要把它们写死进通用文档。

## 本地开发与测试

### 用户命令行（agent CLI）

`terminal/` 提供 Go 编写的独立 `orbit` 程序，没有 TUI，不需要用户安装 Python、Git 或其它语言依赖。
在门户「使用文档 → 终端工具使用」复制安装命令，从本门户下载对应平台压缩包。
支持 macOS、Linux、WSL、原生 Windows，显示下载进度并校验 SHA256。
HTTP 默认可用；现有 Python 服务端和业务规则保留，认证固定七天、无自动续期。
当前发布版本为 0.3.0。默认输出整理为可读列表与详情，`--details` 展开，`--json` 保留机器接口；登录后提醒更新，`orbit update` 按需更新并保留配置与登录。

```bash
orbit login --username 你的用户名
orbit instances list
orbit update --check
orbit uninstall
```

客户端命令、构建发布、HTTP 部署、更新和卸载见 [terminal/README.md](terminal/README.md)。

### 门户测试

门户测试分两层，都不需要 root：

```bash
cd oa/cluster-portal

# 本地冒烟：用假 ctl 层，不碰集群（覆盖路由/校验/状态机/权限）
python3 -m venv .venv-test
.venv-test/bin/pip install -r requirements.txt requests
.venv-test/bin/python tests/smoke_local.py          # 成功打印 SMOKE_OK

# 真机端到端验收（需门户已部署且本机可 ssh root@<ADMIN>）
E2E_BASE=http://<ADMIN>:80 .venv-test/bin/python tests/e2e_live.py
```

## 仓库约定

- **只跟踪源文件**：代码、手册、站点参数与回归夹具、脚本。
- **不跟踪**：`__pycache__/`、虚拟环境（`.venv*`、`venv/`）、门户运行时数据
  （`oa/cluster-portal/var/`、`portal.db*`、`secret`）、日志、`.sqsh` 容器镜像（数 GB，只部署在
  集群 `<IMAGES_MOUNT>/`）。规则见 `.gitignore`。
- **严禁入库明文口令与备份产物**：`users.passwd`、`*.tgz`、`*.key`、`*.pem` 等已在 `.gitignore` 中屏蔽。
  本仓库定位是「可交付、不含密钥」——门户密码的唯一明文权威是目标机的
  `/etc/cluster-portal/users.passwd`（安装器创建，格式为每行 `用户名:密码`）。
- **可执行位**：`oa/cluster-portal/deploy/{install.sh,portal-ctl}` 与 `oa/scripts/*.sh` 需保持 `755`，
  其余文件 `644`；行尾统一 LF（`.gitattributes`）。

## 环境基线

Ubuntu 24.04 · Slurm 26.05.1（源码编译，含 MySQL 会计 + NVML）· enroot 4.2.1 · pyxis v0.24.0 ·
Python 3.12 · Flask 3.1.3 + waitress。

已落地的实例与回归夹具：

| 实例 | 节点构成 | 卡 | sshd | 账户 | 档案（含真实主机名/IP） |
|---|---|---|---|---|---|
| A（现场实例 `ZJU-CV`） | 4 节点：管理 `3090-00` + 3 计算 | 各 4×RTX 3090 | 2022 | `phd,ms,be,ra`（默认 `phd`） | `provision/cluster.conf` |
| B（回归夹具，3090） | 2 节点：管理兼计算 + 1 计算 | 各 3×RTX 3090 | 2022 | 单账户 `lab` | `provision/conf/3090-2node.conf` |
| C（字段夹具，3060） | 3 节点：管理/登录 + 2 计算 | 各 1×RTX 3060 | 2180 | 单账户 `lab` | `provision/conf/field-3node.conf` |

> 本表刻意不写主机名 —— 它同时描述多套集群，写成占位符会在渲染时被填成**本集群**的值而失真。
> 各套的真实主机名/IP 与账户清单在对应 `.conf` 里；`ZJU-CV` / `phd` 等只是现场站点示例，
> 通用文档里一律用 `<CLUSTER_NAME>` / `<ACCOUNTS>` / `<DEFAULT_ACCOUNT>` / `<USER>` 占位符。

版本组合见上方「环境基线」与 `base-cluster/all-in-one-cluster-manual.md`；
两套参数（3090 双盘 / 3060 三节点）同时充当生成器的回归夹具：`make sites` 会分别渲染一遍并对照。
