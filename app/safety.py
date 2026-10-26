"""Guards around every model call: privacy masking, injection screening and draft verification."""

from __future__ import annotations

import re

from .config import MAX_REPLY_CHARS

# ---- Privacy: the model sees client IDs, never names, phone numbers or emails.
PHONE = re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _name_words(c: dict) -> set[str]:
    words = set()
    for name in [c.get("name", "")] + c.get("payer_names", []):
        words.update(w for w in re.split(r"\s+", name) if len(w) >= 3 and w.upper() not in {"LLC", "INC"})
    return words


def mask_text(text: str, client: dict | None, others: list[dict] = ()) -> str:
    """Remove names, phone numbers and emails from free text before it goes to the model.

    The client's own name becomes {first_name}; any other client's name becomes [another person].
    """
    out = EMAIL.sub("[email]", text)
    out = PHONE.sub("[phone]", out)
    own = _name_words(client) if client else set()
    other = set().union(*(_name_words(o) for o in others)) - own if others else set()
    for words, token in ((own, "{first_name}"), (other, "[another person]")):
        for w in sorted(words, key=len, reverse=True):
            out = re.sub(rf"\b{re.escape(w)}\b", token, out, flags=re.I)
    out = re.sub(r"\{first_name\}(\s+\{first_name\})+", "{first_name}", out)
    return re.sub(r"\[another person\](\s+\[another person\])+", "[another person]", out)


# ---- Prompt injection: flag it up front, then the model is also told to treat the text as data.
INJECTION_PATTERNS = [
    r"ignore (all |any |the |your |previous |prior |above )+(instructions|rules|prompts?)",
    r"disregard (all |any |the |your |previous |prior )+(instructions|rules)",
    r"\b(system|developer) (prompt|message|instructions?)\b",
    r"^\s*(system|assistant|developer)\s*:",
    r"you are now\b",
    r"\bmark (me|my (balance|account|lessons?)|it|this|everything) (as )?(paid|settled|zero|cleared)\b",
    r"\b(set|change|make) my balance\b",
    r"\b(list|show|tell me|give me) (all|every|the other) (students|clients|balances|people)\b",
    r"\bjailbreak\b|\bDAN mode\b",
]
_INJ = [re.compile(p, re.I | re.M) for p in INJECTION_PATTERNS]


def screen_untrusted(text: str) -> dict:
    hits = [p.pattern for p in _INJ if p.search(text)]
    return {"suspicious": bool(hits), "patterns": hits, "too_long": len(text) > MAX_REPLY_CHARS}


def wrap_untrusted(text: str) -> str:
    clean = text[:MAX_REPLY_CHARS].replace("</untrusted>", "")
    return f"<untrusted>\n{clean}\n</untrusted>"


# ---- Draft verification: a reminder only reaches the owner if it passes every check.
PLACEHOLDERS = ("{first_name}", "{amount}", "{zelle_email}", "{lesson_list}")
MONEY = re.compile(r"\$\s?\d|\d+(?:\.\d{2})?\s?(?:usd|dollars|долл|грн|руб)", re.I)
BANNED = re.compile(r"\b(lawyer|legal action|court|collections? agency|police|sue|late fee|penalt(y|ies)|interest charge)\b", re.I)


def verify_draft(message: str, mode: str) -> list[str]:
    """Return a list of problems; empty means the draft is safe to show.

    mode='balance' -> exact amount owed is known, so {amount} is required.
    mode='checkin' -> amount is not known, so no dollar figure may appear at all.
    """
    problems = []
    if "{first_name}" not in message:
        problems.append("missing {first_name}")
    if "{zelle_email}" not in message:
        problems.append("missing {zelle_email}")
    if mode == "balance" and "{amount}" not in message:
        problems.append("missing {amount}")
    if mode == "checkin" and "{amount}" in message:
        problems.append("check-in must not state an amount")
    if MONEY.search(message):
        problems.append("contains a typed dollar figure (only {amount} is allowed)")
    if EMAIL.search(message) or PHONE.search(message):
        problems.append("contains a raw email or phone number")
    if BANNED.search(message):
        problems.append("threatening or policy language (late fees, legal, collections)")
    for ph in re.findall(r"\{[a-z_]+\}", message):
        if ph not in PLACEHOLDERS:
            problems.append(f"unknown placeholder {ph}")
    if len(message) > 600:
        problems.append("longer than 600 characters")
    if len(message) < 40:
        problems.append("too short")
    return problems


def fill(message: str, values: dict) -> str:
    for k, v in values.items():
        message = message.replace("{" + k + "}", str(v))
    return message
