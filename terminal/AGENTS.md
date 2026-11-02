# Using the native OrbitCluster CLI from an agent

The client is one Go executable; there is no TUI or Python dependency. HTTP is enabled by default for the laboratory network. Downloads and installers come only from the configured portal. This is a user tool, not an administrator interface.

- The human logs in once with `orbit login`; reuse their saved seven-day credential. Never request their password in chat or read/print auth.json. Credentials belong to the current OS user and exact configured server.
- Run `orbit --help` and subcommand `--help` for parameters. Default text gives concise operational context; use `--details` to expand and `--json` for exact field parsing. Existing JSON data is unchanged; successful login adds top-level `update` metadata. Success JSON is on stdout, failure JSON on stderr. Exit codes: 0 success, 1 rejected operation, 2 parameters/configuration, 3 login required, 4 network failure, 130 cancelled.
- Start with `orbit whoami --json`, `orbit plans --json`, `orbit images list --json`, `orbit accounts --json`, and `orbit instances list --json`.
- Create with explicit name, plan ID, image path/name and hours. Use the instance `id` returned by create for further commands; this differs from Slurm `job_id`.
- `orbit instances wait ID --until ssh-ready --timeout 300 --json` waits without renewing the login. `orbit instances connection ID --json` gives SSH argv/command. Private SSH keys stay on the user's machine.
- Stop/delete/key removal and forced image replacement require explicit human authorization, then `--yes` for noninteractive execution. Creation, restart and image export are real cluster operations, not local edits.
- Do not retry a timed-out write automatically. Query instances/images/logs first to establish whether it succeeded. A repeated create can consume more resources; existing task-name uniqueness remains in force.
- On exit code 3, ask the human to run `orbit login` in their terminal. Do not attempt to renew credentials or scrape browser Cookies.
- Exiting the CLI does not stop a job. Manual stopping does not automatically export container changes. Normal expiry/auto-save, preemption, quotas and cleanup retain the portal's documented behavior.
- Login checks the configured portal for updates but never installs automatically. `orbit update --check` only reads the public manifest. Run `orbit update` when the user asks to update; it preserves stored configuration/credentials and never renews authentication. Windows reports scheduled replacement and its failure-log path; confirm with `orbit --version`. A version notice is not authorization to update.
