"""The three agents (Radar, Collector, Reply handler) and the code that checks their work."""

from __future__ import annotations

import json
import re
from datetime import date, timedelta

from . import llm, prompts, safety, store
from .config import MAX_CLIENTS_PER_RADAR_RUN, ZELLE_EMAIL
from .ledger import balance
from .radar import client_stats

INACTIVE_AFTER_DAYS = 180


def _client_payments(state: dict, cid: str) -> list[dict]:
    return [p for p in state["payments"] if p["client_id"] == cid]


def ledger_for(state: dict, cid: str) -> dict | None:
    start = state["settings"].get("ledger_start")
    if not start:
        return None
    return balance(state["clients"][cid], state["lessons"], _client_payments(state, cid), start, store.as_of(state))


def all_stats(state: dict) -> dict[str, dict]:
    today = store.as_of(state)
    return {cid: client_stats(_client_payments(state, cid), today) for cid in state["clients"]}


# ------------------------------------------------------------------ Radar
def run_radar(state: dict, run_id: str, prefilter: bool = True) -> dict:
    """Classify every recently active client. With prefilter, obvious on-track clients skip the model."""
    stats = all_stats(state)
    rows, skipped = [], {"inactive": 0, "on_track_prefiltered": 0, "marked_inactive_by_owner": 0}
    code_results = {}
    for cid, s in stats.items():
        c = state["clients"][cid]
        if not c.get("active", True):
            skipped["marked_inactive_by_owner"] += 1
            continue
        if not s.get("n_payments"):
            continue
        led = ledger_for(state, cid)
        owes = bool(led and led["owed"] > 0)
        if s["days_since_last"] > INACTIVE_AFTER_DAYS and not owes:
            skipped["inactive"] += 1
            continue
        if prefilter and s["signal"] == "on_track" and not owes:
            skipped["on_track_prefiltered"] += 1
            code_results[cid] = {"client_id": cid, "status": "on_track", "priority": 1,
                                 "reason": f"Paid {s['days_since_last']} days ago; usual gap about {s['typical_gap_days']} days.",
                                 "suggest_reminder": False, "decided_by": "code"}
            continue
        row = {"client_id": cid, **{k: v for k, v in s.items()}}
        if led and led["lessons_charged"]:
            row["ledger"] = {"owed": led["owed"], "unpaid_lessons": len(led["unpaid_lessons"]), "days_overdue": led["days_overdue"]}
        rows.append(row)
    rows.sort(key=lambda r: (-(r.get("ledger", {}).get("owed", 0)), -(r.get("gap_ratio") or 0)))
    rows = rows[:MAX_CLIENTS_PER_RADAR_RUN]

    model_results, summary, usage, calls = {}, "Nobody needs attention right now.", None, []
    if rows:
        user = f"Today is {store.as_of(state)}. Clients:\n" + json.dumps(rows, separators=(",", ":"))
        res = llm.run_agent(agent="radar", run_id=run_id, instructions=prompts.RADAR, user_input=user,
                            schema=prompts.RADAR_SCHEMA)
        usage, calls = res["usage"], res["calls"]
        summary = res["output"]["summary"]
        sent = {r["client_id"] for r in rows}
        for item in res["output"]["clients"]:
            if item["client_id"] in sent and item["client_id"] not in model_results:   # drop invented IDs
                item["decided_by"] = "model"
                model_results[item["client_id"]] = item
        for r in rows:  # anything the model skipped falls back to the rule-based signal
            if r["client_id"] not in model_results:
                model_results[r["client_id"]] = {"client_id": r["client_id"], "status": r["signal"], "priority": 3,
                                                 "reason": "Model skipped this client; showing the rule-based signal.",
                                                 "suggest_reminder": r["signal"] == "maybe_forgot", "decided_by": "code"}
        for item in model_results.values():  # an owed balance always wins, whatever the model said
            led = next((r.get("ledger") for r in rows if r["client_id"] == item["client_id"]), None)
            if led and led["owed"] > 0:
                item.update(status="owes_balance", suggest_reminder=True, priority=max(item["priority"], 4))

    results = {**code_results, **model_results}
    # The model only knows IDs; put names back in locally so the owner reads "Priya", not "C001".
    names = {cid: c["name"] for cid, c in state["clients"].items()}
    unid = lambda s: re.sub(r"\bC\d{3}\b", lambda m: names.get(m.group(0), m.group(0)), s)
    summary = unid(summary)
    for item in results.values():
        item["reason"] = unid(item["reason"])
    radar = {"run_id": run_id, "as_of": store.as_of(state), "summary": summary, "results": results,
             "sent_to_model": len(rows), "skipped": skipped, "usage": usage, "calls": calls, "prefilter": prefilter}
    state["radar"] = radar
    return radar


