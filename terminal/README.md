# OrbitCluster 用户终端

交互式 TUI 与可供 Codex 等 agent 调用的 CLI。使用现有门户账号在终端登录，服务器签发独立令牌；默认从签发时起 **7 天**有效，到期重新登录，没有自动续期。网页仍使用原来的 Cookie 和 12 小时策略。

## 用户安装

支持 macOS、Linux 和 WSL，需要 Python >=3.10、网络能访问集群。无需 sudo；使用用户级独立虚拟环境，不修改系统 Python 或 shell 配置。

服务器需要先部署本分支的终端 API。支持 HTTP 和 HTTPS；只有 HTTP 的实验室内网可使用下面的显式开关，无需先搭建 HTTPS。使用 HTTPS 内部 CA 时，请从管理员获取 **CA 公共证书**。

在仓库根目录，一条命令安装并配置（地址和证书路径换成管理员提供的值）：

```bash
bash terminal/install.sh --source terminal \
  --server 'https://你的集群地址:8443' --ca-bundle /path/to/cluster-ca.crt
```

如果证书由系统已信任的 CA 签发，省略 `--ca-bundle`。安装后运行：

```bash
~/.local/bin/orbit tui
```

首次在 TUI 输入已有门户用户名和密码。关闭程序后，作业继续运行。启动器目录已在 PATH 中时可直接使用 `orbit`。

也可用 pipx 或自己的虚拟环境安装本地包：`pip install ./terminal`，入口同样为 `orbit`。

远端安装器支持指定仓库/ref。**分支推送或发布后**，可以用以下方式分发；私有仓库请使用已授权的源码克隆或 wheel，避免在 URL 放密码：

```bash
curl -fsSL https://raw.githubusercontent.com/hello3x3/OrbitCluster/codex/frontend-optimization/terminal/install.sh \
  -o /tmp/orbitcluster-install.sh
bash /tmp/orbitcluster-install.sh --ref codex/frontend-optimization \
  --server 'https://你的集群地址:8443' --ca-bundle /path/to/cluster-ca.crt
```

`--install-dir`、`--bin-dir`、`--python` 可改变安装位置和 Python。`--repository`、`--ref` 可用于内部镜像或发布标签。pip 下载源沿用 `PIP_INDEX_URL`。更新时重新运行安装命令；卸载只需移除本工具安装目录和带 OrbitCluster 标记的启动器，登录配置可单独保留或清除。

## 仅有 HTTP 的实验室内网

服务器管理员先在 `/etc/cluster-portal/site.conf` 设置 `TERMINAL_REQUIRE_HTTPS=0`，再重启 `cluster-portal`。这是现有终端接口的配置开关，不改变网页登录、资源权限或作业行为；需要服务器已部署终端 API。

从源码安装：

```bash
bash terminal/install.sh --source terminal --server 'http://10.71.106.251' --allow-http
~/.local/bin/orbit tui
```

安装器会保存服务地址和 HTTP 开关。网页“终端工具使用”也可生成对应安装命令，先勾选“允许 HTTP”。已有用户应重新运行新版本安装器；旧版客户端只允许本机 HTTP，不能用于这个内网地址。

已安装新版工具时，可手动配置，顺序如下：

```bash
orbit config set allow_http true
orbit config set server http://10.71.106.251
orbit login --username 你的用户名
orbit tui
```

也可在 TUI 登录页填写 HTTP 地址并勾选“允许 HTTP”。地址仅为示例，替换为管理员提供的实际地址；代码不写死该 IP。环境变量 `ORBIT_ALLOW_HTTP=1` 和 `ORBIT_SERVER=http://集群地址` 同样可用。

HTTP 会明文传输密码和令牌，请仅在明确可信的内网启用。认证仍为服务器签发的独立令牌，默认七天有效，不用 Cookie，不自动续期。客户端不会自动把 HTTPS 降级为 HTTP，两个地址的凭证也不会混用。

## 使用 TUI

`orbit tui` 打开交互界面；直接运行 `orbit` 也会在交互终端中打开 TUI。

- 我的资源：查看状态、截止时间、SSH 连接、日志；保存镜像、停机、重启、删除结束记录。
- 申请资源：选择镜像、套餐、账户、时长和可选节点。保留原有任务名、额度和资源校验。
- 镜像：公共镜像只读；个人镜像可改名、注释、删除。
- 集群：Slurm 调度口径的 CPU/内存/GPU 空闲数量。
- 配额与账户：读取操作系统磁盘配额和账户额度。
- 个人资料：管理 SSH 公钥、显示名和密码。公钥用于连接容器，私钥留在本机。

