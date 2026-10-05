# Service switch, credentials folder and costs

## 1. The service switch

A password-protected on/off switch for the whole service. Locally it gates the app; on AWS it really starts and stops the server (see "Two modes" below).

| State | What happens |
|---|---|
| **Off** (the starting state) | Every API answers `503` with `{"detail": "Service offline", "offline": true}`. The page shows a **Service offline** screen saying where to turn it on. |
| **On** | Everything works. |

The pages have **no on/off button**, and nothing else turns OKVS on or off: the **Control Center** (a separate project) does it, locally and on AWS, and every action there is password-checked. Locally it starts and stops the Docker containers (direct `docker compose` commands refuse to run); on AWS it starts, stops and takes the server to zero. While it runs it is on. The switch described below remains only for running the app outside Docker (for example the tests).

### What stays reachable while off
Only four paths: `/api/health` (container health check), `/api/service/status` (so the page knows the state and its branding), and `/api/service/on` and `/api/service/off` (the switch itself). Everything else under `/api` is refused. The rule lives in `app/gate.py` and is covered by tests, including look-alike paths such as `/api/service/status/extra`.

### Passwords
- Only a salted **scrypt hash** is stored. The password itself is never written anywhere.
- Comparison is constant-time.
- After `service_switch.max_failed_attempts` wrong passwords the switch locks for `lockout_minutes`, and even the right password is refused during the lockout. The count is **global**, not per visitor, because traffic arrives through a CDN. Trade-off: someone guessing wrong can lock you out for a few minutes; the lockout expires by itself.
- The page sends the password only in the request body and clears the field afterwards.

### First run and the demo password
- With `ui.demo_mode: true`, a missing password record is created automatically with a **demo-only starting password** (defined in `app/service_switch.py`) so the demo works straight away.
- With `ui.demo_mode: false` the app **refuses to start** if no password was set, or if it is still the demo one. This is deliberate: the demo password sits in public code, so it must never be in force on a real system.
- Set your own (minimum length is `auth.password_min_length`):
  - Local: `python -m app.cli set-switch-password`
  - Docker: `python scripts/containers.py exec api python -m app.cli set-switch-password`
  - On the AWS server, open a shell with SSM Session Manager (no SSH needed), then run the same command through `docker compose -f docker-compose.aws.yml run --rm api ...` from `/opt/app`.

### State and auto-off
- On/off is saved to `service_switch.state_path` so it survives restarts. A missing or damaged file means **off**.
- `service_switch.auto_off_hours` (💰) turns the service off by itself after that many hours, so a forgotten switch stops AI spending. `0` means never.

### Two modes: local and AWS
| | `service_switch.mode: local` (local runs) | `service_switch.mode: aws` (the AWS setup sets it) |
|---|---|---|
| What "off" does | The app answers "Service offline" to everything except the switch | **The server is stopped**, so the compute charge stops |
| What "on" does | The app starts answering | **The server is started** (about a minute to boot) |
| Who checks the password | The app | A tiny AWS function (`/control`), the **only** door |
| Does the server have its own switch? | Yes | No: "on" simply means the server is running |

### How the AWS switch works
- CloudFront routes `/control/*` to a small Lambda function (source: `deploy/lambda/control.py`, tested, and copied inline into the template by a test-checked rule). It is reachable **only** through CloudFront, which signs the request; its raw address cannot be called directly.
- `GET /control/status` tells the page whether the server is stopped, starting, running or stopping (or at zero). It needs no password.
- It can't start or stop anything. On AWS the **Control Center**, after its password, is the only way to turn the project on, pause it or take it to zero.
- While the server is stopped, requests to `/api/*` get CloudFront's 502/504, which the template turns into the same `503 {"detail": "Service offline", "offline": true}` the app uses, so the page and any direct caller get a clear message.
- The offline screen still appears, because the page comes from S3 and `/control/status` is always up. Branding is remembered in the browser, so the offline screen carries the app's name after the first visit.

### What stopping saves, and what it doesn't
- **Stops billing:** the server's compute, roughly $12/month if it ran all month. AI usage also stops (nothing is running to call it).
- **Keeps billing:** the Elastic IP (about $3.65/month), the disk (about $1.60/month), S3, CloudFront, the container registry, and the function's logs, all small. The floor while stopped is roughly **$5-6/month** (check current prices).
- **Turning on takes about a minute.** Turning on while the server is still shutting down answers "try again in a minute".
- The weekday schedule (`deployment.schedule`) still works alongside the switch: it stops and starts the server on a timer with no password.
- `service_switch.auto_off_hours` applies to **local** mode only. On AWS use the schedule or turn it off yourself.
- If you stop the server some other way (console, CLI), the page simply shows it as stopped.

## 2. Where credentials live

