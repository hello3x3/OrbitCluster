# CLI 0.1.2：可读输出与门户更新

本次在 `codex/frontend-optimization` 开发，沿用独立 Go CLI 和既有 Python 服务端。按平台发布编号统一为 **0.1.2**。认证仍固定七天、到期重新登录；HTTP 默认支持，既有任务、镜像、配额与权限逻辑不变。

## 输出

默认结果适合人和 agent 直接阅读：资源按项展示，套餐/节点/镜像/账户按表格展示，长内容可展开为字段列表；日志保留真实换行。相同异常只显示一次，不重复输出网页派生状态、名称和时间字段。名称不截断，完整路径和 SSH 命令可复制。未知值显示“—”，不会把未知配额当成零或不限。

```bash
orbit instances list
orbit instances show 123
orbit nodes --details
orbit images list --details
orbit instances list --json
```

默认资源示例：

```text
我的资源 · 1 项
#123 · train-vit · 运行中 (RUNNING)
  作业 / 节点 / 账户：424242 / gpu01 / lab
  资源 / 时长：1 GPU / 8 CPU / 32 GiB / 04:00:00
  申请 / 到期：2026-11-02 12:00:00 / 2026-11-02 16:00
```

启动异常、最近异常、保存结果和正在保存标志会额外展示。`show` 给出套餐、镜像路径、SSH 地址/端口和生命周期时间，`--details` 保留其它服务器字段。`--json` 仍返回原 `ok/data` 和完整数据字段，供精确解析；没有删改服务器 API 的字段或数据类型。登录结果新增可选顶层 `update`，原登录 `data` 不变。

## 检查与更新

```bash
orbit update --check
orbit update
orbit --version
```

登录成功后用至多三秒读取已配置门户的公开版本清单，只提醒，不自动安装；失败不改变已完成的认证。更新检查不发送令牌/Cookie。`--check` 不下载包，也不要求登录。按数字比较稳定版本 `major.minor.patch`，例如 0.1.10 高于 0.1.2，不自动降级。

更新只选择同一门户的当前操作系统/架构归档，验证 SHA256、大小、单个普通程序文件和运行所得版本。更新程序及安装记录先在目标目录暂存，并备份原文件，再统一替换；替换失败时恢复旧文件，恢复也失败时保留备份并明确提示修复。原配置文件不改写，凭证文件不读取、不改写；临时服务器/配置参数不被保存为默认值，也不延长认证。

Windows 在当前命令退出后执行，正在使用的其它终端会阻止替换；等待最多五分钟，只重试明确的本机占用结果。返回 `scheduled` 时用 `orbit --version` 确认，失败日志见 `log_file`。PowerShell 5.1 的原生 stderr 和 Windows 文件句柄替换均单独覆盖；采用 [Microsoft 文件信息接口](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-setfileinformationbyhandle) 完成受保护的替换。

首次从旧开发构建 0.2.0 统一到本发布编号，或旧客户端不含 update 命令时，需运行一次门户安装器。便携文件无安装记录时也使用安装器；不会猜测任意目录并覆盖文件。

## 发布

唯一版本源为 `terminal/internal/cli/VERSION`。后续发布：

1. 修改 VERSION，例如 0.1.3。
2. 在仓库根目录运行 `python3 oa/cluster-portal/scripts/bundle_docs.py`。
3. 在 `terminal/` 运行 `go run ./cmd/release`，生成六个平台归档、SHA256 和清单。
4. 在根目录运行 `python3 oa/cluster-portal/scripts/package_delivery.py`，交付门户和客户端包。
5. 按既有部署方式安装并重启门户。

清单包含版本、平台、架构、文件名、可用性、大小和 SHA256。安装脚本读取清单，下载链接读取门户版本，不需重复修改版本字符串。交付包仅包含本版本六个归档，排除旧版本包和运行数据库、凭证、secret。

## 验证记录

- `make test-terminal PYTHON=.venv-test/bin/python`：Go 回归、52 项后端/安装测试、JS 命令生成测试通过。
- `go test -race ./...` 和 `go vet ./...` 通过。
- 本机从隔离 HTTP 门户实际下载、安装、登录、查询、检查更新、注销和清理卸载；默认单资源输出 7 行，原格式化 JSON 为 46 行。后端和作业均为隔离测试数据，不向生产集群提交任务。
- 本机真实客户端已安装为 0.1.2；真实配置保持不变，未打开/复制真实 auth.json，仅比较元数据确认未改写。
- 原生更新测试实际构建两个版本，验证损坏包拒绝、只检查不下载、成功替换、临时参数不持久化、认证字节不变和更新后卸载。Windows 额外验证占用等待以及 bin 目录不可创建文件时原安装不变。
- [六平台原生 CI](https://github.com/hello3x3/OrbitCluster/actions/runs/41471439262) 全部通过（代码提交 `1615685`）：macOS Intel/Apple、Linux amd64/arm64、Windows x64/ARM64。Windows 验证实际占用后重试、受保护文件替换、目录不可写时保留原安装和更新后卸载。
- 首轮 Windows 原生运行发现普通 MoveFileEx 无法覆盖受保护的打开文件；改为 Win32 FileRenameInfoEx 句柄操作后，原生保护和完整更新回归均通过。
- 最终本机验收：`/private/tmp/orbit-cli-012-2hclltns/result.json`，文本预览同目录 `output-preview.txt`；真实 CLI 为 0.1.2。
- 最终交付包：`artifacts/orbitcluster-portal-cli-0.1.2.zip`，SHA256 `7f176d14934d6b2f44163ad01c3cfaa6e771678693eb97a3975823a2534d71c7`。解压后运行交付目录中的门户，六个归档可下载、大小和 SHA256 与清单一致，原网页 `SMOKE_OK`；证据 `/private/tmp/orbit-delivery-012-h3lt63bx/result.json`。

生产服务器未在本轮远程部署。只读检查时线上清单仍为旧 0.2.0，缺少更新所需的 SHA256/大小；应部署新交付包后再面向用户发布 0.1.2。不要将本机测试服务的成功视为生产登录或生产更新验收。
