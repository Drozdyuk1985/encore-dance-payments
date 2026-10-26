"""Exact balances from logged lessons and Zelle payments. Pure code, no model calls."""

from __future__ import annotations

from datetime import date

from .config import PRICES


def lesson_price(client: dict, kind: str) -> float:
    custom = (client.get("rates") or {}).get(kind)
    return float(custom if custom is not None else PRICES[kind])


def balance(client: dict, lessons: list[dict], payments: list[dict], start: str, as_of: str) -> dict:
    """Pay off lessons oldest-first with payments made on or after the ledger start date."""
    # Lessons dated after today are scheduled, not owed yet; they start counting on their date.
    mine = sorted((l for l in lessons if l["client_id"] == client["id"] and start <= l["date"] <= as_of), key=lambda l: l["date"])
    paid = sorted((p for p in payments if p["date"] >= start), key=lambda p: p["date"])
    charged = round(sum(l["price"] for l in mine), 2)
    total_paid = round(sum(p["amount"] for p in paid), 2)

    pool, unpaid = total_paid, []
    for l in mine:
        if pool >= l["price"] - 0.005:
            pool -= l["price"]
        else:
            unpaid.append({"date": l["date"], "kind": l["kind"], "price": l["price"], "partly_paid": round(pool, 2)})
            pool = 0.0

    owed = round(max(charged - total_paid, 0.0), 2)
    oldest = unpaid[0]["date"] if unpaid else None
    return {
        "client_id": client["id"],
        "lessons_charged": len(mine),
        "charged": charged,
        "paid": total_paid,
        "owed": owed,
        "credit": round(max(total_paid - charged, 0.0), 2),
        "unpaid_lessons": unpaid,
        "oldest_unpaid": oldest,
        "days_overdue": (date.fromisoformat(as_of) - date.fromisoformat(oldest)).days if oldest else 0,
    }
