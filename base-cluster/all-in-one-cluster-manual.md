# 实验室 GPU 集群全量部署手册（All-in-One）

> **范围**：本手册把三份文档合并为一份，按顺序可在**新机器**上从零手动部署并验收通过：
> NFS 共享盘 → Slurm 26.05.1（源码编译，**一步到位带会计插件**）→ enroot + pyxis 容器 → Prometheus+Grafana 监控 → 多用户（磁盘配额 + slurmdbd 会计 + 建号/扩容脚本 + fairshare）。
> 内容经过 2026-09-05 在本集群（`admin/<GPU02>/<GPU03>`，Ubuntu 24.04）**实际部署并逐条验收**，所有踩过的坑都已固化为正文章节的操作步骤或醒目标注，照做即可一次成功。
>
> 源文档（本手册是它们的合并升级版）：`cluster-deploy-manual.md`（集群本体）、`slurm-monitoring-runbook.md`（监控）、`slurm-multiuser-runbook.md`（多用户化）。

---

## 0. 总览

### 0.1 架构与角色

```
admin  (管理节点 = NFS 服务端 + slurmctld + slurmdbd + slurmd* + Prometheus + Grafana + 登录节点)
<GPU02>  (GPU 计算节点 = slurmd + 驱动 + enroot/pyxis)
<GPU03>  (GPU 计算节点 = slurmd + 驱动 + enroot/pyxis)
* 本集群 admin 也插了 GPU 并作为第 3 个计算节点；若你的管理节点不带 GPU，保持纯管理节点即可（相关步骤有标注）
```

| 角色 | 需要装的东西（按章节） |
|---|---|
| admin | 第 1 章基础 + 第 2 章 NFS 服务端 + 第 3/4 章 Slurm（编译+控制端+slurmd）+ 第 5 章 enroot/pyxis + 第 6 章监控 + 第 7 章多用户 |
| `<GPU02>` / `<GPU03>` | 第 1 章基础 + 第 2 章 NFS 客户端 + 第 3/4 章 Slurm（编译+计算端）+ 第 5 章 enroot/pyxis + 第 7 章配额客户端 |

### 0.2 变量表（先定好，全文替换）

| 变量 | 本手册示例值 | 说明 |
|---|---|---|
| 管理节点主机名/IP | `admin` / `<ADMIN_IP>` | 换成你的实际值 |
| GPU 节点主机名/IP | `<GPU02>`、`<GPU03>` / `<GPU02_IP>`、`<GPU03_IP>` | 加节点按同格式追加 |
| 集群名 ClusterName | `lab` | slurm.conf 与 sacctmgr 必须同名 |
| 默认账户名 | `lab` | sacctmgr account 与用户关联 |
| 数据盘 | `/dev/sda` | ⚠️ 先 `lsblk` 确认不是系统盘（第 2.1 节） |
| 用户 | `lab`，UID=1000 | 所有节点 UID 必须一致 |
| NFS 共享 | 只导出 `/share` 一块 | 家目录 `/share/home/<user>` |
| Slurm 版本 | 26.05.1 | 源码编译（本手册唯一主路径） |
| enroot / pyxis | 4.2.1 / v0.24.0 | enroot 官方只发 GitHub .deb |
| Grafana 密码 | `<GRAFANA_PASSWORD>` | 文档不落明文，自设 |
| slurmdbd DB 密码 | `<SLURMDB_PASSWORD>` | 随机生成存 `/root/.slurmdb.pass`(600) |

### 0.3 端口总表

| 端口 | 服务 | 节点 |
|---|---|---|
| 22 / 2180 | sshd（本集群 sshd 实测在 2180，标准环境为 22） | 全部 |
| 111/2049/875 | NFS / rpcbind / rquotad（配额查询） | admin |
| 6817 / 6818 | slurmctld / slurmd | 全部 |
| 6819 | slurmdbd | admin |
| 3306 | MariaDB | admin（仅本机） |
| 9090 / 9100 / 8080 / 8085 / 9835 | Prometheus / node_exporter / slurm 导出器 / 队列导出器 / GPU 导出器 | 见第 6 章 |
| 3000 | Grafana | admin |

### 0.4 执行约定（重要）

1. **全程以 root 执行**：命令里**不写 sudo**。登录方式不限（控制台/ssh root），root 下要切换成普通用户验证时用 `runuser -u <用户> -- <命令>`。
2. **每段命令前标注执行机器**：`[全部节点]` = 每台都要跑；`[admin]` = 只在管理节点；`[<GPU02>/<GPU03>]` = 每台 GPU 节点。
3. 文档不写远程登录包装（ssh/scp），**换机器执行靠人工切换**；唯一需要"文件搬家"的两处（munge 密钥、slurm.conf 同步）给出明确说明与替代写法（见 4.3 / 7.5）。
4. `<占位符>` 一律替换成 0.2 变量表的值；`#` 注释可直接复制。
5. **每个大章节末尾都有验收命令**，输出符合标注才算通过，再进入下一章。

### 0.5 随附文件清单（部署时复制到机器上或按文档内联写入）

| 文件 | 用途 | 存放 |
|---|---|---|
| 本手册附录 B 的仪表盘 JSON（或同目录 `grafana-slurm-dashboard.json`） | Grafana 导入 | admin |
| `.sqsh` 镜像（第 5.6 节下载/导入） | 容器作业 | `/share/images/` |
| 第 7 章脚本（add-user.sh / set-quota.sh / show-quota.sh） | 建号与配额 | `/opt/cluster-admin/` |

---

## 1. 全节点基础（[全部节点]）

### 1.1 系统安装
Ubuntu 24.04 Server **Minimized**，软件源只勾 OpenSSH server；先给 root 设密码或准备 root 登录方式，后续章节均以 root 操作。

### 1.2 静态 IP 与主机名
```bash
# 查看网卡名
ip link

# 写 netplan（若已有 50-cloud-init.yaml 则改它，或新建 99-static.yaml 覆盖）
cat > /etc/netplan/99-static.yaml <<'EOF'
network:
  version: 2
  ethernets:
    <网卡名如 eno1>:
      addresses: [<本机IP>/24]
      routes:
        - to: default
          via: <网关IP>
      nameservers:
        addresses: [<网关IP>, 8.8.8.8]
EOF
netplan apply     # ⚠️ 切静态 IP 时 ssh 可能断线，建议控制台操作

hostnamectl set-hostname <主机名>     # admin / <GPU02> / <GPU03>
```

### 1.3 /etc/hosts（[全部节点]，内容相同，含自己）
```bash
cat >> /etc/hosts <<'EOF'
<ADMIN_IP> admin
<GPU02_IP> <GPU02>
<GPU03_IP> <GPU03>
EOF
# ⚠️ 把系统自动生成的“127.0.1.1 <安装时主机名>”改成“127.0.1.1 <该机正式主机名>”
# ⚠️ 每台都要包含它自己：缺失会导致 slurmd 重启后 Unable to bind listen port (6818)、
#    slurmctld/scontrol 解析失败（见 4.6⑥ 与 8.2 排障表）
ping -c1 admin && ping -c1 <GPU02> && ping -c1 <GPU03>   # 两两互通
```

### 1.4 内核参数（Ubuntu 24.04 限制 userns，enroot 必需）
```bash
cat > /etc/sysctl.d/99-containers.conf <<'EOF'
kernel.unprivileged_userns_clone=1
kernel.apparmor_restrict_unprivileged_unconfined=0
kernel.apparmor_restrict_unprivileged_userns=0
EOF
sysctl --system
sysctl kernel.apparmor_restrict_unprivileged_userns    # 期望输出 0
```

### 1.5 时间同步（munge 跨机认证依赖）
```bash
timedatectl set-timezone Asia/Shanghai
timedatectl set-ntp true
apt update && apt install -y chrony
```
- [admin] 允许内网客户端查询（自身继续用默认公网 pool）：
```bash
cat >> /etc/chrony/chrony.conf <<'EOF'
allow <LAN_CIDR>
EOF
systemctl enable --now chrony && systemctl restart chrony
```
- [`<GPU02>/<GPU03>`] 以 admin 为本地时钟源（公网 pool 自动降级为备份，不必删）：
```bash
echo 'server admin iburst prefer' >> /etc/chrony/chrony.conf
systemctl enable --now chrony && systemctl restart chrony
```
- 验收 [全部节点]：`chronyc sources`（GPU 节点应看到 admin 为 `^*`）；`chronyc tracking`（Stratum 正常、Last offset 小）。
- 若开了 ufw：`ufw allow from <LAN_CIDR> to any port 123 proto udp`。

### 1.6 基础工具
```bash
apt install -y git curl wget build-essential make gcc
```

### 1.7 第 1 章验收
```bash
hostname                      # 各自正确
sysctl kernel.apparmor_restrict_unprivileged_userns   # 0
chronyc tracking | grep -E 'Stratum|Last offset'
```

---

## 2. NFS 共享盘（/share）

> 布局：只在 admin 上导出 `/share` 一个目录；用户家目录 `/share/home/<user>`，数据集 `/share/datasets`，镜像缓存 `/share/enroot-cache`，镜像仓库 `/share/images`。GPU 节点挂载同一路径。

### 2.1 数据盘准备 [admin]
```bash
# ⚠️ 第一步确认 /dev/sda 是【数据盘】而不是系统盘！
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS
# 系统盘是带 / 挂载点的那块；若系统装在 sda，把下面 /dev/sda 换成实际数据盘（如 /dev/sdb）

mkfs.ext4 /dev/sda          # ⚠️ 清空数据盘
mkdir -p /share
echo '/dev/sda /share ext4 defaults,noatime 0 2' >> /etc/fstab
mount -a
df -h /share
```
> 关于配额：ext4 支持按 UID 配额，但**不支持按目录**配额（这是第 7.1 节配额口径的依据）；fstab 的 `usrquota` 选项在第 7.1 节再加（避免此时就要 remount）。

### 2.2 目录结构与权限 [admin]
```bash
mkdir -p /share/home && chmod 755 /share/home
mkdir -p /share/enroot-cache && chmod 1777 /share/enroot-cache
mkdir -p /share/datasets && chmod 1777 /share/datasets
mkdir -p /share/images && chmod 755 /share/images
```

| 目录 | 权限 | 说明 |
|---|---|---|
| `/share/home` | 755 root | 家目录根；用户各自建子目录（root 属主，普通用户不能写根） |
| `/share/datasets` | **1777**（sticky） | 共享数据集：人人可读可写、sticky 只许删自己的文件，防误删 |
| `/share/enroot-cache` | 1777 | enroot 镜像缓存根；**组共享子目录由管理员预建 1777**（见 5.3） |
| `/share/images` | 755 root | 预置 .sqsh 镜像：**644 root 属主 = 全员可读、只有 root 可写**。不要改成 1777（任何人覆盖共享镜像=投毒风险）；想让授权用户放镜像用专用组+setgid（见 5.6） |

datasets 的另一种组织方式（小组要互改同一批文件时）——表格备选：

| 方案 | 命令 | 适用 |
|---|---|---|
| 简单方案（本手册采用） | `chmod 1777` | 各自下载自己的数据集，只删改自己的 |
| 规范方案 |  + 用户入组 | 组内互写互删、新文件自动继承组 |

### 2.3 NFS 服务端 [admin]
```bash
apt install -y nfs-kernel-server
cat > /etc/exports <<'EOF'
/share  <LAN_CIDR>(rw,sync,no_subtree_check)
EOF
exportfs -ra
systemctl enable --now nfs-server
showmount -e localhost        # 应列出 /share
```

`/etc/exports` 参数说明：

| 参数 | 作用 | 本手册取值 | 备注 |
|---|---|---|---|
| `rw` | 读写（`ro`=只读） | rw | |
| `sync` | 写请求同步落盘再应答（`async` 快但掉电丢数据） | sync | |
| `no_subtree_check` | 关闭子树检查，减少协议问题 | 加 | |
| `root_squash`（默认，**不要写 no_root_squash**） | 把客户端 root(uid0) 压成 nobody(65534) | 保持默认 | 安全：计算节点被攻破也不能篡改共享盘 |
| `all_squash` | 把**所有**客户端用户压成 nobody | 不用 | 会破坏多用户属主 |

> ⚠️ **root_squash 是本集群踩过的最深的坑（部署日志 §5.4），先记住结论**：
> 当 **① 作业以 root 提交 ② `-o`/输出落 `/share` ③ 跑在 NFS 客户端节点（`<GPU02>/<GPU03>`）** 三条件同时成立时，
> 批处理作业会在启动 ~1 秒内死掉（输出 0 字节且属主 nobody，slurmctld 日志 `WTERMSIG 53`），与容器/pyxis 无关。
> 对策：root 提交的作业 `-o` 一律用节点本地路径（如 `/tmp/test-%j.out`）；**多用户（第 7 章）后作业都由普通用户提交，天然不受影响**。不要在 exports 里加 `no_root_squash`。

### 2.4 NFS 客户端 [`<GPU02>/<GPU03>`]
```bash
apt install -y nfs-common
mkdir -p /share
cat >> /etc/fstab <<'EOF'
admin:/share  /share  nfs _netdev,rw,hard,intr,noatime,actimeo=60 0 0
EOF
mount -a
df -h /share        # 挂载成功（不再是根分区设备）
```

