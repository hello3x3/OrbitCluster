# OrbitCluster 集群门户（Cluster Portal）

面向实验室 Slurm + enroot/pyxis 集群的**小型 Web 门户**，支撑 50+ 用户同时注册申请资源。
部署在登录/管理节点（本集群 `admin`），用浏览器完成：账号开通、资料维护、资源申请、
SSH 进容器、看日志、停机。

- 运行位置：`http://admin:8000`（内网 `http://<ADMIN_IP>:8000`）
- 技术栈：Python 3 + Flask + SQLite(WAL) + waitress；root 特权收敛到白名单助手
  `/usr/local/sbin/portal-ctl`（sudoers 只放行这一个命令给运行账号 `portal`）

## 功能（对照需求）

| 需求 | 实现 |
|---|---|
| 登录系统；用户须注册才能使用；**仅管理员可注册新用户** | 无公开注册页；管理员在「用户管理」开通账号（普通用户自动在 `admin/<GPU02>/<GPU03>` 建号 + 配额 + sacctmgr 关联，也可对已存在的 OS 账号只开通门户） |
| 申请前必须维护个人信息：**SSH 登录密钥**、**想要的端口**（≥10000、避开常用/他人已申请端口） | 「个人资料」页管理多把 SSH 公钥（写入该用户 `~/.ssh/authorized_keys`，门户统一维护）与端口池；未满足“≥1 密钥 且 ≥1 端口”前禁止申请。端口校验：10000–65535、不在保留常用端口表、全门户唯一 |
| 资源申请界面：镜像分组下拉(公共 `/share/images/*.sqsh` + **个人镜像** `/share/images/<用户名>/`) + **管理员可配置套餐**(名称/CPU/GPU型号/内存/最长时长 maxtime) + 任务名(必填) + 时长(默认12h，**≤套餐maxtime**，超出提示找管理员) + 节点(默认留空=Slurm自动调度) + SSH端口；可**同时申请多个**；申请后可**停机**/看日志；界面**不展示提交命令** | 「申请资源」单页提交真实 `sbatch` 作业；时长超过套餐 maxtime 时提示"需平台管理员协助申请"，管理员用「代申请资源」可代任何人提交（用该用户端口，底层 OS root `runuser`=等价 `sudo -u`，配额归属被代用户）；运行中资源「连接/详情」直接给出 **`ssh -p <端口> 用户名@真实节点IP`** |
| **镜像保存**：容器启动后，用户在运行中的资源上「保存镜像」把当前容器状态导出为个人镜像（`/share/images/<用户>/<名称>.sqsh`，名称仅英文/数字/下划线，计入该用户 /share 配额）；资源**到期自动保存一次**（`auto_<任务名>_<时间戳>.sqsh`）后自动停机 | 作业以 `--container-name=portal --container-writable` 提交（rootfs 落在计算节点 `ENROOT_DATA_PATH` 下 `pyxis_<jobid>_portal`，作业结束自动清理）；保存 = 以该用户身份 `enroot export` 该运行中容器（分钟级，异步执行并回写该资源行的保存结果）；到期扫描线程按「开始时刻+时长」触发「先自动保存再停机」，Slurm 时长内已含 15 分钟保存余量 |
| **配额/额度透明**：普通用户在「我的资源」读自己磁盘配额（OS repquota 实读）与 Slurm 关联/QoS/优先级/总额度（sacctmgr 实读）；管理员在「用户管理」**改配额**（软=硬，直写 OS setquota 并回读确认，非门户 DB 记录） | 配额不是门户库存量：展示与修改都以集群 OS/Slurm 为权威；系统保留账号（root/portal…）与无同名 OS 账号的纯平台管理员不可设配额 |

## 目录结构

