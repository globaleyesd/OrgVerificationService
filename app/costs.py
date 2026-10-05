"""Cost estimate for a time range. Pure function: give it measured usage and prices, get a breakdown.

This is an ESTIMATE built from our own meters (server uptime, AI tokens, stored files),
priced with the rates in config.yaml. It is available immediately. AWS's own bill can
differ and arrives up to about a day late. See docs/SERVICE_SWITCH_AND_COSTS.md.

Included : server compute, public IP, disk, document storage, AI usage.
Not included: CloudFront and data transfer, request charges, tax, and anything outside this project.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

HOURS_PER_MONTH = 730.0   # AWS convention for monthly-priced items
GIB = 1024 ** 3

NOT_INCLUDED = [
    "CloudFront and data transfer",
    "Request charges (S3, CloudFront)",
    "Container registry storage",
    "Taxes",
]


class RangeError(ValueError):
    pass


@dataclass
class Usage:
    uptime_seconds: float = 0.0
    llm_tokens: dict = field(default_factory=dict)   # model -> (input_tokens, output_tokens)
    storage_bytes: float = 0.0                       # document bytes stored during the period


def parse_range(start: str, end: str, max_days: int, now: datetime | None = None) -> tuple[datetime, datetime]:
    """Parse ISO timestamps (UTC if no zone). End may not be in the future by more than a minute."""
    try:
        s, e = (datetime.fromisoformat(x.replace("Z", "+00:00")) for x in (start, end))
    except (ValueError, AttributeError):
        raise RangeError("Start and end must be valid dates and times")
    s, e = (d if d.tzinfo else d.replace(tzinfo=timezone.utc) for d in (s, e))
    now = now or datetime.now(timezone.utc)
    if e <= s:
        raise RangeError("End must be after start")
    if (e - s).total_seconds() > max_days * 86400:
        raise RangeError(f"The range can be at most {max_days} days")
    if (e - now).total_seconds() > 60:
        e = now   # never bill the future
        if e <= s:
            raise RangeError("Start is in the future")
    return s.astimezone(timezone.utc), e.astimezone(timezone.utc)


def _line(key: str, label: str, detail: str, usd: float) -> dict:
    return {"key": key, "label": label, "detail": detail, "usd": round(usd, 4)}


def estimate(start: datetime, end: datetime, usage: Usage, rates, llm_prices: dict, root_volume_gb: float) -> dict:
    period_hours = (end - start).total_seconds() / 3600.0
    up_hours = min(usage.uptime_seconds / 3600.0, period_hours)
    lines = [
        _line("compute", "Server (compute)", f"{up_hours:.1f} h running", up_hours * rates.instance_hourly_usd),
        _line("public_ip", "Public IP address", f"{period_hours:.1f} h", period_hours * rates.elastic_ip_hourly_usd),
        _line("disk", "Server disk", f"{root_volume_gb:g} GB for {period_hours:.1f} h",
              root_volume_gb * rates.ebs_gb_month_usd * period_hours / HOURS_PER_MONTH),
    ]
    gb = usage.storage_bytes / GIB
    lines.append(_line("storage", "Document storage", f"{gb:.3f} GB for {period_hours:.1f} h",
                       gb * rates.s3_gb_month_usd * period_hours / HOURS_PER_MONTH))
    warnings = []
    for model in sorted(usage.llm_tokens):
        tin, tout = usage.llm_tokens[model]
        price = llm_prices.get(model)
        if price is None:
            warnings.append(f"No price configured for model '{model}', so its usage is not costed")
            usd = 0.0
        else:
            usd = tin / 1e6 * price[0] + tout / 1e6 * price[1]
        lines.append(_line(f"ai:{model}", f"AI usage ({model})", f"{int(tin):,} in / {int(tout):,} out tokens", usd))
    if not usage.llm_tokens:
        lines.append(_line("ai", "AI usage", "no usage recorded", 0.0))
    total = round(sum(l["usd"] for l in lines), 4)
    return {
        "start": start.isoformat(), "end": end.isoformat(),
        "period_hours": round(period_hours, 2),
        "lines": lines, "total_usd": total,
        "warnings": warnings, "not_included": NOT_INCLUDED,
        "calculated_at": datetime.now(timezone.utc).isoformat(),
        "basis": "estimate from this project's own usage meters, priced from config.yaml",
    }


# Names of models that are paid per token (Anthropic API, Amazon Bedrock and its regional profiles). Anything else
# without a price is taken to be a model run on your own hardware (Ollama).
HOSTED_MODEL = re.compile(r"^(claude|anthropic\.|(us|eu|apac|global)\.|amazon\.|meta\.|mistral\.|cohere\.|ai21\.|deepseek\.|openai\.)")


def ai_usage_line(model: str, tokens_in: int, tokens_out: int, prices: dict, kind: str) -> str | None:
    """The AI_USAGE log line for one AI call (None when nothing was used, e.g. a cached answer). The Control Center
    reads these from the logs to show the Claude API cost live; the database keeps its own record as before."""
    if not (tokens_in or tokens_out):
        return None
    m = ai_costs({model: (int(tokens_in), int(tokens_out))}, prices)["models"][0]
    return "AI_USAGE " + json.dumps({"model": model, "input_tokens": m["input_tokens"], "output_tokens": m["output_tokens"],
                                     "usd": m["usd"], "local": m["local"], "priced": m["priced"], "kind": kind},
                                    separators=(",", ":"))


def ai_costs(llm_tokens: dict, prices: dict) -> dict:
    """AI (LLM) usage priced per model: what a test run of the dev deployment cost. A model served by Ollama
    ("name:tag", or priced at zero) runs on your own hardware and costs nothing; an unpriced hosted model is listed
    with priced=False so it is never shown as free by mistake."""
    models, total = [], 0.0
    for model in sorted(llm_tokens):
        tin, tout = llm_tokens[model]
        price = prices.get(model)
        local = (price is not None and not any(price)) or (price is None and not HOSTED_MODEL.match(model))
        usd = 0.0 if local or price is None else tin / 1e6 * price[0] + tout / 1e6 * price[1]
        total += usd
        models.append({"model": model, "input_tokens": int(tin), "output_tokens": int(tout), "usd": round(usd, 4),
                       "local": local, "priced": local or price is not None})
    return {"models": models, "total_usd": round(total, 4)}