使用 Tab、方向键和鼠标操作，`r` 刷新，`Ctrl+Q` 退出。网络请求不会阻塞界面；刷新保留选中的资源。停机、删除等操作有确认窗口。

## CLI 与 agent

首次登录由用户自己在终端执行，密码隐藏输入；后续 agent 可使用同一操作系统用户下的已保存凭证。

```bash
orbit login --username alice
orbit whoami --json
orbit plans --json
orbit images list --json
orbit accounts --json
orbit instances create --name experiment --plan 1 --image cuda-base --hours 4 --json
orbit instances list --json
orbit instances wait 123 --until ssh-ready --timeout 300 --json
orbit instances connection 123 --json
orbit instances logs 123 --lines 300 --json
orbit images save --instance 123 --name experiment-env --json
orbit instances wait 123 --until saved --timeout 1800 --json
orbit instances stop 123 --yes --json
orbit instances restart 123 --json
orbit instances delete 123 --yes --json
```

`123` 为门户实例 **id**，不是 Slurm job_id。套餐可用 ID 或唯一名称；镜像可用完整路径、唯一名称或省略 `.sqsh` 的名称，重名时要求明确路径。

更多命令：

```bash
orbit quota --json
orbit nodes --json
orbit profile show --json
orbit profile name 'Alice'
orbit profile password
orbit keys list --json
orbit keys add --file ~/.ssh/id_ed25519.pub
orbit keys delete 5 --yes
orbit images comment experiment-env '我的实验环境'
orbit images rename experiment-env new-name
orbit images delete new-name --yes
orbit logout
```

`--json` 成功结果写 stdout，错误 JSON 写 stderr；不会输出令牌或密码。退出码：0 成功、1 业务拒绝/等待超时、2 参数/配置错误、3 需要登录、4 网络故障、130 取消。每个命令支持 `--help`。

非交互停机、删除、公钥删除和强制覆盖镜像需要 `--yes`。仅在用户已授权该操作时使用。登录可从 stdin 读取密码：`orbit login --username alice --password-stdin`，不要把密码放在命令参数、聊天或 shell 历史中。

**写请求超时不会自动重试**。先查询资源、镜像和日志，确认结果后再决定是否重新提交。自动化说明见 [AGENTS.md](AGENTS.md)。

## 客户端配置

优先级：**命令参数 > ORBIT_* 环境变量 > config.json > 默认值**。代码不写死服务器 IP。

```bash
orbit config set server https://你的集群地址:8443
orbit config set ca_bundle /path/to/cluster-ca.crt
orbit config set timeout 120
orbit config set poll_seconds 5
orbit config show
```

| 配置 | 环境变量 | 命令参数 | 默认值 |
|---|---|---|---|
| 服务器基础地址 | `ORBIT_SERVER` | `--server` | 首次配置 |
| 明确允许 HTTP | `ORBIT_ALLOW_HTTP` | `--allow-http` | false |
| 内部 CA 文件 | `ORBIT_CA_BUNDLE` | `--ca-bundle` | 系统 CA |
| 单次请求超时 | `ORBIT_TIMEOUT` | `--request-timeout` | 120 秒 |
| 刷新/轮询间隔 | `ORBIT_POLL_SECONDS` | `--poll-seconds` | 5 秒 |
| 配置目录 | `ORBIT_CONFIG_DIR` | `--config-dir` | XDG 配置目录下的 orbitcluster |

默认文件为 `~/.config/orbitcluster/config.json`、`auth.json`；设置 XDG_CONFIG_HOME 时随之改变。凭证目录权限 700，文件 600，拒绝软链；按完整服务器基础地址分别保存。令牌与密码同样需要保护，不要上传 auth.json。

更换服务器时不会将另一服务器的令牌发过去。客户端不跟随重定向、不使用 Cookie，也不提供跳过 TLS 验证的选项。HTTP 需要显式开启 `allow_http`，可用于管理员提供的实验室内网 IP 或域名；HTTPS 仍校验证书。命令行 `--allow-http` 只对本次调用生效，持久配置请用上述 `config set` 或安装器。

## 认证期限与撤销

