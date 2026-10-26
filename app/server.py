"""FastAPI backend: serves the one-page app and a small JSON API."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import agents, config, llm, store
from .ledger import lesson_price

app = FastAPI(title="Encore payment desk")


@app.exception_handler(llm.BudgetExceeded)
async def _budget(_, exc):
    return JSONResponse({"error": str(exc), "kind": "budget"}, status_code=429)


@app.exception_handler(llm.NoKey)
async def _nokey(_, exc):
    return JSONResponse({"error": str(exc), "kind": "no_key"}, status_code=400)


def _run_id() -> str:
    return uuid.uuid4().hex[:10]


def _ws(ws: str) -> dict:
    try:
        return store.load(ws)
    except ValueError as e:
        raise HTTPException(400, str(e))


def _view(ws: str, state: dict) -> dict:
    """Everything the page needs, computed fresh from the stored state."""
    stats = agents.all_stats(state)
    clients = []
    for cid, c in state["clients"].items():
        clients.append({**c, "stats": stats[cid], "ledger": agents.ledger_for(state, cid),
                        "reminders_sent": sum(1 for r in state["reminders"] if r["client_id"] == cid and r.get("sent")),
                        "promises": [p for p in state["promises"] if p["client_id"] == cid]})
    clients.sort(key=lambda c: c["stats"].get("last_payment") or "", reverse=True)
    as_of = store.as_of(state)
    month = as_of[:7]
    return {
        "workspace": ws,
        "as_of": as_of,
        "settings": state["settings"],
        "clients": clients,
        "lessons": sorted(state["lessons"], key=lambda l: l["date"], reverse=True),
        "radar": state.get("radar"),
        "reminders": state["reminders"][-50:],
        "promises": state["promises"],
        "flags": state["flags"],
        "imports": state["imports"],
        "families": store.family_suggestions(state),
        "kpis": {
            "owed_total": round(sum((c["ledger"] or {}).get("owed", 0) for c in clients), 2),
            "owing_clients": sum(1 for c in clients if (c["ledger"] or {}).get("owed", 0) > 0),
            "collected_this_month": round(sum(p["amount"] for p in state["payments"] if p["date"].startswith(month)), 2),
            "payments_total": len(state["payments"]),
            "clients_total": len(clients),
        },
        "prices": config.PRICES,
        "zelle_email": config.ZELLE_EMAIL,
        "studio": config.STUDIO_NAME,
    }


@app.get("/")
def index():
    return FileResponse(config.STATIC / "index.html")


@app.get("/api/status")
def status():
    return {"model": config.MODEL_NAME, "key": config.key_source(), "tokens_today": llm.tokens_used_today(),
            "daily_budget": config.DAILY_TOKEN_BUDGET, "workspaces": list(store.WORKSPACE_NAMES)}


@app.get("/api/state")
def get_state(ws: str = "sample"):
    return _view(ws, _ws(ws))


class ImportBody(BaseModel):
    ws: str
    csv_text: str = Field(max_length=5_000_000)
    filename: str = ""


@app.post("/api/import")
def import_bank(body: ImportBody):
    state = _ws(body.ws)
    try:
        summary = store.import_bank(state, body.csv_text, body.filename)
    except ValueError as e:
        raise HTTPException(400, str(e))
    store.save(body.ws, state)
    return {"summary": summary, "state": _view(body.ws, state)}


class ClientUpdate(BaseModel):
    ws: str
    client_id: str
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    language: str | None = None
    notes: str | None = None
    active: bool | None = None
    rate_private: float | None = None
    rate_group: float | None = None


@app.post("/api/clients/update")
def update_client(body: ClientUpdate):
    state = _ws(body.ws)
    c = state["clients"].get(body.client_id)
    if not c:
        raise HTTPException(404, "no such client")
    for f in ("name", "phone", "email", "language", "notes", "active"):
        v = getattr(body, f)
        if v is not None:
            c[f] = v.strip() if isinstance(v, str) else v
    for kind in ("private", "group"):
        v = getattr(body, f"rate_{kind}")
        if v is not None:
            c.setdefault("rates", {})[kind] = v if v > 0 else None
    store.save(body.ws, state)
    return _view(body.ws, state)


class MergeBody(BaseModel):
    ws: str
    into_id: str
    from_id: str


@app.post("/api/clients/merge")
def merge(body: MergeBody):
    state = _ws(body.ws)
    if body.into_id not in state["clients"] or body.from_id not in state["clients"]:
        raise HTTPException(404, "no such client")
    store.merge_clients(state, body.into_id, body.from_id)
    store.save(body.ws, state)
    return _view(body.ws, state)


class LessonBody(BaseModel):
    ws: str
    client_id: str
    date: str
    kind: str = Field(pattern="^(private|group)$")
    count: int = Field(default=1, ge=1, le=10)


@app.post("/api/lessons/add")
def add_lesson(body: LessonBody):
    state = _ws(body.ws)
    c = state["clients"].get(body.client_id)
    if not c:
        raise HTTPException(404, "no such client")
    try:
        date.fromisoformat(body.date)
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")
    for _ in range(body.count):
        state["lessons"].append({"id": store.new_id("L"), "client_id": body.client_id, "date": body.date,
                                 "kind": body.kind, "price": lesson_price(c, body.kind)})
    if not state["settings"].get("ledger_start") or body.date < state["settings"]["ledger_start"]:
        state["settings"]["ledger_start"] = body.date
    store.save(body.ws, state)
    return _view(body.ws, state)


class IdBody(BaseModel):
    ws: str
    id: str


@app.post("/api/lessons/delete")
def delete_lesson(body: IdBody):
    state = _ws(body.ws)
    state["lessons"] = [l for l in state["lessons"] if l["id"] != body.id]
    store.save(body.ws, state)
    return _view(body.ws, state)


class SettingsBody(BaseModel):
    ws: str
    ledger_start: str | None = None


@app.post("/api/settings")
def settings(body: SettingsBody):
    state = _ws(body.ws)
    state["settings"]["ledger_start"] = body.ledger_start or None
    store.save(body.ws, state)
    return _view(body.ws, state)


class WsBody(BaseModel):
    ws: str
    prefilter: bool = True


@app.post("/api/radar")
def radar(body: WsBody):
    state = _ws(body.ws)
    result = agents.run_radar(state, _run_id(), prefilter=body.prefilter)
    store.save(body.ws, state)
    return {"radar": result, "state": _view(body.ws, state)}


class DraftBody(BaseModel):
    ws: str
    client_ids: list[str] = Field(max_length=25)


@app.post("/api/reminders/draft")
def draft(body: DraftBody):
    state = _ws(body.ws)
    return agents.draft_reminders(state, _run_id(), body.client_ids)


class SentBody(BaseModel):
    ws: str
    client_id: str
    text: str
    tone: str
    channel: str


@app.post("/api/reminders/sent")
def mark_sent(body: SentBody):
    state = _ws(body.ws)
    state["reminders"].append({"id": store.new_id("R"), "client_id": body.client_id, "text": body.text[:1000],
                               "tone": body.tone, "channel": body.channel, "sent": True, "sent_on": store.as_of(state)})
    store.save(body.ws, state)
    return _view(body.ws, state)


class ReplyBody(BaseModel):
    ws: str
    client_id: str
    text: str = Field(min_length=1, max_length=4000)


@app.post("/api/replies")
def reply(body: ReplyBody):
    state = _ws(body.ws)
    if body.client_id not in state["clients"]:
        raise HTTPException(404, "no such client")
    out = agents.handle_reply(state, _run_id(), body.client_id, body.text)
    store.save(body.ws, state)
    out["state"] = _view(body.ws, state)
    return out


@app.post("/api/flags/resolve")
def resolve_flag(body: IdBody):
    state = _ws(body.ws)
    state["flags"] = [f for f in state["flags"] if f["id"] != body.id]
    state["promises"] = [p for p in state["promises"] if p["id"] != body.id]
    store.save(body.ws, state)
    return _view(body.ws, state)


@app.post("/api/reset")
def reset(body: WsBody):
    if body.ws != "sample":
        raise HTTPException(400, "Only the sample workspace can be reset from the app.")
    return _view(body.ws, store.reset(body.ws))


@app.get("/api/usage")
def usage():
    recs = llm.read_usage()
    runs: dict[str, dict] = {}
    for r in recs:
        run = runs.setdefault(r["run_id"], {"run_id": r["run_id"], "agents": [], "ts": r["ts"], "input_tokens": 0,
                                             "cached_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "cost_usd": 0.0})
        if r["agent"] not in run["agents"]:
            run["agents"].append(r["agent"])
        for k in ("input_tokens", "cached_tokens", "output_tokens", "reasoning_tokens", "cost_usd"):
            run[k] += r[k]
    for run in runs.values():
        run["cost_usd"] = round(run["cost_usd"], 6)
    return {"calls": recs[-60:][::-1], "runs": list(runs.values())[::-1][:30], "price_per_m": config.PRICE_PER_M,
            "tokens_today": llm.tokens_used_today(), "daily_budget": config.DAILY_TOKEN_BUDGET}
