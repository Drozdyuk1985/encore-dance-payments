"""Payment-rhythm statistics per client. Pure code: the model reads these numbers, it never computes them."""

from __future__ import annotations

from datetime import date
from statistics import median

from .config import PRICES


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def client_stats(payments: list[dict], as_of: str) -> dict:
    """Stats for one client's payments (already sorted by date)."""
    if not payments:
        return {"n_payments": 0}
    dates = sorted({p["date"] for p in payments})
    amounts = [p["amount"] for p in payments]
    gaps = [_days(a, b) for a, b in zip(dates, dates[1:])]
    recent_gaps = gaps[-6:]
    typical_gap = round(median(recent_gaps)) if recent_gaps else None
    days_since = _days(dates[-1], as_of)
    big = PRICES["group"] * 4
    stats = {
        "n_payments": len(payments),
        "first_payment": dates[0],
        "last_payment": dates[-1],
        "days_since_last": days_since,
        "typical_gap_days": typical_gap,
        "median_amount": round(median(amounts), 2),
        "last_amount": payments[-1]["amount"],
        "total_paid": round(sum(amounts), 2),
        "paid_last_60_days": round(sum(p["amount"] for p in payments if _days(p["date"], as_of) <= 60), 2),
        "large_payments": sum(1 for a in amounts if a >= big),
        "last_6_payments": [{"date": p["date"], "amount": p["amount"]} for p in payments[-6:]],
    }
    stats["median_amount_before_last"] = round(median(amounts[:-1]), 2) if len(amounts) > 1 else None
    stats["gap_ratio"] = round(days_since / typical_gap, 2) if typical_gap else None
    stats["signal"] = _signal(stats)
    return stats


def _signal(s: dict) -> str:
    """A simple rule-based first guess. The Radar agent can overrule it, but must explain why."""
    n, since, gap, ratio = s["n_payments"], s["days_since_last"], s["typical_gap_days"], s["gap_ratio"]
    if n == 1:
        return "new" if since <= 21 else "one_time"
    before = s["median_amount_before_last"]
    if s["last_amount"] >= PRICES["group"] * 4 and before and s["last_amount"] >= 3 * before:
        return "package_or_prepaid"  # last payment is far bigger than this client's usual one
    if ratio is None or since <= 10 or ratio <= 1.5:
        return "on_track"
    if since > 75:
        return "likely_stopped"
    return "maybe_forgot"