# ------------------------------------------------------------------ Collector
def _reminder_history(state: dict, cid: str) -> tuple[int, int | None]:
    sent = [r for r in state["reminders"] if r["client_id"] == cid and r.get("sent")]
    if not sent:
        return 0, None
    last = max(r["sent_on"] for r in sent)
    return len(sent), (date.fromisoformat(store.as_of(state)) - date.fromisoformat(last)).days


def _allowed_tones(n_sent: int) -> list[str]:
    return ["friendly"] if n_sent == 0 else ["friendly", "firm"] if n_sent == 1 else ["firm", "final"]


def _lesson_list(unpaid: list[dict]) -> str:
    def day(iso: str) -> str:
        d = date.fromisoformat(iso)
        return f"{d:%b} {d.day}"

    def join(xs: list[str]) -> str:
        return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]

    parts = []
    for kind, one, many in (("private", "private lesson", "private lessons"), ("group", "group class", "group classes")):
        days = [day(l["date"]) for l in unpaid if l["kind"] == kind]
        if days:
            parts.append(f"{one if len(days) == 1 else many} on {join(days)}")
    return "your " + " and ".join(parts) if parts else "your recent lessons"


def _template(mode: str) -> str:
    if mode == "balance":
        return ("Hi {first_name}! Quick reminder about {amount} for {lesson_list}. "
                "You can Zelle it to {zelle_email}. Thank you! 💃")
    return ("Hi {first_name}! I don't think I've seen a Zelle from you for the last few lessons. "
            "Could you check when you have a sec? It goes to {zelle_email}. Thanks!")


