# Sidebar navigation and shared-asset overview

2026-11-02 user request: implement sidebar navigation, default-expanded accordion groups, restrained emoji on every menu item, partitioned cluster views, daily storage usage, documentation and an installable terminal skill. Continue `codex/frontend-optimization` from `a60e4fc`; preserve the selected nebula identity and all existing task/image/auth operations.

## UI

A 256px left sidebar and fluid right workspace. Groups: 任务 (我的资源/我的镜像/申请资源), 集群 (集群占用情况/门户内活跃资源), 资产 (数据集/模型仓库/镜像管理), 使用文档 (快速入门/平台开发文档/终端工具使用). Native details/summary accordions start open and may be collapsed independently. Existing administrator controls remain in a role-filtered management group. Profile and logout move to an accessible avatar dropdown at the upper right. Body 16px, table 15px; preserve all columns/actions and use local table scrolling when necessary. Mobile navigation uses an accessible drawer.

## Cluster views

`/status` becomes occupancy only; `/status/active` contains portal-active jobs. Remove the redundant personal-job table as explicitly requested. Add a separate read-only `cluster-status` helper command that queries all real Slurm partitions without altering legacy `sinfo`, submission or scheduling behavior. Show true partition names and GPU types (3090/4090/PRO6000 where actually present), never fabricate partitions. Deduplicate physical nodes for totals when they belong to multiple partitions. Unknown usage stays unknown rather than zero.

## Assets

User clarification: there is no backend catalog yet; datasets/models are shared assets and this task is FRONTEND ONLY for those pages. `/assets/datasets` and `/assets/models` show the intended columns and a clear “功能待完善” state. No fabricated metrics, filesystem scans, snapshot APIs, collector configuration or scheduled jobs are introduced. Daily storage refresh remains a future capability, not a claimed implemented feature. `/assets/images` reuses existing image operations with existing user/root permissions.

## Documents and skill

Reuse current quick guide. Extract the existing terminal section into a shared include and create a dedicated terminal page. Development page indexes existing architecture and source docs; bundle the existing standalone architecture HTML so deployment is self-contained. Add self-contained `skills/orbitcluster/SKILL.md` for user CLI operations, seven-day non-renewing login and agent-safe JSON workflows; link download/install instructions from terminal docs. No admin capability or credential scraping in the skill.

## Execution and verification

- [x] Add behavior tests first for protected routes, partition accounting and honest asset placeholders.
- [x] Implement sidebar/avatar and larger type; preserve IDs/data-* and existing role guards.
- [x] Implement separate cluster reader and occupancy/active pages; verify legacy helper output unchanged.
- [x] Implement shared-asset placeholder pages, explicitly marked 功能待完善; no backend collector or scheduled job, per user clarification.
- [x] Reuse documentation, package terminal skill and validate it.
- [x] Run full pytest, webpage smoke, generated-install command checks, script syntax and browser checks for all roles at desktop/mobile, accordion/drawer/avatar, long text and empty/error states.
- [ ] Refresh guide illustrations affected by navigation, record verification, commit and push the development branch without merging/deploying.
