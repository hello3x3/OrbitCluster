# 提交后反馈与本轮修改验收

后续 **0.1.2 可读输出、登录更新提醒与自更新** 的设计及验收见 [cli-0.1.2.md](cli-0.1.2.md)。下文保留上一轮独立 CLI 交付记录。

日期：2026-11-02。修改基线：`8aec464`。最新明确要求优先：仅 CLI 给 agent 使用，不再开发 TUI，客户端尽量不用 Python，实验室内网默认 HTTP。

| 用户反馈 | 当前实现 | 验证依据 |
|---|---|---|
| 蓝色更亮、logo 更大 | 保留既有蓝色与选定星云 logo，导航图标 48px | 浏览器检查 logo 宽度 48；未重绘品牌图案 |
| 左侧“OrbitCluster 集群门户”单行 | 侧栏宽度与标题排版适配，保持单行 | 1440/1024/768/390/320px，文字单行且不重叠关闭按钮 |
| 左侧任务/集群/资产/文档分组，默认展开 | 保留上一轮实现与菜单权限 | 原页面和角色回归保留 |
| 集群按分区、活跃资源独立页 | 保留已有实现 | 原分区与 portal-ctl 测试保留通过 |
| 共有数据集/模型仓库无后端 | 仍为“功能待完善”，不添加扫描、上传或删除 | 原资产占位与权限测试保留 |
| 登录页不显示左侧导航，只留文档入口 | 登录页面无侧栏、无顶部工作台栏，保留登录区下方使用文档链接 | 浏览器五种宽度；`test_login_shell_removed_and_docs_remain` |
| 点“复制安装命令”就复制 | HTTPS Clipboard API；HTTP 点击同步兼容复制；被浏览器拒绝则明确选中提示 | 真实非安全 HTTP origin 剪贴板写入及手动回退验证 |
| 安装能看见进度 | 检测/下载/校验/解压/安装五个阶段，curl 下载进度可见 | Bash 安装实测五阶段；Windows CI 含 PowerShell 五阶段测试 |
| 不要 TUI，只需 agent CLI | 移除 Python/TUI 客户端；Go 标准库独立 CLI | `orbit` 显示帮助，旧 `orbit tui` 返回迁移提示而不是启动或崩溃 |
| 用户不需要 Python、pip、Git 依赖 | 安装包只含一个原生可执行文件；客户端没有外部 Go 模块 | `go list -m all` 仅本模块；Mac 实装将 Python/pip/Git/Go 替换为失败桩仍完成 |
| 安装只连接你们门户，用 GET/POST | GET 下载本门户脚本、压缩包与 SHA256；正常使用现有 GET/POST API | 脚本无 GitHub/pip 下载；原生 CLI 到 Flask 的真实 HTTP 集成 |
| 不用 clone，只下载压缩包 | 六个平台约 2.4–2.8MB 压缩包，一个 orbit/orbit.exe | Go release builder，归档结构与 SHA256 校验 |
| macOS/Linux/原生 Windows | macOS/Linux/WSL Bash，Windows PowerShell；各平台 arm64/amd64 | Mac 完整实测；其它平台由原生 CI 结果补充，不能把交叉构建称为原生运行 |
| 默认支持 HTTP | CLI/文档生成器默认支持；站点默认、生成模板、部署脚本缺省值均为 0 | 默认 HTTP 全流程集成；明确 HTTPS-only 配置仍拒绝 HTTP；HTTPS 证书测试通过 |
| 七天有效，不自动续期、不用 Cookie | 保留原服务器签发/撤销/过期协议和按地址隔离的凭证格式 | 原终端 API 测试保留；CLI 过期、隔离、无 Cookie 测试 |
| 卸载方式 | `orbit uninstall`；可选 `--purge`；默认保留配置，仅删除安装记录文件 | 真卸载实测；默认保留、purge 保护其它目录/文件与符号链接测试 |
| 修复本机使用错误 | 停止旧 TUI 路径；本机配置错误给退出码/JSON，不再走 UI worker；读取凭证无需修改目录权限 | CLI 配置/权限/无 TUI 测试，真实终端运行 |
| 不改变现有服务功能 | 资源、镜像、配额、账号、公钥和调度仍经过原业务函数；Python 后端保留 | 后端全套回归及原网页 SMOKE_OK |