def draft_reminders(state: dict, run_id: str, client_ids: list[str]) -> dict:
    items, context = [], {}
    radar = (state.get("radar") or {}).get("results", {})
    for cid in client_ids:
        if cid not in state["clients"]:
            continue
        c = state["clients"][cid]
        led = ledger_for(state, cid)
        mode = "balance" if led and led["owed"] > 0 else "checkin"
        n_sent, last_days = _reminder_history(state, cid)
        item = {"client_id": cid, "mode": mode, "language": c.get("language") or "English",
                "allowed_tones": _allowed_tones(n_sent), "reminders_sent_before": n_sent,
                "days_since_last_reminder": last_days}
        if mode == "balance":
            item.update(days_overdue=led["days_overdue"], unpaid_lessons=len(led["unpaid_lessons"]))
        elif cid in radar:
            item["radar_reason"] = radar[cid]["reason"]
        items.append(item)
        context[cid] = {"mode": mode, "ledger": led, "allowed": item["allowed_tones"]}

    if not items:
        return {"drafts": [], "usage": None}

    def ask(batch: list[dict]) -> tuple[dict, dict]:
        user = "Write one reminder per item:\n" + json.dumps(batch, ensure_ascii=False, separators=(",", ":"))
        res = llm.run_agent(agent="collector", run_id=run_id, instructions=prompts.COLLECTOR, user_input=user,
                            schema=prompts.COLLECTOR_SCHEMA)
        return {d["client_id"]: d for d in res["output"]["drafts"]}, res

    got, res = ask(items)
    usages, all_calls = [res["usage"]], list(res["calls"])

    def problems_for(cid: str, d: dict | None) -> list[str]:
        if not d:
            return ["no draft returned"]
        p = safety.verify_draft(d["message"], context[cid]["mode"])
        if d["tone"] not in context[cid]["allowed"]:
            p.append(f"tone {d['tone']} not allowed yet; use one of {context[cid]['allowed']}")
        return p

    failed = {cid: problems_for(cid, got.get(cid)) for cid in context}
    failed = {k: v for k, v in failed.items() if v}
    first_try_failures = dict(failed)
    if failed:  # one retry, only for the drafts that failed, with the exact problems listed
        retry = [dict(next(i for i in items if i["client_id"] == cid), fix_these_problems=p) for cid, p in failed.items()]
        got2, res2 = ask(retry)
        usages.append(res2["usage"])
        all_calls += res2["calls"]
        for cid in list(failed):
            if not problems_for(cid, got2.get(cid)):
                got[cid] = got2[cid]
                failed.pop(cid)

    drafts = []
    for cid, ctx in context.items():
        c = state["clients"][cid]
        if cid in failed:
            d = {"client_id": cid, "tone": ctx["allowed"][0], "message": _template(ctx["mode"])}
            source = "template (model draft failed checks twice)"
        else:
            d = got[cid]
            source = "model, passed checks" + (" after one retry" if cid in first_try_failures else "")
        led = ctx["ledger"]
        values = {"first_name": (c["name"].split() or ["there"])[0], "zelle_email": ZELLE_EMAIL,
                  "amount": f"${led['owed']:,.0f}" if led and led["owed"] else "",
                  "lesson_list": _lesson_list(led["unpaid_lessons"]) if led else ""}
        drafts.append({"client_id": cid, "name": c["name"], "phone": c.get("phone", ""), "email": c.get("email", ""),
                       "mode": ctx["mode"], "tone": d["tone"], "template": d["message"],
                       "text": safety.fill(d["message"], values), "source": source,
                       "first_try_problems": first_try_failures.get(cid, [])})
    total = {k: sum((u or {}).get(k, 0) for u in usages) for k in ("input_tokens", "cached_tokens", "output_tokens",
                                                                    "reasoning_tokens", "model_calls", "latency_ms")}
    total["cost_usd"] = round(sum((u or {}).get("cost_usd", 0) for u in usages), 6)
    return {"drafts": drafts, "usage": total, "calls": all_calls}


# ------------------------------------------------------------------ Reply handler
DOLLARS = re.compile(r"\$\s?([\d,]+(?:\.\d{1,2})?)")