fstab 挂载参数说明：

| 参数 | 作用 | 备注 |
|---|---|---|
| `_netdev` | 等网络就绪再挂载 | **必须**，否则重启后 /share 挂不上（常见坑） |
| `hard,intr` | 服务端不可达时挂起等待而不是报错 | 数据安全 |
| `actimeo=60` | 属性缓存 60s，目录列表即时性好 | 可调 |

### 2.5 统一用户 lab（UID 全集群一致，家目录在 NFS）
> ⚠️ 顺序：必须先有 `/share`（2.1/2.2），再建用户，否则 `useradd -m` 失败。

```bash
# [admin] 建家目录 + 账号 + 免密 sudo（lab 是“管理员操作账号”；普通科研用户不加 sudo，建号脚本见 7.5）
mkdir -p /share/home
useradd -m -d /share/home/lab -u 1000 -s /bin/bash lab    # 已存在则跳过
chown -R lab:lab /share/home/lab
echo 'lab ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/lab

# [<GPU02>/<GPU03>] 只登记账号，不建家目录（-M；家目录经 NFS 自动可见）
useradd -u 1000 -d /share/home/lab -s /bin/bash -M lab    # 已存在则跳过
echo 'lab ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/lab

# [全部节点] 统一密码
passwd lab
```

### 2.6 第 2 章验收
```bash
# [<GPU02>/<GPU03>] 普通用户写自家目录（属主应为 lab，不是 nobody）
runuser -u lab -- touch /share/home/lab/$(hostname).test && ls -l /share/home/lab/
# [<GPU02>/<GPU03>] 1G 大文件写测试（oflag=direct 绕过客户端缓存，落到 datasets；测完删除）
time runuser -u lab -- dd if=/dev/zero of=/share/datasets/nfs-write-test bs=1M count=1024 oflag=direct status=progress
rm -f /share/datasets/nfs-write-test
# [admin] 在服务端确认文件属主是 lab 而不是 nobody（root_squash 未误伤普通用户）
ls -l /share/home/lab/
```
> 若文件属主是 `nobody`：说明你以 root 在建/写文件（root_squash 预期行为），改用 lab 身份即可。
---

## 3. 编译安装 Slurm 26.05.1（[全部节点]，每台各编一次）

> 编译产物在 `/usr/local/{sbin,bin,lib/slurm,include/slurm}`，配置在 `/etc/slurm`。
> ⚠️ **一步到位带 MySQL 会计插件（本次踩坑后固化的关键改进）**：源码构建只有在 configure 时能探测到
> `mysql_config` 才会编译会计存储插件。首次部署若漏了，之后想开会计（第 7 章）必须**整包重编**。
> 下面依赖里加了 `libmariadb-dev` 并预置软链，configure 会自动带上，**以后不用重建**。

### 3.1 依赖
```bash
mkdir -p /opt/build
apt update
apt install -y build-essential autoconf automake libtool pkg-config \
  libmunge-dev libpmix-dev libpmi2-0-dev libssl-dev libhwloc-dev libyaml-dev \
  libcurl4-openssl-dev libdbus-1-dev libbpf-dev libmariadb-dev munge libpmix2t64
```
依赖说明（缺了会怎样，全部实测过）：

| 包 | 作用 | 缺了会怎样 |
|---|---|---|
| `libdbus-1-dev` | cgroup/v2 插件编译依赖 | cgroup/v2 插件不编译，slurmd 启动报 `cannot create cgroup context for cgroup/v2` |
| `libpmix-dev` + `libpmix2t64` | PMIx（MpiDefault=pmix） | `Invalid MPI type 'pmix'` / 运行时缺库 |
| `libmariadb-dev` | **MySQL 会计插件**（提供 mariadb_config） | 会计插件不编译，第 7 章要整包重建 |
| `libhwloc-dev`、`libyaml-dev`、`libcurl4-openssl-dev` 等 | 可选功能 | configure 会静默跳过对应功能，一般无碍 |
| `munge` | 跨机认证 | 无 munge 集群跑不起来 |

### 3.2 NVML 头文件（--with-nvml 需要；GPU 自动探测）
```bash
cd /tmp
wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb
dpkg -i cuda-keyring_1.1-1_all.deb
apt update
apt-cache search cuda-nvml-dev
apt install -y cuda-nvml-dev-13-3        # 换成查到的最大版本号
# CUDA 13 布局：头文件在 /usr/local/cuda-13.x/include；可能没有 /usr/local/cuda 软链，建一个：
ln -s /usr/local/cuda-13.3 /usr/local/cuda     # 版本号按实际
```

### 3.3 下载 + 预置 mysql_config 软链 + configure + make
```bash
cd /opt/build
wget https://download.schedmd.com/slurm/slurm-26.05.1.tar.bz2
tar xjf slurm-26.05.1.tar.bz2 && cd slurm-26.05.1

# ⚠️ 关键：让 configure 找到 mysql 支持（mariadb 提供的是 mariadb_config，做软链）
ln -sf /usr/bin/mariadb_config /usr/bin/mysql_config
ls -l /usr/bin/mysql_config

./configure --prefix=/usr/local --sysconfdir=/etc/slurm \
    --with-munge=/usr --with-nvml --with-pmix \
    CPPFLAGS="-I/usr/local/cuda/include" \
    LDFLAGS="-L/usr/local/cuda/lib64/stubs"
```
configure 参数说明：

| 参数 | 作用 | 备注 |
|---|---|---|
| `--prefix=/usr/local` | 安装前缀 | 插件目录自动为 /usr/local/lib/slurm |
| `--sysconfdir=/etc/slurm` | 配置文件目录 | 与 systemd 单元一致 |
| `--with-munge=/usr` | munge 认证 | |
| `--with-nvml` | GPU 自动探测（gres.conf 可用 `AutoDetect=nvml`） | 需要 3.2 的 NVML 头文件 |
| `--with-pmix` | PMIx 运行时 | ⚠️ **不带路径**（Ubuntu 头文件在 pmix2 下，走 pkg-config；写 `=/usr` 会报 No PMIX installation found） |
| `--with-mysql` | 提示 unrecognized，**可忽略** | 26.05 已无此选项；是否带 mysql 插件取决于能否探测到 `mysql_config` |
| `CPPFLAGS/LDFLAGS` | CUDA 头文件与 stub 库 | 编译期链接用 |

configure 后**必须确认**（三行缺一不可）：
```bash
grep -E 'mysql_config|MySQL' config.log | head -5
# 期望看到: checking for mysql_config... /usr/bin/mysql_config
#           MySQL 10.11.14 test program built properly.   （版本号按实际）
#           config.status: creating src/plugins/accounting_storage/mysql/Makefile
```
> ⚠️ 若 configure 输出 **mysql_config not found**：`/usr/bin/mysql_config` 软链没建成功或没装 libmariadb-dev。
> 装好后**重新 configure**（不要直接 make），否则插件还是缺失。

```bash
make -j$(nproc)
make install
# 编译验证：NVML 与 MySQL 插件都必须存在
strings /usr/local/lib/slurm/gres_gpu.so | grep -i nvml && echo "NVML OK"
ls -l /usr/local/lib/slurm/accounting_storage_mysql.so /usr/local/lib/slurm/jobcomp_mysql.so
```
> 只有一台有源码时，其它同架构同发行版机器可把 `/usr/local/{sbin,bin,lib,include}` 打包拷贝过去，省去重复编译。

### 3.4 slurm 用户与目录（编译版必须手工建）
```bash
useradd -r -u 64030 -s /usr/sbin/nologin slurm 2>/dev/null || true
install -d -o slurm -g slurm /var/lib/slurm/slurmctld /var/lib/slurm/slurmd \
  /var/spool/slurm /var/log/slurm
```

### 3.5 systemd 单元（源码 etc/ 自带，configure 已替换路径）
```bash
cp /opt/build/slurm-26.05.1/etc/slurmctld.service /etc/systemd/system/
cp /opt/build/slurm-26.05.1/etc/slurmd.service     /etc/systemd/system/
cp /opt/build/slurm-26.05.1/etc/slurmdbd.service   /etc/systemd/system/   # 第 7 章会计用
systemctl daemon-reload
```

---

## 4. 控制端配置（[admin]：munge + slurm.conf + slurmctld）

> ⚠️ 前置：3 章编译已完成；主机名必须是 `admin`（1.2/1.3），否则 slurmctld 报 `This host (xxx) not a valid controller`。

### 4.1 munge 密钥（只在 admin 生成一次）
```bash
# Ubuntu 24.04 的 munge 0.5.15 用 mungekey（create-munge-key 不存在）
mungekey -c -f
chown munge:munge /etc/munge/munge.key
chmod 400 /etc/munge/munge.key
systemctl enable --now munge
munge -n | unmunge          # 本机自检：能解出自己即 OK
```
> ⚠️ munge 三条铁律（本集群全踩过）：
> 1. **同步密钥到任何节点后必须重启该节点的 munged**——munged 只在启动时读密钥，光换文件内存里还是旧的（报 Invalid credential）；
> 2. admin **自己**重新生成/覆盖密钥后也要重启自己的 munged（否则全网对 admin 认证失败，srun 报 Protocol authentication error）；
> 3. 跨机验证一条命令同时查 key+时钟：`munge -n | ssh <节点> unmunge`——报 `Response too old or too new` 是时钟漂移（查 chrony），报密钥错误是 key 不一致（md5sum 对比）。

### 4.2 slurm.conf（源码编译版不会自动建 /etc/slurm，先建目录）
```bash
mkdir -p /etc/slurm
cat > /etc/slurm/slurm.conf <<'EOF'
ClusterName=lab
SlurmctldHost=admin

AuthType=auth/munge
CryptoType=crypto/munge
SlurmUser=slurm
MpiDefault=pmix
ProctrackType=proctrack/cgroup
ReturnToService=1
SlurmctldPidFile=/run/slurmctld.pid
SlurmdPidFile=/run/slurmd.pid
SlurmctldPort=6817
SlurmdPort=6818
StateSaveLocation=/var/lib/slurm/slurmctld
SlurmdSpoolDir=/var/lib/slurm/slurmd
SwitchType=switch/none

GresTypes=gpu
SelectType=select/cons_tres
SelectTypeParameters=CR_Core_Memory
SchedulerType=sched/backfill
TaskPlugin=task/cgroup,task/affinity

NodeName=admin CPUs=16 Boards=1 SocketsPerBoard=1 CoresPerSocket=8 ThreadsPerCore=2 RealMemory=31933 Gres=gpu:3060:1
NodeName=<GPU02> CPUs=16 Boards=1 SocketsPerBoard=1 CoresPerSocket=8 ThreadsPerCore=2 RealMemory=31933 Gres=gpu:3060:1
NodeName=<GPU03> CPUs=16 Boards=1 SocketsPerBoard=1 CoresPerSocket=8 ThreadsPerCore=2 RealMemory=128653 Gres=gpu:3060:1
MailProg=/bin/true

PartitionName=gpu Nodes=ALL Default=YES MaxTime=INFINITE State=UP
EOF
```
> ⚠️ NodeName 行的 CPUs/RealMemory/Gres 要用各节点 `slurmd -C` 的实测输出填写（见 4.3 表格下方说明），
> 别手抄；`ThreadsPerCore` 与 `CoresPerSocket` 组合要和实测一致，否则注册信息不匹配（INVALID_REG）。

slurm.conf 主要参数说明：

| 参数 | 作用 | 可选配置/备注 |
|---|---|---|
| `ClusterName` | 集群名 | 与 sacctmgr cluster 同名（第 7 章） |
| `SlurmctldHost` | 控制端主机名 | 必须等于 admin 的主机名 |
| `AuthType=auth/munge` + `CryptoType=crypto/munge` | 跨机认证 | munge 是唯一选择 |
| `SlurmUser=slurm` | 运行用户 | 缺了报 `Unauthorized credential for client UID=64030` |
| `ProctrackType=proctrack/cgroup` | 进程跟踪走 cgroup | 需 cgroup.conf（4.3）；排障可临时退回 `proctrack/linuxproc` |
| `SelectType=select/cons_tres` + `SelectTypeParameters=CR_Core_Memory` | 资源选择：按核心+内存分配 | 可选 `CR_CPU_Memory`（按线程粒度）、`CR_GPU`（同时按 GPU）等 |
| `GresTypes=gpu` | 启用 GPU 类资源 | 需要 gres.conf（4.4） |
| `SchedulerType=sched/backfill` | 回填调度 | 默认 also `sched/builtin` |
| `TaskPlugin=task/cgroup,task/affinity` | 作业级 cgroup 隔离 + 绑核 | 配合 cgroup.conf 的 Constrain* |
| `ReturnToService=1` | 节点恢复后自动回到服务 | 0=需手动 scontrol update |
| `PartitionName=gpu Nodes=ALL Default=YES MaxTime=INFINITE State=UP` | 分区定义 | `MaxTime` 可设上限；`Nodes=ALL` 表示所有节点入分区 |
| `MailProg=/bin/true` | 邮件上报程序置空 | 免告警 |

