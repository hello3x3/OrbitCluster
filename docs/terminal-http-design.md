> Historical 0.1.1 record. Superseded by [native CLI feedback implementation](cli-feedback-and-validation.md): HTTP is now the default and the client has no Python/TUI.

# Explicit HTTP support for the user terminal

2026-11-02: user has only an HTTP laboratory portal and requests a working HTTP terminal flow.

- Existing server switch `TERMINAL_REQUIRE_HTTPS=0` already permits HTTP for the terminal API; no authentication or permission rewrite is needed.
- Expand existing client `allow_http` from loopback-only to explicit opt-in for the configured HTTP endpoint. Default remains false. Never automatically downgrade HTTPS, follow redirects, disable certificate checks, change token lifetime, renew tokens or reuse credentials between HTTP/HTTPS origins.
- Add the same opt-in to installer, TUI login and website command generator, with one concise plaintext-transport notice. Persist the setting when the user chooses it so later commands work normally.
- Retain server-issued seven-day tokens, no cookies, no automatic renewal and all existing business rules.
- Publish a fixed client commit with HTTP support before updating the website installer reference, so generated commands never point to an incompatible old installer.
- Update user docs/skill with server config, client setup and the requirement for a deployed terminal API. Do not change or restart the live server.

Verification: default rejection, explicit opt-in, exact-origin credential isolation, CLI/TUI and installer persistence, HTTP end-to-end login/create/query/expiry; HTTPS certificate validation and full regression remain green. Browser command generation still rejects HTTP until the checkbox is selected.

## Verified implementation

- Client version `0.1.1`; website installer pinned to `8b0990ebf469a575636760de3ad177d56cd40645`. The published raw installer was downloaded and compared byte-for-byte with the tested local installer.
- Full terminal and portal regression: `85 passed`, including real HTTP and HTTPS sockets, server-side exact expiry, original webpage smoke, resource operations, URL validation and credential isolation.
- Built both wheel and sdist. Installed the wheel through the real shell installer into a disposable user directory; HTTP config persisted and local fake-cluster login, identity, resource query and logout succeeded.
- Browser checks on `/guide` and `/guide/terminal`: HTTP is refused until checked, copied command includes `--allow-http` and the compatible fixed ref, HTTPS retains CA configuration, HTTP excludes CA, no horizontal page overflow at 1440/768/390/320 px, no script errors.
- New/updated skill passed `quick_validate.py`; shell quoting, cleanup and failed-download behavior passed the generated-command checks.
- Production was not changed or restarted. To use the real HTTP site, deploy the terminal API, set `TERMINAL_REQUIRE_HTTPS=0` in its existing site configuration (or corresponding environment override), restart the portal, and install/update the client to `0.1.1` with HTTP explicitly enabled. Token lifetime remains `TERMINAL_TOKEN_DAYS=7`.
