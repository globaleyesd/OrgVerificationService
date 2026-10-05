# Deploying to AWS from VS Code (cheapest setup)

**Status: written carefully but never run against a real AWS account** (the build environment had no network or AWS access). The template passes structural checks and security tests, but run `--dry-run` first, validate the template (below), and expect to fix small things on the first real deploy.

## What you get
```
Browser ──https──> CloudFront ──/ (page)──────> private S3 bucket (page files)
                       │
                       ├──/control/*──signed──> tiny switch function: status for the page; answers /api while at zero
                       │
                       └──/api/*──http:80────> one small ARM server (Docker: API + database)
                                                  ├─ pulls its image from ECR
                                                  ├─ reads/writes the private documents bucket
                                                  ├─ keeps password hashes, user accounts and the cookie key in a locked-down credentials bucket
                                                  └─ calls Claude: the Anthropic API (key in the credentials bucket) or Bedrock (its own role)
```

## Cost choices (all deliberate, 💰)
| Choice | Instead of | Why |
|---|---|---|
| One t4g.small server running Docker | Fargate, load balancer, several services | Fixed small bill for 10 users |
| Postgres in a container | Managed database | Roughly doubles the bill otherwise |
| No NAT gateway, public subnet, one Elastic IP | NAT gateway | A NAT gateway has a large fixed monthly fee |
| CloudFront in front of S3 and the API | Load balancer + certificate setup | HTTPS for free on the default address; CloudFront's free tier is generous |
| `CPUCredits: standard` | Default "unlimited" | Never pay for surplus CPU credits (may slow under sustained load) |
| No WAF, no CloudWatch agent, no detailed monitoring | Those services | Each adds monthly cost |
| ECR keeps only the 3 newest images | Keep everything | Storage cost |
| Optional weekday working-hours schedule (`deployment.schedule`) | Always on | Roughly halves compute |
| **Switch = stop the server** (a tiny Lambda behind CloudFront) | Leaving the server running while "off" | Off really stops the compute charge. The function stays inside the free tier |
| Claude Haiku for answers (`config.aws.local.yaml`) | A larger model | About half a cent per question, billed only when someone asks |

Rough monthly infrastructure bill in us-east-1 (check current prices): **about $17-18 always-on, about $9 with the working-hours schedule, and about $5-6 while the switch has the server stopped**, before AI usage. That is the server (about $12 always-on), the Elastic IP (about $3.65, billed even when stopped), disk (about $1.60) and small items. The in-app **Costs** button shows the live estimate.

## One-time setup
1. Install: Python 3.12, AWS CLI v2, Docker (with buildx), VS Code. Recommended extensions are listed in `.vscode/extensions.json` (AWS Toolkit, cfn-lint, Docker, YAML).
2. **AWS access.** Use a dedicated least-privilege deploy role or IAM Identity Center login rather than root keys. It needs: CloudFormation, EC2/VPC, IAM roles, S3, CloudFront, ECR, SSM (`send-command`, `get-parameter`), Budgets and Scheduler. Choose one:
   - Sign in with the AWS Toolkit or `aws configure sso`, then `set AWS_PROFILE=...` (or put `profile:` in `secrets.local.yaml`).
   - Or put keys in `secrets.local.yaml`. They are passed to the AWS and Docker commands through their environment only, never written to disk or printed, and never sent to AWS by this project.
3. Copy `config.local.example.yaml` to `config.local.yaml` and set your display name and colours. It is git-ignored.
4. Optional: create `deploy/params.local.json` (git-ignored) with `{"BudgetEmail": "you@example.com"}` for budget alerts.
5. Put the **server's** settings in `config.aws.local.yaml` (git-ignored). The deploy uploads it as the server's `config.local.yaml`, so your own `config.local.yaml` (for example the local model) stays on your machine. The local model can't run on the small server, so the deploy refuses `llm.provider: local`. Two ways to give the server Claude:
   - **Anthropic API** (`llm.provider: anthropic`, `llm.model_answer: claude-haiku-4-5-20251001`): after the first deploy, save the key **on the server** (below). The server's role gets no Bedrock access.
   - **Bedrock** (`llm.provider: bedrock`): no key; enable the model for your account in Bedrock, put the **Bedrock model id** in `llm.model_answer` and its price under `costs.llm_prices_per_mtok`.
6. Validate the template before the first deploy: install cfn-lint, then `cfn-lint deploy/cloudformation/stack.yaml`. The VS Code cfn-lint extension does this as you edit.

## Deploy
Branches: work on `dev` (it runs locally with Docker Compose), then merge into `prod` and deploy from there:
`git checkout prod && git merge dev && python deploy/deploy.py all`. Steps that change AWS refuse to run on any other
branch; the preview (`--dry-run`), `outputs` and `cost-sheet` run anywhere.

