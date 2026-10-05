# Commands

Run everything from the project folder (the one containing `config.yaml`). The VS Code terminal (Terminal > New Terminal) opens there. Where Windows differs, both forms are shown. `kb-verifier` is the default `project_name`; use yours if you changed it.

## One-time setup
- Install: Python 3.12+, Docker Desktop (includes buildx), AWS CLI v2, VS Code
- Install the one Python package the tools need:
  - `pip install PyYAML`
- Create your private files (both are git-ignored):
  - Mac/Linux: `cp secrets.example.yaml secrets.local.yaml` then edit it
  - Windows: `copy secrets.example.yaml secrets.local.yaml` then edit it
  - Optional display name and colours: copy `config.local.example.yaml` to `config.local.yaml` the same way
- Create the local helper files (random database password, `creds/` and `data/` folders, empty `config.local.yaml` if missing):
  - `python scripts/init_local.py`

## Run it locally
- Start (Docker):
  - Mac / Windows: `docker compose up --build`
  - Linux (so the `creds/` and `data/` folders are writable): `APP_UID=$(id -u) APP_GID=$(id -g) docker compose up --build`
- Open in a browser:
  - `http://localhost:8000/` (launcher)
  - `http://localhost:8000/ask` (role-checked)
  - `http://localhost:8000/add` (any signed-in user; every page sends you to `/signin` first)
- Turn the service on: it starts **off**; enter the switch password on the offline screen
- Load the demo documents (synthetic, in `samples/`; the first run downloads the search model):
  - `docker compose run --rm api python -m app.cli ingest /srv/samples/employee --level employee`
  - `docker compose run --rm api python -m app.cli ingest /srv/samples/super`
  - Your own files or folders work the same way: `... ingest /srv/samples/PATH [--level employee]` (mount them under `samples/`)
  - List: `... list-documents`. Mark one readable by Employees: `... set-document-level DOC_ID employee`
  - After a change to how files are read or split: `docker compose exec api python -m app.cli reindex` (reads every stored original again; ids and levels stay)
