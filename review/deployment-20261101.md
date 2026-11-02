# ZJU-CV 部署记录（2026-11-01）

> 范围：在 3090-00/01/02/03 上完成底层手册 **§4–§7（跳过 §6 监控）**，并在 3090-00 安装门户。
> 本文记录本次部署中**与手册不一致的改动**、**遇到的问题及解决办法**、**最终验收结果**和**回滚线索**。
> 不记录任何口令：root 助手登录口令、MariaDB/slurmdbd 口令均只在目标机 root-only 文件中。

## 0. 站点实参

| 项 | 值 |
|---|---|
| 集群名 | `ZJU-CV`（Slurm 内部规范化为 `zju-cv`） |
| 管理节点 | `3090-00` = 10.71.106.251，兼计算 |
| 计算节点 | `3090-01` .181 / `3090-02` .182 / `3090-03` .183 |
| GPU | 每台 4×RTX 3090，驱动 595.91.07 |
| Slurm | 26.05.1（源码编译，带 NVML） |
| 账户 | `phd` / `ms` / `be` / `ra`，默认 `phd` |
| 账户 QoS | `acct-phd` / `acct-ms` / `acct-be` / `acct-ra`，priority=5、单用户不限、暂不抢占 |
| `/share` | 3090-00 `/dev/md0` 58T，ext4 usrquota，NFS 导出给 10.130.136.0/24 |
| `/images` | 3090-00 `/dev/md1` 7.3T，ext4，NFS 单独导出并挂到 01/02/03 |
| enroot | 4.2.1 |
| pyxis | v0.24.0 |
| enroot cache | `/scratch/enroot-cache/user-$(id -u)`，本地盘，7 天清理 |
| 监控 | 未安装（按本次范围跳过 §6） |
| 门户 | 已安装：3090-00:80，`root` 为唯一超级管理员 |

## 1. 与手册不同的改动

### 1.1 使用生成器的 4 节点 Slurm 配置，而不是手册 §4.2 的手写版本

手工手册的 §4.2 只列了 3 个 `NodeName`（3090-00/01/02，没有 3090-03），且：
- 3090-02 的 `RealMemory=128491`，`slurmd -C` 实测为 **128492**；
- 手册 §4.6 建议 `gres.conf` 用 `AutoDetect=nvml`，但实测它推导出的 GRES 类型是
  `nvidia_geforce_rtx_3090`，与 NodeName 的 `Gres=gpu:3090:4` 不一致，容易 `INVALID_REG`。

实际采用：
- 用 `provision/render.sh` 按修正后的 `provision/cluster.conf` 生成 4 节点配置；
- `gres.conf` 用显式方式：
  ```conf
  Name=gpu Type=3090 File=/dev/nvidia0
  Name=gpu Type=3090 File=/dev/nvidia1
  Name=gpu Type=3090 File=/dev/nvidia2
  Name=gpu Type=3090 File=/dev/nvidia3
  ```
- `NodeName` 用 `Gres=gpu:3090:4`。

### 1.2 修正 `provision/cluster.conf`

因实际磁盘与实测内存和原配置不同，改为：

```conf
SHARE_DEVICE=/dev/md0
IMAGES_DEVICE=/dev/md1
NODE 3090-02=... cpus=36 mem_mb=128492 ...
```

`deploy/etc/slurm/slurm.conf` 和 `deploy/etc/fstab.3090-00` 已同步这个修正。

### 1.3 `/images` 独立 NFS 导出

`/images` 是 3090-00 的独立盘（7.3T），计算节点必须能看到 `.sqsh`，否则
`--container-image=/images/xxx.sqsh` 会启动失败。

实际做法：
- 3090-00：`/etc/exports` 增加
  ```text
  /images  10.130.136.0/24(rw,sync,no_subtree_check)
  ```
  并 `exportfs -ra`；
- 01/02/03：`/etc/fstab` 增加
  ```fstab
  3090-00:/images /images nfs _netdev,rw,hard,intr,noatime,actimeo=60,x-systemd.requires-mounts-for=/share 0 0
  ```
  然后 `mount /images`。

### 1.4 enroot cache 改为本地 + 7 天清理

没有使用手册默认的 `/share/enroot-cache`：

```conf
ENROOT_CACHE_PATH /scratch/enroot-cache/user-$(id -u)
```

并新增：

