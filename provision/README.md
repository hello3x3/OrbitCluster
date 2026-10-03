# provision —— 集群配置生成器（部署时只改一个文件）

把「部署一套新集群要做的全文替换」变成「编辑一个 `cluster.conf`，其余全部生成」。

## 在部署链路里的位置

```
⓪ provision/     ← 本目录：一个 cluster.conf → 全部配置 + 实值版文档（不碰任何机器）
① base-cluster/  ← 按生成的文档部署底层集群（NFS / Slurm / enroot / 监控 / 多用户）
② oa/            ← 按生成的文档部署门户
③ 验收            ← 脚本 + 手册「验收」一节
```

本目录**只生成文件**，不 ssh、不安装、不改任何系统状态；安装动作仍按手册执行。
改集群参数永远改 `cluster.conf` 后重新 `make`，不要直接改机器上的生成物。

## 为什么

原来的做法是：手册里散落着 `<GPU01>`、`<ADMIN_IP>` 等占位符，外加 130 处实值 `admin`；
换机器时要**全局替换**，而 `admin` 同时又是门户的角色名/路由名/账号名 —— 一替换就误伤。

现在：节点名/IP、卡型卡数、ssh 端口、账户名、存储布局、套餐……**全部集中在 `cluster.conf`**，
目标文件由脚本生成。机器上的 `/etc/hosts`、`/etc/slurm/*.conf`、`/etc/exports`、`/etc/fstab`
追加行、enroot 配置、sshd 加固文件、门户 `site.conf`/`plans.json` 都从这里推出来。

## 用法

```bash
# 在仓库根目录：一条 make 就够（首次会自动生成 provision/cluster.conf 并提示你先改）
make                      # ★ 就地替换：文档 + 机器配置都写回当前仓库
make SET="SSH_PORT=2222 USER=alice"   # 临时覆盖个别配置项，不写回文件
make check                # 打印「需要人工核对」清单
make reset                # 还原仓库模板
make out                  # 不改仓库，另存到 provision/out/
make help                 # 全部目标

# 等价的手工方式：
cp provision/cluster.conf.example provision/cluster.conf   # 1) 拷一份，改里面的值
$EDITOR provision/cluster.conf
provision/render.sh -c provision/cluster.conf --in-place   # 2) 就地替换到仓库
cat MANIFEST.md                                            # 3) 看"哪台机器放哪些文件"
provision/render.sh -c provision/cluster.conf --print      #    只打印不落盘（先看效果）
provision/reset.sh                                         # 4) 还原模板
```

`render.sh` **只生成文件，不 ssh 任何机器、不改任何系统状态**。
（真正的分阶段执行器 `deploy.sh` 是后续第 ② 步，本目录暂不含。）

## 目录

```
provision/
├── README.md                  # 本文件
├── render.sh                  # 生成器（纯 bash + 模板，无第三方依赖）
├── cluster.conf.example       # ★ 带注释的样例：拷贝 → 编辑
├── conf/
│   ├── field-3node.conf       # 现场集群（admin+gpu02+gpu03，1×RTX3060，sshd 2180）
│   └── 3090-2node.conf        # 3090 集群（2 节点，3×RTX3090，sshd 2022，镜像独立盘）
├── templates/                 # 目标文件的模板（@@变量@@ 占位）
└── out/                       # 生成物（已 gitignore）
```

## `cluster.conf` 里要改什么

| 段 | 键 | 说明 |
|---|---|---|
| 集群标识 | `CLUSTER_NAME` / `ACCOUNT` | slurm 集群名 / 会计账户名（建号脚本与门户共用） |
| 网络系统 | `LAN_CIDR` / `SSH_PORT` / `TIMEZONE` / `USER` | 网段、sshd 端口、时区、集群用户（文档示例里的 `<USER>`） |
| 存储 | `SHARE_DEVICE` / `SHARE_MOUNT` / `IMAGES_DEVICE` / `IMAGES_MOUNT` | 数据盘与镜像盘（`IMAGES_DEVICE` 留空=同盘） |
| Slurm | `SLURM_VERSION` / `PARTITION` / `GRES_MODE` / `GPU_PREFIX` | 版本、分区名、gres 生成方式、展示名前缀 |
| 门户 | `PORTAL_PORT` | 门户监听端口 |
| 节点 | `NODE <名>=<IP> role=… gpus=… gpu_model=… cpus=… mem_mb=… [sockets=… threads=…]` | **第一个 NODE 必须是管理节点** |

