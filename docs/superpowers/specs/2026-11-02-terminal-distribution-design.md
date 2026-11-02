# Native CLI distribution and feedback implementation

Latest user decision (2026-11-02) supersedes the earlier TUI and bundled-Python proposal: CLI only for agents; avoid Python on clients, default laboratory HTTP in deployment, implement all accumulated portal/installation/uninstall feedback and exercise the whole lifecycle on this Mac.

- Go standard-library CLI, one executable per OS/architecture, no Python/TUI/dependency setup. Preserve existing GET/POST API, all user commands, permissions and seven-day non-renewing Bearer credentials. Backend remains Python.
- Portal-only GET downloads: Bash and PowerShell installers, six platform archives and SHA256, skill. Clear missing-artifact response; filename allowlist and no symlink traversal.
- Stage/download progress, digest/version/archive validation before setup. Update/legacy migration, installed-file manifest, safe uninstall with optional purge, no server job/data changes.
- HTTP is the default in CLI, guide and fresh server deployment; preserve explicit HTTPS-only overrides and certificate verification. Never follow authenticated redirects or retry uncertain writes.
- Single-line sidebar title with enlarged 48px logo. Login has no sidebar/topbar, keeps bottom documentation link. HTTP-compatible click-to-copy with honest manual fallback.
- Verify real download/install/login/query/logout/uninstall, archive-failure preservation, CLI JSON/exit codes, full existing backend regression and responsive UI. Preserve real user config/auth while testing. Cross-build six targets and run native CI before claiming cross-platform execution.