```ini
# /etc/systemd/system/enroot-cache-clean.service
[Unit]
Description=Clean old enroot cache

[Service]
Type=oneshot
ExecStart=/usr/bin/find /scratch/enroot-cache -mindepth 1 -mtime +7 -delete
```

```ini
# /etc/systemd/system/enroot-cache-clean.timer
[Unit]
Description=Daily enroot cache cleanup

[Timer]
OnCalendar=*-*-* 04:30:00
Persistent=true

[Install]
WantedBy=timers.target
```

已 `enable --now enroot-cache-clean.timer`，四台一致。
注意：这是站点级改动，生成器的通用模板仍是 `/share/enroot-cache`；以后重新 `make` 生成
`deploy/etc/enroot/enroot.conf` 时要留意不要覆盖回共享 cache。

### 1.5 不创建 `/share/datasets`

按用户要求跳过；因此 §7.3 的 datasets 约定未执行。

### 1.6 账户初始值

- 四账户全部 `priority=5`；
- QoS `MaxTRESPU` 为空（单用户不限卡）；
- QoS `Preempt` 为空（暂不抢占）；
- 关联：`root`、`zju` 默认账户 `phd`，QoS `acct-phd`。

### 1.7 门户安装在 3090-00，端口 80

- 代码目录：`/opt/cluster-portal`；数据目录：`/var/lib/cluster-portal`；运行账号：`portal`。
- 站点配置：`/etc/cluster-portal/site.conf`（来自 `deploy/etc/cluster-portal`）。
- 访问地址：`http://3090-00/` 或 `http://10.71.106.251/`（默认 HTTP 80，不再带 `:8000`）。
- 因为服务以 `portal` 非 root 用户运行，systemd 单元加了：
  ```ini
  AmbientCapabilities=CAP_NET_BIND_SERVICE
  ```
  否则 waitress 绑定 80 会 `Permission denied`。
- 默认只有一个超级管理员 **root**；密码随机生成，写在 `/root/.cluster-portal-admin`（600）。
  root 后续可在门户里创建其他管理员/普通用户；普通管理员看不到「管理账户」和「资源套餐」入口。
- 管理员创建支持三种方式：**自动建号**（真正建 OS 账号+配额+Slurm 关联）、
  **OS 账号已存在**（补配额/关联）、**仅门户管理员**（不建 OS，不设配额）。
  早期代码对 role=admin 的“自动建号”实际只建门户账号，已修复。
- 安装脚本已自动执行 `portal-ctl accounts-init`，当前账户 `phd/ms/be/ra` 均为 `existing`，没有重复创建。

### 1.8 pip 使用浙江大学镜像

系统级 `/etc/pip.conf`：

```ini
[global]
index-url = https://mirrors.zju.edu.cn/pypi/web/simple
timeout = 120
```

门户 venv 也继承该配置；已用 `pip download --no-deps flask` 验证从 ZJU 镜像成功下载。
首次安装时曾因 `files.pythonhosted.org` 读超时失败，见 2.11。

## 2. 部署中遇到的问题与解决

### 2.1 四台 munge key 不一致

**现象**：`/etc/munge/munge.key` 的 md5 四台各不相同（说明各节点此前各自生成过 key）。
**风险**：Slurm 跨机认证会报 Invalid credential。
**解决**：
1. 在 3090-00 用 `mungekey -c -f` 重新生成 key；
2. 通过 base64 + sudo 分发到四台，`chown munge:munge`、`chmod 400`；
3. 四台 `systemctl restart munge`；
4. 验证：
   - 四台 md5 一致；
   - `3090-00 munge -n | ssh 3090-0x unmunge` 全部 `Success (0)`，说明 key 和时钟都正常。

### 2.2 `make` 拒绝再次就地渲染

**现象**：
```text
仓库当前已经是实值版，不能再次就地渲染。
```
原因：上一轮已经执行过 `make` 把文档占位符替换成实值，生成器有意禁止二次就地渲染。

**解决**：不动仓库文档，改用 staging 输出：

```bash
./provision/render.sh -c provision/cluster.conf -o .deploy-new
```

使用 `.deploy-new/etc` 下的配置部署，完成后删除 staging；部署阶段没有执行 `make reset`，全部完成后才执行 `make reset` + `make clean`（见 §6）。

### 2.3 手册只有 3 个 NodeName / 3090-02 内存不一致

**解决**：采用 1.1 的生成器 4 节点配置；3090-02 用实测 `128492`。
四台 `/etc/slurm/slurm.conf` 最终 md5 一致。

