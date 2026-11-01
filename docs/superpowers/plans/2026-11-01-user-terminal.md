# User Terminal Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Deliver installable user TUI/CLI with independent, configurable seven-day authentication and unchanged existing web behavior.

**Architecture:** Add a terminal-only authenticated Blueprint, request-local identity and an explicit allowlist invoking existing user handlers. Add an independent Python client shared by CLI and Textual screens. No job lifecycle policy changes.

**Tech Stack:** Flask, SQLite, Waitress; Python >=3.10, httpx, Textual, argparse; pytest and existing fake-ctl smoke.

**Spec:** docs/superpowers/specs/2026-11-01-user-terminal-design.md

## Global Constraints

- Default token lifetime 7 days, absolute expiry, no refresh token or renewal.
- Existing web Cookie lifetime 12 hours and original CSRF/security/cluster policies unchanged.
- Server configuration uses PORTAL_* > site.conf > defaults; client uses flags > ORBIT_* > config.json > defaults.
- No hardcoded deployment address. HTTPS by default; internal CA supported.
- New branch codex/user-terminal; commits grouped by independently verified feature.

## Review Focus

- Redirects or server changes must not send credentials to a different server.
- Token authentication must not bypass ownership checks or browser CSRF.
- Deactivation/re-enable and password changes must not revive old tokens.
- TUI refresh must preserve selected rows and keep the event loop responsive.
- Installation and credential persistence must preserve private permissions and not expose secrets.

### Task 1: Independent server authentication

**Files:** Create portalapp/terminal_auth.py, tests/test_terminal_api.py; modify portalapp/siteconf.py and app.py registration.

**Interfaces:** terminal_auth.init_schema(db); issue(db, user, device, now=None); authenticate(db, raw, now=None); revoke(db, raw). Blueprint POST auth/login, GET auth/me, POST auth/logout.

- [ ] Write pytest tests for HTTPS, wrong password, hashing, exact 7-day boundary, fixed expiry, multi-device revoke, password/disable invalidation and existing Cookie isolation; observe failure.
- [ ] Implement independent token storage, login rate limiting and configurable lifetime; add minimal registration.
- [ ] Run authentication tests and existing smoke; commit authentication.

### Task 2: Existing user operations over the terminal API

**Files:** Create portalapp/terminal_api.py; modify request-local user/CSRF hooks in app.py, tests/test_terminal_api.py; add TLS proxy config to run.py and deployment example.

**Interfaces:** register_terminal_api(app, get_db, callbacks). New routes below /api/terminal/v1 mirror only existing user operations. Requests use form encoding for writes to preserve validators; responses are JSON.

- [ ] Write tests for resource create/list/log/stop/restart/save/delete, image operations, profile/keys, quotas/accounts, ownership denial and no Set-Cookie; observe failure.
- [ ] Implement explicit adapters and read callbacks, suppress browser-only session writes in terminal requests; leave old routes/CSRF untouched.
- [ ] Test original smoke and new API; commit adapters.

### Task 3: Installable client and CLI

**Files:** Create terminal/pyproject.toml, src/orbitcluster/{config,credentials,client,cli,__main__}.py and terminal/tests/.

**Interfaces:** Settings.load(overrides); CredentialStore keyed by server; Client login/logout/identity and request(method,path,data). CLI orbit with user operations, JSON and stable exit codes.

- [ ] Write failing configuration, file permissions/symlink, TLS/redirect, expiry, malformed response, CLI argument/output/error tests.
- [ ] Implement shared client and commands; no automatic retry of writes; credentials never in output.
- [ ] Run client/CLI tests and build/install wheel; commit client.

### Task 4: Interactive TUI

**Files:** Create terminal/src/orbitcluster/tui.py and terminal/tests/test_tui.py.

**Interfaces:** OrbitApp(Settings, client=None); shared Client from Task 3; login then tabs for resources/apply/images/nodes/quota/profile.

- [ ] Write headless tests for login, selection, apply, actions, auth expiry and nonblocking refresh; observe failure.
- [ ] Implement focused Chinese screens, modal confirmation/input, network workers, raw text rendering, preserved selection and explicit loading/errors.
- [ ] Run TUI and complete client/API tests; commit TUI.

### Task 5: Installer, documentation and final verification

**Files:** Create terminal/install.sh, terminal/README.md, terminal/AGENTS.md, oa/cluster-portal/deploy/terminal.Caddyfile.example; modify root README, Makefile and siteconf template documentation.

**Interfaces:** install.sh supports source/ref/repository/prefix/bin-dir/server/CA. Installs orbit without sudo into a user venv.

- [ ] Write installer tests and run failing checks, implement installation and configuration.
- [ ] Validate real install in temporary directories, wheel, CLI, TUI, new server tests and original smoke. Record Linux-only existing security-test baseline.
- [ ] Fresh whole-branch review under requesting-code-review; fix material new findings with tests. Commit final documentation and report hashes and deployment prerequisites.
