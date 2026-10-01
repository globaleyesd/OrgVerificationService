"""Cost by service over the last N days, day by day, for the cost dashboard.

Uses the same meters and prices as the calculator (costs.estimate), split into whole UTC days and grouped into
the services a person recognises (the app server, the AI model, the stored documents, ...). Costs are counted from
when this project first recorded anything, so days before it existed are not charged for an address or a disk
that did not exist yet.

Pure functions: the endpoint in main.py reads the meters and the live status of each service and passes them in.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .costs import NOT_INCLUDED, Usage, estimate

HEARTBEATS_DAILY_SQL = ("SELECT date_trunc('day', at AT TIME ZONE 'UTC'), count(*) FROM usage_heartbeat "
                        "WHERE at >= %(start)s AND at < %(end)s GROUP BY 1")
TOKENS_DAILY_SQL = ("SELECT date_trunc('day', at AT TIME ZONE 'UTC'), model, coalesce(sum(input_tokens),0), coalesce(sum(output_tokens),0) "
                    "FROM llm_usage WHERE at >= %(start)s AND at < %(end)s GROUP BY 1, 2")
DOCUMENTS_SQL = "SELECT created_at, size_bytes FROM documents WHERE created_at < %(end)s"
FIRST_RECORD_SQL = ("SELECT least((SELECT min(at) FROM usage_heartbeat), (SELECT min(created_at) FROM documents), "
                    "(SELECT min(at) FROM llm_usage))")


@dataclass
class Meters:
    """What the meters recorded in the window. Day keys are UTC midnights."""
    beats_by_day: dict            # day -> heartbeat count
    tokens_by_day: dict           # day -> {model: (input, output)}
    documents: list               # [(created_at, size_bytes)]
    first_record: datetime | None


# The services shown on the dashboard, in a fixed order. `lines` are the calculator's line keys each one owns
# ("ai:local" and "ai:hosted" are resolved per model by its price: a model priced at zero runs on your own hardware).
SERVICES = [
    {"key": "server", "label": "App server", "what": "Answers questions and serves the pages (the api container; on AWS, the EC2 instance)", "lines": ["compute"]},
    {"key": "database", "label": "Database", "what": "Postgres with pgvector: documents, passages and usage records", "lines": []},
    {"key": "local_ai", "label": "Local AI model", "what": "Ollama on this machine's GPU", "lines": ["ai:local"]},
    {"key": "hosted_ai", "label": "Hosted AI", "what": "Anthropic API or Amazon Bedrock, paid per question", "lines": ["ai:hosted"]},
    {"key": "storage", "label": "Document storage", "what": "The original uploaded files (local folder; on AWS, S3)", "lines": ["storage"]},
    {"key": "disk", "label": "Server disk", "what": "The server's own disk (AWS EBS)", "lines": ["disk"]},
    {"key": "public_ip", "label": "Public IP address", "what": "The server's fixed internet address (AWS Elastic IP)", "lines": ["public_ip"]},
]


def read_meters(conn, start: datetime, end: datetime) -> Meters:
    p = {"start": start, "end": end}
    with conn.cursor() as cur:
        cur.execute(HEARTBEATS_DAILY_SQL, p)
        beats = {_utc(d): int(n) for d, n in cur.fetchall()}
        cur.execute(TOKENS_DAILY_SQL, p)
        tokens: dict = {}
        for d, model, i, o in cur.fetchall():
            tokens.setdefault(_utc(d), {})[model] = (int(i), int(o))
        cur.execute(DOCUMENTS_SQL, p)
        docs = [(_utc(c), float(s)) for c, s in cur.fetchall()]
        cur.execute(FIRST_RECORD_SQL)
        first = cur.fetchone()[0]
    return Meters(beats, tokens, docs, _utc(first) if first else None)


def _utc(d: datetime) -> datetime:
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)


def _service_of(line_key: str, llm_prices: dict) -> str | None:
    if line_key.startswith("ai:"):
        price = llm_prices.get(line_key[3:])
        return "local_ai" if price is not None and not any(price) else "hosted_ai"
    if line_key == "ai":
        return None   # the calculator's "no AI usage" placeholder
    for s in SERVICES:
        if line_key in s["lines"]:
            return s["key"]
    return None


def by_service(meters: Meters, start: datetime, end: datetime, *, rates, llm_prices: dict, root_volume_gb: float,
               heartbeat_seconds: int, status: dict) -> dict:
    """Daily cost per service from `start` to `end`, plus each service's total and live status.

    status: service key -> {"state": running|idle|stopped|aws_only|off, "detail": "..."} from live checks.
    A service is listed when it cost at least a cent in the window or is running now ("costing" says which).
    """
    # Models served by Ollama ("name:tag") run on your own hardware: free even when nobody added them to the price list
    # (for example models tried once and removed), so they don't raise "no price configured" warnings.
    llm_prices = dict(llm_prices)
    for models in meters.tokens_by_day.values():
        for model in models:
            if model not in llm_prices and ":" in model:
                llm_prices[model] = [0, 0]
    since = max(start, meters.first_record) if meters.first_record else end
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    daily, totals, warnings = [], {s["key"]: 0.0 for s in SERVICES}, set()
    while day < end:
        a, b = max(day, since), min(day + timedelta(days=1), end)
        usd = {}
        if b > a:
            usage = Usage(uptime_seconds=meters.beats_by_day.get(day, 0) * heartbeat_seconds,
                          llm_tokens=meters.tokens_by_day.get(day, {}),
                          storage_bytes=sum(size for created, size in meters.documents if created < b))
            est = estimate(a, b, usage, rates, llm_prices, root_volume_gb)
            warnings.update(est["warnings"])
            for line in est["lines"]:
                key = _service_of(line["key"], llm_prices)
                if key and line["usd"]:
                    usd[key] = round(usd.get(key, 0.0) + line["usd"], 6)
                    totals[key] += line["usd"]
        daily.append({"date": day.date().isoformat(), "usd": usd, "total_usd": round(sum(usd.values()), 6)})
        day += timedelta(days=1)

    services = []
    for s in SERVICES:
        st = status.get(s["key"], {"state": "off", "detail": ""})
        costing = totals[s["key"]] >= 0.005   # rounds to at least one cent
        if not costing and st["state"] not in ("running", "idle"):
            continue
        services.append({"key": s["key"], "label": s["label"], "what": s["what"], "state": st["state"],
                         "detail": st.get("detail", ""), "usd": round(totals[s["key"]], 4), "costing": costing})
    services.sort(key=lambda s: (not s["costing"], -s["usd"]))
    return {
        "start": start.isoformat(), "end": end.isoformat(), "days": len(daily),
        "tracking_since": meters.first_record.isoformat() if meters.first_record else None,
        "total_usd": round(sum(totals.values()), 4),
        "running_now": sum(1 for s in services if s["state"] in ("running", "idle")),
        "services": services,
        "series": [{"key": s["key"], "label": s["label"]} for s in sorted(services, key=lambda s: [x["key"] for x in SERVICES].index(s["key"])) if s["costing"]],
        "daily": daily,
        "warnings": sorted(warnings), "not_included": NOT_INCLUDED,
        "calculated_at": datetime.now(timezone.utc).isoformat(),
        "basis": "estimate from this project's own usage meters, priced from config.yaml",
    }
