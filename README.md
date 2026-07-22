# Gem Picker

A daily small-cap stock/crypto picker. Every day it screens thousands of tickers against a
quantitative "gem" rubric, hands the shortlist to Claude for a final judgment call and risk
classification, and (once connected) executes a small real trade via Robinhood/Coinbase's
agentic-trading MCP servers. Designed to run unattended, but currently operated manually via the
dashboard while MCP execution gets wired up.

## How it works

### 1. Screening (deterministic, no LLM)

Two independent screeners run every time: `src/gempicker/screeners/stock_screener.py` and
`crypto_screener.py`. Both follow the same shape — hard filters first, then a weighted score over
whatever signals are available — and both write their top N candidates into a single shortlist
file at `data/shortlists/{date}.json`.

**Stocks:**
1. Pull the full US common-stock universe from Finnhub (~18,000 symbols), cached weekly.
2. Hard filter: market cap in `$STOCK_MIN_MARKET_CAP`–`$STOCK_MAX_MARKET_CAP` (default $50M–$2B),
   listed on a major exchange (NASDAQ/NYSE/CBOE/IEX — OTC and foreign-ADR listings are excluded;
   see "Data quality notes" below for why), minimum average dollar volume, and must have financial
   filings on record at SEC EDGAR (excludes shells).
3. Score the survivors on: revenue growth (SEC EDGAR XBRL, freshest of the ASC 606/legacy tags,
   annual periods only), net open-market insider buying vs. selling (parsed from individual Form 4
   XMLs — transaction codes P/S in dollars, not raw filing counts), price momentum (Finnhub's
   13-week return, with a penalty for being *too* overextended — chasing blow-off tops is
   explicitly discouraged), and social momentum (StockTwits + Reddit mention volume/sentiment;
   sources that return no data are excluded from the average, not defaulted to neutral).
4. Take the top `STOCK_SHORTLIST_SIZE` (default 8), enrich them with an FMP company-profile call
   (budget-capped at 250/day, so it's shortlist-only, never used for the broad screen).

**Crypto:**
1. Pull the top ~1000 coins by market cap from CoinGecko.
2. Hard filter: outside the top 100 by market cap (this is a *small*-cap picker) but above a
   junk floor, minimum 24h-volume/market-cap ratio, and — critically — must actually be listed on
   Coinbase (checked against Coinbase's live product list), since picking a coin that can't be
   bought is useless.
3. Score on: TVL/protocol-revenue trend (DeFiLlama, DeFi tokens only), on-chain transfer activity
   (Etherscan, EVM tokens only), social momentum (CoinGecko community data), and price momentum
   (same overextension penalty as stocks).
4. Take the top `CRYPTO_SHORTLIST_SIZE` (default 8).

**Scoring math** (`scoring/normalize.py`): each signal normalizes to 0–100, then a weighted
average is taken over whichever signals actually had data (missing data doesn't drag a score
down just because a free API had a gap) — but the result is then **dampened by
`sqrt(available_weight / total_weight)`**. This exists because of a real bug found in production:
a stock with only 1 of 4 signals available scored a "perfect" 100 because that one signal
absorbed 100% of the renormalized weight. The dampening means a candidate built on sparse evidence
can no longer out-rank a well-rounded one just because the little data it has looks good.

### 2. Judgment + execution (Claude, headless)

The `run` command hands the shortlist to Claude Code running non-interactively
(`claude -p --output-format json`, see `judge/`) with instructions to pick the single best
candidate across *both* asset classes (this system buys one thing per day, not one of each),
assign a risk tier (low/medium/high), and — if not a dry run — place a real trade using whichever
MCP server (Robinhood or Coinbase) matches the pick, then write a structured result to
`data/shortlists/{date}.result.json`. `judge/result_parser.py` validates that result: a dry run
that somehow contains an order is a hard error (catches Claude ignoring instructions), and a live
run with no order and no stated reason is also a hard error (catches a trade silently not
happening).

### 3. Recording

Every attempt is logged to `data/gempicker.db` (SQLite): the full shortlist, the pick, its score
breakdown, Claude's rationale and any red flags it weighed, and (if live) the order details. A
`(date, dry_run)` pair can only be claimed once — see "Idempotency & retries" below.

## Data quality notes (found the hard way)

A few real issues turned up while testing against live data, all fixed in code, worth knowing
about since they shape why the pipeline looks the way it does:

- **Finnhub's `/stock/candle` (OHLCV) endpoint is paywalled** on the free tier as of mid-2026
  (returns 403). Volume and momentum come from the still-free `/stock/metric` endpoint instead,
  which conveniently already provides average trading volume and pre-computed price-return
  percentages.
- **FMP's legacy `/api/v3/*` endpoints are dead** for any key created after August 2025; their
  newer `/stable/*` API also paywalls `key-metrics` and `stock-screener` on the free plan.
  `/stable/profile` is what's actually free and used here.
- **Finnhub's "US" stock universe includes OTC and foreign-ADR listings** (e.g. exchange values
  like `OTC MARKETS`, `ASX - ALL MARKETS`) that reliably fail the SEC EDGAR filings check anyway —
  but only after burning 2 extra rate-limited API calls each to check their liquidity first.
  These are now excluded right after the market-cap filter using data already fetched (the
  `exchange` field), for free — this alone roughly halves the expensive phase's candidate count.
- **Finnhub and FMP can materially disagree on market cap** for thinly-covered small-caps —
  verified live, one stock showed $667M on Finnhub vs $34.7M on FMP, a ~19x gap that would put it
  on opposite sides of the eligibility filter depending on which source you trust. Since only
  Finnhub gates the initial screen (FMP is budget-capped and shortlist-only), a >2x disagreement
  is now flagged (`market_cap_data_mismatch`) rather than silently trusted — visible in the
  dashboard and to Claude's judgment step.
- **Reddit works fine even with no credentials configured.** Every Reddit call catches auth
  failures and returns `None` (treated as neutral, not a red flag), so leaving
  `REDDIT_CLIENT_ID`/`REDDIT_CLIENT_SECRET` blank while an API application is pending doesn't
  break anything.

## Performance notes

Cold-cache runs are slow — the free-tier rate limits are the actual bottleneck, not the code.
Finnhub's free tier is 60 calls/min. Three mechanisms keep runs bounded and honest about it:

- **OTC is skipped before any spend**: 73% of Finnhub's raw "US" universe (~13,500 of ~18,400
  symbols) is OTC, identified from the `mic` field already present in the free universe list —
  so the real per-symbol profile workload is only the ~5,000 major-exchange symbols.
