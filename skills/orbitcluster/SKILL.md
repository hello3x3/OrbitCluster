---
name: orbitcluster
description: 安装和使用 OrbitCluster 用户终端，查询或申请实验室集群资源、连接容器、查看日志和管理个人镜像。适用于用户要求通过 orbit CLI 或 agent 操作 OrbitCluster 的任务，不用于集群管理员配置。
---

# OrbitCluster 用户终端

OrbitCluster 用户命令是 `orbit`，供用户与 agent 调用；没有 TUI，也不提供管理员能力。

## 安装与认证

先检查 `orbit --version`，命令不在 PATH 时检查 `~/.local/bin/orbit`；原生 Windows 默认 `%LOCALAPPDATA%\OrbitCluster\bin\orbit.exe`。客户端独立运行，无需 Python、pip、Git 或 Go。

安装脚本和对应平台压缩包仅从用户提供的门户地址下载。macOS/Linux/WSL 用 `/terminal/install.sh`（Bash），原生 Windows 用 `/terminal/install.ps1`（PowerShell）。门户 `/guide/terminal` 提供命令生成器。不要猜地址/HTTPS 端口，不要从别处 clone 仓库或临时 pip 安装替代包。

macOS/Linux 示例，替换为用户提供的真实地址：

```bash
(
  installer="$(mktemp)" || exit 1
  trap 'rm -f "$installer"' EXIT
  curl --fail --show-error --progress-bar 'http://用户提供的地址/terminal/install.sh' -o "$installer" &&
  bash "$installer" --server 'http://用户提供的地址'
)
```

HTTP 默认支持，适用于用户的实验室内网；HTTP 会明文传输凭证。HTTPS 正常校验证书，内部 CA 使用 `--ca-bundle '/本机证书路径'`，不跳过验证。安装器显示进度并校验 SHA256；下载缺包或失败时报告问题，不回退到 Git/Python 安装。服务器需要部署现有终端 API 和客户端发布包，skill 不代替管理员改配置。

让用户本人在真实终端执行 `orbit login --username 用户名` 隐藏输入密码。不要索取聊天中的密码，不读取、复制或输出 `auth.json`，不使用浏览器 Cookie。通过 `orbit whoami --json` 检查身份；凭证默认七天，按返回时间到期，由用户重新登录，无自动续期。新 CLI 可读取旧版按服务地址隔离的配置与凭证。

## 容器环境与存储

- **本系统以容器为核心**：每个资源是独立容器；容器拥有完整 root 权限，容器内 `su -` 的 root 密码是 `218`（仅容器内，不是宿主机 root）。日常用普通用户，只有安装系统包/改系统配置时才切换。
- 基础容器已预装 Miniconda，路径 `/opt/miniconda3`；执行 `source /opt/miniconda3/etc/profile.d/conda.sh` 后使用 `conda`。不要建议用户在家目录重复安装 Miniconda。
- 家目录 `/share/home/<用户名>` 只放代码和小型配置；提醒用户不要把大型数据集、conda 环境、checkpoint 或大量结果放在 home（NFS + 配额）。
- 数据优先从只读 NAS `/mnt/nas/...` 直接读取，不要整份复制到 home。代码和少量重要文件放 home 持久保存；容器内环境需要长期保留时保存个人镜像。

## 查询与申请

优先使用 `--json`。成功写 stdout，错误写 stderr。先查询实际可用项，不猜套餐、镜像或账户：

```bash
orbit whoami --json
orbit plans --json
orbit images list --json
orbit accounts --json
orbit nodes --json
orbit quota --json
orbit instances list --json
```

申请使用用户需求中的名称、套餐、镜像、账户和时长，具体参数以 `orbit instances create --help` 为准。任务名 3–10 个字符，同一用户下不区分大小写且不能重复；停止任务不会释放名称。创建、重启和保存镜像会产生真实集群操作，应在用户请求授权的范围内执行。

后续操作使用创建结果中的门户实例 **id**，不是 Slurm `job_id`：

```bash
orbit instances wait ID --until ssh-ready --timeout 300 --json
orbit instances connection ID --json
orbit instances logs ID --lines 300 --json
orbit images save --instance ID --name 保存名称 --json
orbit instances wait ID --until saved --timeout 1800 --json
```

把 `ID` 换成实际门户实例 id。SSH 连接使用已登记公钥对应的本机私钥，私钥不上传平台。

## 写操作与恢复

- 停机、删除资源/镜像/公钥、强制覆盖镜像等操作在用户已经授权后使用 `--yes`，不要把查询请求当作写操作授权。授权已经明确时无需重复询问。
- 手动停机不会自动保存环境。用户需要保留修改时，先保存镜像并确认成功；保存不包含进程、内存和挂载数据。
- 重启使用原镜像并重新分配 SSH 端口，不恢复未保存的修改。退出 CLI 不会停止任务。
- 写请求超时后先查询任务、镜像、日志，判断是否已成功，不盲目重试创建、重启或镜像导出。
- 退出码 3 表示需登录，让用户在终端重新认证；2 是配置/参数问题，4 是网络问题，1 是业务拒绝或等待超时，0 成功。

个人镜像、显示名、密码与 SSH 公钥的其他命令先读对应的 `--help`。资产页中的数据集和模型仓库目前是前端占位，不能声称已通过 CLI 查询或管理这些资产。

## 更新与卸载

更新使用同一门户安装命令。用户要求卸载时用 `orbit uninstall`，明确要求清除本机配置和登录时再加 `--purge`；已获用户授权的非交互执行加 `--yes --json`。`orbit logout` 撤销当前服务器令牌，卸载或本机清理不会停止集群任务或删除服务器资产。卸载默认保留本机配置，不扩展为删除其它目录。