```
cluster-portal/
├── deploy/
│   ├── install.sh        # root 在 admin 上执行的一键安装
│   ├── portal-ctl        # root 特权助手（提交/建号/停机/日志/公钥/sinfo/配额/slurm-info/
│   │                     #   save-image/images/rm-log），web 经 sudo 调用
│   └── install.sh 内联   # systemd 单元、sudoers 片段
├── portalapp/            # Flask 应用
│   ├── app.py            # 路由/页面/权限/CSRF
│   ├── db.py             # SQLite 模型、种子套餐与常用端口表、实例并发约束
│   ├── auth.py           # PBKDF2 口令哈希、公钥格式校验、CSRF
│   ├── ctl.py            # portal-ctl 客户端（sudo -n）＋节点状态缓存
│   ├── templates/ static/# 中文界面（Jinja2 + 原生 JS/CSS）
├── run.py                # waitress 生产入口
├── bootstrap.py          # 初始化/重置门户账号（--force）
├── requirements.txt      # flask、waitress
└── tests/                # smoke_local.py（假 ctl 冒烟） / e2e_live.py（真机验收）
```

## 部署（在 admin 上以 root 执行）

```bash
# 1) 把本目录拷到 admin（示例）：
scp -r cluster-portal root@admin:/tmp/cluster-portal-src

# 2) 安装并启动（端口可改，默认 8000）：
bash /tmp/cluster-portal-src/deploy/install.sh /tmp/cluster-portal-src 8000

# 3) 默认管理员 root 的初始密码（安装时自动生成，仅 root 可读）：
cat /root/.cluster-portal-admin        # 默认管理员账号: root（最高管理员）
```

安装脚本做的事：创建运行账号 `portal`、数据目录 `/var/lib/cluster-portal`；
代码部署到 `/opt/cluster-portal`；建立 venv 装依赖；安装 `/usr/local/sbin/portal-ctl`
与 sudoers 白名单（`portal ALL=(root) NOPASSWD: /usr/local/sbin/portal-ctl`）；
生成 systemd 服务 `cluster-portal`（waitress，0.0.0.0:8000，开机自启）。

## 日常使用

管理员：
1. 登录 `root` 账号（系统默认管理员/最高管理员，安装时自动创建，初始密码见
   `/root/.cluster-portal-admin`）→「用户管理」→ 开通用户：填用户名（=Linux 账号）、
   配额、初始门户密码（留空自动生成，**只显示一次**）。
   - 方式「自动建号」：三节点同步建号（UID 一致）+ `/share` 配额 + sacctmgr 关联。
   - 方式「OS 账号已存在」：跳过建号，仅初始化门户目录。
2. 「资源套餐」维护预设的 CPU/内存/GPU 组合（启用/停用/增删；镜像由 `/share/images` 自动发现，无需登记）。
3. 可停用/启用、重置密码（随机生成，仅显示一次）、**删除用户**、**改配额**。
   - 「改配额」：输入如 `200G / 500G / 1T`，门户以 root 执行 `set-quota.sh` 直写 OS
     `/share` 配额（软=硬即时生效），并回读 `repquota` 确认；「配额（OS 实读）」列显示
     「已用 / 硬上限」。普通用户行任何管理员可改；管理员行仅 root 或本人可改；
     系统保留账号与无同名 OS 账号者不能设配额。
   - 「删除」：对门户代建号的普通用户会先注销集群账号（终止其全部作业，删除
     `admin/<GPU02>/<GPU03>` 上的账号、sacctmgr 关联与 NFS 家目录数据）再删门户记录；
     对管理员或以“existing”方式开通的用户只删门户记录、保留其 OS 账号与数据。
   - 用户列表列含义：**SSH密钥**＝该用户已登记公钥数（写入 `~/.ssh/authorized_keys`）；
     **端口数**＝其端口池登记数；**活跃资源**＝排队/运行中实例数。

用户：
1. 用管理员给的账号密码登录 →「个人资料」粘贴 **SSH 公钥**（`ssh-keygen -t ed25519`
   生成的 `.pub` 内容）并登记 **端口**（如 28771，可登记多个）。
