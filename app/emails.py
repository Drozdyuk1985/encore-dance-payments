"""Read Zelle "you received money" notification emails (pasted text or .eml files). Pure code.

Emails arrive faster than a bank export, so a payment can count the same evening. Each email is
sorted into: payment, suspicious (looks like a Zelle scam), not_a_payment, or unknown_format
(a Zelle email this code could not read; the Email Reader agent may try it).
"""

from __future__ import annotations

import email
import html
import re
from datetime import date, datetime
from email.utils import parsedate_to_datetime

from .bank import display_name, normalize_name

NAME = r"[A-Za-z][A-Za-z.'&\- ]{1,60}?"
AMOUNT = r"\$\s?(?P<amount>\d{1,3}(?:,\d{3})*(?:\.\d{2})?)"
PATTERNS = [
    re.compile(rf"(?P<name>{NAME})\s+sent you\s+{AMOUNT}", re.I),
    re.compile(rf"You received\s+{AMOUNT}\s+from\s+(?P<name>{NAME})(?:[.,\n]|\s+(?:on|via|with|through)\b)", re.I),
    re.compile(rf"{AMOUNT}\s+(?:was\s+)?(?:sent to you|received)\s+from\s+(?P<name>{NAME})(?:[.,\n]|\s+(?:on|via|with)\b)", re.I),
]
SENT_MONEY = re.compile(rf"(?P<name>{NAME})\s+sent you\s+money", re.I)  # Chase: "NAME sent you money" + "Amount $X"
AMOUNT_LINE = re.compile(rf"Amount\s*:?\s*{AMOUNT}", re.I)
MEMO = re.compile(r"^\s*Memo\s*:?[ \t]*(?P<memo>[^\n]{1,80})", re.I | re.M)

# Phrases that real "you received money" alerts never contain but Zelle scams do.
SCAM = re.compile(
    r"\b(pending|on hold|upgrade (?:to|your)|business account|verify your (?:account|identity)|"
    r"click (?:here|below|the link)|send (?:back|the money back)|refund the|overpa(?:id|yment)|"
    r"gift card|unlock|limit (?:reached|exceeded)|log ?in to (?:confirm|accept))\b", re.I)
TRUSTED_SENDERS = re.compile(r"@(?:[\w-]+\.)*(?:chase\.com|zellepay\.com|zelle\.com)\b", re.I)


def _plain(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", text)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"[ \t ]+", " ", text)


def from_eml(raw: str) -> dict:
    """Turn an .eml file into {'text', 'sender', 'date'}."""
    msg = email.message_from_string(raw)
    parts = []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_type() in ("text/plain", "text/html"):
            payload = part.get_payload(decode=True)
            if payload:
                parts.append(_plain(payload.decode(part.get_content_charset() or "utf-8", errors="ignore")))
    when = None
    try:
        when = parsedate_to_datetime(msg["Date"]).date().isoformat() if msg["Date"] else None
    except (TypeError, ValueError):
        pass
    body = "\n".join(parts) or _plain(msg.get_payload() or "")
    return {"text": f"Subject: {msg['Subject'] or ''}\n{body}", "sender": msg["From"] or "", "date": when}


def split_pasted(text: str) -> list[dict]:
    """Several pasted emails are split at lines of dashes; failing that, at each 'From:' (or 'Subject:') header."""
    text = "\n" + text.replace("\r\n", "\n")
    if re.search(r"\n\s*[-=_]{3,}\s*\n", text):
        chunks = re.split(r"\n\s*[-=_]{3,}\s*\n", text)
    elif len(re.findall(r"\nFrom:", text)) > 1:
        chunks = re.split(r"\n(?=From:)", text)
    elif len(re.findall(r"\nSubject:", text)) > 1:
        chunks = re.split(r"\n(?=Subject:)", text)
    else:
        chunks = [text]
    out = []
    for chunk in chunks:
        chunk = chunk.strip()
        if len(chunk) < 15:
            continue
        sender = re.search(r"^From:\s*(.+)$", chunk, re.M)
        out.append({"text": _plain(chunk), "sender": sender.group(1).strip() if sender else "", "date": _header_date(chunk)})
    return out


def _header_date(text: str) -> str | None:
    m = re.search(r"^(?:Date|Sent):\s*(.+)$", text, re.M)
    if m:
        try:
            return parsedate_to_datetime(m.group(1).strip()).date().isoformat()
        except (TypeError, ValueError):
            for fmt in ("%b %d, %Y", "%B %d, %Y", "%m/%d/%Y"):
                try:
                    return datetime.strptime(m.group(1).strip()[:18].strip(" ,"), fmt).date().isoformat()
                except ValueError:
                    continue
    m = re.search(r"\b(?:on|Date:?)\s+(\d{1,2}/\d{1,2}/\d{4}|[A-Z][a-z]{2,8} \d{1,2}, \d{4})", text)
    if m:
        for fmt in ("%m/%d/%Y", "%b %d, %Y", "%B %d, %Y"):
            try:
                return datetime.strptime(m.group(1), fmt).date().isoformat()
            except ValueError:
                continue
    return None


def classify(mail: dict, today: str | None = None) -> dict:
    text, sender = mail["text"], mail.get("sender", "")
    base = {"date": mail.get("date") or today or date.today().isoformat(), "sender": sender}
    if not re.search(r"zelle", text, re.I):
        return {**base, "kind": "not_a_payment", "why": "does not mention Zelle"}
    if SCAM.search(text) or (sender and "@" in sender and not TRUSTED_SENDERS.search(sender)):
        hit = SCAM.search(text)
        why = f"scam warning sign: “{hit.group(0)}”" if hit else f"sender is not Chase or Zelle ({sender})"
        return {**base, "kind": "suspicious", "why": why}
    if re.search(r"\byou sent\b|\byou(?:'ve| have)? requested\b|\brequested (?:money|\$\s?\d)", text, re.I):
        return {**base, "kind": "not_a_payment", "why": "outgoing payment or a request, not money received"}
    memo = MEMO.search(text)
    extra = {"memo": memo.group("memo").strip()} if memo else {}
    m, a = SENT_MONEY.search(text), AMOUNT_LINE.search(text)
    if m and a:  # Chase's real layout, checked first
        return {**base, **_payment(m.group("name"), a.group("amount")), **extra}
    for pat in PATTERNS:
        m = pat.search(text)
        if m:
            return {**base, **_payment(m.group("name"), m.group("amount")), **extra}
    return {**base, "kind": "unknown_format", "why": "mentions Zelle but no 'X sent you $Y' line was found"}


def _payment(name: str, amount: str) -> dict:
    name = re.sub(r"^(?:Hi|Hello|Good news|Great news)[,!:]?\s+", "", name.strip(), flags=re.I)
    name = re.sub(r"^(?:Zelle\s*)?payment\s+", "", name, flags=re.I).strip(" .,:")  # "Zelle® payment NAME sent you money"
    # Chase writes payers in capitals; on a flattened line keep just the trailing ALL-CAPS name ("PM Zelle payment JANE DOE").
    caps = re.search(r"(?:\b[A-Z][A-Z.'&\-]+\s+)+[A-Z][A-Z.'&\-]+$", name)
    if caps and re.search(r"[a-z]", name):
        name = caps.group(0)
    return {"kind": "payment", "payer": display_name(name), "payer_key": normalize_name(name),
            "amount": round(float(amount.replace(",", "")), 2)}