### 2.4 GRES 类型与 NodeName 不匹配

**现象**：`slurmd -C` 默认输出 `Gres=gpu:nvidia_geforce_rtx_3090:4`，而手册 NodeName 写 `gpu:3090:4`。
**解决**：使用显式 `gres.conf`（`Type=3090`）并让 NodeName 用 `gpu:3090:4`，四台节点注册后
`sinfo/scontrol` 均显示 `gpu:3090:4`，没有 `INVALID_REG`。

### 2.5 enroot/pyxis 下载网络问题

**现象**：
- 直接从 GitHub release 下载 enroot deb 超时；
- `git ls-remote` 访问 GitHub 报 GnuTLS 错误。
**解决**：
- enroot deb 改用 `gh-proxy.com` 镜像下载；
- pyxis 用 `codeload.github.com` 下载 v0.24.0 tar.gz；
- libnvidia-container 从已配置的 NVIDIA CUDA apt 源安装（1.20.1）。

### 2.6 GPU 容器只有 `CUDA_VISIBLE_DEVICES`，没有 `/dev/nvidia*`

**现象**：
```text
inside_CVD=0
inside_NVIDIA=
ls: /dev/nvidia0: No such file or directory
```
**定位**：临时加 hook 打印 `ENROOT_ENVIRON` 和 host 环境，发现：
- pyxis v0.24.0 把 `CUDA_VISIBLE_DEVICES=0` 放进了 enroot 进程环境；
- 但没有写进 `ENROOT_ENVIRON` 文件；
- 原 `95-slurm-gpus.sh` 只从 env 文件读 `CUDA_VISIBLE_DEVICES`，所以没有生成
  `NVIDIA_VISIBLE_DEVICES`，`98-nvidia.sh` 直接跳过了挂载。

**解决**：给 `95-slurm-gpus.sh` 增加 fallback：

```bash
cvd=$(sed -n 's/^CUDA_VISIBLE_DEVICES=//p' "${env_file}" | head -1)
if [ -z "${cvd}" ]; then
    cvd="${CUDA_VISIBLE_DEVICES:-}"
fi
if [ -n "${cvd}" ]; then
    printf 'NVIDIA_VISIBLE_DEVICES=%s\n' "${cvd}" >> "${env_file}"
fi
```

已同步到四台，并修正：
- `provision/templates/enroot.hooks.d-95-slurm-gpus.sh`
- `base-cluster/all-in-one-cluster-manual.md` §5.5
- `deploy/etc/enroot/hooks.d/95-slurm-gpus.sh`

修复后四台 GPU 容器均验证：
```text
CVD=0 NVIDIA=0
/dev/nvidia0
```

### 2.7 `sacctmgr` 权限错误

**现象**：最初用 `zju` 身份直接执行 `sacctmgr add account`，报：
```text
Only admins/operators/coordinators can add accounts
```
日志里留下了一批 `Security violation`。
**解决**：改用 `sudo sacctmgr`（root）完成账户/QoS/关联创建。
这些是历史日志，当前账户/QoS/关联正常；没有清理审计日志。

### 2.8 `slurm.conf` 配置 hash 告警

**现象**：滚动安装配置时，slurmctld 报各节点 `slurm.conf` 与控制器不一致。
**解决**：四台统一安装同一份配置后，全部重启 `slurmd`；随后 md5 一致，节点 idle，作业正常。
告警是重启前的历史日志。

### 2.9 配额 fstab 的 sed 匹配失败

**现象**：手册 §7.2 用 `/dev/sda /share` 做 sed 匹配，但实际 fstab 是
`UUID=... /share ext4 defaults,noatime,nofail`，匹配不到。
**解决**：用 Python 按“第二字段 == /share 且类型 ext4”精确修改 options，追加 `usrquota`；
备份原 fstab 后再 remount、quotacheck、quotaon。
最终：

```text
/share ... rw,noatime,quota,usrquota ...
```

### 2.10 `runuser -u zju` 失败（测试方法问题，不是部署故障）

**现象**：root 下想用 `runuser -u zju -- srun`，报 `runuser: may not be used by non-root users`。
**原因**：当时 SSH 会话本身是 `zju`，不是 root。
**解决**：直接用 `ssh 3090-00 'srun ...'` 以 zju 身份测试。

### 2.11 pip 默认源超时导致门户安装中断