- Measure answer quality and speed on your own question set (`data/eval_questions.json`, see the script's header):
  - `docker compose exec -T api python - --model qwen3:4b < scripts/eval_answers.py`
- Check the whole local stack end to end (service on, demo users created, samples loaded): `python scripts/smoke_local.py`
- Step-by-step walkthrough: [RUNNING_LOCALLY.md](RUNNING_LOCALLY.md)
- Create the two demo users (random passwords, shown **once**; save them in a password manager):
  - `python -m app.cli create-demo-users`
  - This writes to the local `creds/` folder, which the container reads too. Or inside Docker: `docker compose run --rm api python -m app.cli create-demo-users`
- Sign in at `http://localhost:8000/ask` (in demo mode you just click a user; no password to type):
  - **Eileen** = Super (sees everything and the Costs button)
  - **AllMinusEileen** = Employee (everyone-but-Eileen view)
  - Use Chrome or Firefox locally (Safari rejects secure cookies on plain http://localhost)
- Preview the screens with fake sample data (no server, no Docker):
  - Open `app/web/ask.html?mock=1` or `app/web/add.html?mock=1` in a browser
  - Or with the server running: `http://localhost:8000/ask?mock=1`
  - Extras: add `&as=employee` for the Employee view, `&state=off` to start on the offline screen
- Follow the logs: `docker compose logs -f api`
- Stop: `docker compose down`
- Stop and delete the local database: `docker compose down -v`
- Turn the service on or off (the pages have no button for it any more; asks for the switch password):
  - `docker compose exec api python -m app.cli service on` (or `off`, or `status`)
- Set your own switch password (needed before `ui.demo_mode: false`):
  - `python -m app.cli set-switch-password`  (or `docker compose run --rm api python -m app.cli set-switch-password`)
- Manage users:
  - List (names and roles only): `python -m app.cli list-users`
  - Set a password yourself (prompted, not echoed): `python -m app.cli set-user-password eileen`
  - Replace both demo users with new random passwords: `python -m app.cli create-demo-users --reset`

## Test
- All tests (no Docker or AWS needed): `python -m unittest discover -s tests -t .`
- One file: `python -m unittest tests.test_costs`
- One test: `python -m unittest tests.test_service_switch.SwitchTests.test_lockout_after_repeated_failures_then_expires`

## Check nothing secret can be committed (before every push)
- Confirm the private files are ignored (each line should print the rule that ignores it):
  - `git check-ignore -v creds/service_switch.json secrets.local.yaml config.local.yaml data/db_password`
- List what git would track that looks private (should show only the two `*.example.yaml` files):
  - Mac/Linux: `git ls-files | grep -Ei "creds|secret|local\.yaml|\.env|\.pem"`
  - Windows PowerShell: `git ls-files | Select-String -Pattern "creds|secret|local\.yaml|\.env|\.pem"`
- See everything git is ignoring: `git status --ignored`
- If a secret was ever committed: **rotate it**. Deleting the commit is not enough

## Push to AWS
- Sign in (pick one):
  - SSO: `aws configure sso`, later `aws sso login --profile YOUR_PROFILE`
  - Then tell the tools which profile: PowerShell `$env:AWS_PROFILE="YOUR_PROFILE"`, cmd `set AWS_PROFILE=YOUR_PROFILE`, Mac/Linux `export AWS_PROFILE=YOUR_PROFILE`
  - Or put `profile:` (or keys) in `secrets.local.yaml`
- Confirm who you are: `aws sts get-caller-identity`
- Check the template before the first deploy:
  - `pip install cfn-lint`
  - `cfn-lint deploy/cloudformation/stack.yaml`
- Preview every command without running anything: `python deploy/deploy.py all --dry-run`
- Deploy everything (stack, image, page, config, server update): `python deploy/deploy.py all`
- Or one step at a time:
  - `python deploy/deploy.py stack` (create or update the AWS resources)
  - `python deploy/deploy.py image` (build the arm64 image and push it to ECR)
  - `python deploy/deploy.py ui` (upload the web pages to the S3 page bucket)
  - `python deploy/deploy.py config` (upload the non-secret config files only)
  - `python deploy/deploy.py update` (tell the server to pull the new image and restart)
  - `python deploy/deploy.py outputs` (print the site address, bucket names, instance id)
- Use the newest base server image (this **replaces the server**): `python deploy/deploy.py stack --new-ami`
- VS Code instead of the terminal: Terminal > Run Task, then choose
  - "Deploy: preview all steps (dry run)"
  - "Deploy: everything"
  - "Deploy: AWS stack only" / "build and push image" / "upload web page" / "upload non-secret config" / "update server"
  - "Deploy: show site address and names"
  - "Local: run tests" / "Local: start with Docker"

### Day to day
- Changed only the pages (`app/web`): `python deploy/deploy.py ui`
- Changed the code: `python deploy/deploy.py image` then `python deploy/deploy.py update`
- Changed `config.yaml` or `config.local.yaml`: `python deploy/deploy.py config` then `python deploy/deploy.py update`
- Changed the template: `python deploy/deploy.py stack`

## Turn the AWS service off and on
- Normal way: use the **Service on / off** pill (or the offline screen) in the page and enter the switch password. Off stops the server; On starts it (about a minute)
- Check the state without a password: open `https://YOUR_SITE/control/status`
- Emergency (needs AWS admin rights, bypasses the password):
  - Stop: `aws ec2 stop-instances --instance-ids INSTANCE_ID`
  - Start: `aws ec2 start-instances --instance-ids INSTANCE_ID`
- Look at the switch function's logs: `aws logs tail /aws/lambda/kb-verifier-control --follow`

## Work on the AWS server
- Open a shell (no SSH port is open; needs the AWS Session Manager plugin):
  - `aws ssm start-session --target INSTANCE_ID` (get `INSTANCE_ID` from `python deploy/deploy.py outputs`)
- Then, inside that shell:
  - `cd /opt/app`
  - Status: `sudo docker compose -f docker-compose.aws.yml --env-file .env ps`
  - Logs: `sudo docker compose -f docker-compose.aws.yml --env-file .env logs -f api`
  - Pull the latest and restart: `sudo /opt/app/update.sh`
  - Restart only the API: `sudo docker compose -f docker-compose.aws.yml --env-file .env restart api`
  - Create the two demo users in the protected S3 credentials bucket (random passwords printed once):
    `sudo docker compose -f docker-compose.aws.yml --env-file .env run --rm api python -m app.cli create-demo-users`
  - Set a user's password yourself (prompted, not echoed): `sudo docker compose -f docker-compose.aws.yml --env-file .env run --rm api python -m app.cli set-user-password eileen`
  - List users: `sudo docker compose -f docker-compose.aws.yml --env-file .env run --rm api python -m app.cli list-users`
  - Save the Anthropic API key (asked, not shown; then restart the API): `sudo docker compose -f docker-compose.aws.yml --env-file .env run --rm api python -m app.cli set-llm-key`
  - Set the real switch password: `sudo docker compose -f docker-compose.aws.yml --env-file .env run --rm api python -m app.cli set-switch-password`
  - If you have turned on Session Manager session logging, the one-time passwords printed by `create-demo-users` will be in those logs. Use `set-user-password` (prompted, not echoed) instead
- Check the result of a remote update: `aws ssm list-command-invocations --instance-id INSTANCE_ID --details`

## Going from demo to real use
- In `config.yaml` or `config.local.yaml`: `ui.demo_mode: false` and `ui.allow_mock: false`
- Set the switch password on the server (the command above). Until a non-demo password exists the app refuses to start
- Re-deploy the config: `python deploy/deploy.py config` then `python deploy/deploy.py update`

## Tear down
- Empty the page bucket: in the S3 console choose the bucket (name from `outputs`) > **Empty** (it is versioned, so the console button is the reliable way)
- Delete the images: `aws ecr batch-delete-image --repository-name kb-verifier --image-ids imageTag=latest`
- Delete the stack:
  - `aws cloudformation delete-stack --stack-name kb-verifier`
  - `aws cloudformation wait stack-delete-complete --stack-name kb-verifier`
- The documents bucket and the credentials bucket are kept on purpose. To remove them by hand: empty and delete the documents bucket in the console; for the credentials bucket first edit its bucket policy (it denies everyone but the server's role), then empty and delete it
