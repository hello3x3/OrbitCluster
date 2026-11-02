---
name: orbitcluster
description: 安装和使用 OrbitCluster 用户终端，查询或申请实验室集群资源、连接容器、查看日志和管理个人镜像。适用于用户要求通过 orbit CLI 或 agent 操作 OrbitCluster 的任务，不用于集群管理员配置。
---

# OrbitCluster 用户终端

OrbitCluster 的用户命令是 `orbit`，交互界面是 `orbit tui`。以用户授权的现有平台账号操作；该 skill 不提供管理员能力。

## 安装与认证

先检查 `orbit --version`；命令不在 PATH 时检查 `~/.local/bin/orbit`。安装需要 Python 3.10+，支持 macOS、Linux、WSL，无需 sudo。

没有安装时，先向用户取得管理员提供的 HTTPS 服务地址和可选内部 CA 公共证书路径。不要猜测 HTTP 网页对应的 HTTPS 端口，不要关闭证书验证。使用项目安装器，替换下面的服务地址；内部 CA 可在 bash 命令末尾加 `--ca-bundle '/本机绝对路径/cluster-ca.crt'`：

```bash
(
  install_script="$(mktemp)" || exit 1
  trap 'rm -f "$install_script"' EXIT
  curl -fsSL 'https://raw.githubusercontent.com/hello3x3/OrbitCluster/344116330a2568d5c909c5fcc07b54a682e9b944/terminal/install.sh' -o "$install_script" &&
  bash "$install_script" --ref 344116330a2568d5c909c5fcc07b54a682e9b944 --server 'https://管理员提供的地址'
)
```

让用户本人在其终端执行 `orbit login --username 用户名`，隐藏输入平台密码。不要索取聊天中的密码，不读取、复制或输出 `auth.json`，不使用浏览器 Cookie。已有登录可通过 `orbit whoami --json` 检查；凭证默认七天，以服务器返回的到期时间为准，到期由用户重新登录，不自动续期。

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
- 重启使用原镜像并重新分配 SSH 端口，不恢复未保存的修改。关闭 TUI 不会停止任务。
- 写请求超时后先查询任务、镜像、日志，判断是否已成功，不盲目重试创建、重启或镜像导出。
- 退出码 3 表示需登录，让用户在终端重新认证；2 是配置/参数问题，4 是网络问题，1 是业务拒绝或等待超时，0 成功。

个人镜像、显示名、密码与 SSH 公钥的其他命令先读对应的 `--help`。资产页中的数据集和模型仓库目前是前端占位，不能声称已通过 CLI 查询或管理这些资产。