### 4.3 cgroup.conf（26.05 新语法：CgroupAutomount 已废弃、ConstrainMemory 改名 ConstrainRAMSpace）
```bash
cat > /etc/slurm/cgroup.conf <<'EOF'
ConstrainCores=yes
ConstrainRAMSpace=yes
ConstrainDevices=yes
EOF
```

| 参数 | 作用 | 备注 |
|---|---|---|
| `ConstrainCores=yes` | 作业只能使用分到的 CPU 核心 | cgroup v2 下对应 cpuset/cpu |
| `ConstrainRAMSpace=yes` | 内存硬上限（超限 OOM 杀作业） | |
| `ConstrainDevices=yes` | 设备隔离 | cgroup v2 无 devices 控制器时部分生效；GPU 独占靠 GRES（每节点 1 卡自然互斥） |

### 4.4 启动控制端并验收
```bash
systemctl enable --now slurmctld
scontrol ping     # 期望 Slurmctld(primary) at admin
```
第 4 章验收：
```bash
scontrol ping
# 此时计算节点还没注册（slurmd 未启），sinfo 显示 down/drain 属正常；做完 4.5-4.8 后再看即 idle
```
### 4.5 NVIDIA 驱动（[每台带 GPU 的节点]，含 admin 若也插卡）
```bash
lspci | grep -i nvidia
ubuntu-drivers devices            # 找到 recommended 的那行
ubuntu-drivers install --gpgpu     # --gpgpu = 无头计算驱动
# ⚠️ headless-open 包组可能不带 nvidia-smi，报 command not found 时补装（版本号换实际的）：
#   apt install -y nvidia-utils-595-server
reboot
nvidia-smi                        # 重启后验证
```
> 所有 GPU 节点驱动版本必须一致，并与容器镜像 CUDA 大版本兼容。

### 4.6 计算端 munge + 配置同步 + gres.conf [`<GPU02>/<GPU03>`；admin 若跑 slurmd 同样做]

> ⚠️ 顺序提醒：本机 slurmd 只有在 `/etc/slurm/slurm.conf` **包含本机 NodeName 行**且 munge 密钥与全网一致时才能注册成功。
> 26.05 新行为：本地配置缺失/不含自己时 slurmd 会走 DNS SRV“从控制器拉配置”并无限重试（日志 `fetch_config: DNS SRV lookup failed`）。

**① munge 密钥（两段式）**
```bash
# 密钥文件在 admin 的 /etc/munge/munge.key，把它原样复制到本机 /tmp/munge.key 后执行：
# （复制方式不限：scp/rsync/U 盘/带外管理均可；文档不写远程命令。注意方向与端口差异）
install -o munge -g munge -m 400 /tmp/munge.key /etc/munge/munge.key
systemctl enable --now munge
# ⚠️ 密钥更新/同步后必须重启本机 munged：munged 只在启动时读密钥
systemctl restart munge
munge -n | unmunge                 # 本机自检
# 跨机验证在 admin 执行：munge -n | ssh <节点> unmunge（无报错=key+时钟都 OK）
```

**② 配置文件同步（内容与 admin 完全一致）**
```bash
mkdir -p /etc/slurm
# 把 admin 的 /etc/slurm/slurm.conf、cgroup.conf 复制到本机同路径（复制方式同上）
# ⚠️ gres.conf 本机自建（见下），不要从 admin 拷——它按本机硬件探测
```
> 之后 admin 每改一次 slurm.conf（加 NodeName/加会计），都要**同步到所有节点并重启 slurmd**，否则两边配置不一致（第 7.5 节会计开启时专门强调）。

**③ gres.conf（编译版带 nvml，一行自动探测）**
```bash
mkdir -p /etc/slurm
cat > /etc/slurm/gres.conf <<'EOF'
AutoDetect=nvml
EOF
```

| 方式 | 适用 | 内容 |
|---|---|---|
| `AutoDetect=nvml`（本手册） | 编译版（--with-nvml） | 自动探测全部 GPU |
| 手动每卡一行 | apt 版无 nvml 插件时 | `Name=gpu Type=<型号> File=/dev/nvidia0`（编号从小到大） |

**④ 实测本机资源并核对 NodeName 行**
```bash
slurmd -C
# 输出示例: NodeName=<GPU02> CPUs=16 ... RealMemory=31933 Gres=gpu:3060:1
# 拿这个结果与 admin 的 /etc/slurm/slurm.conf NodeName 行比对；不一致就改 admin 那份再同步
```

**⑤ 启动 slurmd**
```bash
systemctl enable --now slurmd
# ⚠️ 若 restart 卡在 deactivating 不退出（实测会遇到），强杀重来：
#   systemctl stop slurmd; pkill -9 slurmd; sleep 1; systemctl start slurmd
```

**⑥ 开机顺序加固（重启后必做一次，缺失会启动失败）**
```bash
mkdir -p /etc/systemd/system/slurmd.service.d
printf '[Unit]\nAfter=network-online.target\nWants=network-online.target\n' > /etc/systemd/system/slurmd.service.d/override.conf
systemctl daemon-reload
```
> slurmd 重启起不来的两大根因：① /etc/hosts 缺本机自身 IP（1.3）；② 网络未就绪（本条 drop-in 解决）。
> 若节点被标 DOWN：`scontrol update nodename=<节点> state=idle` 拉回。

### 4.7 admin 兼作计算节点的特别说明
> 本集群 admin 也插了 RTX 3060 并跑 slurmd（第 3 个计算节点）。做法：
> ① admin 的 slurm.conf 已含 `NodeName=admin ... Gres=gpu:3060:1`；
> ② admin 本机也执行 4.6 的 ③⑤⑥（建 gres.conf + 启 slurmd + 加固），不用再拷密钥（密钥就在本机）。
> ⚠️ 实测坑：给 admin 加了 NodeName 却**漏建 /etc/slurm/gres.conf** → admin 显示 `IDLE+DRAIN+INVALID_REG`
> （slurmd 注册的 Gres 与 slurm.conf 不符）；补 gres.conf 重启 slurmd 即恢复。
> 若你的管理节点不带 GPU：跳过本节与 4.5，slurm.conf 里不要写 admin 的 NodeName。

### 4.8 第 4 章验收（[admin] 上执行）
```bash
scontrol ping                            # Slurmctld(primary) at admin
sinfo                                    # 全部节点 idle
scontrol show node <GPU02> -d | grep -E 'Gres|CPUTot|RealMemory'   # Gres=gpu:N
# GPU 调度：确认作业跑在【计算节点】上（别用 echo $HOSTNAME——它会被提交端先展开）
srun --gres=gpu:1 hostname               # 应输出 <GPU02>/<GPU03>/admin，不是提交端误判
srun --gres=gpu:1 nvidia-smi             # 应显示被分配的那张卡
```
---

## 5. enroot + pyxis 容器（[全部节点]，含 admin）

> 不需要 Docker：enroot 直接与镜像仓库通信（docker:// 协议）或挂载本地 .sqsh；pyxis 是 Slurm SPANK 插件，
> 让 `srun --container-image=...` 直接可用。**登录端解析参数、计算端启动容器，两边都要装**（无预编译 pyxis 包，每台各编译一次约 1 分钟）。

### 5.1 安装 enroot
```bash
apt install -y squashfs-tools squashfuse
arch=$(dpkg --print-architecture)
cd /root
# 官方只发 GitHub release 的 .deb（nvidia.github.io apt 路径已停用）
curl -fSsL -O https://github.com/NVIDIA/enroot/releases/download/v4.2.1/enroot_4.2.1-1_${arch}.deb
# GitHub 直连不通时加镜像前缀：https://gh-proxy.com/https://github.com/...（本网实测 38MB/s）
# enroot+caps 是可选 setuid 帮助器（普通用户 import 用）；本方案 import 由 root 做，可跳过
apt install -y ./enroot*.deb
enroot version            # 4.2.1
```

### 5.2 enroot.conf（每台各一份，内容相同）
```bash
cat > /etc/enroot/enroot.conf <<'EOF'
ENROOT_RUNTIME_PATH /run/enroot/user-$(id -u)
ENROOT_CACHE_PATH   /share/enroot-cache/group-$(id -g)
ENROOT_DATA_PATH    /scratch/enroot-data/user-$(id -u)
ENROOT_MOUNT_HOME   n
ENROOT_RESTRICT_DEV y
ENROOT_ROOTFS_WRITABLE y
ENROOT_REMAP_ROOT   n
EOF
```

| 参数 | 作用 | 本手册取值 | 备注 |
|---|---|---|---|
| `ENROOT_RUNTIME_PATH` | 运行时临时目录（挂载点等） | `/run/enroot/user-$(id -u)` | 父目录 /run/enroot 需 1777 + 开机重建（5.3） |
| `ENROOT_CACHE_PATH` | 镜像缓存（docker:// 拉取落这里） | `/share/enroot-cache/group-$(id -g)` | NFS 全网一份；组目录预建 1777 才能共享（5.3） |
| `ENROOT_DATA_PATH` | 容器 rootfs/数据层（本地盘） | `/scratch/enroot-data/user-$(id -u)` | 中间目录 /scratch/enroot-data **必须预建 1777**（5.3，踩过坑） |
| `ENROOT_MOUNT_HOME` | 是否自动挂载 $HOME | n（不挂） | 容器内家目录自己用 --container-mounts 挂 |
| `ENROOT_RESTRICT_DEV` | 限制 /dev 设备 | y | 安全 |
| `ENROOT_ROOTFS_WRITABLE` | rootfs 可写层 | y | |
| `ENROOT_REMAP_ROOT` | 是否把提交者 remap 成容器内 root | **n** | 默认以**提交者身份**进容器（不做 uid→0 映射）：容器内是本人 uid，写 /share 属主即本人、不触发 root_squash；确需以 root 操作时单次加 srun `--container-remap-root` |

### 5.3 运行时目录（三处 1777 + 开机重建）
```bash
mkdir -p /scratch && chmod 1777 /scratch
# ⚠️ 踩坑修复：/scratch/enroot-data 若由 root 先跑 enroot 生成会是 0700 root（enroot 建目录 umask 077），
#    普通用户容器 mkdir 报 Permission denied。必须管理员预建 1777（幂等，已存在也执行一次无害）：
mkdir -p /scratch/enroot-data && chmod 1777 /scratch/enroot-data
mkdir -p /run/enroot && chmod 1777 /run/enroot      # /run 是 tmpfs，重启即清
echo 'd /run/enroot 1777 root root -' > /etc/tmpfiles.d/enroot.conf
systemd-tmpfiles --create
# NFS 缓存组目录预建（组共享前提：enroot 自动 mkdir 是 0700 只归第一人；预建 1777 后 mkdir -p 不改权限）
# 每个需要共享的组补一条；gid 用 id -g <用户名> 查（别拿 UID 当 gid）。例 lab 组：
mkdir -p /share/enroot-cache/group-1000 && chmod 1777 /share/enroot-cache/group-1000
```

### 5.4 nvidia-container-cli（容器内挂 GPU 的前置，缺了 98-nvidia.sh 直接报 Command not found）
```bash
# 方式 A（推荐，GitHub release）：下载 libnvidia-container 最新 amd64 两个 deb：
#   libnvidia-container1_<版本>_amd64.deb 与 libnvidia-container-tools_<版本>_amd64.deb
apt install -y ./libnvidia-container1_*.deb ./libnvidia-container-tools_*.deb
nvidia-container-cli --version     # 有输出即成功
# 方式 B（nvidia 官方 apt 源可达时）：apt install -y libnvidia-container-tools
```

### 5.5 GPU 可见性钩子（每个节点都要；**用下面的修复版全文**）
> 原理：enroot 4.x 挂 GPU 靠 `/etc/enroot/hooks.d/98-nvidia.sh`，它需要 ① nvidia-container-cli（5.4）
> ② 环境变量 `NVIDIA_VISIBLE_DEVICES`（未设置时直接 exit 0 = 不挂卡，容器能跑但里面看不到 GPU）。
> Slurm 只给 GPU 作业注入 `CUDA_VISIBLE_DEVICES`（实测），不注入 `NVIDIA_VISIBLE_DEVICES`，
> 所以需要一个前置钩子（文件名 95 < 98）做转换，并补默认能力 `compute,utility`（enroot 默认只给 utility，不够 CUDA 用）。

> ⚠️ **本集群 2026-09-05 修复的 CPU 容器 bug（务必用下面的修复版，勿用旧版）**：
> 旧版钩子末行是 `[ -n "${cvd:-}" ] && printf ...` 且开着 `set -eu`——当作业**不带 GPU**（纯 CPU 容器）时
> env 里没有 `CUDA_VISIBLE_DEVICES`，该 test 为假，而它恰好是脚本最后一条语句，整个脚本以退出码 1 结束，
> enroot 判定启动失败：所有 CPU 容器作业秒挂（报 `[ERROR] /etc/enroot/hooks.d/95-slurm-gpus.sh exited with return code 1`）。
> GPU 作业有值可写所以从未暴露。修复 = env 缺失提前 exit 0 + 末尾显式 `exit 0`。