2. 「申请资源」→ 选镜像（公共/我的 两组）与套餐 → 任务名/时长/节点/端口 → 提交。
3. 「我的资源」：顶部「我的配额/额度」展示你的磁盘配额（OS 实读已用/硬上限）与
   Slurm 关联账号/QoS/优先级/总额度（sacctmgr 实读，未设限制显示“不限”）；下方为
   实例列表：运行中点「连接/详情」会给出 **`ssh -p <端口> 用户名@真实节点IP`** 命令
   （直接给 IP，非节点名），用登记公钥对应的私钥即可登录容器；可「停机」/「日志」；
   **运行中的资源可「保存镜像」**（把容器当前状态导出为个人镜像，保存结果显示在行下方；
   个人镜像计入你的 /share 配额）；资源**到期时门户自动保存一次**（`auto_<任务名>_<时间戳>.sqsh`）再停机；
   对已停止/结束的实例可 **「重新启动」**（复用同一行，按原参数重新入队）或
   **「删除」**（删记录+清理该作业日志文件）。

## 运维

```bash
systemctl status cluster-portal            # 服务状态
journalctl -u cluster-portal -n 100        # 服务日志
systemctl restart cluster-portal           # 重启
curl http://127.0.0.1:8000/login           # 健康检查

# 重置默认管理员(root)的门户密码（在 admin 节点以 OS root 执行）：
PORTAL_DATA=/var/lib/cluster-portal /opt/cluster-portal/venv/bin/python \
  /opt/cluster-portal/bootstrap.py root <新密码> admin --force

# 数据：/var/lib/cluster-portal/portal.db（备份=拷走该文件，建议先 systemctl stop）
# 代码：/opt/cluster-portal；助手：/usr/local/sbin/portal-ctl
# 卸载：systemctl disable --now cluster-portal；rm -rf /opt/cluster-portal /var/lib/cluster-portal
#       /usr/local/sbin/portal-ctl /etc/sudoers.d/cluster-portal
```

手动同步到新部署机器后重跑 `deploy/install.sh` 即可升级（rsync 覆盖代码、不动数据）。

## 安全模型与已知边界

- web 进程以非特权用户 `portal` 运行；所有特权动作都经过 `portal-ctl`（root），
  参数在助手内做严格正则/白名单校验（用户名、配额、端口、节点、镜像路径、时长），
  提交作业用 `runuser -u <用户> -- sbatch ...`，不拼接任意 shell。
- 门户用户列表是「门户+集群账号」双写；`portal` 用户只开放给 sudoers 白名单命令，
  请不要为它放开其它 sudo 权限，也不要给服务单元加 `NoNewPrivileges`（会阻断 sudo）。
- **SSH 公钥的两处存储**（这是最容易困惑的地方）：
  - 「个人资料」页显示的是**门户自己 `ssh_keys` 表**里的记录，**不是**直接读 OS 文件；
  - 用户主动增删公钥时，门户会把整份清单写回该用户的 `~/.ssh/authorized_keys`
    （因此经门户删除某把公钥会同步生效）。
  - **打开「个人资料」页时会自动做一次单向导入：OS → 门户 DB**（只补缺失项，
    **绝不回写 OS 文件**）。所以如果你直接手工往 `~/.ssh/authorized_keys` 里加了公钥，
    打开一次个人资料页就能在门户里看到它。
  - 但**不建议手工改** `authorized_keys`：门户回写时是"整体重写"，带选项的行
    （如 `from="..."`、`command="..."` 前缀）因为不被识别为公钥，会在下次经门户
    增删公钥时被静默丢弃。
- **作业能登录门户但一提交就报 `Invalid account or account/partition combination specified`**：
  该用户在 Slurm 会计里没有账户关联，而集群开着 `AccountingStorageEnforce=associations`。
  一条命令补：`portal-ctl ensure-assoc <用户>`。根因是**「OS 账号已存在」这条开通路径不经过
  `add-user.sh`**（只有 `add-user.sh` 会建关联），历史上漏了这一步；现在 `portal-ctl` 在
  **开通 / 初始化 / 提交前**都会幂等补齐（见 `_ensure_assoc`），账户名取站点配置的 `ACCOUNT`（默认 `lab`）。
