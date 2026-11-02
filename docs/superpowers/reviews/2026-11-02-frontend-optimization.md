# Frontend and text guide validation

Branch: `codex/frontend-optimization`, based on `codex/user-terminal` at `3441163`.
User confirmed blue emphasis and subsequently accepted the overall page on 2026-11-02; requested brighter blue, a tighter header, responsive tables and GPT Image branding.

## Scope

- Shared palette, typography, spacing, tables, forms, alerts and modals; existing selectors and data retained.
- Presentation text changes remove decorative emoji and clarify automatic port allocation.
- Existing server code changed only to add public GET `/guide` and its pinned client-release constant.
- New isolated guide JavaScript validates input, quotes shell arguments and supports copy/fallback selection.
- No existing routes, permissions, database schema, authentication lifetimes, request parameters, polling or cluster actions changed. No deployment. Guide screenshots were added in the approved refinement stage below.

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

Fresh whole-branch reviewer checked `3441163..79be5e1` against the spec and plan: no critical, important or minor finding; ready for user review. Independently reran the two guide route tests and generated-shell checks successfully.

Review boundaries: no production-cluster/TLS exercise, no claim that the existing macOS CTL test passes, and no formal guide screenshots. These remain outside this local preview stage; Linux verification is required before deployment, and screenshots require user page approval.

The macOS ownership limitation was independently reproduced on an owned temporary file: UID 501, GID 20, group 501 absent; `os.chown(path, 501, 501)` is denied. This matches the unchanged helper's ownership operation. No permission policy was relaxed.

## Implementation choices

- Keep the pure install-command generator in `guide-install.mjs`, separate from browser bindings in `guide.js`, so actual generated shell can be tested without a fake DOM. Cost: one additional small module.
- Commit styles and guide together (`79be5e1`) because navigation links depend on the new guide route. Cost: a coarser commit boundary; every committed checkout remains runnable.

The user subsequently confirmed the page and requested refinements; approved guide screenshots are recorded below.

## Approved refinement and guide illustrations

- User feedback: “我觉得没什么问题”, followed by requests for brighter blue, compact/aligned header, adaptive tables and GPT Image logo/favicon.
- Emphasis is now `#2563eb`, hover `#1d4ed8`; shared header/content gutter and 1480px maximum. Normal desktop header measured 57px including the border.
- At 1920/1440/1280/1024px the resource table fits its container; at 768/390px only the table scrolls. Header and content left edges match at every checked width; all columns/actions remain. Duration/ID values stay intact.
- Built-in GPT Image generated the symbol and horizontal logo. Transparent PNG/ICO assets are stored in `static/brand/`; exact prompts and provenance are in `docs/brand-assets.md`. Dynamic portal naming remains live text.
- Three 2x screenshots from the actual local demo pages use red outlines only on click targets. Public guide images have alt text, intrinsic dimensions, lazy loading and original-size links. No real accounts/keys/cluster data were used.
- Final checks after image integration: full pytest **56 passed** (12.86s); original webpage smoke **SMOKE_OK**; command execution checks passed; responsive and guide-copy browser checks passed; public images passed at 1440/1024/390/320px with no console errors. PNG/ICO loads and alpha channels checked.
- Refinement self-review: only CSS, presentation templates and static assets changed after the earlier independent review; existing backend/business JavaScript unchanged. No additional permission or lifecycle changes. The Linux CTL check limitation above remains.

## Selected nebula identity and client clarification

- The user selected C (nebula nucleus + diagonal orbit + satellite). Current referenced assets use `nebula` filenames; exact GPT Image prompts and export paths are in `docs/brand-assets.md` and `docs/selected-nebula-prompts.json`.
- Real portal templates changed: base navigation/favicon, login layout, resource-page headings/quota spacing, status/profile heading groups. Shared styles also apply to the existing image/apply/admin pages. Documentation remains one navigation item, labeled “使用文档”; `/` and login redirects remain unchanged.
- Extended the existing guide navigation test to verify anonymous home still leads to login, authenticated user home still leads to `/my`, and all five business links coexist with the documentation link.
- An initial regression run caught removal of the existing `Slurm` quota label; the user-visible label was restored as “磁盘用量与 Slurm 账户额度”. Final full pytest: **56 passed**; original smoke: **SMOKE_OK**; guide generated-command execution checks passed.
- In-app browser: logged into local demo, checked all five business pages and documentation within the same six-item navigation; tested the original connection dialog; desktop 1440px and mobile 390px show no document overflow, all ten resource columns remain, logo loads, no console errors. Browser left on `/my`, viewport reset to the user's default size. Current resource-page screenshot saved outside source under `OrbitCluster-client-preview/my-resources.jpg`.
- Backend handlers, business JavaScript, form field names/actions, role guards, polling and authentication lifetimes unchanged in this iteration. No production deployment. Prior Linux CTL platform-verification limitation remains documented above.