- **A per-run fetch budget** (`STOCK_PROFILE_FETCH_BUDGET`, default 1000) counts *every* profile
  network call — first-time fetches AND weekly-TTL refreshes (an earlier version counted file
  existence as "cached", letting expired files trigger unbudgeted refreshes). Beyond the budget,
  expired-but-present profiles are served stale (a weeks-old market cap still gates a $50M–$2B
  band fine) and never-seen symbols are deferred. Deferred symbols are sampled in a
  daily-seeded random order, so no fixed slice of the universe is systematically invisible.
- **`gempicker warm-cache`** backfills every missing/expired profile in one resumable,
  rate-limited pass (~1–2 hours from empty; interrupt and re-run freely). Run it once after
  setup and the daily screen starts from full universe coverage. Per-symbol TTLs are jittered
  (7–10 days) so a one-shot backfill doesn't expire all at once a week later.

The dashboard's Run tab streams live progress (`[stocks] ...1200/4960 symbols checked...`)
instead of a silent spinner, specifically because slow runs are indistinguishable from hangs.

A cross-process lock (`src/gempicker/lock.py`, `data/.pipeline.lock`) prevents two screens/runs
from ever executing concurrently — important because overlapping runs both waste the daily
new-lookup budget twice *and* risk exceeding Finnhub's combined rate limit. It's enforced at the
CLI level (`screen` and `run` both take it), not just in the UI, so it also protects against the
CLI and dashboard (or cron) colliding.

## Idempotency & retries

