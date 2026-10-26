"""Exact balances from logged lessons and Zelle payments. Pure code, no model calls."""

from __future__ import annotations

from datetime import date

from .config import PRICES


def lesson_price(client: dict, kind: str) -> float:
    custom = (client.get("rates") or {}).get(kind)
    return float(custom if custom is not None else PRICES[kind])


def package_usage(client: dict, lessons: list[dict], packages: list[dict], as_of: str) -> tuple[list[dict], set[int]]:
    """Private lessons on or after a package's purchase date come off that package (oldest package first).

    Returns (packages with used/remaining counts, python ids of the lessons the packages cover).
    """
    mine = sorted((p for p in packages if p["client_id"] == client["id"]), key=lambda p: p["date"])
    left = {p["id"]: p["total_lessons"] - p.get("already_used", 0) for p in mine}
    covered = set()
    privates = sorted((l for l in lessons if l["client_id"] == client["id"] and l["kind"] == "private" and l["date"] <= as_of),
                      key=lambda l: l["date"])
    for l in privates:
        pack = next((p for p in mine if p["date"] <= l["date"] and left[p["id"]] > 0), None)
        if pack:
            left[pack["id"]] -= 1
            covered.add(id(l))
    usage = [{**p, "remaining": max(left[p["id"]], 0), "used": p["total_lessons"] - max(left[p["id"]], 0)} for p in mine]
    return usage, covered


def balance(client: dict, lessons: list[dict], payments: list[dict], start: str, as_of: str,
            packages: list[dict] = ()) -> dict:
    """Pay off charges oldest-first with payments made on or after the ledger start date.

    Charges are lessons not covered by a prepaid package, plus package purchases made since the start date.
    """
    usage, covered = package_usage(client, lessons, list(packages), as_of)
    # Lessons dated after today are scheduled, not owed yet; they start counting on their date.
    mine = [l for l in lessons if l["client_id"] == client["id"] and start <= l["date"] <= as_of and id(l) not in covered]
    mine += [{"date": p["date"], "kind": "package", "price": p["price"]} for p in usage
             if start <= p["date"] <= as_of and not p.get("settled")]
    mine.sort(key=lambda l: l["date"])
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
        "packages": usage,
        "lessons_covered_by_package": len(covered),
    }
