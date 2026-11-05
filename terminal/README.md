# OrbitCluster CLI

供用户和 agent 调用的独立命令行程序。没有 TUI；客户端使用 Go 标准库，无需 Python、pip、Git 或 Go 运行环境。服务器继续使用现有 Python 后端，资源和权限规则不变。

## 安装

访问门户的「使用文档 → 终端工具使用」，选择 macOS/Linux/WSL 或 Windows PowerShell，复制安装命令。脚本和平台压缩包均由你们部署的门户提供；普通用户不再访问 GitHub，也不现场安装语言依赖。

macOS / Linux / WSL 示例（换成实际服务地址）：

```bash
(
  installer="$(mktemp)" || exit 1
  trap 'rm -f "$installer"' EXIT
  curl --fail --show-error --progress-bar 'http://10.71.106.251/terminal/install.sh' -o "$installer" &&
  bash "$installer" --server 'http://10.71.106.251'
)
```

安装显示五个阶段及下载进度，先校验 SHA256 再运行。默认安装到 `~/.local/share/orbitcluster`，启动器为 `~/.local/bin/orbit`。找不到命令时使用完整路径或将 `~/.local/bin` 加入 PATH。`--install-dir`、`--bin-dir`、`--config-dir` 可自定义目录；不用 sudo，不修改系统 PATH。

