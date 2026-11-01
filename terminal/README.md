# OrbitCluster 用户终端

普通用户的交互式 TUI 与可供 agent 调用的 CLI。使用现有门户账号登录，服务器签发独立的固定期限令牌，默认七天；到期后重新登录。

本包要求 Python >=3.10。服务器必须先部署 `/api/terminal/v1` 接口与 HTTPS 入口。

本地源码安装：`bash terminal/install.sh --source terminal`（在仓库根目录执行）。完整安装、配置、部署与命令说明随本分支一起提供。