`cpus` / `mem_mb` 取自该机 `slurmd -C | head -1`；`sockets`/`threads` 不填则按单路 2 线程估算。
留空 `cpus`/`mem_mb` 也能渲染，但 `slurm.conf` 里会是 `@@待实测@@` 占位符，并出现在
`MANIFEST.md` 的"待实测回填"清单里 —— **上线前必须填**。

## 生成物

```
out/
├── etc/hosts.<节点>            ← 每台一份：只有 127.0.1.1 那行不同（用手册 1.3 的踩坑点）
├── etc/slurm/slurm.conf        ← 节点行由 NODE 行生成（含 Gres=）
├── etc/slurm/gres.conf         ← explicit 模式按卡数生成；auto 模式就是 AutoDetect=nvml
├── etc/enroot/enroot.conf
├── etc/ssh/sshd_config.d/10-portal-only.conf   ← 门户模式必做（禁止用户登录宿主机）
├── etc/exports                 ← 单盘一行 / 独立镜像盘两行
├── etc/fstab.<节点>            ← 只给"要追加的数据盘/NFS 行"，系统盘行保留安装器原文
├── etc/chrony/chrony.conf.append.{mgr,gpu}
├── etc/cluster-portal/site.conf + plans.json   ← 门户站点配置与套餐种子
├── docs/                       ← ★ markdown 渲染成实值版（见下节）
│   ├── README.md、base-cluster/all-in-one-cluster-manual.md
│   ├── oa/{01-部署手册,02-管理手册,03-使用手册}.md、oa/cluster-portal/README.md
│   └── _REPLACEMENT-REPORT.md  ← 替换统计 + 需人工核对的清单
├── opt/cluster-admin/*.sh      ← 直接拷仓库里那份，避免两处维护
└── MANIFEST.md                 ← 下发清单（哪台机器放哪些文件）
```

## 文档渲染（`<占位符>` → 本集群实值）

仓库里的 markdown 是**模板**：站点相关的值一律写成 `<占位符>`，
`render.sh` 会渲染成实值版放进 `out/docs/`，可直接交给部署/使用者阅读。

| 占位符 | 来自 cluster.conf |
|---|---|
| `<ADMIN>` `<ADMIN_IP>` | 第一个 NODE 行 |
| `<GPU01>`…`<GPU0N>`、`<GPU01_IP>`… | 第 1…N-1 个**计算**节点（按位置从 01 编号；管理节点只算 `<ADMIN>`） |
| `<ADMIN_CPUS>` / `<GPU01_MEM>` / `<GPU01_GPUS>` … | 各 NODE 行的 cpus/mem_mb/gpus（手册 §4.2 的 slurm.conf 示例用） |
| `<GPU_TYPE>` | 第一个带卡节点的 gpu_model |
| `<LAN_CIDR>` `<SSH_PORT>` `<CLUSTER_NAME>` `<ACCOUNT>` `<USER>` `<TIMEZONE>` `<PORTAL_PORT>` `<SHARE_MOUNT>` `<IMAGES_MOUNT>` `<PARTITION>` `<SLURM_VERSION>` | 同名配置键 |

不参与替换的（本就不是站点值）：`<新建用户名>`（手册命令模板，表示"这里填你要建的用户名"） `<UID>` `<KEY>` `<YYYYMMDD_HHMM>` `<GRAFANA_PASSWORD>` `<SLURMDB_PASSWORD>`；`base-cluster/images/**/Dockerfile` 里的 `<MAINTAINER_NAME>` / `<MAINTAINER_EMAIL>` 也不渲染（Dockerfile 不在 markdown 渲染范围内，构建时自行替换）。

> **仓库里不写任何真实姓名**：文档示例中的集群用户一律是 `<USER>`（渲染成 cluster.conf 里的 `USER`）；
> 手册里"新用户上线流程"的命令模板用 `<新建用户名>`（**不替换**，因为那是"此处填你的用户名"的意思，
> 一旦被替换成某个具体账号，命令就从"模板"变成了"照抄即可"，会误导读者）；
> 真实用户只出现在 `oa/config-snapshot/users.json` 的 `<USERNAME>` 占位里。