```bash
cat > /etc/enroot/hooks.d/95-slurm-gpus.sh <<'SCRIPT'
#!/usr/bin/env bash
# 98-nvidia.sh 的前置钩子（文件名 95 < 98，保证先于它执行）
# 修复 2026-09-05: CPU 作业(无 CUDA_VISIBLE_DEVICES)时末行 test 失败导致退出码 1
set -eu
env_file="${ENROOT_ENVIRON:-}"
if [ -z "${env_file}" ] || [ ! -f "${env_file}" ]; then
    exit 0
fi
grep -q '^NVIDIA_DRIVER_CAPABILITIES=' "${env_file}" || \
    printf 'NVIDIA_DRIVER_CAPABILITIES=compute,utility\n' >> "${env_file}"
if grep -q '^NVIDIA_VISIBLE_DEVICES=' "${env_file}"; then
    exit 0
fi
cvd=$(sed -n 's/^CUDA_VISIBLE_DEVICES=//p' "${env_file}" | head -1)
if [ -n "${cvd}" ]; then
    printf 'NVIDIA_VISIBLE_DEVICES=%s\n' "${cvd}" >> "${env_file}"
fi
exit 0
SCRIPT
chmod +x /etc/enroot/hooks.d/95-slurm-gpus.sh
bash -n /etc/enroot/hooks.d/95-slurm-gpus.sh && echo SYNTAX_OK
# 冒烟（CPU 场景必须返回 0）：
printf 'PATH=/usr/bin\nHOME=/root\n' > /tmp/ct-env
ENROOT_ENVIRON=/tmp/ct-env /etc/enroot/hooks.d/95-slurm-gpus.sh; echo "SMOKE_RC=$?"
```
> 可选（多节点 MPI / PyTorch 分布式才需要；单机单卡跳过）：
> `cp /usr/share/enroot/hooks.d/50-slurm-pmi.sh /usr/share/enroot/hooks.d/50-slurm-pytorch.sh /etc/enroot/hooks.d/`

### 5.6 编译安装 pyxis + 挂进插件栈
```bash
# ⚠️ 必须用【编译版 Slurm】的头文件（/usr/local/include/slurm）；别装 apt 的 libslurm-dev（23.11 头文件，链错版本）
# ⚠️ pyxis v0.24.0 是纯 Makefile 构建：没有 autogen.sh/configure，直接 make
apt install -y make gcc          # 1.6 已含，保险再确认
git clone https://github.com/NVIDIA/pyxis.git /opt/build/pyxis
cd /opt/build/pyxis
make -j$(nproc) && make install

# 挂进 Slurm 插件栈（plugstack.conf 默认不存在，先建）
mkdir -p /etc/slurm/plugstack.conf.d
echo 'include /etc/slurm/plugstack.conf.d/*' > /etc/slurm/plugstack.conf
cat > /etc/slurm/plugstack.conf.d/pyxis.conf <<'EOF'
required /usr/local/lib/slurm/spank_pyxis.so runtime_path=/run/pyxis execute_entrypoint=0 container_scope=job sbatch_support=1 use_enroot_load=1 use_squashfuse=1
EOF
mkdir -p /run/pyxis && chmod 1777 /run/pyxis
echo 'd /run/pyxis 1777 root root -' >> /etc/tmpfiles.d/enroot.conf
systemd-tmpfiles --create
systemctl restart slurmd
srun --help | grep container-image    # 能看到 --container-image 即安装成功
```

pyxis 配置参数说明（一行式，可按需增删）：

| 参数 | 作用 | 取值说明 |
|---|---|---|
| `required` | 加载失败则作业失败（vs optional 仅告警） | 保持 |
| `runtime_path=/run/pyxis` | 容器运行时目录 | 1777 + 开机重建（上面已做） |
| `execute_entrypoint=0` | 是否执行镜像 ENTRYPOINT | 0=不执行（与 docker 习惯不同，按需） |
| `container_scope=job` | 作业结束 epilog 自动清理容器 | v0.24.0 只有 job/global 两档；global=保留复用 |
| `sbatch_support=1` | 让 sbatch/salloc 也认识 --container-* | 默认即 1 |
| `use_enroot_load=1` | 适配 enroot 4.x 缓存镜像存储 | **enroot 4.x 必须开**（<4.0 用默认两步 import+create） |
| `use_squashfuse=1` | .sqsh 直接 squashfuse 挂载启动 | 秒级启动、几乎不占盘；不开则每次全量解包到 ENROOT_DATA_PATH（慢且吃盘，实测建议开） |

### 5.7 enroot 自检与集群镜像预置
```bash
# 自检（国内 Docker Hub 不可达时用 DaoCloud 源 docker://docker.m.daocloud.io/library/...）
cd /root     # enroot import 不带 -o 时产物落在当前目录
enroot import docker://docker.m.daocloud.io/library/alpine:latest
ls -la *.sqsh
enroot create -n alpine-test ./library+alpine+latest.sqsh
enroot start alpine-test      # 进入容器 shell，exit 退出
enroot remove -f alpine-test

# 集群镜像流程（推荐）：管理员在 admin（NFS 服务端）root 预置 .sqsh 到共享盘，用户按路径直接用、免特权
# 例（NGC 国内可达）：
#   enroot import -o /share/images/pytorch-24.03.sqsh docker://nvcr.io#nvidia/pytorch:24.03-py3
# 或从缓存镜像转出：enroot export <镜像名> | tee /share/images/<名>.sqsh > /dev/null
# ⚠️ .sqsh 命名带版本/tag，更新写新文件名，别原地覆盖在用镜像；/share/images 保持 755 root（勿 1777）
```

### 5.8 第 5 章验收（核心：容器 + GPU + NFS 全链路，root 与普通用户各一遍）
```bash
# ① root GPU 容器（拉取模式，网络可达时）
srun --gres=gpu:1 --container-image=nvcr.io/nvidia/pytorch:24.03-py3 \
     --container-mounts=/share:/share \
     bash -c 'python -c "import torch; print(torch.cuda.device_count())" && \
              touch /share/from-container-$(hostname) && ls -l /share/from-container-$(hostname)'
#    期望：device_count()==1，文件属主为提交者（不是 root，uid 映射生效）
# ② 共享 .sqsh 模式（离线/秒级启动；镜像已预置到 /share/images 时）
srun --gres=gpu:1 --container-image=/share/images/<你的镜像>.sqsh \
     --container-mounts=/share:/share python -c "import torch;print(torch.cuda.device_count())"

# ③【普通用户】（多用户化的核心验证；lab 已建）
runuser -u lab -- srun -D /tmp -N1 -w <GPU02> -o /tmp/lab-ct-%j.out \
    --container-image=/share/images/ubuntu-22.04.sqsh cat /etc/os-release ; echo EXIT=$?
#    ↑ CPU 容器：修复 5.5 钩子后应 EXIT=0（修复前必挂 exit 1）
runuser -u lab -- srun -D /tmp -N1 -w <GPU02> --gres=gpu:1 -o /tmp/lab-ct-%j.out \
    --container-image=/share/images/pytorch-2.12.1-cuda13.0-cudnn9-devel.sqsh \
    python3 -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
#    期望：True NVIDIA GeForce RTX 3060

# ④ 交互会话（ENROOT_REMAP_ROOT=n 默认以本人身份进容器；确需 root 时加 --container-remap-root）
salloc --gres=gpu:1 --time=02:00:00
srun --pty --container-image=/share/images/<你的镜像>.sqsh \
     --container-mounts=/share/home/$USER:/workspace bash
# ⑤ 批处理示例（普通用户从登录节点提交；root 提交时 -o 用本地 /tmp 路径，见 2.3 警告框）
sbatch --gres=gpu:1 -o /tmp/test-%j.out --wrap 'nvidia-smi -L'
```
> 常见误判：容器内跑 `nvidia-smi` 报 `execve(): nvidia-smi: No such file or directory` = **镜像里没有该二进制**
> （ubuntu 基础镜像无 nvidia-smi/python），不是集群问题；用 pytorch 镜像验证 GPU。
---

## 6. 监控栈（Prometheus + 导出器 + Grafana，[admin] 为主）

> 拓扑：每个节点跑 node_exporter(:9100) 与 gpu_exporter(:9835)；admin 额外跑 slurm_exporter(:8080) 与
> jobqueue_exporter(:8085)；Prometheus(:9090) 5s 抓取全部；Grafana(:3000) 出面板。
> 前提：Slurm 已跑通（第 4 章）。若目标集群的 slurm 源码构建**没有 JSON 序列化插件**（`squeue --json` 报 fatal），
> 不影响本方案——所有导出器都解析 `-h` 文本输出。

### 6.1 安装 Prometheus 与 node_exporter [全部节点]
```bash
apt-get update
apt-get install -y prometheus prometheus-node-exporter python3-prometheus-client
# 无 python3-prometheus-client 包时：pip3 install prometheus-client
systemctl enable --now prometheus-node-exporter
systemctl is-active prometheus-node-exporter        # active
curl -s localhost:9100/metrics | head -c 80         # 有输出即正常
```

### 6.2 Prometheus 配置 [admin]
> 监控类 job 抓取间隔 5s（与导出器 5s 刷新同步）；全局默认 15s。
```bash
cat > /etc/prometheus/prometheus.yml <<"EOF"
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: prometheus
    static_configs:
      - targets: ["localhost:9090"]
  - job_name: node
    scrape_interval: 5s
    static_configs:
      - targets: ["admin:9100", "<GPU02>:9100", "<GPU03>:9100"]
  - job_name: slurm
    scrape_interval: 5s
    static_configs:
      - targets: ["localhost:8080"]
  - job_name: gpu
    scrape_interval: 5s
    static_configs:
      - targets: ["admin:9835", "<GPU02>:9835", "<GPU03>:9835"]
  - job_name: slurm-jobs
    scrape_interval: 5s
    static_configs:
      - targets: ["localhost:8085"]
EOF
systemctl restart prometheus      # 之后改配置可用 systemctl reload prometheus 热加载
sleep 2
curl -s localhost:9090/-/healthy   # Prometheus Server is Healthy.
```
> 主机名按 0.2 变量表替换；新加计算节点时在 node/gpu 两个 job 的 targets 里追加。

### 6.3 导出器（python 实现，零第三方二进制依赖）

**目录 [全部节点]**：`mkdir -p /opt/slurm-monitor`

**slurm 调度导出器（:8080，只装 admin）** [admin]：
```bash
cat > /opt/slurm-monitor/slurm_exporter.py <<"PYEOF"
#!/usr/bin/env python3
"""slurm 实时调度指标导出器 (:8080) - 解析 sinfo/squeue 文本(无 JSON 插件也可用)"""
import subprocess, time
from prometheus_client import start_http_server, Gauge

NODE_ALLOC = Gauge("slurm_node_allocated_cpus", "", ["node"])
NODE_IDLE  = Gauge("slurm_node_idle_cpus", "", ["node"])
NODE_TOTAL = Gauge("slurm_node_total_cpus", "", ["node"])
NODE_STATE = Gauge("slurm_node_state", "", ["node", "state"])
JOB_COUNT  = Gauge("slurm_job_count", "", ["state", "partition"])

def scrape():
    # 节点: %N 节点 | %T 状态 | %C 分配/空闲/其他/总数 | %G gres
    # 注意: sinfo 状态可能带后缀(mixed-、idle* 等), 用 strip(" *-") 归一化
    out = subprocess.check_output(["sinfo", "-h", "-N", "-o", "%N|%T|%C|%G"], text=True)
    NODE_ALLOC.clear(); NODE_IDLE.clear(); NODE_TOTAL.clear(); NODE_STATE.clear()
    for line in out.splitlines():
        p = line.strip().split("|")
        if len(p) < 3: continue
        node, state = p[0], p[1].lower().strip(" *-")
        try:
            a, i, o, t = p[2].split("/")
            NODE_ALLOC.labels(node=node).set(int(a))
            NODE_IDLE.labels(node=node).set(int(i))
            NODE_TOTAL.labels(node=node).set(int(t))
        except ValueError:
            continue
        NODE_STATE.labels(node=node, state=state).set(1)
    # 队列: 每作业一行 %t 状态 %P 分区
    q = subprocess.check_output(["squeue", "-h", "-o", "%t|%P"], text=True)
    JOB_COUNT.clear()
    for line in q.splitlines():
        p = line.strip().split("|")
        if len(p) < 2: continue
        JOB_COUNT.labels(state=p[0], partition=p[1]).inc()

if __name__ == "__main__":
    start_http_server(8080)
    while True:
        try:
            scrape()
        except Exception:
            pass
        time.sleep(5)
PYEOF
```

**队列明细导出器（:8085，只装 admin，含用户、命令与优先级）** [admin]：
```bash
cat > /opt/slurm-monitor/jobqueue_exporter.py <<"PYEOF"
#!/usr/bin/env python3
"""实时队列表导出器 (:8085) - 每作业: 作业号/用户/作业名/状态/分区/优先级/节点/命令
优先级列 %Q = slurm 整数优先级(1~10 档时为 QoS 档值), 与 squeue -o %Q 一致
命令列 %o: sbatch <脚本> 显示脚本路径; sbatch --wrap/srun 为 (null), 转空串"""
import subprocess, time
from prometheus_client import start_http_server, Gauge

JOB = Gauge("slurm_job_info", "1 if job present",
            ["job_id", "name", "user", "partition", "state", "priority", "nodes", "cmd"])

def scrape():
    raw = subprocess.check_output(
        ["squeue", "-h", "-o", "%i|%P|%u|%j|%t|%N|%Q|%o"], text=True)
    JOB.clear()
    for line in raw.splitlines():
        parts = line.split("|")
        if len(parts) < 8: continue
        job_id, partition, user, name, state, nodes, priority = parts[:7]
        cmd = "|".join(parts[7:]).strip()
        if cmd == "(null)": cmd = ""
        JOB.labels(job_id=job_id, name=name, user=user, partition=partition,
                   state=state, priority=priority, nodes=nodes, cmd=cmd).set(1)

if __name__ == "__main__":
    start_http_server(8085)
    while True:
        try:
            scrape()
        except Exception:
            pass
        time.sleep(5)
PYEOF
```

