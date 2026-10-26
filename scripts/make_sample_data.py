"""Generate the fictional demo data in data/sample/ (Chase CSV format, invented people).

Run:  python scripts/make_sample_data.py
Each client is built to show one payment pattern the Radar should recognise.
"""

from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "sample"
END = date(2026, 10, 23)
START = date(2026, 6, 29)
rng = random.Random(409)


def weekly(first: date, last: date, step: int = 7):
    d = first
    while d <= last:
        yield d
        d += timedelta(days=step)


payments: list[tuple[date, str, float]] = []


def add(name: str, days, amount: float):
    for d in days:
        payments.append((d, name, amount))


# Pattern per fictional client
add("MAYA ORLOVA", weekly(date(2026, 7, 7), date(2026, 10, 20)), 160)              # on track, weekly private
add("TOM BECKER", weekly(date(2026, 7, 9), date(2026, 9, 3)), 70)                  # group regular, went quiet
add("PRIYA SHAH", weekly(date(2026, 7, 1), date(2026, 9, 23), 14), 320)            # 2 privates every 2 weeks, then silence
add("LUCAS MOREAU", [date(2026, 7, 21)], 160)
add("LUCAS MOREAU", [date(2026, 8, 18)], 1500)                                     # prepaid package
add("NINA PETROVA", weekly(date(2026, 7, 6), date(2026, 10, 19)), 140)             # two groups a week, on track
add("OWEN PARK", [date(2026, 8, 3)], 70)                                           # one trial class
add("MARK KLEIN", weekly(date(2026, 7, 8), date(2026, 10, 21), 14), 160)           # two parents alternate weeks
add("RUTH KLEIN", weekly(date(2026, 7, 15), date(2026, 10, 14), 14), 160)
add("BLUE HERON LLC", [date(2026, 7, 1), date(2026, 8, 3), date(2026, 9, 1), date(2026, 10, 1)], 160)  # company pays monthly
add("SARA LINDQVIST", weekly(date(2026, 7, 10), date(2026, 9, 11), 21), 210)       # 3 groups every 3 weeks, now late
add("ELI ROSEN", [date(2026, 10, 20)], 70)                                         # brand new
add("ANYA KOVAL", weekly(date(2026, 7, 6), date(2026, 9, 28)), 160)                # weekly private, 4 weeks silent
add("ELENA VOLKOV", weekly(date(2026, 7, 2), date(2026, 8, 27)), 70)               # same family, payer switched
add("IGOR VOLKOV", weekly(date(2026, 9, 3), date(2026, 10, 22)), 70)
add("DAVID CHEN", weekly(date(2026, 7, 2), date(2026, 7, 30)), 160)                # stopped in summer

rows = []
for d, name, amt in payments:
    ref = "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789") for _ in range(11))
    kind = rng.choice(["QUICKPAY_CREDIT", "PARTNERFI_TO_CHASE"])
    rows.append((d, "CREDIT", f"Zelle payment from {name} {ref}", amt, kind))

# Noise the parser must drop: payroll, rent, outgoing Zelle, card payment, website payouts.
for d in weekly(START, END, 14):
    rows.append((d, "DEBIT", "ORIG CO NAME:ADP WAGE PAY  CO ENTRY DESCR:WAGE PAY  IND NAME:SAMPLE STUDIO INC", -4200.00, "ACH_DEBIT"))
    rows.append((d + timedelta(days=1), "DEBIT", f"Zelle payment to Guest Teacher {rng.randint(10**10, 10**11)}", -60.00, "QUICKPAY_DEBIT"))
for d in weekly(START, END, 30):
    rows.append((d, "CHECK", "CHECK 1001  ", -1200.00, "CHECK_PAID"))
    rows.append((d + timedelta(days=2), "DEBIT", "Payment to Chase card ending in 0000", -950.00, "LOAN_PMT"))
    rows.append((d + timedelta(days=3), "CREDIT", "ORIG CO NAME:Wixcom  CO ENTRY DESCR:Wix Paymen  IND NAME:SAMPLE STUDIO INC", 900.00, "ACH_CREDIT"))

rows = [r for r in rows if START <= r[0] <= END]
rows.sort(key=lambda r: r[0], reverse=True)
bal = 25000.0
lines = ["Details,Posting Date,Description,Amount,Type,Balance,Check or Slip #"]
for d, det, desc, amt, typ in rows:
    bal += amt
    lines.append(f'{det},{d:%m/%d/%Y},"{desc}",{amt:.2f},{typ},{bal:.2f},,')
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "chase_sample.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