Run the tasks from **Terminal > Run Task** (or `python deploy/deploy.py <step>`; every command is listed in [../docs/COMMANDS.md](../docs/COMMANDS.md)):

1. **Deploy: preview all steps (dry run)**: prints every command and runs nothing.
2. **Deploy: everything**: stack, image, page, config, server update. The first stack creation takes around 10 minutes because of CloudFront.
3. Run **Deploy: show site address and names**, open `SiteUrl`, enter the switch password to turn the service on, then create the demo users (below). The pages are at `SiteUrl/` (launcher), `SiteUrl/ask` (role-checked) and `SiteUrl/add` (any signed-in user). Every page sends you to `SiteUrl/signin` first.

On the first deploy the server starts before any image exists, so its start-up script reports "Image not pushed yet". That is expected: the `image`, `config` and `update` steps fix it.

Day to day:
- Changed the pages only: **upload web page**.
- Changed the code: **build and push image**, then **update server**.
- Changed `config.yaml` or `config.local.yaml`: **upload non-secret config**, then **update server**.

### Save the Anthropic API key (llm.provider: anthropic)
Open a shell on the server (`aws ssm start-session --target INSTANCE_ID`) and from `/opt/app` run:
`sudo docker compose -f docker-compose.aws.yml --env-file .env run --rm api python -m app.cli set-llm-key`
then `sudo docker compose -f docker-compose.aws.yml --env-file .env restart api`. The key is asked for, not shown, and saved in
the credentials bucket that only the server's role can read. It is never in a file on your machine, the template or git.

### Create the demo users (after the first deploy)
The server must be running (turn the service on first). Open a shell on it with `aws ssm start-session --target INSTANCE_ID`, then from `/opt/app` run:
`sudo docker compose -f docker-compose.aws.yml --env-file .env run --rm api python -m app.cli create-demo-users`
It prints random passwords for **Eileen** (Super) and **AllMinusEileen** (Employee) once. Save them in a password manager. The accounts are written straight into the protected credentials bucket by the server's role; nothing is uploaded from your machine. If you use Session Manager session logging, prefer `set-user-password` (prompted, not echoed). Then open `SiteUrl/ask`: in demo mode you just click Eileen or AllMinusEileen (no password to type). The passwords are for the password form that replaces the picker when `ui.demo_mode` is false.

### Going from demo to real use
1. Set `ui.demo_mode: false` and `ui.allow_mock: false` (this also removes the passwordless user picker in favour of the password form).
2. Deploy, open a shell on the server with SSM Session Manager (no SSH port is open), and run, from `/opt/app`:
   `docker compose -f docker-compose.aws.yml run --rm api python -m app.cli set-switch-password`
   then `docker compose -f docker-compose.aws.yml --env-file .env up -d`.
   Until a non-demo password exists the app refuses to start.
3. Replace the demo users with real ones. There is no admin screen for users yet; use the `app.cli` commands in [../docs/COMMANDS.md](../docs/COMMANDS.md).

## Turning it on, off and down to zero: the Control Center
The pages have no on/off button. Projects are controlled from the **Control Center**, a separate project
(its own repository, see its README) that lists every project and asks for its own password on every change.

| Level | What runs | Roughly per month | Back to running |
|---|---|---|---|
| Running | everything | $17-18 | - |
| Stopped | nothing; server, disk and Elastic IP still exist | $5-6 | about a minute |
| Zero | nothing; server, disk and Elastic IP are deleted | cents (stored files only) | about 10-15 minutes |

- This stack provides the Control Center's **power adapter**: the function `<project>-power` (source `deploy/lambda/power.py`,
  copied inline and kept identical by a test). It has no URL; only the Control Center's role can invoke it.
- **Going to zero:** the adapter runs `/opt/app/backup.sh` on the server (a stopped server is started for it), which dumps
  the database to `s3://<documents bucket>/backups/` (a dated copy and `db-latest.sql.gz`). Only if that succeeds does it
  set the stack parameter `Power=zero`, which deletes the server, its disk, its Elastic IP and the working-hours schedules.
  `/api/*` is then routed to the switch function, which answers `503 Service offline`, and the page says the project is at zero.
- **Coming back:** `Power=on` recreates them. On its first start the new server restores `db-latest.sql.gz` **before** the
  app starts, so the app never creates empty tables first. Documents, accounts (credentials bucket), the page and the image
  were never deleted.
- **Register this project** with the Control Center after deploying:
  `python deploy/cc.py register --stack kb-verifier --project-region <region> --name "Knowledge Verifier" --costs running=18,stopped=6,zero=0.2`
  (run in the control-center project).
- `Power` is set only by the adapter. Normal deploys keep its current value, so deploying while at zero stays at zero.
- The old password switch at `/control/on|off` still exists (same password and lockout as before) for scripts; `/control/status`
  tells the page the state. Locally, `python -m app.cli service on|off` replaces the button.

