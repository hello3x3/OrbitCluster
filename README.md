# OrbitCluster

面向**实验室小型 GPU 集群**的一体化工程：把「集群怎么搭、用户怎么自助用、换台机器怎么复现」
固化成可照做的文档与可安装的代码。集群本体为 **Slurm + enroot/pyxis** 容器化 GPU 调度。

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
├── .gitignore / .gitattributes
├── base-cluster/                    # ① 底层集群（门户的前提）
│   ├── all-in-one-cluster-manual.md # 全量部署手册（1690 行，逐章带验收命令）
│   ├── grafana-slurm-dashboard.json # Grafana 仪表盘（= 手册附录 B）
│   └── important.md                 # 交互容器 SSH 登录的 srun 示例
└── oa/                              # ② 门户交付包
    ├── README.md                    # ★ 交付包总览 + 五步复现路线（先读这个）
    ├── 01-部署手册.md               # 门户安装/初始化/验收（在 base-cluster 之后读）
    ├── 02-管理手册.md               # 用户/套餐/代申请/密码文件/备份恢复/排障
    ├── 03-使用手册.md               # 面向最终用户：登录、资料、申请、连接、日志、停机
    ├── cluster-portal/              # 门户完整源码（Flask + SQLite + waitress）
    │   ├── portalapp/               #   应用、鉴权、DB、特权助手客户端、模板与静态资源
    │   ├── deploy/                  #   install.sh 一键安装 + portal-ctl（root 特权助手）
    │   ├── tests/                   #   smoke_local（假 ctl，免集群）/ e2e_live（真机）/ e2e_ssh_flake
    │   ├── run.py / bootstrap.py    #   生产入口 / 账号初始化与重置
    │   └── README.md                #   门户自身文档（功能、安全模型、已知边界）
    ├── config-snapshot/             # 现场非密钥配置快照（env-notes.md 换机器必看）
    └── scripts/                     # backup-portal.sh（整机备份）/ verify-install.sh（自检）
```

代码与文档中出现的 `base-cluster/…`、`oa/…` 均相对**本仓库根目录**；`/opt/cluster-portal`、
`/var/lib/cluster-portal`、`/etc/cluster-portal` 等则是目标机器上的绝对路径。

## 从零复现

1. **读现场快照**：`oa/config-snapshot/env-notes.md`，记下要替换的变量（主机名 / IP / ssh 端口
   2180 / 分区 / 镜像清单）。
2. **部署底层集群**：照 `base-cluster/all-in-one-cluster-manual.md` 逐章执行并跑每章验收。
3. **部署门户**：照 `oa/01-部署手册.md` 把 `oa/cluster-portal/` 拷到管理节点一键安装
   （`deploy/install.sh`，幂等；自动创建默认管理员 `root`，初始口令落盘
   `/root/.cluster-portal-admin`）。
4. **迁移或重设账号**：复制旧机 `/etc/cluster-portal/users.passwd`，或在管理页重建套餐/用户。
5. **验收**：`oa/scripts/verify-install.sh` 自检 + 手册「验收」一节做端到端验证。

日常管理与最终用户说明分别见 `oa/02-管理手册.md`、`oa/03-使用手册.md`。

## 本地开发与测试

门户测试分两层，都不需要 root：

```bash
cd oa/cluster-portal

# 本地冒烟：用假 ctl 层，不碰集群（覆盖路由/校验/状态机/权限）
python3 -m venv .venv-test
.venv-test/bin/pip install -r requirements.txt requests
.venv-test/bin/python tests/smoke_local.py          # 成功打印 SMOKE_OK

# 真机端到端验收（需门户已部署且本机可 ssh root@admin）
E2E_BASE=http://admin:8000 .venv-test/bin/python tests/e2e_live.py
```

## 仓库约定

- **只跟踪源文件**：代码、手册、配置快照、脚本。
- **不跟踪**：`__pycache__/`、虚拟环境（`.venv*`、`venv/`）、门户运行时数据
  （`oa/cluster-portal/var/`、`portal.db*`、`secret`）、日志、`.sqsh` 容器镜像（数 GB，只部署在
  集群 `/share/images/`）。规则见 `.gitignore`。
- **严禁入库明文口令与备份产物**：`users.passwd`、`*.tgz`、`*.key`、`*.pem` 等已在 `.gitignore`
  中屏蔽；模板 `oa/config-snapshot/etc-cluster-portal/users.passwd.example` 例外，它是示例格式说明。
  本仓库定位是「可交付、不含密钥」——门户密码的唯一明文权威只在目标机的
  `/etc/cluster-portal/users.passwd`。
- **可执行位**：`oa/cluster-portal/deploy/{install.sh,portal-ctl}` 与 `oa/scripts/*.sh` 需保持 `755`，
  其余文件 `644`；行尾统一 LF（`.gitattributes`）。

## 环境基线

Ubuntu 24.04 · Slurm 26.05.1（源码编译，含 MySQL 会计 + NVML）· enroot 4.2.1 · pyxis v0.24.0 ·
Python 3.12 · Flask 3.1.3 + waitress。现场实例：`admin`(管理/登录) + `<GPU02>`/`<GPU03>`，
各 1×RTX 3060，sshd 端口 `2180`，分区 `gpu`，门户 `http://admin:8000`。
详见 `oa/config-snapshot/env-notes.md` 与 `versions.txt`。