**GPU 导出器（:9835，每台有 NVIDIA GPU 的节点，兼容 GeForce）** [每台 GPU 节点]：
```bash
cat > /opt/slurm-monitor/gpu_exporter.py <<"PYEOF"
#!/usr/bin/env python3
"""GPU 指标导出器 (:9835) - 基于 nvidia-smi, 零外部依赖, 兼容 GeForce"""
import http.server, socket, subprocess

def collect():
    out = subprocess.check_output([
        "nvidia-smi", "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
        "--format=csv,noheader,nounits"], text=True)
    host = socket.gethostname()
    lines = []
    for row in out.splitlines():
        f = [x.strip() for x in row.split(",")]
        if len(f) < 7: continue
        idx, name, util, mem_used, mem_tot, temp, pwr = f
        def g(t): return t if t.replace(".", "", 1).isdigit() else "0"
        name_e = name.replace("\\", "\\\\").replace("\"", "\\\"")
        base = f"gpu=\"{idx}\",name=\"{name_e}\",host=\"{host}\""
        lines.append(f"nvidia_gpu_utilization_percent{{{base}}} {g(util)}")
        lines.append(f"nvidia_gpu_memory_used_bytes{{{base}}} {float(g(mem_used))*1048576:.0f}")
        lines.append(f"nvidia_gpu_memory_total_bytes{{{base}}} {float(g(mem_tot))*1048576:.0f}")
        lines.append(f"nvidia_gpu_temperature_celsius{{{base}}} {g(temp)}")
        lines.append(f"nvidia_gpu_power_watts{{{base}}} {g(pwr)}")
    return "\n".join(lines) + "\n"

class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/metrics":
            self.send_response(404); self.end_headers(); return
        try:
            body = collect().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            body = f"collect error: {e}\n".encode()
            self.send_response(500); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass

http.server.HTTPServer(("0.0.0.0", 9835), H).serve_forever()
PYEOF
chmod +x /opt/slurm-monitor/*.py
```

### 6.4 systemd 单元与启动
```bash
# [admin] slurm 调度导出器
cat > /etc/systemd/system/slurm-exporter.service <<"EOF"
[Unit]
Description=Slurm scheduler exporter
After=network.target

[Service]
ExecStart=/usr/bin/python3 /opt/slurm-monitor/slurm_exporter.py
Restart=always
User=root          # 改成能执行 sinfo/squeue 的用户

[Install]
WantedBy=multi-user.target
EOF

# [admin] 队列明细导出器
cat > /etc/systemd/system/jobqueue-exporter.service <<"EOF"
[Unit]
Description=Slurm job queue exporter
After=network.target

[Service]
ExecStart=/usr/bin/python3 /opt/slurm-monitor/jobqueue_exporter.py
Restart=always
User=root

[Install]
WantedBy=multi-user.target
EOF

# [每台 GPU 节点] GPU 导出器
cat > /etc/systemd/system/gpu-exporter.service <<"EOF"
[Unit]
Description=GPU exporter (nvidia-smi)
After=network.target

[Service]
ExecStart=/usr/bin/python3 /opt/slurm-monitor/gpu_exporter.py
Restart=always
User=root

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
```
```bash
# [admin] 启动调度 + 队列导出器并验证
systemctl enable --now slurm-exporter jobqueue-exporter
sleep 2
systemctl is-active slurm-exporter jobqueue-exporter        # active active
curl -s localhost:8080/metrics | grep -E '^slurm_node_state'   # 每节点一行, state 为 idle/mixed/alloc...
curl -s localhost:8085/metrics | grep slurm_job_info            # 空队列无输出, 正常

# [每台 GPU 节点] 启动 GPU 导出器并验证
systemctl enable --now gpu-exporter
sleep 2
curl -s localhost:9835/metrics | grep '^nvidia_gpu' | head -6
# 期望: nvidia_gpu_utilization_percent{gpu="0",name="...",host="<本机名>"} 0
```

### 6.5 Grafana 安装 [admin]

**方式 A：官方 apt 源（网络正常时）**
```bash
mkdir -p /etc/apt/keyrings
curl -fsSL https://apt.grafana.com/gpg.key | gpg --dearmor -o /etc/apt/keyrings/grafana.gpg
echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" \
  > /etc/apt/sources.list.d/grafana.list
apt-get update && apt-get install -y grafana
```
> ⚠️ 两个坑（实测）：① signed-by 的 keyring **必须 `gpg --dearmor` 成二进制**，ASCII 文本会报 NO_PUBKEY；
> ② 本网络对 apt.grafana.com/packages.grafana.com 限速 ~20KB/s 装不动 → 走方式 B。

**方式 B：GitHub release + 加速代理（实测使用）**
```bash
# 先查最新版本号与资产名（资产名带构建号，直接猜 URL 会 404）
curl -s https://api.github.com/repos/grafana/grafana/releases/latest | \
  python3 -c "import json,sys; d=json.load(sys.stdin); print(d['tag_name']); \
  [print(a['browser_download_url']) for a in d['assets'] if 'linux_amd64.deb' in a['name'] and 'enterprise' not in a['name']]"
# 以 v13.2.1 为例（本集群实际安装版本）；GitHub 直连慢可加 gh-proxy.com 前缀（本网实测 38MB/s）：
curl -sL -o /tmp/grafana.deb \
  "https://gh-proxy.com/https://github.com/grafana/grafana/releases/download/v13.2.1/grafana_13.2.1_33191028959_linux_amd64.deb"
dpkg -i /tmp/grafana.deb && rm -f /tmp/grafana.deb
```

**启动**
```bash
systemctl enable --now grafana-server
# 首次启动跑数据库迁移，约 30 秒后才监听 3000：
for i in $(seq 1 30); do ss -tln | grep -q ':3000 ' && break; sleep 1; done
curl -s localhost:3000/api/health    # {"database":"ok","version":...}
```

### 6.6 数据源 + 仪表盘（改密码后执行）
```bash
# 设 Grafana 密码（默认 admin/admin，建议立即改）
grafana-cli admin reset-admin-password '<GRAFANA_PASSWORD>'

# 创建 Prometheus 数据源（uid=promds）
curl -s -u admin:<GRAFANA_PASSWORD> -X POST -H "Content-Type: application/json" \
  -d '{"name":"Prometheus","type":"prometheus","url":"http://localhost:9090","access":"proxy","isDefault":true,"uid":"promds"}' \
  localhost:3000/api/datasources

# 导入仪表盘：把本手册【附录 B】的 JSON 全文（或同目录 grafana-slurm-dashboard.json）存为 /tmp/dash.json 后执行：
python3 - <<"PY"
import json, urllib.request, base64
dash = json.load(open("/tmp/dash.json"))
body = json.dumps({"dashboard": dash, "overwrite": True}).encode()
req = urllib.request.Request("http://localhost:3000/api/dashboards/db", data=body,
    headers={"Content-Type": "application/json"})
req.add_header("Authorization", "Basic " + base64.b64encode(b"admin:<GRAFANA_PASSWORD>").decode())
print(urllib.request.urlopen(req, timeout=15).read()[:200])
PY

# 设为首页（打开根地址即见仪表盘）
curl -s -u admin:<GRAFANA_PASSWORD> -X PUT -H "Content-Type: application/json" \
  -d '{"homeDashboardUID":"slurm-realtime","theme":"","timezone":""}' \
  localhost:3000/api/org/preferences
```

### 6.7 第 6 章验收
```bash
# ① 所有抓取目标 up（应 10 个: prometheus/node×3/slurm/gpu×3/slurm-jobs）
curl -s localhost:9090/api/v1/targets | python3 -c \
  "import json,sys; [print(t['labels']['job'], t['scrapeUrl'], t['health']) for t in json.load(sys.stdin)['data']['activeTargets']]"

# ② 无作业时应为 空闲3/有作业0/异常0（三数之和恒等于节点总数）
for q in "count(slurm_node_state{state=\"idle\"}) or on() vector(0)" \
         "count(slurm_node_state{state=~\"alloc|mixed\"}) or on() vector(0)"; do
  curl -s -m 5 "localhost:9090/api/v1/query" --data-urlencode "query=$q"; echo; done

# ③ 提交一个作业, 30 秒内“有作业节点”变 1、队列表出现该作业（root 提交时 -o 用本地 /tmp）
sbatch --gres=gpu:1 -o /tmp/test-%j.out --time=00:05:00 \
  --container-image=/share/images/<你的镜像>.sqsh \
  --wrap="nvidia-smi -L || true"
squeue -o "%.8i %.12u %.14j %.3t %N"

# ④ 浏览器打开 http://<admin>:3000 → 「SLURM 实时调度总览」自动刷新 10s
```
> 计数查询里 `or on() vector(0)` 不能省：PromQL 对"0 个"返回空向量，Stat 面板 lastNotNull 会残留旧值
> （“没作业却显示 1 个有作业节点”就是它造成的）；面板已内置，勿在面板里删。
---

## 7. 多用户化（磁盘配额 + 会计 + 建号/扩容脚本）

> 目标：每个用户账号 → 家目录 `/share/home/<user>`（NFS 全网一份）→ 每人 `/share` 总量 500G 配额（可随时扩容）
> → 公共数据集 `/share/datasets`（顶层平铺 sticky，可读他人、只可删改自己的）→ 用户自行 sbatch/srun（含容器）。
> 决策（已确认）：配额口径=每用户 /share 总量；datasets=顶层平铺；会计=启用 slurmdbd+MariaDB；admin 继续当计算节点。

### 7.1 先厘清“配额”的三种含义

| 维度 | 管什么 | 由谁实现 | 本章位置 |
|---|---|---|---|
| 磁盘配额 | 每人在 /share 上的存储量（500G） | **ext4 usrquota**（与 Slurm 无关） | 7.2 |
| 调度优先级 | 谁先跑（fairshare 动态公平） | slurmdbd + priority/multifactor | 7.6（默认 FIFO） |
| 作业级限制 | 并发作业数/时长/GPU 卡时上限 | sacctmgr QoS / 关联限额 | 7.6（可选） |

> ⚠️ ext4 只有按 UID 的配额、**没有按目录的配额**。500G 统计的是该用户在整个 /share 上的全部文件
> （家目录 + 数据集 + enroot 缓存）。想“家目录与数据集分开设限”需要 XFS(pquota) 或独立盘，另议。

### 7.2 磁盘配额启用 [admin]
```bash
apt-get install -y quota

# fstab 持久化（先把 2.1 那行加上 usrquota；先备份）
cp /etc/fstab /etc/fstab.bak-multiuser
sed -i 's#^\(/dev/sda /share ext4 defaults\),noatime#\1,noatime,usrquota#' /etc/fstab
grep '^/dev/sda' /etc/fstab          # 期望: /dev/sda /share ext4 defaults,noatime,usrquota 0 2

# 立即生效（NFS 客户端不受影响）
mount -o remount,usrquota /share
findmnt -T /share -o OPTIONS        # 含 usrquota

# 建配额文件并开启
quotacheck -cugm /share              # 生成 /share/aquota.user（fs 根下）
quotaon /share
quotaon -p /share                    # 期望 user quota on; group/project off
ls -la /share/aquota.user
```

配额大小换算（1K 块 = 配额值）：

| 配额 | blocks | setquota 示例 |
|---|---|---|
| 100G | 104857600 | `setquota -u <user> 104857600 104857600 0 0 /share` |
| **500G（默认）** | **524288000** | `setquota -u <user> 524288000 524288000 0 0 /share` |
| 1T | 1073741824 | `setquota -u <user> 1073741824 1073741824 0 0 /share` |

> soft=hard 相等 = **无宽限期、到顶即拒写**（扩容随时一条命令）。查：`repquota -u /share` / `quota -u <user>`。
> ⚠️ 配额在**服务端强制**（admin 的 /dev/sda），计算节点无需配额工具；但用户要在 `<GPU02>/<GPU03>` 上**自查询**需要：
> [admin] `systemctl enable --now quotarpc`（rpc.rquotad，配额包自带，默认 disabled；NFS 配额查询服务）
> [`<GPU02>/<GPU03>`] `apt-get install -y quota`（只要客户端 quota 命令）。然后任意节点 `quota -s` 都能查到服务器配额。