def handle_reply(state: dict, run_id: str, cid: str, text: str) -> dict:
    c = state["clients"][cid]
    screen = safety.screen_untrusted(text)
    masked = safety.mask_text(text, c, [o for k, o in state["clients"].items() if k != cid])
    names = {k: v["name"] for k, v in state["clients"].items()}
    unid = lambda s: re.sub(r"\bC\d{3}\b", lambda m: names.get(m.group(0), m.group(0)), s)
    today = date.fromisoformat(store.as_of(state))
    seen_amounts: set[float] = set()
    actions: list[dict] = []

    def get_balance() -> dict:
        led = ledger_for(state, cid)
        if not led:
            return {"note": "The owner has not started logging lessons, so no exact balance is known."}
        seen_amounts.update({led["owed"], led["paid"], led["charged"], *[l["price"] for l in led["unpaid_lessons"]]})
        return {"owed": led["owed"], "unpaid_lessons": [{"date": l["date"], "kind": l["kind"], "price": l["price"]}
                                                       for l in led["unpaid_lessons"]], "credit": led["credit"]}

    def recent_payments(days: int) -> dict:
        days = max(1, min(int(days), 120))
        since = (today - timedelta(days=days)).isoformat()
        rows = [{"date": p["date"], "amount": p["amount"]} for p in _client_payments(state, cid) if p["date"] >= since]
        seen_amounts.update(r["amount"] for r in rows)
        return {"days": days, "payments": rows}

    def find_payments_by_amount(amount: float, days: int) -> dict:
        days = max(1, min(int(days), 60))
        since = (today - timedelta(days=days)).isoformat()
        rows = [{"date": p["date"], "amount": p["amount"], "linked_to_this_client": p["client_id"] == cid}
                for p in state["payments"] if abs(p["amount"] - float(amount)) < 0.01 and p["date"] >= since]
        seen_amounts.update(r["amount"] for r in rows)
        return {"matches": rows[-10:], "count": len(rows)}

    def record_promise(pay_by: str, note: str) -> dict:
        try:
            d = date.fromisoformat(pay_by)
        except ValueError:
            return {"error": "pay_by must be YYYY-MM-DD"}
        if not (today <= d <= today + timedelta(days=60)):
            return {"error": "promise date must be within the next 60 days"}
        rec = {"id": store.new_id("P"), "client_id": cid, "pay_by": pay_by, "note": unid(note[:200]), "recorded_on": today.isoformat()}
        state["promises"].append(rec)
        actions.append({"type": "promise", **rec})
        return {"saved": True, "pay_by": pay_by}

    def flag_for_owner(reason: str) -> dict:
        rec = {"id": store.new_id("F"), "client_id": cid, "reason": unid(reason[:300]), "created_on": today.isoformat()}
        state["flags"].append(rec)
        actions.append({"type": "flag", **rec})
        return {"flagged": True}

    tools = {"get_balance": get_balance, "recent_payments": recent_payments,
             "find_payments_by_amount": find_payments_by_amount, "record_promise": record_promise,
             "flag_for_owner": flag_for_owner}

    def blocked(why: str) -> dict:
        flag_for_owner(f"Reply looked like an attempt to manipulate the assistant ({why}). Nothing was changed.")
        out = {"intent": "suspicious", "is_suspicious": True, "needs_owner": True,
               "owner_summary": f"This reply contains instructions aimed at the assistant, so it was blocked {why}. "
                                "No balance was changed and no other client's data was shared.",
               "suggested_reply": "Hi {first_name}! I'll double-check my records and get back to you.",
               "suggested_reply_filled": safety.fill("Hi {first_name}! I'll double-check my records and get back to you.",
                                                     {"first_name": (c["name"].split() or ["there"])[0]})}
        return {"result": out, "screen": screen, "masked_input": masked, "trace": [], "actions": actions,
                "code_checks": [f"Blocked {why}."], "usage": None, "calls": [], "blocked_by": why}

    if screen["suspicious"]:  # obvious attacks never reach the model: zero tokens, zero risk
        return blocked("by the code pre-screen before reaching the AI")

    user = (f"Today is {today.isoformat()}. Client {cid} replied to a payment reminder.\n"
            f"Pre-screen by code: {json.dumps(screen)}\n{safety.wrap_untrusted(masked)}")
    try:
        res = llm.run_agent(agent="reply", run_id=run_id, instructions=prompts.REPLY, user_input=user,
                            schema=prompts.REPLY_SCHEMA, tools=prompts.REPLY_TOOLS, tool_impls=tools)
    except llm.ContentFiltered:
        return blocked("by the model provider's safety filter")
    out = res["output"]
    out["owner_summary"] = unid(out["owner_summary"])

    # Code-side checks on what the model said.
    checks = []
    if screen["suspicious"] and not out["is_suspicious"]:
        out["is_suspicious"], out["needs_owner"] = True, True
        checks.append("Code pre-screen found injection patterns; marked suspicious even though the model did not.")
    stated = {float(a.replace(",", "")) for a in DOLLARS.findall(out["suggested_reply"])}
    unsupported = sorted(a for a in stated if not any(abs(a - s) < 0.01 for s in seen_amounts))
    if unsupported:
        out["suggested_reply"] = DOLLARS.sub("[amount removed]", out["suggested_reply"])
        out["needs_owner"] = True
        checks.append(f"Removed dollar amounts no tool returned: {unsupported}")
    first = (c["name"].split() or ["there"])[0]
    out["suggested_reply_filled"] = safety.fill(out["suggested_reply"], {"first_name": first, "zelle_email": ZELLE_EMAIL})
    return {"result": out, "screen": screen, "masked_input": masked, "trace": res["trace"], "actions": actions,
            "code_checks": checks, "usage": res["usage"], "calls": res["calls"]}