**现象**：`install.sh` 第 3 步 `pip install` 访问 `files.pythonhosted.org` 时
`ReadTimeoutError`，安装退出。
**解决**：使用浙江大学 PyPI 镜像，并在系统级 `/etc/pip.conf` 固定：
```ini
[global]
index-url = https://mirrors.zju.edu.cn/pypi/web/simple
timeout = 120
```
重新执行安装脚本后依赖安装成功；随后用 `pip download --no-deps flask` 验证镜像可用。

### 2.12 门户改 80 端口时非 root 绑定失败风险

**现象/风险**：门户服务以 `portal` 用户运行；把 `PORTAL_PORT` 改为 80 后，普通用户无法绑定
小于 1024 的特权端口。
**解决**：
1. systemd 单元加 `AmbientCapabilities=CAP_NET_BIND_SERVICE`；
2. `Environment=PORTAL_PORT=80`；
3. `daemon-reload` + 重启；
4. 验证 `ss -lntp | grep ':80 '` 显示 python 监听，`curl http://127.0.0.1/login` 返回 200。
同时把 `install.sh` 改为默认端口 80，并在 `PORT<1024` 时自动生成该 capability，避免重装后再次踩坑。

### 2.13 门户 root 登录/管理页验证

- `curl http://127.0.0.1/login`：HTTP 200；
- 用 `/root/.cluster-portal-admin` 的初始密码 + CSRF 登录：成功进入「用户管理」；
- `GET /admin/users`：HTTP 200；
- 外部访问 `http://10.71.106.251/login`：HTTP 200。
- 门户只有 `root` 一个超级管理员（由 `install.sh` 创建）；其他管理员/用户需 root 后续创建。

### 2.14 门户建号失败：root 到计算节点 SSH 被 AllowUsers 拦住

**现象**：在门户开通 `shujiuhe`（管理员 + 自动建号）时，`portal-ctl provision-user` 报：
```text
无法在 3090-01 创建 /opt/cluster-admin: root@3090-01: Permission denied (publickey).
```
**根因**：`portal-ctl` 以 root 身份通过 SSH 到各计算节点执行 `add-user.sh`，但计算节点的
`/etc/ssh/sshd_config.d/10-portal-only.conf` 实际是 `AllowUsers zju`，root 被挡；
同时 3090-00 的 root 没有到计算节点的 SSH key。

**解决**：
1. 在 3090-00 生成 root ed25519 key（仅内网管理用）；
2. 把公钥追加到四台 `/root/.ssh/authorized_keys`（600）；
3. 把四台的 `AllowUsers zju` 改为 **`AllowUsers root zju`**，保留 `PermitRootLogin prohibit-password`；
4. `sshd -t && systemctl reload ssh`，在 3090-00 以 root 验证 `ssh -p 2022 root@3090-0x hostname`；
5. 重新执行：
   ```bash
   portal-ctl provision-user shujiuhe 500G --accounts phd
   ```
   成功创建 UID=1001（四台一致）、`/share/home/shujiuhe`、500G 配额、`phd/acct-phd` 关联。

> ⚠️ 门户的特权助手依赖 **3090-00 root → 各计算节点 root 的 key 登录**；
> `AllowUsers` 不能只留 `zju`，否则门户建号/停机/日志等跨节点操作都会失败。

### 2.15 user-accounts 默认账户为空

**现象**：`portal-ctl user-accounts zju` 返回 `"default": ""`，但 `sacctmgr` 里默认账户是 `phd`。
**根因**：`sacctmgr show user` 的用户名字段是 `User`，助手用了 `format=Name,DefaultAccount`，
首列为空，导致 `_user_defaults()` 解析不到用户名。
**解决**：把助手 `_user_defaults()` 改为 `format=User,DefaultAccount`，重新安装
`/usr/local/sbin/portal-ctl`；修复后 `user-accounts zju/shujiuhe` 均返回 `default: phd`。

### 2.16 门户「配额（OS 实读）」显示为「—」

**现象**：`shujiuhe` 在 OS 上已有 500G 配额，但门户用户管理页配额列仍显示「—」。
**根因**：portal systemd 单元开了 `PrivateDevices=yes`，而 `BindReadOnlyPaths` 被写死成
`/dev/sda`；实际 `/share` 是 RAID 设备 `/dev/md0`。portal 沙箱里看不到 `/dev/md0`，
`repquota /share` 静默失败（rc=0 但无数据），页面只能显示「—」。

