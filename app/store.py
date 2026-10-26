"""One JSON file per workspace: 'sample' (shipped demo data) and 'real' (your data, git-ignored)."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import date
from pathlib import Path

from .bank import display_name, normalize_name, parse_chase_csv
from .config import SAMPLE_DIR, WORKSPACES

WORKSPACE_NAMES = ("sample", "real")
_lock = threading.Lock()


def _path(ws: str) -> Path:
    if ws not in WORKSPACE_NAMES:
        raise ValueError(f"Unknown workspace {ws!r}")
    return WORKSPACES / ws / "state.json"


def _empty() -> dict:
    return {
        "clients": {},
        "payments": [],
        "lessons": [],
        "reminders": [],
        "promises": [],
        "flags": [],
        "radar": None,
        "imports": [],
        "packages": [],
        "settings": {"ledger_start": None, "as_of": None},
    }


def load(ws: str) -> dict:
    p = _path(ws)
    if not p.exists():
        state = _seed_sample() if ws == "sample" else _empty()
        save(ws, state)
        return state
    state = json.loads(p.read_text(encoding="utf-8"))
    state.setdefault("packages", [])
    return state


def save(ws: str, state: dict) -> None:
    p = _path(ws)
    p.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
        tmp.replace(p)


def reset(ws: str) -> dict:
    state = _seed_sample() if ws == "sample" else _empty()
    save(ws, state)
    return state


def as_of(state: dict) -> str:
    return state["settings"].get("as_of") or date.today().isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


def _next_client_id(state: dict) -> str:
    n = len(state["clients"]) + 1
    while f"C{n:03d}" in state["clients"]:
        n += 1
    return f"C{n:03d}"


def client_for_payer(state: dict, payer_key: str) -> str | None:
    for cid, c in state["clients"].items():
        if payer_key in c["payer_keys"]:
            return cid
    return None


def _client_id_for_new_payer(state: dict, p: dict) -> tuple[str, bool]:
    cid = client_for_payer(state, p["payer_key"])
    if cid:
        return cid, False
    cid = _next_client_id(state)
    state["clients"][cid] = {"id": cid, "name": p["payer"], "payer_keys": [p["payer_key"]], "payer_names": [p["payer"]],
                             "phone": "", "email": "", "language": "English", "notes": "", "rates": {}, "active": True}
    return cid, True


def _same_payment(a: dict, payer_key: str, amount: float, day: str) -> bool:
    """Email and bank copies of one Zelle: same payer and amount, dates at most 3 days apart."""
    return (a["payer_key"] == payer_key and abs(a["amount"] - amount) < 0.01
            and abs((date.fromisoformat(a["date"]) - date.fromisoformat(day)).days) <= 3)


def import_bank(state: dict, csv_text: str, filename: str = "") -> dict:
    parsed = parse_chase_csv(csv_text)
    known = {p["id"] for p in state["payments"]}
    added, new_clients, confirmed = 0, 0, 0
    for p in parsed["payments"]:
        if p["id"] in known:
            continue
        twin = next((e for e in state["payments"] if e.get("source") == "email" and not e.get("bank_confirmed")
                     and _same_payment(e, p["payer_key"], p["amount"], p["date"])), None)
        if twin:  # already counted from the email; the bank line just confirms it
            twin.update(bank_confirmed=True, bank_id=p["id"], date=p["date"])
            confirmed += 1
            continue
        cid, is_new = _client_id_for_new_payer(state, p)
        new_clients += is_new
        p.update(client_id=cid, source="bank")
        state["payments"].append(p)
        known.add(p["id"])
        added += 1
    state["payments"].sort(key=lambda p: (p["date"], p["id"]))
    summary = {
        "kind": "bank",
        "file": filename,
        "rows_in_file": parsed["rows_total"],
        "zelle_payments_found": parsed["rows_kept"],
        "rows_dropped_not_zelle_income": parsed["rows_total"] - parsed["rows_kept"] - parsed["rows_skipped_bad"],
        "new_payments": added,
        "confirmed_email_payments": confirmed,
        "duplicates_skipped": parsed["rows_kept"] - added - confirmed,
        "new_clients": new_clients,
        "imported_on": date.today().isoformat(),
    }
    state["imports"].append(summary)
    return summary


def import_email_payments(state: dict, results: list[dict], label: str = "Zelle emails") -> dict:
    """Add payments read from Zelle emails. `results` are emails.classify() outputs (kind == 'payment')."""
    added, dupes, new_clients, rows = 0, 0, 0, []
    for r in results:
        if r.get("kind") != "payment":
            rows.append(r)
            continue
        if any(_same_payment(p, r["payer_key"], r["amount"], r["date"]) for p in state["payments"]):
            dupes += 1
            rows.append({**r, "kind": "duplicate", "why": "already counted (same payer, amount and date ±3 days)"})
            continue
        p = {"id": new_id("E"), "date": r["date"], "payer": r["payer"], "payer_key": r["payer_key"],
             "amount": r["amount"], "source": "email", "bank_confirmed": False, "memo": r.get("memo", "")}
        cid, is_new = _client_id_for_new_payer(state, p)
        new_clients += is_new
        p["client_id"] = cid
        state["payments"].append(p)
        added += 1
        rows.append({**r, "kind": "imported", "client": state["clients"][cid]["name"]})
    state["payments"].sort(key=lambda p: (p["date"], p["id"]))
    summary = {"kind": "email", "file": label, "emails_read": len(results), "new_payments": added,
               "duplicates_skipped": dupes, "suspicious_blocked": sum(1 for r in results if r.get("kind") == "suspicious"),
               "not_payments": sum(1 for r in results if r.get("kind") in ("not_a_payment", "unknown_format")),
               "new_clients": new_clients, "imported_on": date.today().isoformat()}
    state["imports"].append(summary)
    return {"summary": summary, "rows": rows}


def merge_clients(state: dict, into_id: str, from_id: str) -> None:
    """Fold one client into another, e.g. two parents paying for the same dancer."""
    if into_id == from_id:
        return
    a, b = state["clients"][into_id], state["clients"].pop(from_id)
    a["payer_keys"] = sorted(set(a["payer_keys"]) | set(b["payer_keys"]))
    a["payer_names"] = sorted(set(a["payer_names"]) | set(b["payer_names"]))
    for coll in ("payments", "lessons", "reminders", "promises", "flags"):
        for item in state[coll]:
            if item.get("client_id") == from_id:
                item["client_id"] = into_id


def family_suggestions(state: dict) -> list[dict]:
    """Clients whose payer names share a surname, e.g. two parents. Owner confirms before merging."""
    by_surname: dict[str, list[str]] = {}
    for cid, c in state["clients"].items():
        for key in c["payer_keys"]:
            words = key.split()
            if len(words) >= 2 and len(words[-1]) >= 4 and not words[-1] in {"LLC", "INC"}:
                by_surname.setdefault(words[-1], [])
                if cid not in by_surname[words[-1]]:
                    by_surname[words[-1]].append(cid)
    return [{"surname": s.title(), "client_ids": ids} for s, ids in sorted(by_surname.items()) if len(ids) > 1]


def _seed_sample() -> dict:
    """Build the demo workspace from the files in data/sample/."""
    state = _empty()
    import_bank(state, (SAMPLE_DIR / "chase_sample.csv").read_text(encoding="utf-8"), "chase_sample.csv")
    seed = json.loads((SAMPLE_DIR / "seed.json").read_text(encoding="utf-8"))
    state["settings"].update(seed["settings"])
    by_payer = {normalize_name(k): v for k, v in seed["client_details"].items()}
    for c in list(state["clients"].values()):
        extra = by_payer.get(c["payer_keys"][0])
        if extra:
            c.update({k: v for k, v in extra.items() if k != "merge_into"})
    for payer, target in seed.get("merges", {}).items():
        src, dst = client_for_payer(state, normalize_name(payer)), client_for_payer(state, normalize_name(target))
        if src and dst:
            merge_clients(state, dst, src)
    for extra in seed.get("extra_clients", []):
        cid = _next_client_id(state)
        state["clients"][cid] = {"id": cid, "payer_keys": [], "payer_names": [], "email": "", "rates": {},
                                 "active": True, **extra}
    by_name = lambda n: next((k for k, c in state["clients"].items() if c["name"] == n), None)
    for l in seed["lessons"]:
        cid = by_name(l["client"]) if "client" in l else client_for_payer(state, normalize_name(l["payer"]))
        if cid:
            from .ledger import lesson_price
            state["lessons"].append({"id": new_id("L"), "client_id": cid, "date": l["date"], "kind": l["kind"],
                                     "price": lesson_price(state["clients"][cid], l["kind"])})
    for k in seed.get("packages", []):
        add_package(state, by_name(k["client"]), k["paid"], k["date"], k.get("already_used", 0), settled=k.get("settled", False))
    for c in state["clients"].values():
        c["name"] = display_name(c["name"])
    state["imports"][0]["imported_on"] = state["settings"]["as_of"]
    return state


def apply_notes(state: dict, kind: str, rows: list[dict]) -> dict:
    """Save confirmed preview rows: lessons from a pasted schedule, or carried-over balances from a 'who owes' list."""
    from .ledger import lesson_price

    today = as_of(state)
    added, skipped, new_clients = 0, 0, 0
    for r in rows:
        cid = r.get("client_id") or ""
        if not cid and r.get("new_name"):
            name = " ".join(r["new_name"].split())[:60]
            cid = next((k for k, c in state["clients"].items() if c["name"].lower() == name.lower()), "")
            if not cid:
                cid = _next_client_id(state)
                state["clients"][cid] = {"id": cid, "name": name, "payer_keys": [], "payer_names": [], "phone": "",
                                         "email": "", "language": "English", "notes": "added from Notes", "rates": {},
                                         "active": True}
                new_clients += 1
        if cid not in state["clients"]:
            skipped += 1
            continue
        if kind == "owes":
            item = {"client_id": cid, "date": today, "kind": "balance", "price": round(float(r["amount"]), 2),
                    "time": "", "note": "carried over from Notes"}
        else:
            if r.get("kind") not in ("private", "group") or not r.get("date"):
                skipped += 1
                continue
            item = {"client_id": cid, "date": r["date"], "kind": r["kind"], "time": r.get("time", ""),
                    "price": lesson_price(state["clients"][cid], r["kind"]), "note": "from Running schedule"}
        dup = any(l["client_id"] == cid and l["date"] == item["date"] and l["kind"] == item["kind"]
                  and l.get("time", "") == item["time"] and (kind != "owes" or l["price"] == item["price"])
                  for l in state["lessons"])
        if dup:
            skipped += 1
            continue
        state["lessons"].append({"id": new_id("L"), **item})
        added += 1
        start = state["settings"].get("ledger_start")
        if not start or item["date"] < start:
            state["settings"]["ledger_start"] = item["date"]
    return {"added": added, "skipped_duplicates_or_invalid": skipped, "new_clients": new_clients}


def add_package(state: dict, client_id: str, paid: int, day: str, already_used: int = 0,
                price: float | None = None, settled: bool = False) -> dict:
    from .config import PACKAGE_TIERS
    from .ledger import lesson_price

    if paid not in PACKAGE_TIERS:
        raise ValueError(f"Package must be one of {sorted(PACKAGE_TIERS)} paid lessons")
    total = paid + PACKAGE_TIERS[paid]
    pack = {"id": new_id("K"), "client_id": client_id, "date": day, "paid_lessons": paid,
            "free_lessons": PACKAGE_TIERS[paid], "total_lessons": total,
            "already_used": max(0, min(int(already_used), total)),
            "price": round(float(price if price is not None else paid * lesson_price(state["clients"][client_id], "private")), 2),
            "settled": bool(settled)}
    state.setdefault("packages", []).append(pack)
    if not state["settings"].get("ledger_start"):
        state["settings"]["ledger_start"] = day
    return pack


def add_manual_payment(state: dict, client_id: str, amount: float, day: str, method: str = "cash") -> dict:
    """Cash or check paid in person. Not in the Zelle data, so it cannot be double-counted with it."""
    c = state["clients"][client_id]
    p = {"id": new_id("M"), "date": day, "payer": c["name"], "payer_key": "", "amount": round(float(amount), 2),
         "source": method, "client_id": client_id}
    state["payments"].append(p)
    state["payments"].sort(key=lambda x: (x["date"], x["id"]))
    return p
