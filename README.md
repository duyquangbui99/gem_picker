# Gem Picker

Fully autonomous daily small-cap stock/crypto picker. See `.claude/plans/project-automated-small-cap-stock-crypto-keen-backus.md`
(or ask Claude) for the full design writeup — this file is just the setup/usage cheat sheet.

## Status

Built: deterministic screening pipeline (stock + crypto), scoring, SQLite trade log with
same-day idempotency, the Claude-headless judgment/execution step, and the launchd automation
files. **Not yet done, and required before this can run for real:**

1. Fill in `.env` with real free-tier API keys (see `.env.example` for the list — Finnhub,
   FMP, CoinGecko, Etherscan, Reddit, SEC EDGAR contact email).
2. Connect the Robinhood and Coinbase agentic-trading MCP servers and fund their dedicated
   sub-accounts — run through `scripts/setup_mcp.sh` interactively and fill in
   `docs/mcp_tools_discovered.md` with the real tool names, then tighten `.claude/settings.json`
   from server-level grants to those specific tool names.
3. Validate with `uv run python -m gempicker.cli run --date $(date +%F)` (dry-run by default)
   a few times before ever passing `--live`.
4. Only once 1-3 are done: install the launchd job (see below) to go fully autonomous.

## Setup

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh   # if uv isn't installed
cd "Gem Picker"
uv sync
cp .env.example .env   # then fill in real API keys
```

## Usage

```sh
# screen only (no LLM, no trades) -- inspect the shortlist
PYTHONPATH=src uv run python -m gempicker.cli screen

# full pipeline, dry-run (default) -- exercises the Claude judgment step but places no trade
PYTHONPATH=src uv run python -m gempicker.cli run

# full pipeline, LIVE -- places a real $5 trade
PYTHONPATH=src uv run python -m gempicker.cli run --live

# recent live picks / CSV export
PYTHONPATH=src uv run python -m gempicker.cli report
PYTHONPATH=src uv run python -m gempicker.cli export-csv
```

(`PYTHONPATH=src` is a workaround for an editable-install quirk in this environment — see
git history / ask Claude if `uv run gempicker ...` starts working without it and this can be
dropped.)

## Enabling daily automation (launchd)

Only do this after steps 1-3 above are actually done and you've watched a few dry runs succeed:

```sh
launchctl bootstrap gui/$(id -u) launchd/com.quangbui.gempicker.plist
launchctl kickstart -k gui/$(id -u)/com.quangbui.gempicker   # force an immediate test run
```

Logs land in `data/logs/`. To stop: `launchctl bootout gui/$(id -u)/com.quangbui.gempicker`.
