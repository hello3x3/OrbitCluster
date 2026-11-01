# Using OrbitCluster from an agent

This file describes the user-facing `orbit` tool, not an administrator interface.

- The human logs in once with `orbit login`; reuse their saved seven-day credential. Never request their password in chat or read/print auth.json. Credentials belong to the current OS user and exact configured server.
- Run `orbit --help` and subcommand `--help` for parameters. Use `--json`; success JSON is on stdout, failure JSON on stderr. Exit codes: 0 success, 1 rejected operation, 2 parameters/configuration, 3 login required, 4 network failure, 130 cancelled.
- Start with `orbit whoami --json`, `orbit plans --json`, `orbit images list --json`, `orbit accounts --json`, and `orbit instances list --json`.
- Create with explicit name, plan ID, image path/name and hours. Use the instance `id` returned by create for further commands; this differs from Slurm `job_id`.
- `orbit instances wait ID --until ssh-ready --timeout 300 --json` waits without renewing the login. `orbit instances connection ID --json` gives SSH argv/command. Private SSH keys stay on the user's machine.
- Stop/delete/key removal and forced image replacement require explicit human authorization, then `--yes` for noninteractive execution. Creation, restart and image export are real cluster operations, not local edits.
- Do not retry a timed-out write automatically. Query instances/images/logs first to establish whether it succeeded. A repeated create can consume more resources; existing task-name uniqueness remains in force.
- On exit code 3, ask the human to run `orbit login` in their terminal. Do not attempt to renew credentials or scrape browser Cookies.
- Closing the tool does not stop a job. Manual stopping does not automatically export container changes. Normal expiry/auto-save, preemption, quotas and cleanup retain the portal's documented behavior.