### 7.3 /share/datasets 使用约定
- 顶层 1777 sticky 已就绪（2.2）。每个用户下载自己的数据集即可：他人可读、你只能删改自己的文件。
- **同名会撞**：sticky 下无法覆盖/删除他人同名文件 → 建议命名带用户名或日期。
- 所有用户保持默认 umask 022（文件 644），他人才能读；不要在 .bashrc 里改 umask 077。
- root 在计算节点下载会变 nobody 所有（root_squash，见 2.3 警告框）——数据下载一律用普通用户。

### 7.4 会计：MariaDB + slurmdbd [admin]

**① 安装 MariaDB 并建库建账号**
```bash
apt-get install -y mariadb-server
systemctl enable --now mariadb

# 密码随机生成，仅存本机 /root/.slurmdb.pass(600)；文档一律用 <SLURMDB_PASSWORD> 占位
PASS=$(openssl rand -hex 12)
echo -n "$PASS" > /root/.slurmdb.pass
chmod 600 /root/.slurmdb.pass

mariadb <<SQL
CREATE DATABASE IF NOT EXISTS slurm_acct_db;
CREATE USER IF NOT EXISTS 'slurm'@'localhost' IDENTIFIED BY '$PASS';
GRANT ALL PRIVILEGES ON slurm_acct_db.* TO 'slurm'@'localhost';
FLUSH PRIVILEGES;
SQL
```

**② MariaDB 调优（消除 slurmdbd 启动告警 + 写入性能）**
> slurmdbd 启动会检查数据库变量，不满足推荐值（≥ 一半）就打一行 error（不影响运行但建议满足）。
> 检查项与阈值来自 slurm 源码：`innodb_buffer_pool_size`≥2G、`innodb_lock_wait_timeout`≥450、
> `innodb_log_file_size`≥32M、`max_allowed_packet`≥8M。
```bash
cat > /etc/mysql/mariadb.conf.d/99-slurm.cnf <<'CNF'
[mysqld]
innodb_buffer_pool_size = 2G
innodb_lock_wait_timeout = 450
max_allowed_packet = 64M
CNF
systemctl restart mariadb
```

**③ slurmdbd.conf（属主 slurm、权限 600）**
```bash
mkdir -p /var/log/slurm && chown slurm:slurm /var/log/slurm
cat > /etc/slurm/slurmdbd.conf <<CONF
DbdHost=admin
DbdPort=6819
SlurmUser=slurm
StorageType=accounting_storage/mysql
StorageHost=localhost
StoragePort=3306
StorageUser=slurm
StoragePass=<SLURMDB_PASSWORD>
StorageLoc=slurm_acct_db
AuthType=auth/munge
LogFile=/var/log/slurm/slurmdbd.log
PidFile=/run/slurmdbd/slurmdbd.pid
DebugLevel=info
PurgeEventAfter=1month
PurgeJobAfter=3month
PurgeResvAfter=1month
PurgeStepAfter=1month
CONF
chown slurm:slurm /etc/slurm/slurmdbd.conf && chmod 600 /etc/slurm/slurmdbd.conf
```

slurmdbd.conf 参数说明：

| 参数 | 作用 | 备注 |
|---|---|---|
| `StorageType=accounting_storage/mysql` | 存储后端 | 需要第 3 章编译时的 mysql 插件（已带） |
| `StorageHost/Port/User/Pass/Loc` | 数据库连接 | 密码来自 /root/.slurmdb.pass |
| `DbdHost/DbdPort` | slurmdbd 自身地址 | 6819 |
| `SlurmUser=slurm` | 运行身份 | 与配置文件的属主一致 |
| `Purge*After` | 历史数据自动清理周期 | 作业 3 个月/步骤 1 个月等，可调 |
| `LogFile/PidFile` | 日志与 PID | 目录需 slurm 可写（/var/log/slurm、/run/slurmdbd 由 systemd RuntimeDirectory 建） |

**④ 启动 slurmdbd**
```bash
# slurmdbd.service 已在 3.5 拷贝；启动并确认无告警
systemctl enable --now slurmdbd
sleep 3
systemctl is-active slurmdbd
ss -tlnp | grep 6819
tail -3 /var/log/slurm/slurmdbd.log    # 期望无 "not recommended" 告警
```

**⑤ slurm.conf 开启会计（admin），并同步到全部节点**
```bash
cat >> /etc/slurm/slurm.conf <<'CONF'

# ---- 会计 (slurmdbd) 启用 ----
AccountingStorageType=accounting_storage/slurmdbd
AccountingStorageHost=admin
AccountingStoragePort=6819
JobAcctGatherType=jobacct_gather/linux
JobAcctGatherFrequency=30
CONF
systemctl restart slurmctld
```
> ⚠️ 两个实测教训（务必照做）：
> ① `AccountingStorageHost` 必须填 **slurmdbd 所在主机名（admin）**，不能写 `localhost`——写 localhost 时
> 只有 admin 本机能连会计，`<GPU02>/<GPU03>` 上的客户端会去连自己的 6819 报 Connection refused；
> ② 客户端工具（sacct/sacctmgr）读**本机** /etc/slurm/slurm.conf——改完必须把 slurm.conf **同步到全部节点
> 并重启 slurmd**，否则计算节点上报 "Slurm accounting storage is disabled"（配置没会计行）或 refused（有行但 host 错）。
> 前提：各节点能解析 admin（1.3 hosts 已写）。

```bash
# [<GPU02>/<GPU03>] 用你习惯的方式把 admin 的 /etc/slurm/slurm.conf 覆盖到本机后：
systemctl restart slurmd
md5sum /etc/slurm/slurm.conf      # 三节点应一致
sacct -X -n -o JobID,User,State   # 三节点都应能出记录（不再 disabled/refused）
```

**⑥ sacctmgr 建 cluster/account/user 关联 [admin]**
```bash
sacctmgr -i add cluster lab                       # 若提示已存在则忽略（slurmctld 首次连上会自建）
sacctmgr -i add account lab Organization=lab Description="默认账户"
sacctmgr -i add user root account=lab adminlevel=Administrator
sacctmgr -i add user lab account=lab
sacctmgr show assoc format=Cluster,Account,User,AdminLevel
```
> 会计刚开时 `AccountingStorageEnforce` 默认为 none（不做强制），没有关联的用户也能提交；
> 关联的意义在于 fairshare 份额与 sacctmgr 限额（7.6）。历史从启用当天起累积。

### 7.5 管理脚本（建号 + 配额，[admin] 一次性安装）
```bash
mkdir -p /opt/cluster-admin
cat > /opt/cluster-admin/add-user.sh <<'SCRIPT'
#!/usr/bin/env bash
# 用法:
#   在 admin 上:   /opt/cluster-admin/add-user.sh <用户名> [配额如 500G]
#                  (创建家目录 /share/home/<用户名> 并设置 /share 配额)
#   在 <GPU02>/<GPU03>: /opt/cluster-admin/add-user.sh <用户名>
#                  (仅建账号与组，不建家目录; 家目录经 NFS 自动可见)
# 可选 -u <UID>: 三节点自动分配 UID 不一致时，在计算节点上强制指定
set -eu
USERNAME=""; QUOTA="500G"; FORCE_UID=""
while [ $# -gt 0 ]; do
  case "$1" in
    -u) FORCE_UID="$2"; shift 2 ;;
    *) if [ -z "$USERNAME" ]; then USERNAME="$1"; shift; else QUOTA="$1"; shift; fi ;;
  esac
done
[ -n "$USERNAME" ] || { echo "用法: add-user.sh <用户名> [配额]  [-u UID]"; exit 1; }
HOST="$(hostname -s)"
if id "$USERNAME" &>/dev/null; then echo "[!] $USERNAME 已存在，跳过"; exit 0; fi

case "$HOST" in
  admin)
    echo "[admin] 创建用户与家目录 ..."
    if [ -n "$FORCE_UID" ]; then
      useradd -m -s /bin/bash -d "/share/home/$USERNAME" -u "$FORCE_UID" -o "$USERNAME"
    else
      useradd -m -s /bin/bash -d "/share/home/$USERNAME" "$USERNAME"
    fi
    UID_NOW="$(id -u "$USERNAME")"
    /opt/cluster-admin/set-quota.sh "$USERNAME" "$QUOTA" || true
    # 自动建 sacctmgr 关联（启用即挂默认 QoS normal = 中等优先级 / 作业限制不限）
    sacctmgr -i add user "$USERNAME" account=lab qos=normal >/dev/null 2>&1 \
      || echo "[!] sacctmgr 关联失败，请手动: sacctmgr add user $USERNAME account=lab qos=normal"
    echo "[admin] 完成: $USERNAME uid=$UID_NOW 家目录=/share/home/$USERNAME 磁盘配额=$QUOTA 作业QoS=normal(中等/不限)"
    echo "[admin] 下一步:"
    echo "        1) passwd $USERNAME"
    echo "        2) <GPU02>/<GPU03> 上执行: /opt/cluster-admin/add-user.sh $USERNAME"
    echo "           (若计算节点自动 UID != $UID_NOW, 加 -u $UID_NOW)"
    echo "        3) 后续调单个用户优先级/配额: sacctmgr(见 7.6)，改后需 systemctl restart slurmctld"
    ;;
  <GPU02>|<GPU03>)
    echo "[$HOST] 创建账号(不建家目录) ..."
    if [ -n "$FORCE_UID" ]; then
      useradd -M -s /bin/bash -d "/share/home/$USERNAME" -u "$FORCE_UID" -o "$USERNAME"
    else
      useradd -M -s /bin/bash -d "/share/home/$USERNAME" "$USERNAME"
    fi
    echo "[$HOST] 完成: $USERNAME uid=$(id -u "$USERNAME")"
    ;;
  *)
    echo "[!] 未知主机 $HOST —— 请分别在 admin/<GPU02>/<GPU03> 上执行本脚本"; exit 1 ;;
esac
SCRIPT

cat > /opt/cluster-admin/set-quota.sh <<'SCRIPT'
#!/usr/bin/env bash
# set-quota.sh —— 设置/扩容某用户在 /share 上的配额（admin 上执行）
# 用法: /opt/cluster-admin/set-quota.sh <用户名> <大小>    例: 500G / 1T / 200M
# ext4 按 UID 计配额，覆盖该用户在 /share 上的全部文件; soft=hard 即时生效
set -eu
U="${1:?用法: set-quota.sh <用户名> <大小如 500G|1T>}"
SZ="${2:?用法: set-quota.sh <用户名> <大小如 500G|1T>}"
case "$SZ" in
  *[Gg]) N="${SZ%[Gg]}";   BLK="$(awk "BEGIN{printf \"%.0f\", $N*1024*1024}")" ;;
  *[Tt]) N="${SZ%[Tt]}";   BLK="$(awk "BEGIN{printf \"%.0f\", $N*1024*1024*1024}")" ;;
  *[Mm]) N="${SZ%[Mm]}";   BLK="$(awk "BEGIN{printf \"%.0f\", $N*1024}")" ;;
  *) echo "大小需带单位 M/G/T, 如 500G"; exit 1 ;;
esac
id "$U" &>/dev/null || { echo "用户 $U 不存在"; exit 1; }
[ "$(hostname -s)" = admin ] || { echo "配额只能在 admin 上设置"; exit 1; }
setquota -u "$U" "$BLK" "$BLK" 0 0 /share
echo "== 已设置: $U 软/硬上限 = $SZ (blocks=$BLK) =="
quota -u "$U" | tail -3
SCRIPT

cat > /opt/cluster-admin/show-quota.sh <<'SCRIPT'
#!/usr/bin/env bash
# show-quota.sh —— 查看 /share 全部用户配额使用情况（admin 上执行）
repquota -u /share
SCRIPT
chmod +x /opt/cluster-admin/*.sh
```

**新用户上线流程**：
```bash
# ① admin
/opt/cluster-admin/add-user.sh <USER> 500G
# ② <GPU02>、<GPU03>（UID 不一致时加 -u <admin 上的 UID>）
/opt/cluster-admin/add-user.sh <USER>
# ③ admin 设初始密码
passwd <USER>
# ④ 会计关联：① 已由 add-user.sh 自动完成(qos=normal)；手工补做时的等价命令:
#    sacctmgr -i add user <USER> account=lab qos=normal
# ⑤ skel 使用须知（可选，新家目录自动带；内容见下 CLUSTER-README.txt 段落）
```

用户须知模板（写入 `/etc/skel/CLUSTER-README.txt`，新家目录自动携带）：
```
【目录】 /share/home/<用户名> 家目录(NFS 共享) | /share/datasets 公共数据集区 | /share/images 镜像(只读)
【配额】 每人在 /share 总量默认 500G（家目录+数据集+缓存合计）。查看: quota -s。扩容找管理员。
【数据集】 /share/datasets 全读、只能删改自己下载的；同名无法覆盖别人的，命名带上用户名/日期。
【作业】 sbatch -p gpu -N1 -c4 --wrap "echo hi"
        srun -p gpu -N1 --gres=gpu:1 --container-image=/share/images/<镜像>.sqsh python3 train.py
        squeue / scancel <jobid> / sacct
```

### 7.6 每用户静态优先级 + 作业配额（不用 fairshare，2026-09-06 已落地）

> 设计：管理员**人为调控**的静态优先级，不用 fairshare 的自动动态公平。每个用户挂一个 QoS，
> QoS 承载"该用户优先级 + 作业级限制"；所有人默认同档 `normal`（中等、不限），需要时单独给某人
> 建高档 QoS 即可。磁盘配额（ext4）与它无关，见 7.2。