- **作业日志里出现 `couldn't chdir to '/opt/cluster-portal': No such file or directory`**：
  门户服务的工作目录是 `/opt/cluster-portal`（systemd `WorkingDirectory`），而 `sbatch` 默认让
  作业在**提交者进程的 cwd** 下启动 —— 该目录只存在于管理节点，作业落到计算节点就会 chdir 失败
  并回退到 `/tmp`（作业仍能跑，但工作目录不对）。已修：提交时显式带 `--chdir=<用户家目录>`
  （家目录在 NFS 上、各节点都有），同时把被透传进来的 `PWD` 环境变量一并纠正。
- 可用交互镜像目前只有内置 sshd/`start_ssh.sh` 的两个：`cuda12.8.0-devel-ubuntu24.04` 与
  `cuda13.3.1-devel-ubuntu24.04`（镜像下拉由 `/share/images/*.sqsh` 自动扫描，无需登记）。
- 端口在**同一节点**上同一时刻只能有一个实例（门户 DB 唯一约束 + 提交前节点端口预检）；
  不同节点可复用同一端口。节点可留空由 Slurm 自动调度（运行后回填真实节点/IP），指定节点则 `-w` 固定。
- 所有作业以命名容器（`--container-name=portal`）提交：容器 rootfs 成为计算节点
  `/scratch/enroot-data/user-<uid>/pyxis_<jobid>_portal` 的可写目录（保存镜像的前置），
  作业结束由 pyxis 自动清理；因此每次新作业会先在节点本地展开一次镜像（不再走 squashfuse 直挂），
  「运行中」之后容器内 sshd 通常还需 1-2 分钟才就绪。
- 个人镜像目录 `/share/images/<用户名>`（700）由 root 助手维护，web 经 `portal-ctl images` 读取；
  保存的 .sqsh 属主为用户（计入其 /share 配额）；删除由门户代建号的用户会连同该目录一起清理。
- 到期自动保存依赖门户后台线程按「开始运行时刻 + 所选时长」触发（提交时长含 15 分钟余量）：
  若服务当时不可用或导出失败（如配额写满），该次自动保存会跳过并记录原因。
- Slurm 单卡节点排队的注意点：若某节点已有一个 GPU 实例排队，同节点的 CPU 作业可能因
  调度器 `PLANNED` 预留而等待（属 Slurm 正常行为，换空闲节点即可）。
- 「用户管理 → 删除」会对门户建号的用户执行完整注销（三节点账号 + sacctmgr + NFS 家目录），
  对 existing/管理员账号仅删除门户记录。需要更底层的强制清理时仍可用
  `portal-ctl unprovision-user`（root）。
- **平台管理员权限由 root 账号管理**：只有门户账号 `root` 可在用户管理里给其它用户
  「设为管理员 / 取消管理员」；其它管理员不能互相授予/撤销（见「管理员，由 root 管理」）。
- 管理员可用「代申请资源」帮任意有 OS 账号的普通用户提交（选目标用户→用其端口池端口；
  底层 portal-ctl 以 OS root 执行 `runuser`，即等价 `sudo -u <用户>`，配额/计费归属被代用户）。

## 测试

```bash
# 本地冒烟（无需集群/root，假 ctl）：
python3 -m venv .venv-test && .venv-test/bin/pip install -r requirements.txt requests
.venv-test/bin/python tests/smoke_local.py

# 真机验收（需 portal 已部署、本机可 ssh root@admin）：
#   默认以 root（默认管理员）执行；不传 E2E_ADMIN_PWD 时自动读取 admin 节点
#   /etc/cluster-portal/users.passwd 中该用户的当前密码
E2E_BASE=http://admin:8000 .venv-test/bin/python tests/e2e_live.py
# 覆盖：管理员建号(三节点)、登录、资料门槛、密钥/端口与冲突规则、GPU 双实例并发、
#       ssh 进容器见卡、CPU+GPU 同节点并发、日志、停机、端口复用、停用/启用、集群状态、
#       镜像分组(公共/个人)、保存镜像(真实 enroot export)、到期自动保存+自动停机
```