**解决**：
1. 把 portal 单元的 `BindReadOnlyPaths` 改为实际设备 `/dev/md0`；
2. `install.sh` 不再写死 `/dev/sda`，改为 `findmnt -no SOURCE /share` 后 `readlink -f`
   取实际块设备（找不到时才回退 `/dev/sda`）；
3. 重启门户，验证沙箱内 `ls /dev/md0` 可见、`quota-all` 返回 `shujiuhe` 的 500G，
   页面上显示 `24 KiB / 500 GiB`。

### 2.17 root 打开「管理账户」页 500

**现象**：root 访问 `/admin/accounts` 返回 500，日志：
```text
PermissionError: [Errno 13] Permission denied:
/opt/cluster-portal/portalapp/templates/admin_accounts.html
```
**根因**：该模板文件在仓库/部署目录里的权限是 `600`，portal 运行账号 `portal` 读不到。
`install.sh` 正常会 `chmod -R a+rX /opt/cluster-portal`，但本次是手工 rsync 更新代码，
没有重新执行 chmod，rsync 又保留了源文件的 600 权限。

**解决**：
1. `chmod -R a+rX /opt/cluster-portal`（模板改为 644）；
2. 重启 `cluster-portal`；
3. 重启后 `accounts` 表按 `site.conf` 种子化，页面显示 phd/ms/be/ra；
4. 验证 `/admin/accounts` HTTP 200，`/admin/users` 中 `shujiuhe` 显示 `24 KiB / 500 GiB`。

> 以后手工 rsync 部署代码后，记得补 `chmod -R a+rX /opt/cluster-portal`，或直接重跑 `install.sh`。

### 2.18 账户 QoS 报 `no TRES known by type gres/gpu`

**现象**：通过门户新建账户时 `accounts-sync` 失败并回滚：
```text
设置 QoS acct-be 失败: sacctmgr: error: slurmdb_format_tres_str: no TRES known by type gres/gpu
```
**根因**：`slurm.conf` 没有 `AccountingStorageTRES=gres/gpu`；`sacctmgr show tres` 里只有
cpu/mem 等，没有 gres/gpu。QoS 的 `MaxTRESPerUser=gres/gpu=N` 因此无法解析。

**解决**：四台 `slurm.conf` 增加 `AccountingStorageTRES=gres/gpu`，重启 slurmdbd/slurmctld/slurmd；
`sacctmgr show tres` 出现 `gres|gpu`；账户创建与单用户卡数上限恢复正常。

### 2.19 门户沙箱阻止 `useradd/groupadd` 写 `/etc`

**现象**：门户建普通用户报
```text
集群建号失败：管理节点 3090-00 建号失败: groupadd: cannot lock /etc/group; try again later.
```
**根因**：systemd 单元 `ProtectSystem=strict` + `ReadWritePaths=/var/lib/... /etc/cluster-portal /share`
没有放开 `/etc`；root 助手继承 portal 沙箱，`groupadd/useradd` 无法创建锁文件、更新账号库。

**解决**：单元改为 `ReadWritePaths=/var/lib/cluster-portal /etc /share`，重启门户；
门户“自动建号”真实成功（四台 UID 一致、家目录、配额、Slurm 关联均落地）。
portal 进程本身仍是非 root，只有 sudo 白名单的 root 助手能写。

### 2.21 套餐调整：CPU / 1 卡 / 2 卡 / 4 卡

**需求**：只保留四档套餐，并保证一台机器能同时放“四卡套餐 + 2 个 CPU 套餐”。

**最终四档**：

| 套餐 | GPU | CPU / 内存 | 最长时长 |
|---|---:|---:|---:|
| CPU 标准 | 0 | 4C / 8G | 48h |
| GPU 一卡 | 1 | 8C / 16G | 4h（临时调试） |
| GPU 两卡 | 2 | 12C / 32G | 4h（临时调试） |
| GPU 四卡 | 4 | 24C / 40G | 24h |

- 删除旧的 7 个套餐，更新 4 个套餐（无历史实例引用）；
- 同步更新 `db.py` 内置 seed、`render.sh` 生成器、`/etc/cluster-portal/plans.json`；
- 容量实测：3090-00 上同时提交 **4 卡/24C/40G + 2×(4C/8G)**，
  三个作业均 RUNNING，随后全部取消；
