"""One wrapper for every model call: Portkey client, tool loop, token logging and budget limits."""

from __future__ import annotations

import json
import os
import time
from collections import deque
from datetime import date, datetime
from typing import Callable

from openai import BadRequestError, OpenAI

from . import config

_recent_calls: deque[float] = deque()


class BudgetExceeded(RuntimeError):
    pass


class NoKey(RuntimeError):
    pass


class ContentFiltered(RuntimeError):
    """The model provider's own safety filter rejected the prompt (e.g. a jailbreak attempt)."""


def _client() -> OpenAI:
    key = config.portkey_key()
    if not key:
        raise NoKey("PORTKEY_API_KEY is missing. Put it in a .env file in this folder or the folder above it.")
    return OpenAI(
        api_key=key,
        base_url=config.PORTKEY_BASE_URL,
        default_headers={"x-portkey-api-key": key, "x-portkey-provider": "openai"},
        timeout=120,
        max_retries=1,
    )


def cost_usd(inp: int, cached: int, out: int) -> float:
    p = config.PRICE_PER_M
    return round(((inp - cached) * p["input"] + cached * p["cached_input"] + out * p["output"]) / 1_000_000, 6)


def tokens_used_today() -> int:
    if not config.USAGE_LOG.exists():
        return 0
    today, total = date.today().isoformat(), 0
    for line in config.USAGE_LOG.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("ts", "").startswith(today):
            total += rec.get("input_tokens", 0) + rec.get("output_tokens", 0)
    return total


def estimate_call_tokens(*texts: str) -> int:
    """Worst case for one call: input estimated generously (1 token per 3 characters) plus the full output limit."""
    return sum(len(t) for t in texts) // 3 + config.MAX_OUTPUT_TOKENS


def _check_budget(reserve: int) -> None:
    """A strict cap: a call runs only if it fits in today's budget even at its maximum possible size."""
    now = time.time()
    while _recent_calls and now - _recent_calls[0] > 60:
        _recent_calls.popleft()
    if len(_recent_calls) >= config.MAX_CALLS_PER_MINUTE:
        raise BudgetExceeded(f"Rate limit: more than {config.MAX_CALLS_PER_MINUTE} model calls in a minute. Wait a moment.")
    used = tokens_used_today()
    if used + reserve > config.DAILY_TOKEN_BUDGET:
        raise BudgetExceeded(f"Daily token budget reached: {used:,} of {config.DAILY_TOKEN_BUDGET:,} used, and the next call "
                             f"could need up to {reserve:,}. Try again tomorrow.")
    _recent_calls.append(now)


def _log(agent: str, run_id: str, usage, ms: int, gateway_cache: str = "unknown") -> dict:
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    cached = getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", 0) or 0
    reasoning = getattr(getattr(usage, "output_tokens_details", None), "reasoning_tokens", 0) or 0
    rec = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "run_id": run_id,
        "agent": agent,
        "model": config.MODEL_NAME,
        "input_tokens": inp,
        "cached_tokens": cached,
        "output_tokens": out,
        "reasoning_tokens": reasoning,
        "cost_usd": cost_usd(inp, cached, out),
        "latency_ms": ms,
        "gateway_cache": gateway_cache,
    }
    config.LOGS.mkdir(exist_ok=True)
    with config.USAGE_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def run_agent(
    *,
    agent: str,
    run_id: str,
    instructions: str,
    user_input: str,
    schema: dict,
    tools: list[dict] | None = None,
    tool_impls: dict[str, Callable[..., dict]] | None = None,
) -> dict:
    """Run one agent to a JSON answer that matches `schema`. Returns {'output', 'usage', 'trace'}."""
    client = None
    items: list = [{"role": "user", "content": user_input}]
    trace, usage_recs = [], []
    fixed = instructions + json.dumps(schema) + json.dumps(tools or [])
    for step in range(config.MAX_TOOL_STEPS):
        _check_budget(estimate_call_tokens(fixed, user_input, *(str(i) for i in items[1:])))
        client = client or _client()
        t0 = time.time()
        kwargs = dict(
            model=config.MODEL_NAME,
            instructions=instructions,
            input=items,
            max_output_tokens=config.MAX_OUTPUT_TOKENS,
            text={"format": {"type": "json_schema", "name": f"{agent}_output", "schema": schema, "strict": True}},
            prompt_cache_key=f"encore-{agent}",
        )
        if tools:
            kwargs["tools"] = tools
        if os.getenv("PORTKEY_FORCE_REFRESH"):  # measurement mode: skip the gateway's response cache
            kwargs["extra_headers"] = {"x-portkey-cache-force-refresh": "true"}
        try:
            raw = client.responses.with_raw_response.create(**kwargs)
        except BadRequestError as e:
            if "content_filter" in str(e) or "content management policy" in str(e):
                raise ContentFiltered("The model provider's safety filter rejected this text.") from e
            raise
        resp = raw.parse()
        gateway_cache = raw.headers.get("x-portkey-cache-status", "unknown")
        usage_recs.append(_log(agent, run_id, resp.usage, int((time.time() - t0) * 1000), gateway_cache))

        calls = [o for o in resp.output if getattr(o, "type", "") == "function_call"]
        if not calls:
            text = resp.output_text or ""
            try:
                out = json.loads(text)
            except json.JSONDecodeError as e:
                raise RuntimeError(f"{agent} returned text that is not valid JSON") from e
            return {"output": out, "usage": _sum(usage_recs), "calls": usage_recs, "trace": trace}

        items += resp.output
        for c in calls:
            impl = (tool_impls or {}).get(c.name)
            try:
                args = json.loads(c.arguments or "{}")
                result = impl(**args) if impl else {"error": f"unknown tool {c.name}"}
            except Exception as e:  # a bad tool call must not crash the run
                args, result = {"raw": c.arguments}, {"error": str(e)}
            trace.append({"step": step + 1, "tool": c.name, "args": args, "result": result})
            items.append({"type": "function_call_output", "call_id": c.call_id, "output": json.dumps(result)})
    raise BudgetExceeded(f"{agent} stopped after {config.MAX_TOOL_STEPS} steps without finishing (loop guard).")


def _sum(recs: list[dict]) -> dict:
    keys = ("input_tokens", "cached_tokens", "output_tokens", "reasoning_tokens", "cost_usd", "latency_ms")
    total = {k: sum(r[k] for r in recs) for k in keys}
    total["cost_usd"] = round(total["cost_usd"], 6)
    total["model_calls"] = len(recs)
    return total


def read_usage(limit: int = 200) -> list[dict]:
    if not config.USAGE_LOG.exists():
        return []
    lines = config.USAGE_LOG.read_text(encoding="utf-8").splitlines()[-limit:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out
