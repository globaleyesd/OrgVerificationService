# Run the AI locally (no API key)

With `llm.provider: local` the answers come from an open model running on your own machine through
[Ollama](https://ollama.com), so there is no API key and no per-question fee. Everything else (search,
access levels, checked citations) works exactly the same: the model is only handed the passages the
signed-in role may read, and every citation is still checked against the source.

The model is **not trained on your documents** and does not need to be. New documents are searchable as
soon as they are added.

## Set up (once)
- In `config.local.yaml`:
  ```yaml
  llm:
    provider: local
    model_answer: qwen3:4b
    max_context_chunks: 4
  ```
- NVIDIA GPU (recommended): create a `.env` file next to `docker-compose.yml` (git-ignored) containing
  - Windows: `COMPOSE_FILE=docker-compose.yml;docker-compose.gpu.yml`
  - Mac/Linux: `COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml`
  - Without it the model runs on the CPU: it works, but each answer takes much longer
- **Turn on** in the Control Center (http://localhost:8700)
- Download the model (about 2.5 GB, kept in the `ollama` Docker volume):
  - `python scripts/containers.py exec ollama ollama pull qwen3:4b`
- Restart the app so it picks up the setting: `docker compose restart api`

## Choosing a model
Measured on a laptop RTX 2060 (6 GB) with 12 answerable and 2 unanswerable questions about one PDF,
4 passages per question, after the search and citation fixes:

| Model | Size | Correct | Wrong | Median answer time |
|---|---|---|---|---|
| `qwen3:4b` (default) | 2.5 GB | 12/12 | 0 | ~10 s |
| `qwen2.5:3b` | 1.9 GB | 11/12 | 0 | ~3-8 s |
| `gemma3:4b` | 3.3 GB | 11/12 | 0 | ~10 s |
| `phi4-mini` | 2.5 GB | 11/12 | 0 | ~9 s |

All four refused both questions the document does not answer.
- Pick a model that fits entirely in GPU memory (`ollama ps` should say `100% GPU`); `qwen2.5:7b` did not fit
  in 6 GB and was slower without being more accurate
- Fewer passages helped small models: 4 passages gave 11/12 where 8 gave 7/12 (`qwen2.5:3b`)
- The app always sends `think: false`, so reasoning models like Qwen3 answer directly instead of spending a
  minute or more thinking first
- To compare models on your own documents: write questions in `data/eval_questions.json` and run
  `python scripts/containers.py exec -T api python - --model MODEL < scripts/eval_answers.py` (see the script's header)
- To switch: pull the new model, set `llm.model_answer`, add it with `[0, 0]` under
  `costs.llm_prices_per_mtok` in `config.yaml`, then `docker compose restart api`

## Check it
- `python scripts/containers.py exec ollama ollama list` shows the downloaded models
- `python scripts/containers.py exec ollama ollama ps` shows whether the model is loaded and on the GPU or CPU
- Ask a question on `/ask`; the first one loads the model and takes a little longer

## Limits
- The small AWS server (`t4g.small`) cannot run a model. On AWS use `bedrock` (see [CONFIGURATION.md](CONFIGURATION.md))
- A 4B model is enough to demonstrate the flow; a larger model or a hosted one handles harder questions better
