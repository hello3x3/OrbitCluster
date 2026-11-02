# Native CLI implementation plan

Approved scope: specs/2026-11-02-terminal-distribution-design.md (latest user CLI-only correction).

1. Replace Python/TUI with native Go CLI using existing API/config/credential formats; validate command arguments, HTTP defaults, TLS, origin isolation, errors, confirmations and seven-day expiry.
2. Add Go release builder for six archives, portal-only installers with progress/SHA256, safe native setup/uninstall, legacy migration and CI native tests.
3. Add public read-only download routes, default HTTP deploy configuration; fix sidebar title, login shell and HTTP clipboard fallback. Update active docs and agent skill.
4. Run Go, backend and command tests; real local download/install/use/uninstall; browser responsiveness and clipboard checks. Record current evidence in feedback checklist.
5. Independent final review, fix material findings, commit/push existing branch and check native CI. Leave a working local CLI and explicitly report production deployment status.
