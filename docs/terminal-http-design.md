# Explicit HTTP support for the user terminal

2026-11-02: user has only an HTTP laboratory portal and requests a working HTTP terminal flow.

- Existing server switch `TERMINAL_REQUIRE_HTTPS=0` already permits HTTP for the terminal API; no authentication or permission rewrite is needed.
- Expand existing client `allow_http` from loopback-only to explicit opt-in for the configured HTTP endpoint. Default remains false. Never automatically downgrade HTTPS, follow redirects, disable certificate checks, change token lifetime, renew tokens or reuse credentials between HTTP/HTTPS origins.
- Add the same opt-in to installer, TUI login and website command generator, with one concise plaintext-transport notice. Persist the setting when the user chooses it so later commands work normally.
- Retain server-issued seven-day tokens, no cookies, no automatic renewal and all existing business rules.
- Publish a fixed client commit with HTTP support before updating the website installer reference, so generated commands never point to an incompatible old installer.
- Update user docs/skill with server config, client setup and the requirement for a deployed terminal API. Do not change or restart the live server.

Verification: default rejection, explicit opt-in, exact-origin credential isolation, CLI/TUI and installer persistence, HTTP end-to-end login/create/query/expiry; HTTPS certificate validation and full regression remain green. Browser command generation still rejects HTTP until the checkbox is selected.