- 旧套餐 `GPU 三卡` 等已不再提供。

### 2.22 单卡/双卡优先调试节点（3090-03）

**需求**：单卡/双卡是临时调试，优先调度到同一个节点；同时把最长时长降到 4 小时。

**实现**：

- 套餐：GPU 一卡、GPU 两卡 `maxtime_h=4`（CPU 仍 48h，四卡 24h）；
- Slurm：3090-03 增加 `Features=light`（`/etc/slurm/slurm.conf` + `scontrol update`）；
- 门户：`GPU_LIGHT_FEATURE=light`；1/2 卡**自动调度**时加 `--prefer=light`；
- `--prefer` 是软偏好，3090-03 满载时自动回退；显式指定节点时不加 prefer。

**真机验证**：

- 1 卡与 2 卡作业自动提交，命令均带 `--prefer=light`，两个作业都落在 **3090-03**；
- 随后取消，队列为空。

### 2.23 NAS 只读挂载与容器只读透传

**需求**：七条 NAS 共享先只读挂载，容器内也只读。

**实现**：

- 四个节点 `/etc/fstab` 增加七条 NFS 挂载，统一：
  `ro,nfsvers=3,hard,proto=tcp,noatime,nofail,_netdev`；
- NAS 实测不支持 NFSv4.2（`Protocol not supported`），必须用 NFSv3；
- `/etc/cluster-portal/site.conf` 增加七条 `EXTRA_MOUNTS=/mnt/nas/...:ro`；
- `10.71.106.219` 的导出 ACL 原来未包含 3090-02：
  - 已通过 NAS `10.71.106.220` 的 sudo 修改 `/etc/exports`，给
    `10.130.136.182` 增加 `ro` 导出；
  - 四个节点现在全部直连 `10.71.106.219:/volume1/datasets4`；
  - 3090-00 上临时使用的 re-export 已移除。

**真机验证**：

- 四节点各 7 个 `/mnt/nas/*` 挂载，全部 `ro`；
- root 在 `/mnt/nas/share` 写文件返回 `Read-only file system`；
- 门户 CPU 作业提交命令的 `--container-mounts` 中七个 NAS 路径全部带 `:ro`；
- 测试作业已取消，队列为空。

> 当前 3090-02 已改为直连；NAS 上仅这一条 `.182` 是服务端 `ro`，
> 其余共享的服务端导出仍可能是 rw，但客户端和容器都是 ro。

### 2.24 端口自动分配 / 镜像分区 / 活跃资源截止时间

**需求**：

1. 用户不再维护 SSH 端口，平台自动分配 ≥10000 的空闲随机端口；
2. 「我的镜像」同时展示公共镜像和自己的镜像，分区显示；
3. 「集群状态 → 门户内活跃资源」显示每个任务的上海时间截止时间。

**实现**：

- `db.alloc_port()`：从 10000–65535 随机分配，跳过保留端口、已占用 lease、活跃实例端口
  和停机冷却期；`user_ports.port` 唯一索引防并发冲突；提交失败释放 lease；
- 普通申请、管理员代申请、**重新启动**全部改为自动分配；重启会得到新端口，不复用旧端口；
- 个人资料页移除端口池维护，申请页/代申请页移除端口选择；
- `/images` 普通用户视图分为「公共镜像」和「我的镜像」两张表；
- `enrich_instance.deadline_cn` 按 `started_at + walltime` 计算，统一 `Asia/Shanghai` 输出；
  排队未开始时显示「未开始」。

**真机验证**：

- 申请/代申请提交后自动分配端口（实测 22496 等），无手选端口控件；
- 普通用户 `/images` 同时出现公共镜像与个人镜像分区；
- `/status` 显示「截止时间（上海）」；运行中任务显示例如 `2026-11-14 06:45`；
- 重启得到新的随机端口（本地回归覆盖）；
- `make test`：`CTL_SECURITY_OK` + `SMOKE_OK`。

### 2.25 用户终端功能上线（frontend-optimization）

**上线内容**：

- 合并 `codex/frontend-optimization`（包含 `codex/user-terminal` 的终端 API、CLI/TUI、认证与 HTTPS 部署）；
- 网页保留原 HTTP 80 入口，同时新增 Caddy HTTPS 8443 入口；
- 终端 API：`/api/terminal/v1/*`，Bearer 令牌，默认 7 天，不自动续期；
- Caddy 使用内部 CA，HTTPS 地址：`https://10.71.106.251:8443`。