A `(date, dry_run)` pair can only be claimed once — re-running the same day/mode is a safe no-op
if it already succeeded. But a **failure** (`judge_error`/`error`/`lock_contention`) is always
immediately retryable, not a permanent block for the rest of the day — `claim_day()` in `db.py`
distinguishes "genuinely done" (`filled`/`skipped_dry_run`) from "failed, try again" from "still
running" (a `started` row less than 2 hours old). Every failure now also persists an
`error_message` (visible in the dashboard's "Inspect a day" view under a red error box) instead of
just a bare status code.

## Status: what's built vs. what's left

Built: both screeners (verified against live data), the confidence-aware scoring, SQLite trade
log with idempotency and error persistence, the Claude-headless judgment step, the Streamlit
dashboard, cross-process locking, and the launchd automation files.

**Required before this can run fully autonomously:**

1. ~~Fill in `.env` with real free-tier API keys~~ — done (Reddit client id/secret still blank,
   pending an API application; degrades gracefully without it, see above).
2. Connect the Robinhood and Coinbase agentic-trading MCP servers and fund their dedicated
   sub-accounts — run through `scripts/setup_mcp.sh` interactively and fill in
   `docs/mcp_tools_discovered.md` with the real tool names, then tighten `.claude/settings.json`
   from server-level grants to those specific tool names.
   - **Also required, verified live:** this workspace must be *trusted* before headless runs will
     honor `.claude/settings.json`'s permissions at all — run `claude` interactively here once and
     accept the trust dialog. Until then, headless runs silently ignore the permission allow-list.
3. Validate with several dry runs (`gempicker run`, no `--live`) before ever passing `--live`.
4. Only once 1-3 are done: install the launchd job (see below) to go fully autonomous.

In the meantime, use the dashboard to run picks manually and log your own manual buys against the
daily recommendation in the "My Trades" tab.

## Setup

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh   # if uv isn't installed
cd "Gem Picker"
uv sync
cp .env.example .env   # then fill in real API keys
```

## Usage

### Dashboard (recommended)

```sh
uv run streamlit run src/gempicker/ui/app.py
```

Opens a local browser UI with three tabs:

- **Run** — pick a date, toggle dry-run/live, click to run the pipeline (or "Quick screen only"
  to see candidates without spending an LLM call). Streams live progress. Buttons disable
  automatically while a run is in progress (checked across processes, not just this browser tab).
  Live requires MCP connected (step 2 above).
- **Dashboard** — history of past picks, and for any selected day: the winning pick, Claude's
  rationale and any red flags it weighed, the full shortlist it beat, and (if that attempt failed)
  the actual error message.
- **My Trades** — log what you actually bought by hand (date, symbol, amount spent, price paid);
  fetches live prices to show unrealized P&L per trade and in total. Useful right now since MCP
  execution isn't connected yet and you're buying manually off the daily recommendation.

### CLI

```sh
# one-time (or occasional) profile-cache backfill -- resumable, ~1-2h from empty
uv run python -m gempicker.cli warm-cache

# screen only (no LLM, no trades) -- inspect the shortlist
uv run python -m gempicker.cli screen

# full pipeline, dry-run (default) -- exercises the Claude judgment step but places no trade
uv run python -m gempicker.cli run

# full pipeline, LIVE -- places a real $5 trade
uv run python -m gempicker.cli run --live

# recent live picks / CSV export
uv run python -m gempicker.cli report
uv run python -m gempicker.cli export-csv
```

If `uv run python -m gempicker.cli ...` ever fails with `ModuleNotFoundError: No module named
'gempicker'`, prefix the command with `PYTHONPATH=src` — an editable-install quirk in this
environment occasionally resets; the dashboard and `run_daily.sh` already set this automatically.

## Enabling daily automation (launchd)

Only do this after the setup steps above are actually done and you've watched a few dry runs
succeed:

```sh
launchctl bootstrap gui/$(id -u) launchd/com.quangbui.gempicker.plist
launchctl kickstart -k gui/$(id -u)/com.quangbui.gempicker   # force an immediate test run
```

Logs land in `data/logs/`. To stop: `launchctl bootout gui/$(id -u)/com.quangbui.gempicker`.

## Project layout

```
src/gempicker/
  config.py            settings (.env-backed), paths
  models.py             pydantic models: ScoredCandidate, ShortlistPayload, JudgeResult
  db.py                 SQLite schema, idempotency (claim_day), trade log
  lock.py                cross-process lock preventing concurrent screen/run
  pipeline.py            orchestrates the two screeners into one shortlist, isolates failures
  cli.py                 typer app: screen / run / report / export-csv
  data_sources/          one module per free API (finnhub, coingecko, sec_edgar, fmp, ...)
  scoring/                normalize.py (shared math) + stock_scoring.py / crypto_scoring.py
  screeners/              stock_screener.py / crypto_screener.py -- the actual filter+score logic
  judge/                  prompt_builder, claude_runner (headless claude -p), result_parser
  trade_log.py            thin facade over db.py for recording picks
  ui/app.py               Streamlit dashboard
scripts/
  run_daily.sh            launchd entry point
  setup_mcp.sh             one-off interactive MCP connection checklist
launchd/
  com.quangbui.gempicker.plist
docs/
  mcp_tools_discovered.md fill in during MCP setup
tests/                    34 tests, fixture-based, no live API calls
```
