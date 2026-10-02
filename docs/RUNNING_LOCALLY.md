# Run it locally first, then push to AWS

## Before you start
- **Docker Desktop or Rancher Desktop** (choose in `config.local.yaml`, see [CONTAINERS.md](CONTAINERS.md)) and Python 3.12. Run `python scripts/containers.py check` to see what is missing. For answers, either run a free local model (see [LOCAL_MODEL.md](LOCAL_MODEL.md)) or use an **Anthropic API key**. With neither, the app runs and only asking questions on `/ask` is turned off. Local runs call the AI through the public API; the AWS setup uses Amazon Bedrock instead, so no key is ever stored there
- Internet on the first run: Docker images, and the search model (about 130 MB) which downloads the first time you add a document and is then kept in `data/models`

## Set up (once)
- `pip install -r requirements-dev.txt`
- Create your private files (both git-ignored):
  - Mac/Linux: `cp secrets.example.yaml secrets.local.yaml`; Windows: `copy secrets.example.yaml secrets.local.yaml`
  - Optional: edit `secrets.local.yaml` and put your key under `llm.api_key` (restart the app after adding it)
- `python scripts/init_local.py`
- Optional: copy `config.local.example.yaml` to `config.local.yaml` for your display name and colours

## Start
- `docker compose up --build` (the first build takes a few minutes)
  - Linux: `APP_UID=$(id -u) APP_GID=$(id -g) docker compose up --build`
- Open `http://localhost:8000/` in Chrome or Firefox (Safari rejects secure cookies on plain http://localhost)
- The service starts **off**: turn it on with `docker compose exec api python -m app.cli service on` and the demo switch password

## Load the demo data
- Create the two demo users (random passwords, shown once; the demo sign-in page doesn't need them):
  - `docker compose run --rm api python -m app.cli create-demo-users`
- Read the synthetic sample documents in (the first one downloads the search model, give it a minute):
  - `docker compose run --rm api python -m app.cli ingest /srv/samples/employee --level employee`
  - `docker compose run --rm api python -m app.cli ingest /srv/samples/super`
- Check what is stored: `docker compose run --rm api python -m app.cli list-documents`

## Walk through the demo
- `/ask`: click **Eileen**, ask "When does the reporting platform license expire?" and click the `[1]` markers to see the highlighted passage
- Sign out, click **AllMinusEileen**, ask the same question: fewer sources, no level tags, and "some sources were not available at your access level"
- `/add`: type a note or drop a file (no sign-in needed). It starts at the **top** level, so AllMinusEileen cannot see it yet
- `/review` (Eileen only): choose who can read each document. Mark your new note "Employee and above", then ask AllMinusEileen about it
- **Costs** (Eileen only): shows the estimate for any time range, now including real AI usage

## Check it automatically
- With the stack running, the service on, the demo users created and the samples loaded: `python scripts/smoke_local.py`
  - It signs in as both users, adds a note as an anonymous visitor, checks the Employee cannot see it, relabels it, and checks the Employee now can
  - Every line prints PASS or FAIL. **A FAIL is what to send me**
- Unit tests (no Docker): `python -m unittest discover -s tests -t .`

## If something goes wrong
- **Sign-in does nothing:** use Chrome or Firefox, not Safari
- **"The AI service is unavailable":** check the key in `secrets.local.yaml`, your network, and `docker compose logs -f api`
- **First document or first question is slow:** the search model is downloading
- **"No text found in this PDF":** it is a scan; OCR is off
- **File refused:** only pdf, docx, txt, md, csv, xlsx, pptx, html and eml are read. Save old .doc/.xls files as docx/xlsx first
- **Database errors on first start:** wait a few seconds and retry; the API waits for the database to report healthy

## What is real and what is not yet
- **Real:** reading the supported file types, splitting and embedding them, the access rule (a role's AI prompt never contains text above its level), citations that are checked against the source, the `/review` relabel, AI usage recorded for the cost calculator, the daily AI cap
- **Sample only:** the diagram button and "Add a correction" suggestion appear only in `?mock=1`. Real answers can mention a disagreement in words but don't draw it
- **Unused settings:** `llm.model_extract` is reserved for a later feature
- **Unproven until your first run:** the Postgres code (`PgRepository`) has never run against a database. The smoke script is its test
- Uploads are processed while you wait, so a big file takes seconds

## Then push to AWS with the AWS CLI
- Nothing in AWS changes until you run the deploy commands. Order:
  - `aws sts get-caller-identity` (confirms who you are)
  - In `config.local.yaml` set `llm.provider: bedrock` and put **Bedrock model ids** in `llm.model_answer` (they differ from the public API names; add their prices under `costs.llm_prices_per_mtok`). Enable those models in the Bedrock console for your account and region
  - `python deploy/deploy.py all --dry-run`, then `cfn-lint deploy/cloudformation/stack.yaml`
  - `python deploy/deploy.py all`
  - Open the printed address, turn the service on, then on the server create the demo users (see [COMMANDS.md](COMMANDS.md))
  - On AWS there is no samples folder: add documents on the **Add** page, then mark their levels on **Review**
- Full details: [../deploy/README.md](../deploy/README.md) and [COMMANDS.md](COMMANDS.md)