**服务端配置**：

```ini
TERMINAL_TOKEN_DAYS=7
TERMINAL_REQUIRE_HTTPS=1
TERMINAL_LOGIN_MAX_ATTEMPTS=5
TERMINAL_LOGIN_IP_MAX_ATTEMPTS=50
TERMINAL_LOGIN_WINDOW_S=300
TRUSTED_PROXY=127.0.0.1
```

`/etc/caddy/Caddyfile`：

```caddy
{
    admin off
    auto_https disable_redirects
}

https://10.71.106.251:8443 {
    tls internal
    reverse_proxy 127.0.0.1:80
}
```

**CA 分发**：

- Caddy 内部 CA 根证书复制到：
  - `/etc/cluster-portal/terminal-ca.crt`
  - `/share/orbitcluster-terminal-ca.crt`（供用户复制）
- 终端客户端安装时使用：
  ```bash
  orbit config set ca_bundle /本机路径/orbitcluster-terminal-ca.crt
  ```

**验证**：

- `https://10.71.106.251:8443/login` 返回 200；
- HTTP 终端 API 返回 403 `https_required`；
- HTTPS 终端登录返回 7 天令牌，`/auth/me` 返回 200；
- 本机 CLI 使用 Caddy CA 完成 `orbit login`、`whoami --json`、`plans --json`；
- `make test`（SMOKE_OK）和 `make test-terminal`（54 passed）通过。

## 3. 最终验收

### Slurm

```text
3090-00 idle 40 64037 gpu:3090:4
3090-01 idle 40 128550 gpu:3090:4
3090-02 idle 36 128492 gpu:3090:4
3090-03 idle 40 128550 gpu:3090:4
```

### GPU 容器

zju 身份在 3090-00/01/02/03 四台分别执行：

```bash
srun -N1 -w <node> --gres=gpu:1 \
  --container-image=/images/alpine-latest.sqsh \
  sh -c 'echo CVD=$CUDA_VISIBLE_DEVICES NVIDIA=$NVIDIA_VISIBLE_DEVICES; ls /dev/nvidia0'
```

均输出：
```text
CVD=0 NVIDIA=0
/dev/nvidia0
```

### 容器写 NFS 家目录

zju 在容器内挂 `/share/home/zju:/workspace` 写文件成功，宿主机看到属主 `zju:zju`。

### 会计

- `sacct -X` 已记录 JobID 11–15，User=zju、Account=phd、State=COMPLETED；
- `sacctmgr` 账户：phd/ms/be/ra，QoS acct-*，priority=5，MaxTRESPU 空、Preempt 空；
- `srun --account=ms` 被拒绝（zju 不属于 ms），会计强制关联生效。

### 配额

`zju` 的 `/share` 配额按用户要求设为**不限**（soft=hard=0）：

```text
zju       --    6720       0       0
```

计算节点 `quota -s -u zju` 显示 `none`（即不受配额限制）。
普通用户通过 `add-user.sh` 开通时仍默认 500G；需要时再单独调整。

### 服务

```text
munge       active
slurmctld   active
slurmd      active
slurmdbd    active
mariadb     active
nfs-server  active
quotarpc    active
enroot-cache-clean.timer active
cluster-portal             active
```

### 门户

- `systemctl is-active cluster-portal`：`active`
- 监听：`0.0.0.0:80`
- `http://127.0.0.1/login`：HTTP 200
- `http://10.71.106.251/login`：HTTP 200
- root 登录后 `/admin/users`：HTTP 200；页面上有「用户管理」「管理账户」「我的资源」
- root 初始密码文件：`/root/.cluster-portal-admin`（600）
- pip 配置：`/etc/pip.conf` 指向 `https://mirrors.zju.edu.cn/pypi/web/simple`

## 4. 回滚线索

四台均保留：

```text
/root/deploy-backup-20261101-004801/
  fstab
  exports
  munge.key
  systemd/
```

另外：
- `slurmdbd` 密码：`/root/.slurmdb.pass`（600）
- `slurmdbd.conf`：`/etc/slurm/slurmdbd.conf`（slurm:slurm 600）
- 当前业务测试镜像：`/images/alpine-latest.sqsh`
- 旧配置备份在 `/root/deploy-backup-*`，需要回滚可恢复 fstab/exports/munge/systemd。
- 门户 systemd 单元改 80 前的备份：`/root/cluster-portal.service.bak-before-port80`。
- `/etc/pip.conf` 为新增文件，不需要回滚。