**已生效的配置（照抄即可）：**

```bash
# slurm.conf 追加（admin；已含 7.4 的会计行）
cat >> /etc/slurm/slurm.conf <<'CONF'

# ---- 静态优先级(管理员调控) + 强制关联 (2026-09-06) ----
PriorityWeightQOS=1        # 权重=1 → 作业优先级显示值 = QoS.Priority（1~10 整数档）
PriorityFlags=NO_NORMAL_QOS
AccountingStorageEnforce=associations,limits
CONF
# 同步 slurm.conf 到 <GPU02>/<GPU03> 后重启 slurmctld

# QoS 默认档：normal = 中等优先级(5) / 作业限制不限；已执行
sacctmgr -i modify qos normal set priority=5
```

**参数作用表：**

| 参数/字段 | 作用 | 当前值 |
|---|---|---|
| `PriorityWeightQOS` | QOS 因子权重；唯一非零且 =1 → **作业优先级 = QoS.Priority**（直接显示 1~10 整数） | 1 |
| `PriorityFlags=NO_NORMAL_QOS` | **关键**：让 QOS 因子用你设定的 QoS.Priority，而非"按用量归一化"的值（默认会随用量波动，等于隐形 fairshare） | NO_NORMAL_QOS |
| `PriorityWeightFairShare` 等 | 其它因子权重 | 0（fairshare 永久关闭，无自动升降） |
| `AccountingStorageEnforce` | `associations`=无关联拒提交；`limits`=限制真正执行 | associations,limits |
| QoS `Priority` | 该档静态优先级（**整数 1~10**，越大越先跑；同值按提交先后 FIFO） | normal=5 |
| QoS `MaxJobsPerUser` / `MaxSubmitJobsPerUser` / `MaxWallDurationPerJob` / `MaxTRES` / `MaxTRESMins` | 该用户的作业级限制（并发/累计/时长/单作业资源/累计卡时）；空=不限 | 空（不限） |
| QoS `Grp*` | **账户级**共享总额（同 account 用户共享）——要"每人独立"用 `Max*` | 空 |

> 查看真实优先级用整数列：`squeue -o "%.8i %.10u %.9Q %.3t"`（%Q 为整数值；%p 是 0-1 浮点显示会像 0.000000，别看它）。

**日常使用（管理员调控）：**

```bash
# 看当前排队与优先级
squeue -o "%.8i %.10u %.9Q %.3t %R"

# 给某人"单独调高优先级/单独限量"：建专属 QoS（优先级与限制都独立），再指派给他
sacctmgr -i add qos vip-alice priority=8                    # 高档（1~10 里取 8）
sacctmgr -i add qos limit-bob priority=5 maxjobsperuser=2 maxwallduration=48:00:00
sacctmgr -i modify user alice account=lab set qos=normal,vip-alice defaultqos=vip-alice
sacctmgr -i modify user bob   account=lab set qos=normal,limit-bob defaultqos=limit-bob
# ⚠️ ① qos 列表必须保留 normal（account=lab 的默认 QoS 要求有权访问它），defaultqos= 决定作业实际用哪档
#    ② 改完必须 systemctl restart slurmctld 才会刷新关联/QoS 缓存
# 还原某用户
sacctmgr -i modify user alice account=lab set qos=normal defaultqos=normal
# 删除临时档
sacctmgr -i delete qos name=vip-alice
```

> fairshare 说明：本方案**刻意不用** fairshare（`PriorityWeightFairShare=0` 且不设份额）；
> 若要日后切回动态公平，去掉 `NO_NORMAL_QOS`/权重改 fairshare 即可（参考旧版 runbook 附录）。

**无抢占（当前配置与实测结论，2026-09-06）：**

- 本集群**未配置抢占**（slurm.conf 无 `PreemptType`/`PreemptMode`，QoS 也无 `Preempt` 字段）。
- 因此优先级只影响两件事：① 排队时谁在前；② 资源空出后谁先被调度。**正在运行的低优先级作业不会被高优先级打断**。
- 实测：3 个 prio=5 作业跑满 3 卡时，prio=10 的作业持续 `PD` 等待，运行中的 5 一直 `R` 到自然结束；空出 GPU 后 prio=10 才被调度（且排在任何新普通 pending 之前）。排队视图用整数列：`squeue -o "%.8i %.10u %.9Q %.3t"`。
- QoS.Priority 合法范围 0~2³²−1，本方案只用 **1~10** 整数档（0=最低不参与调度加分，正常档 normal=5）。
- 若将来需要"高优先级抢占运行中任务"，必须显式配置 `PreemptType=preempt/qos` + `PreemptMode=CANCEL|REQUEUE` 并给 QoS 配 `Preempt`/`PreemptMode`——会中途杀作业/重排，本方案刻意不用。

### 7.7 第 7 章验收
```bash
# 配额
quotaon -p /share                          # user quota on
repquota -u /share                         # 能看到各用户用量与 500G 上限
# 从计算节点自查询（quotarpc + 客户端 quota）
#   [<GPU02>] quota -u lab                    # 能显示 admin:/share 的行
# 会计
sacct                                   # admin 出表
#   [<GPU02>] sacct                        # 计算节点也能出记录（slurm.conf 已同步）
sacctmgr show assoc format=Cluster,Account,User,AdminLevel
# 容器多用户（lab 身份 CPU/GPU 各一次，见 5.8 ③，期望 EXIT=0 与 torch True）
# 建号验证（新建一个用户后）
id <USER>                                # 三节点 uid 一致
ls -ld /share/home/<USER>                # NFS 家目录
quota -u <USER>                          # 500G 可见
sacct -u <USER>                          # 有作业记录
```
---

## 8. 总验收与排障

### 8.1 全量验收清单（部署完最后跑一遍）

| # | 项 | 命令（在标注机器） | 期望 |
|---|---|---|---|
| 1 | 时钟 | `[全部] chronyc tracking` | Stratum 正常、无大漂移 |
| 2 | NFS | `[<GPU02>] df -h /share`；`runuser -u lab -- touch /share/home/lab/t` | 挂载正常、属主 lab |
| 3 | 集群 | `[admin] scontrol ping`；`sinfo` | UP；三节点 idle |
| 4 | GPU 调度 | `[admin] srun --gres=gpu:1 nvidia-smi` | 显示被分配的卡 |
| 5 | 容器 CPU | `[admin] runuser -u lab -- srun ... ubuntu .sqsh cat /etc/os-release` | EXIT=0 |
| 6 | 容器 GPU | `[admin] runuser -u lab -- srun --gres=gpu:1 ... pytorch .sqsh python3 -c "import torch;print(torch.cuda.is_available())"` | True |
| 7 | 监控抓取 | `[admin] curl -s localhost:9090/api/v1/targets` | 10 个 up |
| 8 | 监控计数 | `[admin]` 查 `count(slurm_node_state{state="idle"}) or on() vector(0)` | 3 |
| 9 | 配额 | `[admin] quotaon -p /share`；`repquota -u /share` | user quota on；有上限 |
| 10 | 配额自查询 | `[<GPU02>] quota -u lab` | 能显示 admin:/share 行 |
| 11 | 会计 | `[admin] sacct`；`[<GPU02>] sacct` | 两处都出表 |
| 12 | 建号链路 | 按 7.5 建一个测试用户并提交 | id/家目录/quota/sacct 全通 |

### 8.2 排障总表（按现象查）

| 现象 | 原因与处理 |
|---|---|
| NFS 挂载 `access denied by server` | exports 网段与客户端 IP 不匹配：`exportfs -v` 看放行范围，改对 `/etc/exports` 后 `exportfs -ra` |
| 重启后 /share 没挂上（df 显示根分区设备） | fstab NFS 行缺 `_netdev`；`mount -a` 恢复并补 `_netdev` |
| slurmd 重启起不来：`Unable to bind listen port (6818)` | ① /etc/hosts 缺本机自身 IP（根因）；② 网络未就绪 → 加 network-online drop-in（4.6⑥）。修好 `scontrol update nodename=<节点> state=idle` 拉回 |
| `srun` 报 `Invalid MPI type 'pmix'` | 缺 `libpmix2t64`，重启 slurmctld/slurmd |
| slurmctld 起不来：`This host (xxx) not a valid controller` | 主机名没改成 admin（SlurmctldHost 要求） |
| munge 跨机认证失败 / `Protocol authentication error` | key 不一致或时钟漂移。跨机一条命令查：`munge -n \| ssh <节点> unmunge`；**同步密钥后必须重启 munged**（只在启动时读 key），admin 自己改过 key 也要重启自己的 munged |
| slurmctld 日志 `Unauthorized credential for client UID=64030` | slurm.conf 缺 `SlurmUser=slurm` |
| `srun --container-image` 找不到参数 | pyxis 插件没加载：查 plugstack.conf、`srun --help \| grep container`、重启 slurmd |
| enroot 容器起不来报 squashfs/fuse 错误 | 装 `squashfs-tools squashfuse`；sysctl(1.4) 生效并重启过 |
| `enroot import` 卡 `Querying registry` | Docker Hub 不可达：换 DaoCloud 源或直接用 nvcr.io |
| root 在 GPU 节点写 /share 被拒 | root_squash **预期行为**：客户端 root → nobody。用普通用户操作；管理操作在 admin（服务端）做，别开 no_root_squash |
| **root 批处理作业 `-o` 落 /share 秒死（输出 nobody、日志 WTERMSIG 53）** | root_squash 三条件同时成立（见 2.3 警告框）：`-o` 改节点本地 `/tmp`；或改用普通用户提交（第 7 章后常态） |
| 容器能启动但看不到 GPU | ① gres 没配好（编译版 `AutoDetect=nvml`）；② enroot GPU 钩子缺 nvidia-container-cli 或 NVIDIA_VISIBLE_DEVICES（5.4/5.5）；③ 驱动与镜像 CUDA 不兼容 |
| `slurmd -C` 不显示 GPU | ① 驱动加载（nvidia-smi）；② /dev/nvidia* 存在；③ gres.conf 正确 |
| 节点 `INVALID_REG+DRAIN` | slurmd 注册与 slurm.conf 不符：最常见是缺 `/etc/slurm/gres.conf`（补 AutoDetect=nvml 重启 slurmd），或 NodeName 行 CPUs/内存/Gres 与 `slurmd -C` 不符 |
| 容器作业 `mkdir: /scratch/enroot-data: Permission denied` | root 先跑过 enroot 把它建成 0700：`chmod 1777 /scratch/enroot-data`（5.3） |
| **纯 CPU 容器秒挂：`95-slurm-gpus.sh exited with return code 1`** | 95 钩子旧版末行退出码 bug——用 5.5 的修复版（末尾显式 exit 0） |
| 计算节点 `sacct` 报 disabled / Connection refused | ① slurm.conf 没同步到该节点（客户端读本机配置）；② `AccountingStorageHost=localhost` 应填 admin。见 7.4⑤ |
| `squeue --json` 报 fatal serializer_required | 源码构建无 JSON 插件，正常；监控导出器全部 `-h` 文本解析 |
| 监控“没作业却显示有作业 1” | PromQL `count(空集)` 返回空向量、lastNotNull 残留旧值：查询加 `or on() vector(0)` + `instant:true`（面板已内置） |
| sinfo 状态 `mixed-`/`idle*` 匹配不到 | 状态带后缀：导出器已 `.lower().strip(" *-")` 归一化，勿删 |
| Grafana apt NO_PUBKEY | signed-by keyring 必须 `gpg --dearmor` 二进制格式 |
| Grafana 装了但 :3000 不通 | 首次启动迁移约 30 秒；等它监听再 curl /api/health |
| 显存数值不对 | 面板单位用 `bytes`；`decmbytes` 是非法单位 |
| 计算节点 `quota` 报无此服务/查不到 | admin 没启 `quotarpc`（7.2）或计算节点没装 quota 客户端包 |
| slurmd restart 卡 deactivating | `systemctl stop slurmd; pkill -9 slurmd; sleep 1; systemctl start slurmd` |
| slurmd 无限刷 `fetch_config: DNS SRV lookup failed` | 本地 slurm.conf 缺/不含本机 NodeName（26.05 回退拉配置走 DNS SRV）：补含本机 NodeName 的 slurm.conf + hosts 本机条目 |
| slurmd 起不来/卡住报 cgroup 错 | 缺 cgroup.conf（与 slurm.conf 一起拷）；或退回 `ProctrackType=proctrack/linuxproc` 排查 |
| 同组第二个用户用共享缓存 Permission denied | enroot 自动建的缓存目录 0700：管理员预建 `group-<gid>` 1777（5.3） |
| apt.grafana.com 限速装不动 | 走 GitHub release + gh-proxy.com 前缀（6.5 方式 B） |

---

## 附录 A：速查与随附文件

### A.1 常用运维命令

```bash
sinfo                          # 节点/分区状态
squeue -o "%.8i %.12u %.14j %.3t %.10M %N"   # 队列明细
scancel <jobid>                # 杀作业
sacct -u <user> -X -o JobID,JobName,State,Elapsed    # 历史
sreport user top               # 用量排行
quota -s                       # 自己配额（任意节点）
repquota -u /share             # 全员配额（admin）
scontrol update nodename=<节点> state=idle    # 拉回节点
journalctl -u slurmd -n 50     # 节点日志
```

