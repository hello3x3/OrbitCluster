# OrbitCluster

面向**实验室小型 GPU 集群**的一体化工程：把「集群怎么搭、用户怎么自助用、换台机器怎么复现」
固化成可照做的文档与可安装的代码。集群本体为 **Slurm + enroot/pyxis** 容器化 GPU 调度。

> **部署从 [`oa/00-生成器部署.md`](oa/00-生成器部署.md) 开始** —— 改一个 `provision/cluster.conf`，
> `make` 出全部机器配置与文档，再按 `MANIFEST.md` 下发。本文档与其余手册是深入参考。

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
│   ├── render.sh                    #   生成器（只生成文件，不 ssh、不改任何机器）
│   ├── lib/render_docs.py           #   markdown 占位符替换 + 待人工核对的报告
│   ├── templates/                   #   目标文件模板（hosts/slurm/gres/exports/fstab/sshd…）
│   ├── conf/                        #   两套**真实**集群参数（回归夹具，与真机对照用）
│   └── out/                         #   生成物（已 gitignore）
├── base-cluster/                    # ① 底层集群（门户的前提）
│   ├── all-in-one-cluster-manual.md # 全量部署手册（1753 行，逐章带验收命令）
│   ├── grafana-slurm-dashboard.json # Grafana 仪表盘（= 手册附录 B）
│   ├── important.md                 # 交互容器 SSH 登录的 srun 示例
│   ├── images/                      # 交互容器镜像的构建源（Dockerfile + 加固的 start_ssh.sh）
│   └── scripts/cluster-admin/       # /opt/cluster-admin 三个脚本（手册 7.5 的正文版）
│       └── add-user.sh / set-quota.sh / show-quota.sh
│                                    #   ★ 不写死主机名：靠 /share 是否本地盘自动判定
│                                    #     自己是管理节点还是计算节点，两套集群通用
└── oa/                              # ② 门户交付包
    ├── README.md                    # ★ 交付包总览 + 复现路线（先读这个）
    ├── 00-生成器部署.md             # ★ 从这开始：改参数 → make → 下发（含验收命令）
    ├── 01-部署手册.md               # 门户安装/初始化/验收（在 base-cluster 之后读）
    ├── 02-管理手册.md               # 用户/套餐/代申请/密码文件/备份恢复/排障
    ├── 03-使用手册.md               # 面向最终用户：登录、资料、申请、连接、日志、停机
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
   `<ADMIN>` / `<GPU01>` / `<LAN_CIDR>` / `<USER>` 等占位符被填成本集群实值，直接可读、可交付。
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
| `deploy/etc/chrony/*`、`deploy/etc/cluster-portal/*` | 追加 / 同路径 | 管理节点 |
| `base-cluster/scripts/cluster-admin/*.sh` | `/opt/cluster-admin/` | 管理节点（就地模式不复制副本，直接 rsync 源目录）|
| `README.md`、`base-cluster/**/*.md`、`oa/**/*.md` | 就地替换，直接阅读 | — |
| `deploy/MANIFEST.md` | 下发清单本身 | — |

### ① 部署底层集群（管理节点 + 各计算节点）

按 **`base-cluster/all-in-one-cluster-manual.md`** 逐章执行 —— 认准**渲染后**的
这一份：里面的占位符已全部填成你的真实值，每章都带验收命令。

- 手册里凡是要落地 `/etc/hosts`、`slurm.conf`、`/etc/exports`、`/etc/fstab` 的地方，
  **直接用 `deploy/etc/` 那份**，不要手写。
- `/opt/cluster-admin/` 三个脚本取 `base-cluster/scripts/cluster-admin/`（或 `out/opt/cluster-admin/`）。
- 交互容器镜像的构建源在 `base-cluster/images/`，含加固过的 `start_ssh.sh`。