Everything credential-like goes through one small interface (`app/credstore.py`):

| `credentials.backend` | Where | Used for |
|---|---|---|
| `file` | The local `creds/` folder (git-ignored and docker-ignored, owner-only permissions, written atomically) | Local runs |
| `s3` | A private, server-only S3 bucket | The AWS setup. The compose file selects it through the environment, so nothing needs editing |
| `secrets_manager` | AWS Secrets Manager | **Planned, not built.** Choosing it stops start-up with a clear message |

### What is stored
- The **service-switch password** record (`service_switch`): a salted scrypt hash.
- **User accounts** (`users/eileen`, `users/allminuseileen`, ...): display name, role and a salted password hash.
- The **session key** (`session_key`): a random 32-byte secret that signs sign-in cookies. This one is a real secret, not a hash: whoever holds it can forge a sign-in. It is created on first use and never leaves the credential store.

Passwords are never stored. Records are created **on the server** (or on your machine for local runs). The deploy script cannot upload them (its allow-list names three non-secret files, and tests enforce that).

### The protected S3 bucket (AWS)
- Private (all four Block Public Access settings), encrypted (SSE-S3), versioned (old versions expire after 7 days), HTTPS-only.
- The bucket policy **denies everyone except the server's IAM role**, administrators included. If you ever need to get in yourself, edit the bucket policy first; that change leaves a CloudTrail record.
- The server's role may read and write records, and list the bucket, but not delete.
- The bucket is kept if the stack is deleted.
- 💰 Cost: a handful of tiny objects and a few requests a day, effectively free. SSE-S3 is used instead of a KMS key, which would add a monthly fee.

### S3 versus Secrets Manager (why this is a trade-off)
- S3 is simpler and nearly free. Secrets Manager adds a per-secret monthly charge, built-in rotation, and per-secret audit trails.
- With S3, secret access is only audited if you turn on CloudTrail data events (extra cost), and there is no rotation: you change a password yourself with the CLI.
- Because only **hashes** are stored (plus the one signing key), a leak is less damaging than a leak of plaintext secrets, but the signing key still deserves the lockdown above.

### Fail-safe behaviour
A missing record is "nothing stored". A **damaged or unreachable** record is an **error**, never "nothing stored", so a glitch can't reset a password to the demo default. If the store is unavailable, sign-in and the switch answer `503` rather than letting anyone in.

## Users and passwords
- Create the two demo accounts (random passwords, printed once): `python -m app.cli create-demo-users`
- Replace them with new random passwords: `python -m app.cli create-demo-users --reset`
- Set a password yourself (the minimum length is `auth.password_min_length`): `python -m app.cli set-user-password USERNAME`
- List accounts (names and roles only): `python -m app.cli list-users`
- Change the service-switch password: `python -m app.cli set-switch-password`

Run them where the credentials live: on your machine for local runs, or on the AWS server (see [COMMANDS.md](COMMANDS.md)).

## 3. Costs

The pages have no cost screen. Costs are watched from the **Control Center**: each project's card shows its AWS projection for the month, the services being billed right now, and the **LLM inference API** spend (today, this month, the estimated credit left), all read from this project's logs. On this machine, `python -m app.cli ai-costs [--days N]` prints the AI usage and its cost per model.

### How the numbers are made
An **estimate from this project's own meters**, priced with the rates in `config.yaml` (`costs.rates`, `costs.llm_prices_per_mtok`). It is instant. AWS's own bill can differ and arrives up to about a day late, so this is the only way to see "now" numbers.

| Line | How it is measured | Price used |
|---|---|---|
| Server | Heartbeat rows (one per `costs.heartbeat_seconds` while the app runs) × interval = hours running | `instance_hourly_usd` per hour running |
| Public IP | Whole period | `elastic_ip_hourly_usd` per hour (billed even when the server is stopped) |
| Disk | `deployment.root_volume_gb`, whole period | `ebs_gb_month_usd` per GB-month, prorated by hours/730 |
| Document storage | Total size of stored documents | `s3_gb_month_usd` per GB-month, prorated |
| AI usage | Tokens in/out per model, recorded per call | `llm_prices_per_mtok` per million tokens. A model without a price is costed at zero with a warning; Ollama models (`name:tag`) count as local and free without a warning |

**Not included in the estimate:** CloudFront and data transfer, request charges, taxes. Keep the rates current: AWS and model prices change.

### Status of the meters
The server writes a heartbeat row while it runs and one `AI_USAGE` log line per AI call (model, tokens, price). The Control Center reads the log lines; `app.cli ai-costs` reads the database.

### Not built: comparing with AWS's own bill
A "compare with AWS" button would call the Cost Explorer API (filtered by a cost-allocation tag). Those calls are charged per request and the data lags, so it is left out of the cheap default. It can be added as an on-demand button later.
