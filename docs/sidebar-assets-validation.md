# Sidebar revision validation

Base: `a60e4fc` on `codex/frontend-optimization`.

## Implemented scope

- Default-open sidebar groups, simple emoji, independent accordion controls, 16px body / 15px tables, mobile drawer with keyboard closure/focus handling, avatar profile/logout menu.
- Separate occupancy and portal-active pages; default/legacy sinfo and task submission retain their behavior. Added only a read-only all-partition helper command and a separate short cache. Global node totals deduplicate shared nodes; incomplete allocation data remains unknown. Failed Slurm lookup is an error rather than healthy zero usage.
- Shared datasets/models are FRONTEND placeholders with 功能待完善, exactly per user clarification. No asset API, filesystem scan, configuration manifest or daily job was created.
- Existing image permissions/actions reused by asset image management. Admin/root nav still uses original role/username restrictions.
- Reused quick guide; self-contained development-doc link; dedicated terminal guide with repo-local OrbitCluster skill installation/download. Bundled architecture and skill are byte-identical to sources.
- Existing business JavaScript changed only the successful delegated-apply destination to `/status/active`, preserving the previous result view after navigation split. No request, validation or auth changes.

## Evidence

2026-11-02, local fake cluster only:

- Full pytest after review fix: **73 passed**, 16.55s.
- Existing webpage smoke: **SMOKE_OK**. Its Shanghai-deadline assertion now checks the separate `/status/active` page, retaining the same original assertion.
- New tests first failed on missing routes/command, then passed; additional failure-path test first exposed a false healthy-empty cluster and passed after explicit command-status handling.
- Generated installer shell checks: passed. New navigation JS syntax and Python compile checks: passed.
- Skill creator validator: **Skill is valid!** PyYAML was loaded from a temporary validation-only directory; no product dependency added.
- Browser: every user page at 1440/1024/768/390/320px; user/admin/root permissions; default-open and independent accordion state; avatar menu/profile; Escape closure; mobile drawer/link navigation; resource connection/log/stop-cancel modals; no document overflow or console exceptions.
- Guide: HTTP/HTTPS handling, command copy and permission-denied fallback; skill download; 2x guide images/alt text/original-size links at desktop/mobile.
- Three guide screenshots refreshed to the new font/viewport, with synthetic local data only.

## Limits

Not deployed, no real Slurm query, no production storage access and no scheduled jobs installed. Install the updated `portal-ctl` together with the portal to enable all-partition lookup. The previously documented macOS UID/GID limitation in the old standalone CTL security script remains; it needs a Linux run before deployment. Existing helper security and write commands were not changed.

## Independent review and fix

Review of `a60e4fc..554267e` found no critical issue and one important count-format issue. Fixed the new reader to request wide, explicitly delimited node/GRES fields and reject capped or malformed data as unknown. Strict parsing handles long model names, multiple GPU models, comma-containing IDX ranges and non-GPU resources without false free-GPU counts. Eleven new regression cases failed first, then passed; all 17 sidebar tests and the full 73-test suite passed.

The formatting constraint was checked against the primary [Slurm sinfo documentation](https://slurm.schedmd.com/sinfo.html): `-O` has a maximum field width, unlike `-o`'s minimum width. The old `sinfo` command and its old parser remain unchanged to preserve the requested existing interface; older read endpoints can still have the pre-existing long-GRES limitation and should be addressed separately if desired.

Reviewer found no other material permission, business-flow, mobile or skill packaging regression. Production Slurm/helper compatibility and the old macOS CTL security-script portability remain outside local validation. No deployment or production jobs performed.

Final browser rerun passed all user/admin/root navigation and existing modals with zero document overflow or console errors. Quick-guide text now distinguishes the two cluster pages, and skill installation supports a custom CODEX_HOME.