## 5. 待办 / 风险

1. **门户已安装**；当前只有 `root` 一个超级管理员，后续用户/管理员由 root 在「用户管理」创建。
2. **未装监控 §6**。
3. `/share/datasets` 未创建（按要求）。
4. `/images` 仅有测试 alpine；正式镜像需导入。
5. 未做重启验证；但：
   - `/share`、`/images` 已写入 fstab；
   - `/run/enroot`、`/run/pyxis` 有 tmpfiles；
   - quota 由 `share.mount` 的 `Wants=quotaon.service` 负责；
   - cache 清理 timer 已 enable。
6. `zju` 的 OS 家目录是 `/home/zju`，不是 `/share/home/zju`；`/share/home/zju` 存在，`zju` 的 `/share` 配额已设为**不限**。
   后续普通用户用 `/opt/cluster-admin/add-user.sh` 会创建 NFS 家目录并默认 500G 配额。
7. **root 免密 SSH 到计算节点是门户的硬依赖**：`AllowUsers root zju`、root key 和
   `PermitRootLogin prohibit-password` 必须保留；重装 sshd 配置时尤其注意。
8. 当前 `shujiuhe` 已补成：管理员 + OS 账号（UID 1001）+ 500G 配额 + phd 关联。
   纯门户管理员请创建时选“仅门户管理员”。
7. 生成器的通用 enroot 模板仍是 `/share/enroot-cache`；本机实际为本地 cache + timer，重新 `make` 时要注意不要覆盖。

## 6. 本次涉及的仓库/生成物

- `provision/cluster.conf`（gitignored 站点实参）：`SHARE_DEVICE=/dev/md0`、`IMAGES_DEVICE=/dev/md1`、3090-02 `mem_mb=128492`
- `provision/templates/enroot.hooks.d-95-slurm-gpus.sh`：pyxis 环境兜底
- `base-cluster/all-in-one-cluster-manual.md`：§5.5 钩子、§5.2 cache 备选说明
- `deploy/etc/enroot/hooks.d/95-slurm-gpus.sh`、`deploy/etc/enroot/enroot.conf`、`deploy/etc/slurm/slurm.conf`、`deploy/etc/fstab.3090-00`：
  部署时生成的站点文件，已随 `make reset` + `make clean` 清理（不作为仓库模板提交）
- `oa/cluster-portal/deploy/install.sh`：默认端口 80 + 特权端口自动加 `AmbientCapabilities`
- 门户相关文档/测试默认端口从 8000 改为 80；系统 `/etc/pip.conf` 指向 ZJU 镜像
- 门户安装目录 `/opt/cluster-portal`、数据目录 `/var/lib/cluster-portal`、站点配置 `/etc/cluster-portal/site.conf`
- `portalapp/app.py`：管理员“自动建号”真正建 OS/配额；新增 `os_mode=none` 仅门户管理员
- `oa/cluster-portal/deploy/portal-ctl`：修复 `_user_defaults()` 的 `User`/`Name` 字段
- `portalapp/templates/admin_users.html` + `static/app.js`：管理员角色新增“仅门户管理员”，纯管理员隐藏配额/账户
- `tests/smoke_local.py`：新增管理员自动建号 / 纯门户管理员的回归用例
- 四台 `/etc/ssh/sshd_config.d/10-portal-only.conf`：`AllowUsers root zju`；3090-00 root key → 计算节点 root 免密
- `install.sh`：`BindReadOnlyPaths` 按 `/share` 实际块设备动态生成（不再写死 `/dev/sda`）
- 当前 portal 单元：`BindReadOnlyPaths=/dev/md0`（修复配额列静默为空）
- `provision/templates/slurm.conf.in`：增加 `AccountingStorageTRES=gres/gpu`
- `oa/cluster-portal/deploy/install.sh`：`ReadWritePaths` 增加 `/etc`（useradd/groupadd 需要）
- `oa/cluster-portal/deploy/portal-ctl`：cap=0 用 `-1` 清限额；默认账户查询修正
- 功能测试报告：[review/functional-test-20261101.md](functional-test-20261101.md)，本轮 65/65 通过

部署完成后已执行 `make reset` + `make clean`，仓库回到模板状态；
本次源码/文档修复与部署记录随后提交。`make test` 通过。
