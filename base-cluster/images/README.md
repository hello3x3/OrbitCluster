# base-cluster/images —— 容器镜像构建源（Dockerfile）

本目录存放**集群 `/share/images/*.sqsh` 的构建源码**。`.sqsh` 本体每个 7–12 GB，
只存在于集群上、不进仓库（见根 `.gitignore` 的 `*.sqsh`）；这里保存的是「怎么造出来的」。

## 目录约定

```
base-cluster/images/
├── README.md                          # 本文件
├── start_ssh.sh                       # ★ 加固版容器入口脚本（所有镜像统一用这一份）
└── cuda12.8.0-devel-ubuntu24.04/      # 一个镜像 = 一个目录（构建上下文）
    ├── Dockerfile                     #   原样来自上游 Docker-Base-Images 仓库
    ├── README.md                      #   原样（顶部加了仓库内说明；上游原文其余未改）
    ├── start_ssh.sh                   #   ★ 已替换为加固版（= ../start_ssh.sh）
    └── start_ssh.original-2026-09.sh  #   存档：线上镜像里那份未加固的原文（逐字节一致）
```

**新增/更新镜像**时，按 `cuda12.8.0-devel-ubuntu24.04/` 的样子放一个同名目录即可：
`Dockerfile` + `README.md` + `start_ssh.sh`（用 `../start_ssh.sh` 这份加固版）。

## 与线上镜像的对应关系（已核实）

| 证据 | 结果 |
|---|---|
| `cuda12.8.0-devel-ubuntu24.04/start_ssh.original-2026-09.sh` vs 从 `.sqsh` 里取出的 `/opt/start_ssh.sh` | **sha256 完全相同**（`a2ee71e2e29f7a06…`，6900 字节） |
| Dockerfile 第 37 行 `echo root:5233 \| chpasswd` vs 镜像 `/etc/shadow` 里 root 的哈希 | `crypt("5233", hash) == hash` → **True** |
| Dockerfile 第 54/55 行 `/opt/ssh-authorized_keys` | 镜像内存在，`-rw-r--r--` |
| 第 64 行 `/opt/miniconda3/envs` 1777 | 镜像内 `drwxrwxrwt` |
| 第 86/87 行 `/tmp/conda-pkgs` 1777 | 镜像内 `drwxrwxrwt` |
| 第 95 行 `WORKDIR /workspace` | 镜像内存在 |
| 第 91 行 vim 5 项缩进设置 | `/etc/vim/vimrc` 命中 5 行 |
| 第 38 行 entrypoint 里的 `service ssh start` | 镜像内 `/opt/nvidia/entrypoint.sh` 命中 |

结论：**`cuda12.8.0-devel-ubuntu24.04.sqsh` 确实是由本目录的 Dockerfile + `start_ssh.sh` 构建的。**

## 重建镜像（docker build → .sqsh）

```bash
cd base-cluster/images/cuda12.8.0-devel-ubuntu24.04
# 1) 构建（start_ssh.sh 已是加固版，不要再换成旧的那份）
docker build -t cuda12.8.0-devel-ubuntu24.04:latest .

# 2) 转成 enroot/pyxis 用的 .sqsh（二选一）
#    a. 镜像已推进 registry：
enroot import -o /tmp/cuda12.8.sqsh docker://<registry>/cuda12.8.0-devel-ubuntu24.04:latest
#    b. 离线：本机有 docker daemon 时可直接吃 dockerd://
docker save cuda12.8.0-devel-ubuntu24.04:latest | \
  enroot import -o /tmp/cuda12.8.sqsh dockerd://cuda12.8.0-devel-ubuntu24.04:latest

# 3) 放到管理节点的 /share/images/（root:root 644；名字随意，门户按 *.sqsh 自动扫描）
scp /tmp/cuda12.8.sqsh root@<ADMIN>:/share/images/
ssh root@<ADMIN> 'chown root:root /share/images/cuda12.8.sqsh && chmod 644 /share/images/cuda12.8.sqsh'

# 4) 冒烟：门户「申请资源」选该镜像起一个 GPU 容器，然后用登记的私钥
#    ssh -p <端口> <用户名>@<节点IP> 进去看 nvidia-smi
```

> 换镜像后建议同步更新 `oa/config-snapshot/images-list.txt` 与 `oa/config-snapshot/env-notes.md`
> 里对镜像的说明（哪些内置了 sshd、能否交互 SSH）。

## ⚠️ 加固要求（重建时务必遵守）

1. **`start_ssh.sh` 必须用加固版**（本目录的 `start_ssh.sh` / `../start_ssh.sh`）。
   加固点：不再只在「非 root 模式」才关口令认证，而是**任何身份**都强制
   `PermitRootLogin=prohibit-password` + `PasswordAuthentication=no` + `PermitEmptyPasswords=no`
   + `PubkeyAuthentication=yes`。
   原因：镜像内置 `sshd_config` 是 `PermitRootLogin yes` + `PermitEmptyPasswords yes`，
   且 root 有固定口令 `5233`（Dockerfile 第 37 行）；而 enroot **共享宿主机网络命名空间** ——
   容器一旦以 root 运行，就等于把一个「口令公开的 sshd」暴露给整个内网。
   加固后 root 模式仍然可用（前提是配了公钥），只是不再认口令。
   > 由此 **上游 README 里「root 模式可用密码 root:5233 登录」的说法在本仓库已作废**；
   > 该 README 已保留原文，仅在顶部加了说明。
2. **不要给 sshd 加 `--container-remap-root`**（上游 README 也要求一律
   `--no-container-remap-root`：userns 重映射出的 root 会让 Ubuntu sshd 直接拒绝服务）。
   门户侧已在 `oa/cluster-portal/deploy/portal-ctl` 里硬拦截该参数。
3. 节点上 `/etc/enroot/enroot.conf` 的 `ENROOT_REMAP_ROOT` 保持 `n`；
   `oa/scripts/verify-install.sh` 会逐台检查并报 FAIL。
4. 构建时**不要**在镜像里写 `ENV SSH_PORT`（会盖住 pyxis 传入的值）。

## 为什么 `.sqsh` 不进 git

单个 7–12 GB，且属于「部署产物」而非源码；`.gitignore` 已屏蔽 `*.sqsh`。
个人镜像（用户「保存镜像」产生）在 `/share/images/<用户名>/`，更不应入库。
