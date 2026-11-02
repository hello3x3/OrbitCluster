# Frontend optimization implementation plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task by task.

**Goal:** Deliver the approved neutral interface with blue emphasis and a public text user guide; retain all existing functions.
**Architecture:** Keep Flask/Jinja and existing business JavaScript. Replace the shared stylesheet, adjust presentation markup, and add one read-only guide route and isolated installation-command generator.
**Tech Stack:** Flask, Jinja, plain CSS and browser JavaScript; existing pytest/fake-cluster fixtures.
**Spec:** `docs/superpowers/specs/2026-11-02-frontend-optimization-design.md`

## Global Constraints

- User confirmed blue emphasis on 2026-11-02: `#365e8e`; neutral background `#f5f6f7`, white surfaces, text `#20262d`, secondary `#59636c`, lines `#dce1e5`.
- Existing routes, roles, field names, selectors, parameters, validation, authentication, polling and cluster actions must remain unchanged.
- Guide is public, read-only, text first; no guide screenshots or placeholders until user approves actual pages.
- No framework, external font or CDN dependency. HTTPS required for terminal; no automatic credential renewal.
- Installer source pinned to published user-terminal commit, centrally defined; no production IP embedded.
- Work only in `codex/frontend-optimization`; do not deploy or merge.

## Review Focus

- Public guide must not expose private data or alter an existing login session.
- Malicious/quoted CA paths and server addresses must remain literal shell arguments.
- HTTP/invalid terminal addresses must not produce runnable commands; no guessed HTTPS port.
- Root and ordinary admin navigation must retain different permissions, also at narrow widths.
- Long resource names, public keys and existing modal actions must remain accessible on mobile.

### Task 1: Shared visual system

**Files:** `portalapp/static/style.css`, presentation-only portions of `templates/base.html`, `login.html`, `my.html`, `profile.html`, `admin_users.html`, `static/app.js` (under `oa/cluster-portal/`).
**Interfaces:** Consumes existing classes/IDs/data attributes. Produces stable shared styling for guide and old pages.

- [x] Replace palette, spacing, type, navigation, tables, forms, alerts and modal styles; preserve all selectors and hidden behavior. Remove decorative emoji in presentation text only. Preserve original section order.
- [x] Inspect local fake-cluster pages at desktop and 390px. Verify resource actions, form controls, logs, confirmation, role navigation, focus and no document overflow. No new unit tests for reversible CSS changes.
- [x] Run `.venv-test/bin/python oa/cluster-portal/tests/smoke_local.py` and `make test-terminal PYTHON=.venv-test/bin/python`.
  Expected: original smoke and 54 terminal checks pass.
- [x] Include with Task 2 in one self-contained commit (new base/login guide links require the guide route).

### Task 2: Public text guide and installer

**Files:** Create `templates/guide.html`, `static/guide.js`, `static/guide-install.mjs`, `tests/test_guide.py`, `tests/test_guide_commands.mjs`; add route/release constant in `portalapp/app.py`; update `docs/user-guide.md` and base/login guide links.
**Interfaces:** Consumes Task 1 styles. New GET `/guide`, endpoint `guide_page`, context `terminal_install_ref`; ES module `buildInstallCommand(server, ca, ref)` returns validated shell text or throws.

- [x] Write route tests: anonymous guide is readable; existing private routes stay protected; login/authorized navigation link to guide; reading guide preserves session and exposes no personal key/account data. Run and observe 404/missing-link failures.
- [x] Write command tests executing generated shell against fake curl/bash: literal quoted CA paths, paths with spaces/apostrophes/substitutions, valid HTTPS base, invalid URLs/HTTP/ref rejection. Observe missing module failure.
- [x] Add route/template/script. Read-only guide has anchor contents, all seven existing draft sections and local installer fields. Default server only from current HTTPS origin; copy with fallback selection. Use `textContent`, quote every user argument, reject credentials/query/fragment/control characters in server and invalid release refs.
- [x] Run `.venv-test/bin/python -m pytest oa/cluster-portal/tests/test_guide.py -q` and `node oa/cluster-portal/tests/test_guide_commands.mjs`.
  Expected: route and command contract tests pass.
- [x] Browser-check anonymous and logged-in guide, links, anchors, installer errors/defaults/copy fallback and mobile. Run original smoke, CTL security and all terminal regression checks.
  Expected: no console errors, original workflows pass.
- [x] Commit both tasks as `feat: refine portal styling and add a text user guide`.

## Delivery

One fresh whole-branch code review, scope audit against `3441163`, record validation in `docs/superpowers/reviews/2026-11-02-frontend-optimization.md`. Keep branch unmerged. Show local preview for user approval; only then add annotated guide screenshots.