> **换集群 = 改参数 + 重新生成**：节点数 / 卡型 / ssh 端口 / 存储布局 / 套餐都写在
> `provision/cluster.conf`，改完 `make`，站点配置（site.conf、plans.json）与全部 `/etc` 配置
> 一起生成到 `deploy/`。门户代码本就不写死 ssh 端口与 GPU 型号，节点列表、分区名运行时探测，
> 因此**换机器不需要改代码**。

### ② 部署门户（管理节点）

按 **`oa/01-部署手册.md`** 把 `oa/cluster-portal/` 拷到管理节点一键安装：

```bash
deploy/install.sh <代码目录> <端口> [站点目录]   # 幂等
                                                # 自动创建默认管理员 root
                                                # 初始口令落盘 /root/.cluster-portal-admin
```

安装器会把应用代码设为 **root 属主**（`portal` 进程不可写代码），并对 systemd 单元做沙箱加固
（`ProtectSystem=strict` + `ReadWritePaths` + `UMask=0077`）。

### ③ 验收

```bash
oa/scripts/verify-install.sh     # 安装自检：含 sshd 白名单 / 代码属主 / enroot 断言
oa/scripts/e2e_security.sh       # 端到端安全验收：用户能进自己的容器、不能进宿主机
oa/scripts/e2e_accounts.py       # 建号全流程（用保留 UID 段，测完自动清理并断言无残留）
```

再按 `01-部署手册.md` 的「验收」一节做一遍人工确认。

### ④ 日常使用

| 我要做什么 | 看哪本 / 敲什么 |
|---|---|
| 开通用户、改配额、代申请、备份恢复、排障 | `oa/02-管理手册.md` |
| 登录门户、申请资源、连容器、看日志、停机 | `oa/03-使用手册.md` |
| 加机器 / 换卡型 / 换端口等改参数 | 编辑 `provision/cluster.conf` → `make` → 下发变化的文件 |
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
| GPU 型号展示名 | site.conf 的 `GPU_MODEL_MAP`（纯数字 token 自动补 `RTX `） | 一般不用改 |
| 套餐种子 | site.conf 的 `SEED_PLANS` 指向的 JSON | 按机型改 |

`site.conf` 由门户进程（`portalapp/siteconf.py`）与 root 助手（`deploy/portal-ctl`）**同时**读取，
优先级：环境变量 `PORTAL_<KEY>` > site.conf > 代码内置默认值。

## 本地开发与测试

门户测试分两层，都不需要 root：

```bash
cd oa/cluster-portal

# 本地冒烟：用假 ctl 层，不碰集群（覆盖路由/校验/状态机/权限）
python3 -m venv .venv-test
.venv-test/bin/pip install -r requirements.txt requests
.venv-test/bin/python tests/smoke_local.py          # 成功打印 SMOKE_OK

# 真机端到端验收（需门户已部署且本机可 ssh root@<ADMIN>）
E2E_BASE=http://<ADMIN>:8000 .venv-test/bin/python tests/e2e_live.py
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

已落地的两套实例：

| 实例 | 节点构成 | 卡 | sshd | 门户 | 档案（含真实主机名/IP） |
|---|---|---|---|---|---|
| A（现场实例） | 3 节点：管理/登录 + 2 计算 | 各 1×RTX 3060 | 2180 | 管理节点 :8000 | `provision/cluster.conf` |
| B（另一套） | 2 节点：管理兼计算 + 1 计算 | 各 **3×RTX 3090** | 2022 | 管理节点 :8000 | `provision/conf/3090-2node.conf` |

> 本表刻意不写主机名 —— 它同时描述两套集群，写成占位符会在渲染时被填成**本集群**的值而失真。
> 两套的真实主机名/IP 分别在 `provision/cluster.conf` 与 `provision/conf/3090-2node.conf` 里。

版本组合见上方「环境基线」与 `base-cluster/all-in-one-cluster-manual.md`；
第二套参数（3090）同时充当生成器的回归夹具：`make sites` 会用它渲染一遍并对照。
