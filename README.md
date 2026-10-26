# Encore: the payment desk for a small dance studio

Encore finds lesson payments that students forgot to send, then drafts friendly reminders the owner can send from their own phone.
It reads Zelle notification emails and the studio's Chase bank export, keeps only incoming Zelle payments (a payment seen in both counts once), compares them with logged lessons, and uses three AI agents plus a small Email Reader:

| Agent | Job | Tools / checks |
|---|---|---|
| **Payment Radar** | Spots regulars whose payments have gone quiet (forgot vs. stopped vs. prepaid) | Reads rhythm statistics computed by code; code overrides it when the ledger shows a balance |
| **Collector** | Writes short reminders in each client's language | Every draft is checked by code (exact amount placeholder, Zelle email, no threats) and retried or replaced by a template |
| **Email Reader** | Reads only Zelle emails whose format code doesn't recognise | Its payer name and amount count only if they appear word for word in the email; scam emails are blocked by code first |
| **Reply handler** | Reads a student's answer ("I already paid", "can I pay Friday?") | `get_balance`, `recent_payments`, `find_payments_by_amount`, `record_promise`, `flag_for_owner`. No tool can mark anyone as paid |

Built for DNA Dance Academy (private lessons $160, group drop-in $70, Zelle to `info@dnadanceacademy.com`).
Model: `gpt-6-luna` through the course Portkey key.

---

## Run it (about 3 minutes)

You need **Python 3.11 or newer**. Nothing else (no database, no Node).

```bash
git clone https://github.com/Drozdyuk1985/encore-dance-payments.git
cd encore-dance-payments

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python run.py                      # opens http://localhost:8000
```

### API key

The app looks for a `.env` file in **this repo folder first, then the folder above it**, so the teaching staff's
`submissions/.env` works as is. It needs one key, named exactly:

```
PORTKEY_API_KEY=...
```

See `.env.example`. If no key is found the app still opens and the ledger works; the AI buttons show a clear error.

### What to try

The app opens on the **Sample studio**: fictional students and a made-up Chase export in `data/sample/`.

1. **Tonight** → **Run Payment Radar**. About 10 seconds. It sorts clients into *owes you*, *maybe forgot*, *likely stopped*, *prepaid*, and so on.
2. **Draft reminders for selected**. Check the drafts (Russian and Ukrainian clients get their own language) and press **Text** to open Messages.
3. **Replies** → pick *Priya Shah* → click **"I paid"** → **Check this reply**. The agent checks the bank record with its tools, finds no payment and flags it.
4. Same tab → pick *Eli Rosen* → **Attack**. The injection is blocked before it reaches the AI.
5. **Import Zelle** → **Load sample emails** → **Read emails**. Two new payments are counted, a duplicate is skipped, a fake "PENDING" email is blocked, and one unusual email is read by the Email Reader.
6. **Tokens & cost** shows the real token counts and cost of every call you just made.

**Reset sample studio** (Import tab) restores the demo data at any time.

### Using real data instead of the sample

Switch the header toggle to **My studio**, open **Import Zelle**, and add Zelle emails (Gmail: open the email → ⋮ → Download message, or paste the text; Apple Mail: drag them in) and/or upload a Chase CSV
(Chase → account → *Download account activity* → CSV). **Copy who owes for Notes** (Tonight tab) gives lines like `Alex $160` for an Apple Notes to-do list. On **Log lessons → Paste from Apple Notes** you can paste a week (or more) of your schedule, e.g. `Tuesday 10/27` / `4pm Priya private` / `6pm group: Nina, Tom`, or your "who owes me" lines (`Alex $160`). Code reads them (no AI), shows a preview, and saves only what you confirm. Only incoming Zelle rows are kept. Your data is stored in
`data/workspaces/real/`, which is git-ignored and never leaves your computer except as client IDs and payment
statistics sent to the model (no names, no bank details; the one exception is an unusual-format Zelle email,
which the Email Reader has to read).


### Use it for your own studio

Encore was built and tested at DNA Dance Academy, but any small business paid through Zelle (dance or music teachers,
tutors, coaches) can use it. Add these optional lines to your `.env`:

```
STUDIO_NAME=Your Studio Name
ZELLE_EMAIL=payments@yourstudio.com
PRICE_PRIVATE=160
PRICE_GROUP=70
```

---

## Safety and cost controls

- **Privacy:** the model sees `C007`-style IDs, not names, phones, emails or non-Zelle bank rows (exception: the Email Reader reads the payer name in an unusual-format Zelle email). Names are put back in on your computer.
- **Prompt injection:** a code pre-screen blocks obvious attacks at zero tokens; replies are wrapped as untrusted data; the course provider's filter is a third layer; and the Reply agent has no tool that changes balances.
- **Invented facts:** amounts come only from code. Drafts may only use `{amount}`; reply drafts with dollar figures no tool returned are scrubbed.
- **Runaway cost:** strict daily token cap (400k; each call must fit at its maximum size before it runs), 20 calls per minute, 6 tool steps per agent, 12k output tokens per call. All are in `app/config.py`.

## Tests and scripts

```bash
python -m pytest -q                 # offline tests: parser, ledger, radar rules, draft checks, masking, injection screen
python scripts/make_sample_data.py  # regenerate the fictional sample data
python scripts/measure.py 3         # real token measurement (calls the model); writes docs/measurements.json
python scripts/screenshots.py       # demo walkthrough screenshots (needs `pip install playwright` and Chrome)
```

## Project layout

```
app/config.py    settings, .env lookup (repo, then parent), prices, budgets
app/bank.py      Chase CSV → incoming Zelle payments only
app/emails.py    Zelle notification emails → payments; scam detection
app/notes.py     pasted Apple Notes schedule / "who owes" list → lessons and balances
app/radar.py     payment-rhythm statistics (pure code)
app/ledger.py    exact balances from logged lessons (pure code)
app/agents.py    Radar, Collector, Reply handler + the checks around them
app/prompts.py   system prompts and JSON schemas
app/safety.py    masking, injection screen, draft verification
app/llm.py       Portkey client, tool loop, token log, budgets
app/server.py    FastAPI API
static/index.html  the whole UI
```