原生 Windows：从门户复制 PowerShell 命令，或下载安装器后运行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Server 'http://集群地址'`。默认程序在 `%LOCALAPPDATA%\OrbitCluster\bin\orbit.exe`。安装器使用系统 `curl.exe`，不需要 WSL；按系统架构选 x64 或 ARM64。

也可直接下载门户提供的 macOS、Linux、Windows 压缩包，解压得到一个 `orbit`/`orbit.exe`。便携文件无需安装；删除该文件即可移除程序，配置另行清理。

## 容器环境与存储

本系统以容器为核心，每个资源是独立容器；容器默认以普通用户身份运行。需要安装系统包/CUDA toolkit 时，在容器内执行 `unshare -Ur --map-root-user bash`，获得仅限容器的用户命名空间 root（不是宿主机 root）。`su` 在展开后的 rootfs 中没有 setuid 位，不能切换 root。普通用户可直接跑 `apt-get update`，但 `apt-get install` 的 dpkg 会报 requires superuser privilege；安装包用 `unshare` root。

基础容器已预装 Miniconda：`/opt/miniconda3`。进入容器后 `source /opt/miniconda3/etc/profile.d/conda.sh` 即可使用 conda；不需要在 home 再安装一份。

`/share/home/<用户名>` 只建议存放代码和小型配置，不要放大型数据集、conda 环境、checkpoint 或大量结果（NFS + 配额）。数据从只读 NAS `/mnt/nas/...` 读取，容器环境需要长期保留时保存个人镜像。
单个个人镜像上限 **20 GiB**；保存前检查容器 rootfs，导出后再检查镜像文件，超限会拒绝。不要把 ckpt/大数据放在容器根目录。**到期自动保存默认关闭，到期直接停机**；需要保留环境请提前手动保存。

## 登录与 agent

```bash
orbit login --username 你的用户名
orbit whoami --json
orbit plans --json
orbit accounts --json
orbit quota --json
orbit nodes --json
orbit images list --json
orbit instances list --json
orbit instances create --name experiment --plan 1 --image cuda-base --hours 4 --json
orbit instances show 123 --json
orbit instances wait 123 --until ssh-ready --timeout 300 --json
orbit instances connection 123 --json
orbit instances logs 123 --lines 300 --json
orbit images save --instance 123 --name experiment-env --json
orbit instances wait 123 --until saved --timeout 1800 --json
orbit instances stop 123 --yes --json
orbit instances restart 123 --json
orbit instances delete 123 --yes --json
orbit --help
```

登录由用户在真实终端隐藏输入密码；自动化受控输入可用 `--password-stdin`。不要将密码放入参数、聊天或 shell 历史，不要让 agent 读取 `auth.json`。服务器签发独立凭证，默认七天，到期重新登录，无自动续期、无 Cookie。新客户端兼容旧 `config.json` 与按完整服务地址保存的凭证。

`123` 是门户实例 id，不是 Slurm job_id。套餐和镜像先查询，不猜名称；镜像重名时使用完整路径。所有停机、删除和强制覆盖必须先获得用户授权，再用 `--yes`；没有 `--yes` 的 JSON 写操作会明确拒绝。写请求超时不自动重试，先查询实际状态。

`--json` 成功写 stdout，失败写 stderr。退出码：0 成功、1 业务拒绝/等待超时、2 参数/配置、3 需登录、4 网络故障、130 取消。直接 `orbit` 显示帮助；旧 `orbit tui` 只返回迁移提示，不启动界面。

## 配置与 HTTP

HTTP 默认可用，适用于实验室可信内网；密码和令牌会明文传输。HTTPS 正常校验证书，不自动降级或跟随重定向。

优先级：命令参数 > `ORBIT_*` 环境变量 > `config.json` > 默认值。默认配置目录 `~/.config/orbitcluster`，支持 `XDG_CONFIG_HOME` 与 `ORBIT_CONFIG_DIR`。凭证只发给完全匹配的服务地址；HTTP/HTTPS 不混用。

```bash
orbit config set server http://集群地址
orbit config show
# 需要 HTTPS 内部 CA 时：
orbit config set ca_bundle /本机路径/cluster-ca.crt
# 明确仅允许 HTTPS（可选）：
orbit config set server https://集群地址
orbit config set allow_http false
```

支持 `ORBIT_SERVER`、`ORBIT_CA_BUNDLE`、`ORBIT_TIMEOUT`（120 秒）、`ORBIT_POLL_SECONDS`（5 秒）、`ORBIT_ALLOW_HTTP`（默认 true）。全局选项包括 `--server`、`--ca-bundle`、`--config-dir`、`--request-timeout`、`--poll-seconds`、`--require-https`。保留显式 `allow_http=false` 时仍拒绝 HTTP，重新运行 HTTP 安装器会配置为允许。

## 更新与卸载

更新：重新执行门户安装命令。下载或校验失败不会替换已有程序。旧 Python 客户端的已标记启动器可迁移到独立 CLI，保留配置和凭证，清理其专用虚拟环境。

```bash
orbit logout
orbit uninstall
# 或彻底清除本机配置和登录：
orbit uninstall --purge
# agent 获得授权后的非交互形式：
orbit uninstall --purge --yes --json
```

卸载默认保留配置；purge 只清理已知的 `config.json`、`auth.json`，保留目录内其它文件，拒绝符号链接。卸载不停止作业、不删除服务器数据。`logout` 撤销当前令牌；仅本机清除不会让服务器令牌提前失效，原令牌仍会到期。

## 管理员发布

开发机需 Go 1.24+，在 `terminal/` 执行：

```bash
go test ./...
go run ./cmd/release
```

生成六个平台压缩包、SHA256 与 manifest 到 `oa/cluster-portal/terminal-downloads/`。发布包不含源码仓库或 Python 依赖。将这个目录随 `oa/cluster-portal` 一起交付，再执行原有 `deploy/install.sh`。源码部署前运行 `python3 oa/cluster-portal/scripts/bundle_docs.py` 同步安装脚本和 skill；这些是服务器/开发操作，不是用户安装步骤。

门户只读下载：`/terminal/install.sh`、`/terminal/install.ps1`、`/terminal/downloads/文件名`、`/terminal/manifest.json`、`/terminal/skill.md`。`TERMINAL_DOWNLOAD_DIR` / `PORTAL_TERMINAL_DOWNLOAD_DIR` 可覆盖发布目录。只允许固定客户端文件名，缺包明确返回「尚未发布」，不会回退到源码安装。

新部署默认 `TERMINAL_REQUIRE_HTTPS=0`、`TERMINAL_TOKEN_DAYS=7`。已有显式 HTTPS-only 配置或环境覆盖保留；内网需要 HTTP 时将原值改为 0 后重启 `cluster-portal`。令牌期限变更只影响新登录，没有自动续期。不改变网页 Cookie、用户权限、作业调度或已有部署服务方式。

GitHub Actions 对六个平台运行本机 Go 测试并上传压缩包。发布者将验证通过的包放入门户，不让普通用户依赖 GitHub。Mac 本机验收与各平台 CI 结果见反馈验收文档。