## What is locked down
- **Both buckets**: all four Block Public Access settings on, encrypted, versioned, requests over plain HTTP denied, no ACLs. The page bucket can be read only by this CloudFront distribution (origin access control). The documents bucket is kept if the stack is deleted.
- **Viewers**: HTTPS only; HSTS, no-sniff, no framing, no referrer, a strict content-security policy (no inline scripts or styles), and a permissions policy that allows only the microphone for this site.
- **Page addresses**: a small CloudFront Function maps `/ask` and `/add` to their files (and redirects a trailing slash); a test runs the function and checks it matches the app's page list.
- **Server**: no SSH, only CloudFront's address list can reach port 80, IMDSv2 required, encrypted disk, SSM Session Manager for access, least-privilege role (pull this one image, use this one bucket, optionally call Bedrock).
- **Secrets**: none in the template, none in the user-data, none uploaded. The deploy script can upload only three non-secret files (checked by tests). The database password is generated on the server.
- **Switch function**: signed requests only (origin access control + IAM auth, permission limited to this distribution). Its role can start/stop only this server, read two records and write one (the lockout counter). Wrong guesses lock it globally and the lockout is stored, so it survives restarts. Logs kept 14 days.
- **Credentials bucket**: holds only salted password hashes (service switch, user accounts) and the cookie-signing key. Private, encrypted, versioned, HTTPS-only, and its bucket policy **denies everyone except the server's IAM role**, administrators included (to get in yourself, edit the policy first; that leaves a CloudTrail record). The role can read and write records but not delete. It is kept if the stack is deleted. Effectively free.
- **API responses** are never cached.

## Known gaps (please read)
1. **CloudFront to the server is plain HTTP.** Viewers are on HTTPS, but the hop from CloudFront to the server crosses the internet unencrypted. Acceptable for a demo; for sensitive data add a domain and certificate on the server and switch the origin to HTTPS before going live.
2. **No IP allow-listing** (needs AWS WAF, extra cost). The service switch and, later, sign-in are the gates. `deployment.allowed_ingress_cidrs` is not used by this template.
3. **No backups yet.** The database lives on the server's disk; if the server is replaced it is lost. Original documents are in S3. A nightly dump to S3 is a good next addition.
4. **Spot instances are not wired in** (`deployment.use_spot` is ignored).
5. **`/add` has no role check beyond sign-in** (by design): any signed-in user can submit; there is no rate limiting or screening yet.
6. **Minimum TLS version** cannot be enforced on the default CloudFront address (needs a custom domain and certificate).
7. The **cost meters** and the **heartbeat** have not run against a real database yet.
8. The **service-switch lockout is global**, so wrong guesses can briefly lock out the owner.
9. The Docker Compose plugin is downloaded from GitHub at server start with a pinned version; verify it fits your policy.
10. **Credentials in S3, not Secrets Manager.** Cheaper and simpler, but no automatic rotation and no per-secret audit trail unless you pay for CloudTrail data events. The cookie-signing key is a real secret (not a hash): whoever holds it can forge sign-ins. Session tokens cannot be revoked before they expire.
11. **Demo sign-in is passwordless.** While `ui.demo_mode` is true, anyone who reaches the site with the service switch on can click Eileen and see everything. Keep the switch off when not demoing; set `ui.demo_mode: false` before real use.
12. **Real answers are new and unproven on AWS.** The server downloads its search model (about 130 MB) the first time a document is added, so the first upload after deploy is slow and needs outbound internet. Original files go to the documents bucket through the server's role. The Postgres code has never run against a database until your first local run.
13. **The switch function is new and unverified against real AWS.** Things to check on the first deploy: that the CloudFront origin access control for Lambda accepts the browser's `x-amz-content-sha256` header on POST; whether your account needs the extra `lambda:InvokeFunction` permission (both are in the template); and that `s3:ListBucket` on the credentials bucket is what lets a missing record read as "not found". If turning on returns 403, start there.
14. **Switch lockout counter is not atomic** (two simultaneous wrong guesses can count as one). Fine for this purpose.
15. **A started server takes about a minute**, and `service_switch.auto_off_hours` does not apply on AWS.
16. **No user admin screen or password reset yet**; accounts are managed with the `app.cli` commands.

## Tearing down
Empty the page bucket (use the **Empty** button in the S3 console, because the bucket is versioned) and the ECR repository first (CloudFormation can't delete non-empty ones), then delete the stack. Exact commands are in [../docs/COMMANDS.md](../docs/COMMANDS.md). The documents and credentials buckets are kept on purpose; delete them by hand if you want them gone (for the credentials bucket, edit its policy first). The Elastic IP is released with the stack.
