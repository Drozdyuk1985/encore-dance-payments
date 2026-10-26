"""Settings, key loading, prices and budgets for the Encore payment desk."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent
REPO = APP_DIR.parent
DATA = REPO / "data"
SAMPLE_DIR = DATA / "sample"
WORKSPACES = DATA / "workspaces"
LOGS = REPO / "logs"
USAGE_LOG = LOGS / "usage.jsonl"
STATIC = REPO / "static"

# The key may live in this repo's .env or in the folder above it (where the teaching staff keep theirs).
# The repo copy wins if both exist.
ENV_FILES = [REPO / ".env", REPO.parent / ".env"]
for _p in ENV_FILES:
    load_dotenv(_p, override=False)

# ---- Studio settings
STUDIO_NAME = os.getenv("STUDIO_NAME", "DNA Dance Academy")
ZELLE_EMAIL = os.getenv("ZELLE_EMAIL", "info@dnadanceacademy.com")
PRICES = {"private": float(os.getenv("PRICE_PRIVATE", "160")), "group": float(os.getenv("PRICE_GROUP", "70"))}

# ---- Model (course key works with gpt-6-luna only)
MODEL_NAME = "gpt-6-luna"
PORTKEY_BASE_URL = os.getenv("PORTKEY_BASE_URL", "https://api.portkey.ai/v1")

# USD per 1M tokens, from MGT 409 Lecture 11 (gpt-6-luna row).
PRICE_PER_M = {"input": 0.10, "cached_input": 0.01, "output": 0.50}

# ---- Budgets: what stops a bug or a bad actor from burning tokens
MAX_OUTPUT_TOKENS = 12_000          # per response; gpt-6-luna's hidden reasoning counts toward this
MAX_TOOL_STEPS = 6                  # model calls inside one agent's tool loop
DAILY_TOKEN_BUDGET = int(os.getenv("DAILY_TOKEN_BUDGET", "400000"))
MAX_CALLS_PER_MINUTE = 20
MAX_REPLY_CHARS = 2_000             # longest student reply the Reply agent will read
MAX_CLIENTS_PER_RADAR_RUN = 80


def portkey_key() -> str | None:
    key = os.getenv("PORTKEY_API_KEY", "").strip()
    return key or None


def key_source() -> str:
    """Which .env file supplied the key, for the status bar. Never returns the key itself."""
    for p in ENV_FILES:
        if p.exists() and "PORTKEY_API_KEY" in p.read_text(encoding="utf-8", errors="ignore"):
            return "repo folder" if p.parent == REPO else "parent folder"
    return "environment" if portkey_key() else "missing"
