"""The AI model behind one small interface. anthropic: the public API (the key is in secrets.local.yaml on your
machine, or in the private credentials store on the AWS server). bedrock: Amazon Bedrock through the server's
IAM role, so no API key is needed. local: an open model served by
Ollama on this machine (the `ollama` service in docker-compose.yml), so no key and no per-question fee."""
from __future__ import annotations

import hashlib
import json
import threading
import urllib.error
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass

import logging

from .config import Config, has_llm_key

log = logging.getLogger(__name__)


class LlmError(Exception):
    pass


class LlmNotConfigured(LlmError):
    """No API key is set, so questions are off. Everything else in the app still works."""


@dataclass
class LlmResult:
    text: str
    tokens_in: int
    tokens_out: int


class AnthropicClient:
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: str, timeout: float = 60, opener=urllib.request.urlopen):
        self._key, self.timeout, self.opener = api_key, timeout, opener

    def complete(self, system: str, user: str, model: str, max_tokens: int, schema: dict | None = None) -> LlmResult:
        body = json.dumps({"model": model, "max_tokens": max_tokens, "system": system,
                           "messages": [{"role": "user", "content": user}]}).encode()
        req = urllib.request.Request(self.URL, data=body, method="POST", headers={
            "x-api-key": self._key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        try:
            with self.opener(req, timeout=self.timeout) as r:
                j = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise LlmError(f"The AI service answered with HTTP {e.code}") from None   # never include the request (it holds the key)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            raise LlmError("Could not reach the AI service") from None
        text = "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text")
        u = j.get("usage", {})
        return LlmResult(text, int(u.get("input_tokens", 0)), int(u.get("output_tokens", 0)))


class OllamaClient:
    """Talks to Ollama's /api/chat. format=json makes small models return parseable JSON; temperature 0 keeps
    the exact-quote citations as literal as the model can manage."""

    def __init__(self, base_url: str, timeout: float = 300, opener=urllib.request.urlopen, schema: dict | None = None):
        self.url, self.timeout, self.opener = base_url.rstrip("/") + "/api/chat", timeout, opener
        self.schema = schema   # a JSON schema the reply must follow; None = any JSON

    def complete(self, system: str, user: str, model: str, max_tokens: int, schema: dict | None = None) -> LlmResult:
        body = json.dumps({"model": model, "stream": False, "format": schema or self.schema or "json", "think": False,   # no slow reasoning step (Qwen3 etc.)
                           "options": {"temperature": 0, "num_predict": max_tokens},
                           "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}).encode()
        req = urllib.request.Request(self.url, data=body, method="POST", headers={"content-type": "application/json"})
        try:
            with self.opener(req, timeout=self.timeout) as r:
                j = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise LlmError(f"The local model answered with HTTP {e.code} (is the model pulled? see docs/LOCAL_MODEL.md)") from None
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            raise LlmError("Could not reach the local model (is the ollama service running?)") from None
        return LlmResult(str((j.get("message") or {}).get("content", "")), int(j.get("prompt_eval_count") or 0), int(j.get("eval_count") or 0))


class BedrockClient:
    def __init__(self, client=None):
        if client is None:
            import boto3
            client = boto3.client("bedrock-runtime")
        self.client = client

    def complete(self, system: str, user: str, model: str, max_tokens: int, schema: dict | None = None) -> LlmResult:
        try:
            r = self.client.converse(modelId=model, system=[{"text": system}], messages=[{"role": "user", "content": [{"text": user}]}],
                                     inferenceConfig={"maxTokens": max_tokens})
            text = "".join(b.get("text", "") for b in r["output"]["message"]["content"])
            return LlmResult(text, int(r["usage"]["inputTokens"]), int(r["usage"]["outputTokens"]))
        except Exception as e:
            raise LlmError(f"Bedrock call failed ({type(e).__name__})") from None


class CachingLlm:
    """Reuses the reply to an identical request (cache_answers). The request holds the question and the exact passages
    the asker's role may read, so a reply never crosses access levels, and a new or relabelled document changes the
    passages and so misses the cache. A reused reply is recorded as 0 tokens."""

    def __init__(self, inner, size: int = 256):
        self.inner, self.size = inner, size
        self._items: OrderedDict[str, LlmResult] = OrderedDict()
        self._lock = threading.Lock()

    def complete(self, system: str, user: str, model: str, max_tokens: int, schema: dict | None = None) -> LlmResult:
        key = hashlib.sha256(json.dumps([system, user, model, max_tokens, schema]).encode()).hexdigest()
        with self._lock:
            hit = self._items.get(key)
            if hit is not None:
                self._items.move_to_end(key)
                return LlmResult(hit.text, 0, 0)
        res = self.inner.complete(system, user, model, max_tokens, schema=schema) if schema else self.inner.complete(system, user, model, max_tokens)
        with self._lock:
            self._items[key] = res
            while len(self._items) > self.size:
                self._items.popitem(last=False)
        return res


class NoKeyClient:
    def complete(self, system: str, user: str, model: str, max_tokens: int, schema: dict | None = None) -> LlmResult:
        raise LlmNotConfigured("no API key is set")


LLM_KEY_RECORD = "llm_api_key"


def use_stored_key(cfg: Config, store) -> None:
    """On the AWS server the Anthropic key is not in a file: `app.cli set-llm-key` saves it, on the server, in the
    private credentials store. Use it when secrets.local.yaml has none."""
    if cfg.llm.provider != "anthropic" or has_llm_key(cfg):
        return
    rec = store.read(LLM_KEY_RECORD)
    if rec and isinstance(rec.get("api_key"), str):
        cfg.secrets.llm.api_key = rec["api_key"]


def make_llm(cfg: Config):
    if cfg.llm.provider == "bedrock":
        client = BedrockClient()
    elif cfg.llm.provider == "local":
        from .answering import ANSWER_SCHEMA
        client = OllamaClient(cfg.llm.local_url, schema=ANSWER_SCHEMA)
    elif not has_llm_key(cfg):
        log.warning("No Anthropic API key (secrets.local.yaml, or app.cli set-llm-key on AWS): the app runs, but questions are turned off")
        return NoKeyClient()
    else:
        client = AnthropicClient(cfg.secrets.llm.api_key)
    return CachingLlm(client) if cfg.llm.cache_answers else client
