# Configuration guide

Settings come from three files, merged in this order (later wins):

| File | Contains | Committed to git? |
|---|---|---|
| `config.yaml` | Non-secret settings with neutral defaults | Yes |
| `config.local.yaml` | Private or machine-specific overrides (display name, colours, tweaks) | **Never** (git-ignored, docker-ignored) |
| `secrets.local.yaml` | API and AWS keys | **Never** (git-ignored, docker-ignored) |

Password records live in the `creds/` folder locally (never committed) or in a protected S3 bucket on AWS. Typos are caught: unknown setting names stop the app with a clear message. 💰 marks options chosen mainly to keep the bill low. In `config.local.yaml`, dictionaries merge and lists are replaced as a whole; passwords and keys are refused there.

## Quick recipes

| I want to... | Change |
|---|---|
| Use my own name and colours without touching the public file | Copy `config.local.example.yaml` to `config.local.yaml` and set `branding.*` |
| Cut the AWS bill | `deployment.schedule.enabled: true`; `service_switch.auto_off_hours: 8` |
| Add a role / clearance level | Add a name to `clearance.levels` (lowest first); add it to `ui.ask_roles` if it may use Ask |
| Stop a role from using Ask | Remove it from `ui.ask_roles` (`/add` stays open to everyone) |
| Allow another file type | Add its extension to `ingestion.allowed_extensions` (a parser must exist for it) |
| Stop audio leaving the browser | `speech.mode: off` or `local` |
| Limit AI spending | Lower `llm.daily_token_cap` and `llm.max_context_chunks` |
| Make the cost calculator accurate | Update `costs.rates` and `costs.llm_prices_per_mtok` to current prices |
| Use a bigger server | `deployment.instance_type: t4g.medium` |
| Create the two demo accounts | `python -m app.cli create-demo-users` (see COMMANDS.md) |
| Go from demo to real use | `ui.demo_mode: false` and `ui.allow_mock: false`, then set the switch password (see SERVICE_SWITCH_AND_COSTS.md) |
| Host the pages on S3 | `server.serve_ui: false` (the AWS compose file's container already only serves the API) |

## config.yaml reference

### Top level
| Setting | Default | Meaning |
|---|---|---|
| `project_name` | `kb-verifier` | Base name for containers, buckets and AWS tags. Lowercase letters, digits, hyphens. |

### `branding`
| Setting | Default | Meaning |
|---|---|---|
| `app_name` | `Knowledge Verifier` | Shown in the header and browser tab. 1-60 characters. |
| `theme.accent` | `#1F7A3D` | Green for buttons, links and active states. This is a **placeholder shade**: replace it with the exact green from your logo. |
| `theme.accent_text` | `#F9F6EE` | Text on top of the accent colour. |
| `theme.background` | `#EFEADC` | Page background (a deeper bone). |
| `theme.surface` | `#F9F6EE` | Panels, header, chat bubbles: bone white. |
| `theme.text` | `#000000` | Main text: darkest black. |
| `theme.muted` | `#4A4A40` | Secondary text. |
| `theme.line` | `#CFC8B4` | Borders. |
| `theme.highlight` | `#F2DE7A` | Highlight behind the matching text in a source. |

All colours must be `#RRGGBB`; anything else is rejected (the values are used in the page's styles, so this also blocks style injection). There is no dark theme: the palette is fixed.

### `server`
| Setting | Default | Meaning |
|---|---|---|
| `serve_ui` | `true` | The server also serves the web page (local runs). Set `false` when the page lives on S3. |

### `credentials`
| Setting | Default | Meaning |
|---|---|---|
| `backend` | `file` | `file` = local folder. `s3` = private server-only S3 bucket (the AWS compose file sets it through the environment). `secrets_manager` is planned and not built. |
| `path` | `creds` | The folder for `file` (git-ignored, docker-ignored, owner-only permissions). |
| `s3_bucket` | `""` | For `s3`. Leave empty on AWS: the server is given the bucket name through its environment (`APP_CREDENTIALS_S3_BUCKET`). |
| `s3_prefix` | `""` | For `s3`. Optional folder inside the bucket, such as `creds/`. |

What is stored there (salted hashes and the cookie-signing key) is described in [SERVICE_SWITCH_AND_COSTS.md](SERVICE_SWITCH_AND_COSTS.md).

### `service_switch`
| Setting | Default | Meaning |
|---|---|---|
| `mode` | `local` | `local`: the app has its own on/off gate. `aws`: the AWS control function is the only switch and starts/stops the server. The AWS compose file sets this; don't edit it. |
| `auto_off_hours` | `0` | 💰 Local mode only. Turn the service off automatically after this many hours. `0` = never. On AWS use `deployment.schedule`. |
| `max_failed_attempts` | `5` | Wrong passwords before a lockout (also passed to the AWS control function). |
| `lockout_minutes` | `10` | Lockout length (global, not per visitor). |
| `state_path` | `data/service_state.json` | Where on/off is remembered. Missing or damaged = off. |

### `costs`
| Setting | Default | Meaning |
|---|---|---|
| `heartbeat_seconds` | `300` | How often the running server records that it is up (measures compute hours). `0` = off. |
| `max_range_days` | `366` | Longest range the calculator accepts. |
| `rates.instance_hourly_usd` | `0.0168` | Server price per hour running. |
| `rates.elastic_ip_hourly_usd` | `0.005` | Public IP per hour, always billed. |
| `rates.ebs_gb_month_usd` | `0.08` | Disk per GB-month. |
| `rates.s3_gb_month_usd` | `0.023` | Document storage per GB-month. |
| `llm_prices_per_mtok` | two models | `[input, output]` dollars per million tokens, per model name. Verify against current price lists. |

### `containers` (set it per machine in config.local.yaml; see [CONTAINERS.md](CONTAINERS.md))
| Key | Default | What it does |
|---|---|---|
| `engine` | `docker-desktop` | `docker-desktop` or `rancher-desktop`: which tool `scripts/containers.py` and the image build use |
| `rancher_runtime` | `moby` | Rancher Desktop only: `moby` (the usual `docker` commands) or `containerd` (`nerdctl`). Match Rancher Desktop's Preferences > Container Engine |
| `gpu` | `auto` | `auto`, `on` or `off`: NVIDIA GPU for the local AI model. `auto` = on with Docker Desktop when `nvidia-smi` exists, off on Rancher Desktop |

### `deployment` (used by deploy scripts only)
| Setting | Default | Meaning |
|---|---|---|
| `region` | `us-east-1` | AWS region. 💰 Prices differ; check data-residency rules first. |
| `instance_type` | `t4g.small` | 💰 Smallest ARM size with 2 GiB memory. The template allows t4g.micro, small, medium. |
| `use_spot` | `false` | Not wired into the template yet; ignored. |
| `root_volume_gb` | `20` | Server disk size. Also used by the cost calculator. |
| `allowed_ingress_cidrs` | `["0.0.0.0/0"]` | Not used by the AWS template (only CloudFront can reach the server). Reserved for directly exposed setups. |
| `domain` | `""` | Reserved for a custom domain (not used yet). |
| `schedule.enabled` | `false` | 💰 Stop the server outside working hours (Mon-Fri). |
| `schedule.timezone` / `start_hour` / `stop_hour` | `UTC` / `8` / `18` | Hours are 0-23. |
| `budget_limit_usd` | `40` | Monthly AWS Budget alert (alert only). The e-mail goes in `deploy/params.local.json`. |

### `clearance` (these names are also the user roles)
| Setting | Default | Meaning |
|---|---|---|
| `levels` | `[employee, super]` | Lowest first. A user reads items at their own level or below. Lowercase letters, digits, underscores. |
| `default_upload_level` | `highest` | Level every **new** upload starts at. Uploaders never choose it: anyone can add, not everyone can read. `highest` fails safe until a reviewer relabels. Only the top level can relabel. |

Unknown levels are always denied.

### `ingestion`
| Setting | Default | Meaning |
|---|---|---|
| `allowed_extensions` | pdf, docx, txt, md, csv, xlsx, pptx, html, eml | Only types that have a reader (listing another is a start-up error). Old .doc/.xls/.ppt/.rtf must be saved in the newer format first. |
| `max_documents` | `100` | Total documents allowed. Protects disk and cost, because `/add` is open to anyone. Further uploads get `409`. |
| `max_file_mb` | `25` | Maximum upload size. |
| `chunk_size_chars` / `chunk_overlap_chars` | `1200` / `150` | Overlap must be smaller than the size. |
| `ocr_enabled` | `false` | 💰 Text recognition for scans; extra CPU and memory. |

### `embeddings`
| Setting | Default | Meaning |
|---|---|---|
| `provider` | `local` | `local`: a small model on this machine through fastembed (💰 no per-call fee; about 130 MB downloaded on first use, kept in `data/models`). `hashing`: a word-matching stand-in with no download, for tests and offline demos only. |
| `model` | `BAAI/bge-small-en-v1.5` | Embedding model. |
| `dimensions` | `384` | Must match the model. Changing it means re-ingesting everything. |

### `llm`
| Setting | Default | Meaning |
|---|---|---|
| `provider` | `anthropic` | `anthropic` (uses `llm.api_key` in `secrets.local.yaml`; without it questions are off, everything else works), `local` (an open model run by the `ollama` service, no key: see [LOCAL_MODEL.md](LOCAL_MODEL.md)) or `bedrock` (uses the server's AWS role: no key anywhere). The AWS setup is designed for `bedrock`. |
| `local_url` | `http://ollama:11434` | Where the local model is served, used only when `provider` is `local`. The default is the `ollama` service in `docker-compose.yml`. |
| `model_extract` | `claude-haiku-4-5-20251001` | Reserved for fact extraction (not used yet). Bedrock uses different model ids. |
| `model_answer` | `claude-sonnet-5-5` | Writes answers. On Bedrock use the Bedrock model id and add its price under `costs.llm_prices_per_mtok`. |
| `max_output_tokens` | `400` | 💰 Cap on answer length. Also the main speed lever for a local model, which writes about 60 tokens a second. Too low cuts the reply off mid-JSON and the answer is lost. |
| `max_context_chunks` | `8` | 💰 Chunks sent per question. |
| `daily_token_cap` | `500000` | 💰 Tokens per day across users. `0` = no cap. |
| `cache_answers` | `true` | Reuse the reply to a repeated question when the passages found are identical, so it comes back instantly and costs nothing. Kept in memory and cleared on restart. A new or relabelled document changes the passages, so it never serves a stale or higher-level answer. |

### `retrieval`, `speech`, `auth`, `audit`
| Setting | Default | Meaning |
|---|---|---|
| `retrieval.top_k` / `min_score` | `20` / `0.25` | Candidates fetched; similarity floor (0-1). |
| `speech.mode` | `browser` | `browser` (free; some browsers send audio to their vendor), `local`, or `off`. |
| `auth.session_minutes` / `password_min_length` / `max_failed_logins` / `lockout_minutes` | `480` / `12` / `5` / `15` | Sign-in rules. Session length is the cookie lifetime; the length applies to new passwords (users and the switch password); wrong passwords lock that **username** for the lockout time. |
| `audit.enabled` / `retain_days` | `true` / `365` | Record who asked what; `0` keeps forever. |

### `ui` (see [UI.md](UI.md))
| Setting | Default | Meaning |
|---|---|---|
| `demo_mode` | `true` | `true`: **passwordless demo sign-in** (the sign-in page shows the two demo users as buttons), the demo service-switch password may be used, and `allow_mock` is permitted. `false`: users sign in with a password, and a non-demo switch password is required (the app refuses to start otherwise). **While `true`, anyone who can reach the site with the service on can become Eileen (Super). Set `false` before real use.** |
| `allow_mock` | `true` | Allows `?mock=1` sample answers. Must be `false` when `demo_mode` is `false`. |
| `ask_roles` | `[super, employee]` | Roles allowed on `/ask` and the ask API. Names must be clearance levels. Not signed in = no access. `/add` has **no** sign-in or role check by design. |

### `storage` and `database`
| Setting | Default | Meaning |
|---|---|---|
| `storage.backend` | `local` | 💰 `local` = server disk; `s3` = durable and cheap, recommended for real use. |
| `storage.local_path` / `s3_bucket` | `/srv/data/files` / `""` | Where files go; empty bucket name = derived from `project_name`. |
| `database.host` / `port` / `name` / `user` | `db` / `5432` / `app` / `app` | 💰 Postgres runs as a container on the same server. The password is generated on first boot and read from a file; it is never in any config. |

## secrets.local.yaml reference
Create it with `cp secrets.example.yaml secrets.local.yaml`. It is only needed for local runs that use `llm.provider: anthropic`, or if you want deploy keys in a file instead of a profile.

| Setting | Needed when | Meaning |
|---|---|---|
| `aws.access_key_id`, `secret_access_key`, `session_token` | Deploying without a profile | Used by `deploy/deploy.py` on your machine only (passed through environment variables, never written or printed). |
| `aws.profile` | Alternative to keys | Named AWS CLI profile. |
| `llm.api_key` | `llm.provider: anthropic`, optional | Local runs. Blank: the app starts and `/ask` answers "Questions are turned off". On AWS the key is not in this file: `app.cli set-llm-key` saves it on the server in the credentials bucket. |
| `admin.username`, `admin.password` | Later | Optional. Leave blank to have one generated when sign-in is added. |

### Where secrets live
- Your machine: `secrets.local.yaml` and `creds/`.
- On AWS: password hashes, user accounts and the cookie-signing key live in the locked-down credentials bucket, created by the server itself.
- Never: git, Docker images, CloudFormation, user-data, logs, or anything the deploy script uploads. Tests check the ignore files, the Dockerfile, the template and the deploy script's upload allow-list.
- If a secret is ever committed by mistake, **rotate it**. Deleting the commit is not enough.
