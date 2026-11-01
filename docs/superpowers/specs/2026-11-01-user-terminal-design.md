# OrbitCluster 用户终端设计

用户已确认本设计：新增普通用户 TUI 和便于 agent 使用的命令入口；终端输入现有门户账号密码，领取服务器签发的独立令牌；默认固定七天有效，到期重新登录，不自动续期。保留网页 Cookie 的十二小时策略及全部集群业务行为。服务器地址和认证期限均可配置，交付无需 sudo 的一键安装方式。

## 边界与架构

- 服务器仍为 Flask / SQLite / Waitress / portal-ctl，不引入新数据库或认证服务。
- 新增 `/api/terminal/v1`，只提供当前用户功能。令牌不被旧网页或管理员路由接受，不扩大管理权限。
- 新接口经独立 Bearer 校验后设置仅当前请求有效的用户上下文，调用明确白名单中的现有业务处理函数。保留原始校验、所有权检查、状态和错误文案，避免复制申请/镜像等业务规则。
- 网页鉴权和 CSRF 校验保持原样；仅成功验证独立令牌的新接口免用浏览器 CSRF。终端不使用 Cookie，接口不创建浏览器会话。
- Python >=3.10 客户端使用 httpx、argparse、Textual。TUI 与 CLI 使用同一客户端、配置和登录凭证。
- 不修改已有调度、端口、套餐、到期保存、抢占或恢复策略。遇到阻碍交付的既有缺陷先向用户说明，不扩展修复范围。

## 认证

- `POST /auth/login` 接收用户名、密码和可选设备名；校验现有用户、哈希密码与启用状态。
- 返回高熵随机令牌（256 bit）、用户信息、UTC 签发/到期时间。服务器只保存令牌 SHA-256 哈希。服务端决定期限。
- SQLite 新增 terminal_tokens 表及密码变更/停用时撤销令牌的触发器。修改密码、重置密码、停用、删除账号后旧令牌不可恢复使用；网页退出不撤销终端令牌。
- `GET /auth/me` 查询身份和期限；`POST /auth/logout` 撤销当前令牌。无 refresh endpoint/token，读取不延长有效期。多设备令牌独立。
- 登录失败采用有限窗口限速；认证响应 `Cache-Control: no-store`；令牌、密码不进入 URL、错误、日志。
- HTTPS 默认强制。测试可明确关闭服务端限制，客户端只允许显式开启的 loopback HTTP。TLS 自签内部 CA 通过 ca_bundle 验证，不提供跳过验证选项。
- HTTPS 反代使用可配置的明确 trusted_proxy 地址，Waitress 只信任该来源的 X-Forwarded-Proto；默认不信任代理，防止直连伪造 HTTPS。

## 配置

- 服务端沿用 `PORTAL_<KEY>` > `/etc/cluster-portal/site.conf` > 默认值：`TERMINAL_TOKEN_DAYS=7`、`TERMINAL_REQUIRE_HTTPS=1`、登录限速设置；`TRUSTED_PROXY` 默认空。
- 客户端优先级：命令参数 > `ORBIT_*` 环境变量 > XDG 配置目录中的 config.json > 默认值。服务器地址无站点硬编码；用户首次登录或安装时配置。
- 默认 config.json 与 auth.json 位于 `${XDG_CONFIG_HOME:-~/.config}/orbitcluster`。凭证目录 0700，文件 0600，原子写入；拒绝凭证软链。凭证按完整服务器基础地址隔离，服务器切换不会误发令牌。
- 可配置 server、ca_bundle、timeout、poll_seconds，支持 ORBIT_CONFIG_DIR。密码只在终端隐藏输入或从 stdin 输入，不支持命令行明文 password 参数。

## 用户功能

- 登录、身份/到期查询、当前设备注销；关闭客户端不停止作业。
- 我的实例、申请、详情、SSH 就绪等待、SSH 命令、日志、停机、重启、保存镜像、删除结束记录。
- 套餐、公共/个人镜像、节点、磁盘与账户额度、公钥管理、显示名及改密。
- TUI 提供中文登录、资源、申请、镜像、集群、配额、个人资料页面。网络调用不阻塞事件循环；危险操作确认；网络故障和到期提示清楚。
- CLI 提供 `--json`，stdout 只放结果，stderr 放诊断；约定退出码：0 成功、1 操作失败、2 参数错误、3 需要登录、4 网络错误。非交互危险操作需要 `--yes`。
- CLI 写请求不自动重试；超时提示核实资源状态，防止重复提交。保留门户既有任务名/账户/时长规则。
- 连接命令返回安全转义的 SSH 命令和 argv，不管理用户私钥。

## 分发与验收

- terminal/ 为独立可安装 Python 包，`orbit` 为入口；源码与 wheel 均可安装。
- install.sh 检查 Python >=3.10，建立用户级 venv，安装包并创建 ~/.local/bin/orbit 启动器；支持本地源码及可配置的远端仓库/ref、安装路径、server、CA。无需 sudo，不修改 shell 启动文件。
- 提供 Caddy 内部 CA / HTTPS 反代示例与升级、安装、首次登录、agent 文档。代码交付与线上部署分开：不自动修改当前生产服务。
- 自动验证期限边界、无滑动续期、令牌哈希、撤销、密码/停用失效、Cookie 隔离、CSRF 保持、越权拒绝、配置优先级、TLS、CLI、TUI headless 交互、一键安装与原有 smoke。
- macOS 的既有助手安全测试依赖 Linux UID/GID 假设；记录已知基线，不修改既有代码来掩盖该差异。