### A.2 随附文件与关键路径

| 文件/路径 | 节点 | 说明 |
|---|---|---|
| `/etc/slurm/{slurm,cgroup,gres,plugstack.conf,slurmdbd}.conf` | 全部/按章 | slurm 配置（三节点 slurm.conf 必须一致） |
| `/usr/local/lib/slurm/accounting_storage_mysql.so` | admin | 会计插件（第 3 章编译时确认存在） |
| `/opt/slurm-monitor/*.py` + systemd 单元 | 按第 6 章 | 监控导出器 |
| `/etc/prometheus/prometheus.yml` | admin | 抓取配置 |
| `/opt/cluster-admin/{add-user,set-quota,show-quota}.sh` | admin | 建号/配额脚本（也拷到 `<GPU02>/<GPU03>`） |
| `/etc/skel/CLUSTER-README.txt` | admin | 新用户须知模板 |
| `/root/.slurmdb.pass`(600) | admin | slurmdbd DB 密码（文档用占位符） |
| `grafana-slurm-dashboard.json` | admin | 仪表盘 JSON（=附录 B） |

### A.3 源文档映射（本手册升级/合并自）

| 本手册章节 | 源文档 |
|---|---|
| 1-5 | cluster-deploy-manual.md（去 sudo 化、加 mysql 插件、95 钩子修复版、root_squash 警告） |
| 6 | slurm-monitoring-runbook.md |
| 7-8 | slurm-multiuser-runbook.md |

## 附录 B：grafana-slurm-dashboard.json 全文（= 同目录同名文件，导入用）

> 保存为 `/tmp/dash.json` 后执行 6.6 的导入命令。uid=`slurm-realtime`，标题「SLURM 实时调度总览」，
> 刷新 10s，**8 个面板**：1 节点状态 / 2 运行排队作业数 / 3 GPU 占用率 / 4 实时队列表（含**优先级**列，
> 值来自队列导出器 `%Q`，1~10 档下即 QoS 档值）/ 5 按用户聚合（每用户每状态作业数，R/PD 分行显示）/
> 6 节点 CPU 分配 / 7 GPU 显存占用 / 8 **节点内存占用（全宽）**（node_exporter `node_memory_*`，已用=总量−可用，
> instance 已转 node 标签）。第 4 号面板状态列显示原始代码（R/PD/CG…）；要中文标签需在面板 overrides 里把
> matcher 字段名 `state` 改成 `状态` 后重新导入。节点内存使用率面板未保留（不展示百分比表）。

```json
{
  "uid": "slurm-realtime",
  "title": "SLURM 实时调度总览",
  "tags": ["slurm"],
  "timezone": "browser",
  "schemaVersion": 39,
  "version": 1,
  "refresh": "10s",
  "time": { "from": "now-1h", "to": "now" },
  "templating": { "list": [] },
  "panels": [
    {
      "id": 1, "type": "stat", "title": "节点状态", "gridPos": { "h": 4, "w": 8, "x": 0, "y": 0 },
      "datasource": { "type": "prometheus", "uid": "promds" },
      "fieldConfig": { "defaults": { "mappings": [], "color": { "mode": "thresholds" }, "thresholds": { "mode": "absolute", "steps": [ { "color": "green", "value": null } ] } }, "overrides": [] },
      "options": { "colorMode": "background", "graphMode": "none", "justifyMode": "auto", "orientation": "horizontal", "reduceOptions": { "calcs": ["lastNotNull"] }, "textMode": "auto" },
      "targets": [
        { "refId": "idle", "expr": "count(slurm_node_state{state=\"idle\"}) or on() vector(0)", "legendFormat": "空闲节点", "instant": true },
        { "refId": "busy", "expr": "count(slurm_node_state{state=~\"alloc|mixed\"}) or on() vector(0)", "legendFormat": "有作业节点", "instant": true },
        { "refId": "bad", "expr": "count(slurm_node_state{state=~\"drain|down|error|maint\"}) or on() vector(0)", "legendFormat": "异常节点", "instant": true }
      ]
    },
    {
      "id": 2, "type": "timeseries", "title": "运行 / 排队作业数", "gridPos": { "h": 8, "w": 8, "x": 8, "y": 0 },
      "datasource": { "type": "prometheus", "uid": "promds" },
      "fieldConfig": { "defaults": { "color": { "mode": "palette-classic" }, "custom": { "fillOpacity": 20, "lineWidth": 2, "showPoints": "never" }, "unit": "short" }, "overrides": [] },
      "options": { "legend": { "displayMode": "list", "placement": "bottom", "calcs": ["lastNotNull"] }, "tooltip": { "mode": "multi" } },
      "targets": [
        { "refId": "A", "expr": "sum by (state) (slurm_job_count)", "legendFormat": "{{state}}" }
      ]
    },
    {
      "id": 3, "type": "stat", "title": "GPU 占用率(%)", "gridPos": { "h": 4, "w": 8, "x": 16, "y": 0 },
      "datasource": { "type": "prometheus", "uid": "promds" },
      "fieldConfig": { "defaults": { "unit": "percent", "color": { "mode": "thresholds" }, "thresholds": { "mode": "absolute", "steps": [ { "color": "green", "value": null }, { "color": "orange", "value": 50 }, { "color": "red", "value": 90 } ] } }, "overrides": [] },
      "options": { "colorMode": "background", "graphMode": "none", "justifyMode": "auto", "orientation": "horizontal", "reduceOptions": { "calcs": ["lastNotNull"] }, "textMode": "auto" },
      "targets": [
        { "refId": "a", "expr": "avg by (host) (nvidia_gpu_utilization_percent)", "legendFormat": "{{host}}" }
      ]
    },
    {
      "id": 4, "type": "table", "title": "实时队列表（作业 / 用户 / 优先级 / 命令）", "gridPos": { "h": 10, "w": 16, "x": 0, "y": 4 },
      "datasource": { "type": "prometheus", "uid": "promds" },
      "fieldConfig": { "defaults": { "color": { "mode": "thresholds" }, "thresholds": { "mode": "absolute", "steps": [ { "color": "green", "value": null } ] } }, "overrides": [] },
      "options": { "showHeader": true, "sortBy": [] },
      "transformations": [
        { "id": "organize", "options": { "excludeByName": { "Time": true, "Value": true, "__name__": true, "instance": true, "job": true }, "indexByName": { "job_id": 0, "user": 1, "name": 2, "state": 3, "priority": 4, "partition": 5, "nodes": 6, "cmd": 7 }, "renameByName": { "job_id": "作业号", "user": "用户", "name": "作业名", "state": "状态", "priority": "优先级", "partition": "分区", "nodes": "节点", "cmd": "命令" } } }
      ],
      "targets": [
        { "refId": "A", "expr": "slurm_job_info", "format": "table", "instant": true }
      ]
    },
    {
      "id": 5, "type": "table", "title": "按用户聚合", "gridPos": { "h": 10, "w": 8, "x": 16, "y": 4 },
      "datasource": { "type": "prometheus", "uid": "promds" },
      "fieldConfig": { "defaults": { "color": { "mode": "thresholds" }, "thresholds": { "mode": "absolute", "steps": [ { "color": "green", "value": null } ] } }, "overrides": [] },
      "options": { "showHeader": true, "sortBy": [] },
      "transformations": [
        { "id": "organize", "options": { "excludeByName": { "Time": true }, "renameByName": { "user": "用户", "state": "状态", "Value": "作业数" } } }
      ],
      "targets": [
        { "refId": "A", "expr": "count by (user, state) (slurm_job_info)", "format": "table", "instant": true, "legendFormat": "{{user}}-{{state}}" }
      ]
    },
    {
      "id": 6, "type": "table", "title": "节点 CPU 分配 (%)", "gridPos": { "h": 8, "w": 12, "x": 0, "y": 14 },
      "datasource": { "type": "prometheus", "uid": "promds" },
      "fieldConfig": { "defaults": { "unit": "percent", "color": { "mode": "thresholds" }, "thresholds": { "mode": "absolute", "steps": [ { "color": "green", "value": null }, { "color": "orange", "value": 60 }, { "color": "red", "value": 90 } ] } }, "overrides": [] },
      "options": { "showHeader": true, "sortBy": [] },
      "transformations": [
        { "id": "organize", "options": { "excludeByName": { "Time": true, "Value": false }, "renameByName": { "node": "节点", "Value": "已分配%" } } }
      ],
      "targets": [
        { "refId": "A", "expr": "100 * slurm_node_allocated_cpus / slurm_node_total_cpus", "format": "table", "instant": true }
      ]
    },
    {
      "id": 7, "type": "timeseries", "title": "GPU 显存占用", "gridPos": { "h": 8, "w": 12, "x": 12, "y": 14 },
      "datasource": { "type": "prometheus", "uid": "promds" },
      "fieldConfig": { "defaults": { "unit": "bytes", "color": { "mode": "palette-classic" }, "custom": { "fillOpacity": 20, "lineWidth": 2, "showPoints": "never" } }, "overrides": [] },
      "options": { "legend": { "displayMode": "list", "placement": "bottom", "calcs": ["lastNotNull"] }, "tooltip": { "mode": "multi" } },
      "targets": [
        { "refId": "A", "expr": "nvidia_gpu_memory_used_bytes", "legendFormat": "{{host}} 已用" },
        { "refId": "B", "expr": "nvidia_gpu_memory_total_bytes", "legendFormat": "{{host}} 总量" }
      ]
    },
    {
      "id": 8, "type": "timeseries", "title": "节点内存占用", "gridPos": { "h": 8, "w": 24, "x": 0, "y": 22 },
      "datasource": { "type": "prometheus", "uid": "promds" },
      "fieldConfig": { "defaults": { "unit": "bytes", "color": { "mode": "palette-classic" }, "custom": { "fillOpacity": 20, "lineWidth": 2, "showPoints": "never" } }, "overrides": [] },
      "options": { "legend": { "displayMode": "list", "placement": "bottom", "calcs": ["lastNotNull"] }, "tooltip": { "mode": "multi" } },
      "targets": [
        { "refId": "used", "expr": "label_replace(node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes, \"node\", \"$1\", \"instance\", \"(.*):[0-9]+\")", "legendFormat": "{{node}} 已用" },
        { "refId": "total", "expr": "label_replace(node_memory_MemTotal_bytes, \"node\", \"$1\", \"instance\", \"(.*):[0-9]+\")", "legendFormat": "{{node}} 总量" }
      ]
    }
  ]
}
```

---

## 附录 C：本手册固化的修复清单（全部来自本集群实战）

| 问题（会踩的坑） | 固化位置 | 一句话对策 |
|---|---|---|
| 首次编译漏 mysql 插件，开会计要整包重建 | 3.1/3.3 | 编译依赖加 libmariadb-dev + `ln -sf mariadb_config mysql_config`，configure 后核对三行输出 |
| pyxis 95 钩子导致纯 CPU 容器必挂 | 5.5 | 用修复版脚本（env 缺失 exit 0 + 末尾显式 exit 0） |
| root 先跑 enroot 把 /scratch/enroot-data 建成 0700 | 5.3 | 预建 1777（幂等执行） |
| root 批处理作业 -o 落 /share 秒死（root_squash） | 2.3 警告框 | root 作业 -o 用 /tmp；或普通用户提交 |
| 计算节点 sacct disabled / refused | 7.4⑤ | slurm.conf 三节点同步 + `AccountingStorageHost=admin`（勿 localhost） |
| 配额无法在计算节点自查询 | 7.2 | admin 启 `quotarpc` + 计算节点装 quota 客户端 |
| 监控空集残留旧值（没作业显示 1） | 6.7 | `or on() vector(0)` + instant（JSON 已内置） |
| sinfo 状态带后缀匹配不到 | 6.3 导出器 | `.lower().strip(" *-")`（勿删） |
| Grafana apt NO_PUBKEY | 6.5 方式 A | keyring 必须 `gpg --dearmor` 二进制 |
| apt.grafana.com 限速 | 6.5 方式 B | GitHub release + gh-proxy.com 前缀 |
| Grafana 首启 30 秒无监听 | 6.5 | 轮询 :3000 再 curl /api/health |
| 显存单位非法 | 附录 B | 单位 `bytes`，表达式保留原始字节 |
| slurmdbd 启动 innodb 告警 | 7.4② | 99-slurm.cnf：buffer_pool≥2G、lock_wait≥450、max_allowed_packet≥64M |
| 管理员改 slurm.conf 忘记同步节点 | 4.6②/7.4⑤ | 每次改完同步全部节点 + 重启 slurmd |

> 密码约定：文档只用占位符 `<GRAFANA_PASSWORD>` / `<SLURMDB_PASSWORD>`；实际值分别存本机
> Grafana（自设）与 `/root/.slurmdb.pass`(600)。
> 一致性：本手册附录 B JSON == 同目录 `grafana-slurm-dashboard.json` == 线上 Grafana（2026-09-05 复核）。

**部署完成。** 从第 1 章到第 7 章每章验收通过后，最后跑一遍第 8.1 节总验收即可交付使用。
