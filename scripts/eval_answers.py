"""Measure answer quality and speed on a fixed set of questions, so a change can be judged by numbers.

Run it inside the api container (the scripts folder is not in the image, so it is piped in):
    docker compose exec -T api python - [options] < scripts/eval_answers.py

Questions live in data/eval_questions.json (git-ignored, because they describe your documents):
    [{"q": "Who discovered brain waves?", "expect": ["berger"]},
     {"q": "What is the capital of France?", "expect": []}]
"expect" lists acceptable answers; any one counts. Letters and digits are compared, so "8 Hz – 12 Hz" matches
"8hz12hz". An empty list means the documents do not answer it and the app should say so.

Options:
    --model NAME     local model to use (default: llm.model_answer)
    --chunks N       passages sent to the model (default: llm.max_context_chunks)
    --no-keyword     vector search only (no keyword search)
    --no-dedupe      keep repeated passages
    --no-schema      plain JSON mode instead of the enforced reply schema
    --strict-quotes  quote check without the stray-space tolerance
    --label TEXT     name for this run in the summary line
Only the local provider (Ollama) is supported. Answers are not cached and nothing is written except AI usage at 0 tokens cost.
"""
import argparse
import dataclasses
import json
import re
import statistics
import sys
import time

sys.path.insert(0, "/srv")
from app import answering  # noqa: E402
from app.config import load_config  # noqa: E402
from app.llm import OllamaClient  # noqa: E402
from app.rbac import allowed_levels  # noqa: E402
from app.services import build_services  # noqa: E402


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def strict_locate(quote, text):
    """The quote check before stray spaces inside words were tolerated."""
    q = (quote or "").strip()
    if len(q) < 3:
        return None
    i = text.find(q)
    if i >= 0:
        return i, i + len(q)
    flat = " ".join(text.lower().split())
    return (0, 1) if " ".join(q.lower().split()) in flat else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model"); ap.add_argument("--chunks", type=int); ap.add_argument("--label", default="")
    ap.add_argument("--questions", default="/srv/data/eval_questions.json")
    for flag in ("--no-keyword", "--no-dedupe", "--no-schema", "--strict-quotes"):
        ap.add_argument(flag, action="store_true")
    a = ap.parse_args()

    cfg = load_config("/srv/config.yaml", "/run/secrets/app_secrets", require_secrets=False, local_path="/srv/config.local.yaml")
    llm_cfg = dataclasses.replace(cfg.llm, model_answer=a.model or cfg.llm.model_answer,
                                  max_context_chunks=a.chunks or cfg.llm.max_context_chunks)
    cfg = dataclasses.replace(cfg, llm=llm_cfg)
    sv = build_services(cfg)
    sv.embedder.embed_query("load the search model")
    if a.no_dedupe:
        answering.distinct = lambda hits: hits
    if a.strict_quotes:
        answering.locate = strict_locate

    repo = sv.repo
    if a.no_keyword:
        class VectorOnly:
            def __getattr__(self, name):
                return getattr(sv.repo, name)

            def search(self, embedding, allowed, top_k, min_score, text=""):
                return sv.repo.search(embedding, allowed, top_k, min_score)
        repo = VectorOnly()

    client = OllamaClient(cfg.llm.local_url, schema=None if a.no_schema else answering.ANSWER_SCHEMA)
    seen_prompts = []

    class Recording:
        def complete(self, system, user, model, max_tokens):
            seen_prompts.append(user)
            return client.complete(system, user, model, max_tokens)

    client.complete("Reply with OK.", "ok", cfg.llm.model_answer, 1)   # load the model first so timings compare fairly
    questions = json.load(open(a.questions, encoding="utf-8"))
    role = cfg.clearance.levels[-1]
    rows, times = [], []
    for item in questions:
        seen_prompts.clear()
        t = time.time()
        r = answering.answer_question(question=item["q"], history=[], role=role, cfg=cfg, repo=repo, embedder=sv.embedder, llm=Recording())
        dt = time.time() - t
        times.append(dt)
        expect = [norm(e) for e in item["expect"]]
        answered = bool(r["citations"])
        in_passages = bool(expect) and bool(seen_prompts) and any(e in norm(seen_prompts[0]) for e in expect)
        if not expect:
            verdict = "OK (refused)" if not answered else "WRONG (answered)"
        elif answered and any(e in norm(r["answer"]) for e in expect):
            verdict = "CORRECT"
        elif answered:
            verdict = "WRONG"
        else:
            verdict = "no answer"
        rows.append((verdict, in_passages, bool(expect)))
        print(f"{dt:5.1f}s  {verdict:<16} {'found' if in_passages else '-----' if expect else '     '}  {item['q']}")
        print(f"        -> {r['answer'][:160]}")

    answerable = [r for r in rows if r[2]]
    correct = sum(r[0] == "CORRECT" for r in answerable)
    wrong = sum(r[0].startswith("WRONG") for r in rows)
    refused_ok = sum(r[0] == "OK (refused)" for r in rows)
    negatives = len(rows) - len(answerable)
    found = sum(r[1] for r in answerable)
    print(f"\nSUMMARY {a.label or cfg.llm.model_answer}: correct {correct}/{len(answerable)}, wrong {wrong}, "
          f"refused correctly {refused_ok}/{negatives}, answer in passages {found}/{len(answerable)}, "
          f"median {statistics.median(times):.1f}s, mean {statistics.mean(times):.1f}s")


main()