- 固定有效期由服务器签发时决定，默认七天；操作和读取不会延长时间。
- 客户端显示到期时间，服务器负责最终校验，改本地时间不能延长凭证。
- 到期后重新输入密码登录；没有 refresh token。
- 多台机器分别领取令牌。`orbit logout` 只撤销当前令牌，不影响网页和其它设备。
- 改密码、管理员重置密码、停用或删除账号会使旧终端凭证失效；停用后重新启用不会恢复旧令牌。
- 网页退出保持其原有浏览器会话规则，不撤销独立终端令牌。
- `orbit logout --local` 只清理本机副本，适用于离线清理；服务器令牌仍到期失效。

## 管理员：部署新增接口与传输配置

本工具的开发不会自动修改现网。按现有 [门户部署手册](../oa/01-部署手册.md) 部署此分支的 `oa/cluster-portal` 代码。启动时只新增终端凭证表和撤销触发器，不改变现有资源表与业务策略。

服务端沿用现有优先级 **PORTAL_<KEY> 环境变量 > /etc/cluster-portal/site.conf > 默认值**：

```ini
TERMINAL_TOKEN_DAYS=7
TERMINAL_REQUIRE_HTTPS=1
TERMINAL_LOGIN_MAX_ATTEMPTS=5
TERMINAL_LOGIN_IP_MAX_ATTEMPTS=50
TERMINAL_LOGIN_WINDOW_S=300
TRUSTED_PROXY=127.0.0.1
```

例如环境变量 `PORTAL_TERMINAL_TOKEN_DAYS=14` 覆盖有效期。修改后重启门户；期限调整对新登录生效，已签发令牌保留原到期时间。配置模板位于 `provision/templates/cluster-portal-site.conf.in`，继续由原生成器交付。

只有 HTTP 时，将上面的 `TERMINAL_REQUIRE_HTTPS` 设为 `0`；客户端同时开启 `allow_http`。若 systemd 环境中设置了 `PORTAL_TERMINAL_REQUIRE_HTTPS=1`，需同步修改该覆盖项。重启后生效：`sudo systemctl restart cluster-portal`。`TERMINAL_TOKEN_DAYS=7` 保持不变。

需要 HTTPS 时，Waitress 不直接终止 TLS。提供了 [Caddy 示例](../oa/cluster-portal/deploy/terminal.Caddyfile.example)，可在新端口上增加 HTTPS 入口，保留现有 HTTP 端口和网页行为：

```bash
export ORBIT_TERMINAL_ADDRESS='https://你的集群地址:8443'
export ORBIT_PORTAL_UPSTREAM='127.0.0.1:80'
caddy run --config oa/cluster-portal/deploy/terminal.Caddyfile.example --adapter caddyfile
```

示例使用 Caddy 内部 CA；管理员从 Caddy 数据目录取得 `pki/authorities/local/root.crt`，通过可信渠道交给用户作为 `--ca-bundle`。不要分发 CA 私钥。已有可信域名证书时也可使用现有反代和 CA。

`TRUSTED_PROXY` 必须是实际反代源 IP，默认空，不接受 `*`。Waitress 只信任该来源的 X-Forwarded-Proto，避免直连 HTTP 伪造 TLS。保持 `TERMINAL_REQUIRE_HTTPS=1` 时，HTTP 终端接口会拒绝认证；原网页仍按原策略运行；无需调整全站 Cookie 有效期或 Cookie Secure 开关。

新接口前缀 `/api/terminal/v1`：登录为 JSON，其它写操作为 form 编码；成功返回 `{ok:true,data:...}`，失败返回 `{ok:false,code,error}`。不开放管理员操作，令牌不会被旧网页路由接受。业务操作经过现有处理函数与所有权检查。

## 开发与验证

```bash
python3 -m venv .venv-test
.venv-test/bin/pip install -r oa/cluster-portal/requirements.txt './terminal' pytest requests build
make test-terminal PYTHON=.venv-test/bin/python
.venv-test/bin/python -m build terminal
```

测试使用假集群，不会提交真实 Slurm 作业。包括令牌期限、撤销、Cookie/CSRF 隔离、用户越权、现有业务适配、CLI 到 Flask 的完整链路、原网页冒烟、TUI headless 操作和安装器参数检查。兼容测试为原 smoke 额外提供默认 SSH 就绪桩，避免调用本机 sudo。

已有作业生命周期、到期自动保存和抢占行为保持门户当前实现；新增工具没有替换这些机制。