## 独立复核后的修复

- 公有与个人镜像同名时，修改/删除只解析个人镜像，拒绝显式公有路径，避免误删个人同名镜像。回归测试先失败后通过。
- 升级拒绝覆盖已被用户替换的启动器；程序本体与安装标记绑定 SHA256。
- 安装的自定义配置目录通过安装记录持续生效；Windows 独立 bin 目录用相邻标记定位安装，卸载检查标记与程序一致。
- Windows 配置目录建立受保护的私有 DACL，移除显式共享授权；读取凭证验证所有者和授权 SID。Windows 原生测试包含显式 Everyone 授权的拒绝与清理。
- Ctrl+C 中断密码输入恢复终端回显，再以 130 退出，真实 PTY 回归验证。
- 等待超时包含单次 HTTP 请求时间，不在超时后返回“成功”。回归测试先失败后通过。
- Windows 延迟卸载脚本带 UTF-8 BOM，覆盖中文用户名/自定义路径。
- purge 不允许用其它配置目录参数扩大删除范围，保留其它应用的 config.json。回归测试先失败后通过。

## 本机完整流程与交付

测试调用部署代码运行的本机假集群门户，不向真实集群提交或删除作业。Mac 实测：curl 从门户下载脚本和原生压缩包 → SHA256/版本校验 → 当前用户真实安装目录 → PTY 隐藏密码登录 → 身份/资源/镜像查询 → 注销 → `uninstall --purge --yes` → 检查程序、启动器和测试配置已清除 → 重新安装可用 CLI 并配置回实验室地址。

没有读取真实 auth.json 内容；只比较文件元数据确认真实凭证未改写。真实配置与登录保留。本机留下 `~/.local/bin/orbit` 的新版 CLI。

构建交付包：在 `terminal/` 执行 `go run ./cmd/release`，再在根目录执行 `python3 oa/cluster-portal/scripts/package_delivery.py`。后者是开发/管理员打包工具，用户安装不使用 Python。产物 `artifacts/orbitcluster-portal-cli-0.2.0.zip` 含门户代码、安装脚本、skill、六个平台包和 SHA256，不包含 var、数据库、secret、auth.json 或服务运行凭证。

正式服务器没有在本轮远程部署或重启。管理员解压交付包后执行原有 `cluster-portal/deploy/install.sh cluster-portal 80`；新部署缺省允许 HTTP。旧的显式 HTTPS-only 配置/环境变量保持优先，需要按你们内网策略设为 0 后重启。不要把旧线上站点暂未提供新下载接口，误报成客户端安装成功。

## 最终验证记录

- `make test-terminal PYTHON=.venv-test/bin/python`：Go 测试通过，后端/安装回归 **50 passed**，JS 命令生成/引号/下载失败清理通过。
- `go test -race ./...`、`go vet ./...` 通过；`go list -m all` 仅本模块，无第三方模块或客户端语言依赖。
- [原生六平台 CI](https://github.com/hello3x3/OrbitCluster/actions/runs/41432093780)：Linux amd64/arm64、macOS Intel/Apple、Windows x64/ARM64 全部成功；Windows 包括 PowerShell 真实下载、SHA256、中文目录、私有 DACL 和卸载测试。
- Windows 首轮 CI 暴露 PowerShell 模块未加载问题，改用系统 .NET 的 SHA256/解压接口后重新验证通过，不要求用户安装模块。
- 本机最终验收证据：`/private/tmp/orbit-native-mac-dj298lo_` 的 `result.json`，五阶段日志和真实 PTY；真实 auth.json 未读取/改写，最终保留新版可用 CLI。
- 浏览器：1440/1024/768/390/320px，标题单行、48px logo、登录无导航、HTTP 自动复制实际剪贴板、手动回退、Windows 命令生成、页面无横向溢出与脚本错误。
- 解压交付包后，从交付目录导入门户，六个平台归档均可下载且 SHA256 匹配，原网页 **SMOKE_OK**；未混入运行时数据库、凭证或 secret。