seed = {
    "settings": {"as_of": "2026-10-25", "ledger_start": "2026-10-12"},
    "merges": {"RUTH KLEIN": "MARK KLEIN"},
    "client_details": {
        "MAYA ORLOVA": {"name": "Maya Orlova", "phone": "555-0101", "language": "English"},
        "TOM BECKER": {"name": "Tom Becker", "phone": "555-0102", "language": "English"},
        "PRIYA SHAH": {"name": "Priya Shah", "phone": "555-0103", "language": "English"},
        "LUCAS MOREAU": {"name": "Lucas Moreau", "phone": "555-0104", "language": "English"},
        "NINA PETROVA": {"name": "Nina Petrova", "phone": "555-0105", "language": "Russian"},
        "OWEN PARK": {"name": "Owen Park", "phone": "555-0106", "language": "English"},
        "MARK KLEIN": {"name": "Leo Klein", "phone": "555-0107", "language": "English",
                       "notes": "Leo is 12. Parents Mark and Ruth take turns paying."},
        "BLUE HERON LLC": {"name": "Jamie Ortiz", "phone": "555-0108", "language": "English",
                           "notes": "Employer Blue Heron LLC pays monthly."},
        "SARA LINDQVIST": {"name": "Sara Lindqvist", "phone": "555-0109", "language": "English"},
        "ELI ROSEN": {"name": "Eli Rosen", "phone": "555-0110", "language": "English"},
        "ANYA KOVAL": {"name": "Anya Koval", "phone": "555-0111", "language": "Ukrainian"},
        "ELENA VOLKOV": {"name": "Elena Volkov", "phone": "555-0112", "language": "Russian"},
        "IGOR VOLKOV": {"name": "Igor Volkov", "phone": "555-0113", "language": "Russian"},
        "DAVID CHEN": {"name": "David Chen", "phone": "555-0114", "language": "English"},
    },
    # Lessons the owner logged since Oct 12. Payments after Oct 12 pay these off oldest-first.
    "lessons": [
        {"payer": "MAYA ORLOVA", "date": "2026-10-13", "kind": "private"},
        {"payer": "MAYA ORLOVA", "date": "2026-10-20", "kind": "private"},
        {"payer": "TOM BECKER", "date": "2026-10-15", "kind": "group"},
        {"payer": "PRIYA SHAH", "date": "2026-10-14", "kind": "private"},
        {"payer": "PRIYA SHAH", "date": "2026-10-21", "kind": "private"},
        {"payer": "NINA PETROVA", "date": "2026-10-12", "kind": "group"},
        {"payer": "NINA PETROVA", "date": "2026-10-12", "kind": "group"},
        {"payer": "NINA PETROVA", "date": "2026-10-19", "kind": "group"},
        {"payer": "NINA PETROVA", "date": "2026-10-19", "kind": "group"},
        {"payer": "MARK KLEIN", "date": "2026-10-14", "kind": "private"},
        {"payer": "MARK KLEIN", "date": "2026-10-21", "kind": "private"},
        {"payer": "ELI ROSEN", "date": "2026-10-20", "kind": "group"},
        {"payer": "ELI ROSEN", "date": "2026-10-22", "kind": "group"},
        {"payer": "IGOR VOLKOV", "date": "2026-10-15", "kind": "group"},
        {"payer": "IGOR VOLKOV", "date": "2026-10-22", "kind": "group"},
        {"client": "Grace Lee", "date": "2026-10-13", "kind": "private"},
        {"client": "Grace Lee", "date": "2026-10-20", "kind": "private"},
    ],
    # Paid by check before tracking started, so there is no Zelle row for her.
    "extra_clients": [{"name": "Grace Lee", "phone": "555-0115", "language": "English",
                       "notes": "Prepaid 10 + 1 package (paid by check)."}],
    "packages": [{"client": "Grace Lee", "paid": 10, "date": "2026-09-01", "already_used": 7, "settled": True}],
}
(OUT / "seed.json").write_text(json.dumps(seed, indent=1), encoding="utf-8")
print(f"wrote {len(lines) - 1} rows ({len(payments)} Zelle payments) to {OUT}")