### `admin` 的处理（重要，最容易误伤）

`admin` 在文档里有**四种含义**，只有第一种该替换：

| 含义 | 例子 | 处理 |
|---|---|---|
| ① 管理节点**主机名** | ``scp -r cluster-portal root@admin:/tmp``、`[admin]`、`http://admin:8000` | ✅ 仓库里已改写成 `<ADMIN>`，渲染时填实值 |
| ② 门户**角色名** | `bootstrap.py（root 缺省即 admin 角色）`、`bootstrap.py root <新密码> admin --force` | ❌ 保持 `admin` |
| ③ 门户**账号名** | `` `admin`（另一管理员账号…） `` | ❌ 保持 `admin` |
| ④ **历史引文** | `旧代码把主机名硬编码成老集群的 admin（node_hostname() != "admin"）` | ❌ 保持 `admin` |

另外这些词虽含 `admin` 但不是主机名，已被词边界规则排除：
`cluster-admin`（路径）、`admin_users` / `admin_required`（路由与装饰器）、
`.cluster-portal-admin`（文件名）、`reset-admin`。

渲染后在 `out/docs/_REPLACEMENT-REPORT.md` 里给出三份清单：

1. 每个文档替换了多少处；
2. **原文里没有被占位符覆盖的小写 `admin`**（当前 8 处，全部是②③④）——
   若哪一行其实是主机名，报告会让它暴露出来，而不是静默出错；
3. 文档引用了但 `cluster.conf` 里没有的节点占位符（如 2 节点集群读 3 节点示例，会提示 `<GPU01>` 系列未映射）。

> 约定：新增/修改文档时，**站点值一律写 `<占位符>`**；`admin` 只在表示角色/账号/历史时保留原样。
> 排除渲染的目录：`oa/sites/**`（别的站点档案）、`oa/config-snapshot/**`（现场实值快照）、
> `provision/**`（生成器自身文档）、`.venv*/`、`out*/`。

## 自检：生成器 vs 两套真实集群

`conf/` 下两个文件不是示例，是**已有集群的真实参数**，用来回归生成器：

```bash
# 现场集群：与 admin 上的真实 /etc/{hosts,exports,slurm/{slurm,gres}.conf,enroot/enroot.conf} 对照
provision/render.sh -c provision/conf/field-3node.conf --clean

# 3090 集群：与 oa/sites/3090-2node/ 的档案对照（档案里是 <ADMIN> 占位符，需先还原）
provision/render.sh -c provision/conf/3090-2node.conf --clean -o provision/out-3090
```

最近一次核对结果（去掉注释后逐行比较）：

| 目标文件 | 现场集群 | 3090 集群 |
|---|---|---|
| `slurm.conf` | ✅ 一致 | ✅ 一致 |
| `gres.conf` | ✅ 一致 | ✅ 一致 |
| `/etc/exports` | ✅ 一致 | ✅ 一致（仅空格对齐不同） |
| `enroot.conf` | ✅ 一致 | ✅ 一致 |
| `/etc/hosts` | ✅（真实机器上多一条**过期**的 `gpu01` 条目，生成器只输出配置内的节点） | ✅（仅条目顺序不同） |
| `site.conf` | ✅（生成器会多写 `SEED_PLANS=`，让 `plans.json` 真正生效） | — |
| `fstab` | ✅ 数据盘行一致 | ✅ NFS 行一致（生成器不输出机器专属的系统盘 UUID 行） |

## 注意

* **改了 `cluster.conf` 就要重新 render**，不要直接改机器上的生成物 —— 否则下次渲染会覆盖，
  而且 `slurm.conf` 的 `Gres=` 与 `gres.conf` 容易不同步（3090 站点 README 记的那个坑）。
* `GRES_MODE=auto`（`AutoDetect=nvml`）推导出的类型名可能是 `nvidia_geforce_rtx_3090`
  这种长名字，会让节点 `IDLE+DRAIN+INVALID_REG`；想要干净名字就用 `GRES_MODE=explicit`。
* 生成器**不处理**：静态 IP/netplan、NVIDIA 驱动、munge 密钥分发、Slurm 源码编译 ——
  这些仍按 `base-cluster/all-in-one-cluster-manual.md` 执行（后续 `deploy.sh` 会把它们也包进来）。
