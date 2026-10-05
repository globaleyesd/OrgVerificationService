# OrgVerificationService

Create knowledge bases that can be accessed with RBAC within the whole organisation.

The service is called **kb-verifier** in its code and container names.

Ask questions across your team's documents and get answers where every claim links back to the exact passage it came from, so answers can be checked quickly.

## Features
- Answers with highlighted source passages for verification
- Works across common document formats (PDF, Word, text, CSV, spreadsheets, slides, and more)
- Role-based access: users only ever get answers built from material at or below their clearance level (roles: Super and Employee by default)
- Diary-style submissions for capturing knowledge as it is learned
- Two pages with their own addresses: `/add` (**Add knowledge**, a big drag-and-drop box, any signed-in user) and `/ask` (**Ask**, chat with a highlighted, collapsible source panel, role-checked)
- Sign-in with two demo accounts, **Eileen** (Super) and **AllMinusEileen** (Employee), stored in a protected S3 bucket on AWS. In demo mode you just click a user; outside it, a password form
- Password-protected on/off switch: locally it makes every API answer "Service offline"; on AWS it really stops the server (no compute charge) and starts it again
- Live cost dashboard: what each running service costs, day by day, for any period (last 30 days by default)
- Optional microphone input
- Configurable, Dockerised, and designed to run cheaply on one small AWS server with the page hosted on S3 + CloudFront

> **Status:** the app does real work: add documents (pdf, docx, txt, md, csv, xlsx, pptx, html, eml), ask questions with checked citations, and mark who can read what on a review page. Run it locally first (see [docs/RUNNING_LOCALLY.md](docs/RUNNING_LOCALLY.md)). The Postgres code has not yet run against a database, and the AWS setup has never been run against a real account.

## Quick start (local)

Requirements: Python 3.12+ and Docker Desktop or Rancher Desktop (see [docs/CONTAINERS.md](docs/CONTAINERS.md); `python scripts/containers.py check` lists anything missing).

```bash
cp secrets.example.yaml secrets.local.yaml   # then edit with your own values
python scripts/init_local.py                 # creates a random DB password, the creds/ and data/ folders
```

Then start it from the dev Control Center (http://localhost:8700, in the AWSControlPanel project): **Turn on**. The Control Center is the only way to start or stop OKVS, locally and on AWS: direct `docker compose` commands refuse to run.

Open `http://localhost:8000/` (or go straight to `/ask` or `/add`). Add `?mock=1` to preview the pages with sample answers. Create the demo users with `python -m app.cli create-demo-users` (see [docs/COMMANDS.md](docs/COMMANDS.md) and [docs/SERVICE_SWITCH_AND_COSTS.md](docs/SERVICE_SWITCH_AND_COSTS.md)).

Run the tests (no Docker needed):

```bash
pip install PyYAML
python -m unittest discover -s tests -t .
```

## Documentation
- [docs/RUNNING_LOCALLY.md](docs/RUNNING_LOCALLY.md): run it locally, load the demo data, walk through the demo, then push to AWS
- [docs/CONTAINERS.md](docs/CONTAINERS.md): Docker Desktop or Rancher Desktop, every dependency, and one command to run the stack
- [docs/LOCAL_MODEL.md](docs/LOCAL_MODEL.md): run the AI on your own machine with a free open model, no API key
- [docs/COMMANDS.md](docs/COMMANDS.md): every command to run locally, deploy to AWS, work on the server and tear down
- [docs/CONFIGURATION.md](docs/CONFIGURATION.md): every setting, with quick recipes
- [docs/UI.md](docs/UI.md): the pages, who can use them, the source panel and the API contract
- [docs/SERVICE_SWITCH_AND_COSTS.md](docs/SERVICE_SWITCH_AND_COSTS.md): the switch, the credentials folder, the cost dashboard
- [deploy/README.md](deploy/README.md): deploying to AWS from VS Code, what is locked down, known gaps

## Credentials
Credentials never go in git, in Docker images, or to any cloud provider:
- `secrets.local.yaml` and the `creds/` folder are git-ignored and docker-ignored. On AWS, password hashes and accounts live in a locked-down S3 bucket that only the server can read.
- Private display settings go in `config.local.yaml` (also git-ignored).
- On AWS the Anthropic API key is typed into the server and kept in its private credentials bucket (or use Bedrock with no key).

## Roadmap
- [x] Sign-in, session cookies and role checks (two demo accounts)
- [ ] User admin screen and password reset
- [x] Document upload and parsing (pdf, docx, txt, md, csv, xlsx, pptx, html, eml)
- [x] Question answering with verified citations
- [x] Review page: mark who can read each document
- [ ] Diary submissions and review queue
- [x] Web UI screens with demo toggle, collapsible source panel, offline screen
- [x] Service switch, credentials folder, cost calculator (maths and screens)
- [x] Docker files and AWS templates (untested against AWS)
- [x] UI connected to the real upload, answer and review endpoints
- [ ] First local run against Postgres (scripts/smoke_local.py)
- [ ] Database backups to S3
- [ ] Secrets Manager credential backend

## Contributing and security
Never commit credentials or real documents; use synthetic sample data only. To report a vulnerability, see [SECURITY.md](SECURITY.md).
