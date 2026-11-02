# Frontend and text guide validation

Branch: `codex/frontend-optimization`, based on `codex/user-terminal` at `3441163`.
User confirmed blue emphasis on 2026-11-02; guide screenshots await page approval.

## Scope

- Shared palette, typography, spacing, tables, forms, alerts and modals; existing selectors and data retained.
- Presentation text changes remove decorative emoji and clarify automatic port allocation.
- Existing server code changed only to add public GET `/guide` and its pinned client-release constant.
- New isolated guide JavaScript validates input, quotes shell arguments and supports copy/fallback selection.
- No existing routes, permissions, database schema, authentication lifetimes, request parameters, polling or cluster actions changed. No deployment or guide screenshots.

## Verification (2026-11-02)

- `.venv-test/bin/python -m pytest -q`: **56 passed** (full repository collection, including terminal HTTPS integration and two new guide tests).
- `.venv-test/bin/python oa/cluster-portal/tests/smoke_local.py`: **SMOKE_OK**.
- `node oa/cluster-portal/tests/test_guide_commands.mjs`: passed actual generated-shell execution against inert local curl/bash programs; covers HTTPS/ref validation, quoted CA paths with apostrophe and shell substitution, fixed release, temporary-file cleanup and download failure.
- Browser: 1440px / 390px user and root/ordinary-admin pages; resource connection, logs and stop-confirm cancellation; role links retained; no document overflow or console errors.
- Guide: 1440px / 390px / 320px, public access and chapter anchors, HTTP rejection, HTTPS-origin suggestion, copy and permission-denied fallback; no console errors.
- `node --check` on new scripts and `git diff --check`: passed.

## Platform limitation

The existing `tests/test_ctl_security.py` was also attempted. With macOS's default temporary-directory alias, its realpath comparison fails; using `TMPDIR=/private/tmp` resolves that comparison, but its normal `set-keys` write still fails before the `authorized_keys` file is created (test assumes Linux-style UID/GID ownership). These helper/test files are unchanged from `3441163`. This is not claimed as a passed check; rerun on the target Linux environment before deployment. No production helper changes were made to accommodate the local test.

## Review

Fresh whole-branch review pending. Formal red-box guide screenshots will be added only after the user confirms the redesigned pages.
