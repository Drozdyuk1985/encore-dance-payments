"""Read text pasted from Apple Notes: a "Running schedule" (lessons) or a "who owes me" list. Pure code, no AI.

Nothing is saved here: these functions only build a preview that the owner checks and confirms.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
WEEKDAY_RE = re.compile(r"\b(mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?\b", re.I)
SLASH_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
MONTH_DATE = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.I)
TIME_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a|p)?\b(?:\s*[-–]\s*\d{1,2}(?::\d{2})?\s*(?:am|pm|a|p)?)?", re.I)
CANCELED = re.compile(r"\b(cancel+ed|cancel|cxl|canceled|cancelled|rescheduled?|off)\b", re.I)
GROUP = re.compile(r"\b(group|class|drop[- ]?in)\b", re.I)
FILLER = {"private", "group", "class", "drop", "in", "dropin", "lesson", "lessons", "and", "with", "am", "pm", "at",
          "studio", "zoom", "online", "the", "a", "w", "hr", "min", "mins", "hour", "rehearsal", "today", "tomorrow",
          "running", "schedule", "week", "weekly", "next", "this", "upcoming", "to", "do", "notes", "note", "owes",
          "owe", "owed", "paid", "unpaid", "total", "todo", "list", "free", "break", "lunch", "pending"}


def _name_index(clients: dict) -> tuple[dict, dict]:
    """first-name and full-name lookups (lowercase) -> client ids."""
    first, full = {}, {}
    for cid, c in clients.items():
        for name in [c.get("name", "")] + c.get("payer_names", []):
            words = name.lower().split()
            if not words:
                continue
            full.setdefault(" ".join(words), set()).add(cid)
        words = c.get("name", "").lower().split()
        if words:
            first.setdefault(words[0], set()).add(cid)
    return first, full


def match_names(text: str, clients: dict) -> tuple[list[set], list[str]]:
    """Return (matches, unknown_names). Each match is a set of possible client ids for one person in the text."""
    first, full = _name_index(clients)
    low = " " + re.sub(r"[^a-z' ]+", " ", text.lower()) + " "
    matches, used = [], set()
    for key in sorted(full, key=len, reverse=True):          # full names first ("priya shah")
        if len(key.split()) > 1 and f" {key} " in low and not any(w in used for w in key.split()):
            matches.append(set(full[key]))
            used.update(key.split())
    for word in low.split():                                  # then first names ("priya")
        if word in first and word not in used:
            matches.append(set(first[word]))
            used.add(word)
    leftovers = [w for w in re.findall(r"\b[A-Z][a-z'’\-]+\b", text)
                 if w.lower() not in used and w.lower() not in FILLER and w.lower()[:3] not in WEEKDAYS
                 and w.lower()[:3] not in MONTHS]
    return matches, leftovers


def _date_in(line: str, year: int) -> date | None:
    m = SLASH_DATE.search(line)
    if m:
        y = int(m.group(3)) if m.group(3) else year
        y = y + 2000 if y < 100 else y
        try:
            return date(y, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    m = MONTH_DATE.search(line)
    if m:
        try:
            return date(year, MONTHS[m.group(1).lower()[:3]], int(m.group(2)))
        except ValueError:
            return None
    return None


def _strip_dates(line: str) -> str:
    return MONTH_DATE.sub(" ", SLASH_DATE.sub(" ", line))


def _time(line: str) -> str:
    for m in TIME_RE.finditer(_strip_dates(line)):
        hour, minute, ap = int(m.group(1)), m.group(2), (m.group(3) or "").lower()
        if minute is None and not ap:
            continue  # a bare number is not a time
        if not 0 < hour <= 23:
            continue
        if ap.startswith("p") and hour < 12:
            hour += 12
        return f"{hour:02d}:{minute or '00'}"
    return ""


def parse_schedule(text: str, clients: dict, week_start: date, today: date) -> list[dict]:
    """One preview row per lesson found. Weekday-only headers are placed in the week that starts on `week_start`."""
    rows, current = [], None
    for raw in text.replace("\r", "").split("\n"):
        line = raw.strip(" \t•-*–·☐☑✓✔")
        if not line:
            continue
        explicit = _date_in(line, week_start.year)
        wd = WEEKDAY_RE.search(line)
        day = explicit or (week_start + timedelta(days=(WEEKDAYS[wd.group(1).lower()[:3]] - week_start.weekday()) % 7) if wd else None)
        matches, unknown = match_names(_strip_dates(line), clients)
        if day and not matches and not unknown and not _time(line):
            current = day                                     # a header line like "Tuesday 10/27"
            continue
        when = day or current
        if not matches and not unknown:
            continue                                          # a note line with no person in it
        kind = "group" if GROUP.search(line) else "private"
        status = "canceled" if CANCELED.search(line) else ("needs_date" if not when else
                                                              "upcoming" if when > today else "ready")
        base = {"line": raw.strip(), "date": when.isoformat() if when else "", "time": _time(line), "kind": kind,
                "status": status}
        for ids in matches:
            rows.append({**base, "client_ids": sorted(ids), "new_name": ""})
        if unknown and (kind == "group" or not matches):
            for name in (unknown if kind == "group" else [" ".join(unknown[:2])]):
                rows.append({**base, "client_ids": [], "new_name": name})
    return rows


OWES_LINE = re.compile(r"^(?P<name>[^\d$]{2,60}?)\s*[-:–—=]?\s*\$\s?(?P<amount>\d{1,5}(?:\.\d{1,2})?)\b|"
                       r"^(?P<name2>[^\d$]{2,60}?)\s+(?P<amount2>\d{2,5}(?:\.\d{1,2})?)\s*(?:\$|usd|dollars)?\s*$", re.I)


def parse_owes(text: str, clients: dict) -> list[dict]:
    """'Alex $160' style lines -> preview rows with an amount and the matching client(s)."""
    rows = []
    for raw in text.replace("\r", "").split("\n"):
        line = raw.strip(" \t•-*–·☐☑✓✔")
        m = OWES_LINE.search(line)
        if not m:
            continue
        name = (m.group("name") or m.group("name2") or "").strip(" :-–—")
        amount = float(m.group("amount") or m.group("amount2"))
        if not name or amount <= 0:
            continue
        matches, unknown = match_names(name, clients)
        rows.append({"line": raw.strip(), "amount": round(amount, 2), "client_ids": sorted(matches[0]) if matches else [],
                     "new_name": "" if matches else " ".join(w.capitalize() for w in name.split()[:3]),
                     "status": "ready"})
    return rows
