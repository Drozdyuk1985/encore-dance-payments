"""Read a Chase account-activity CSV and keep only incoming Zelle payments.

Everything else in the export (payroll, taxes, loans, card payments, outgoing Zelle) is
dropped here and never stored, shown to the model, or written to disk.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import datetime

ZELLE_FROM = re.compile(r"^\s*Zelle payment from\s+(?P<name>.+?)(?:\s+(?P<ref>[A-Za-z0-9]{8,}))?\s*$", re.I)


def normalize_name(name: str) -> str:
    """'Maya  Orlova' and 'MAYA ORLOVA' become the same payer key."""
    cleaned = re.sub(r"[^A-Za-z0-9 ]+", " ", name).upper()
    return re.sub(r"\s+", " ", cleaned).strip()


def display_name(name: str) -> str:
    words = normalize_name(name).split()
    keep_upper = {"LLC", "INC", "PC", "LLP", "II", "III"}
    return " ".join(w if w in keep_upper else w.capitalize() for w in words)


def _parse_date(text: str) -> str | None:
    text = (text or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_amount(text: str) -> float | None:
    try:
        return round(float(str(text).replace("$", "").replace(",", "").strip()), 2)
    except ValueError:
        return None


def parse_chase_csv(text: str) -> dict:
    """Return {'payments': [...], 'rows_total': n, 'rows_kept': k, 'rows_skipped_bad': b}.

    Chase exports have a trailing comma on every row, so rows have one more field than the header.
    """
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    try:
        header = [h.strip().lower() for h in next(reader)]
    except StopIteration:
        return {"payments": [], "rows_total": 0, "rows_kept": 0, "rows_skipped_bad": 0}

    def col(*names: str) -> int | None:
        for n in names:
            if n in header:
                return header.index(n)
        return None

    i_details, i_date = col("details"), col("posting date", "date")
    i_desc, i_amount = col("description"), col("amount")
    if i_date is None or i_desc is None or i_amount is None:
        raise ValueError("This does not look like a Chase activity CSV (need Posting Date, Description, Amount columns).")

    payments, total, bad, seen = [], 0, 0, set()
    for row in reader:
        if not row or not any(cell.strip() for cell in row):
            continue
        total += 1
        try:
            desc = row[i_desc]
            amount = _parse_amount(row[i_amount])
            day = _parse_date(row[i_date])
            details = row[i_details].strip().upper() if i_details is not None else "CREDIT"
        except IndexError:
            bad += 1
            continue
        if amount is None or day is None:
            bad += 1
            continue
        m = ZELLE_FROM.match(desc)
        if not m or amount <= 0 or details not in ("CREDIT", ""):
            continue
        name = m.group("name").strip()
        ref = m.group("ref") or hashlib.sha1(f"{day}|{name}|{amount}".encode()).hexdigest()[:12]
        if ref in seen:
            continue
        seen.add(ref)
        payments.append({
            "id": ref,
            "date": day,
            "payer": display_name(name),
            "payer_key": normalize_name(name),
            "amount": amount,
        })
    payments.sort(key=lambda p: (p["date"], p["id"]))
    return {"payments": payments, "rows_total": total, "rows_kept": len(payments), "rows_skipped_bad": bad}
