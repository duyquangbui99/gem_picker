from pathlib import Path

RISK_TIER_GUIDANCE = """\
- low: upper end of the cap range, real revenue/earnings growth or strong TVL/adoption trend, high liquidity, moderate volatility.
- medium: mid small-cap, growth-stage/speculative but with real fundamentals, moderate liquidity, higher volatility.
- high: micro-cap end, pre-revenue/thin fundamentals or deep speculative crypto, high volatility, sentiment-driven.\
"""

DRY_RUN_ORDER_FIELD = '  "order": null   // dry-run: this must always be null'

LIVE_ORDER_FIELD = """\
  "order": {
    "order_id": "<from the MCP tool's response>",
    "mcp_tool_called": "<exact tool name you called, e.g. mcp__robinhood-trading__place_order>",
    "filled_price": <number or null if not yet available>,
    "filled_qty": <number or null>,
    "filled_usd": <number, should be close to the trade amount>,
    "raw_response": { ...whatever the MCP tool returned, verbatim... }
  }\
"""


def _result_schema(order_field: str) -> str:
    return f"""\
{{
  "date": "<YYYY-MM-DD, matches the shortlist>",
  "dry_run": <true|false>,
  "asset_class": "stock" | "crypto",
  "symbol": "<the ticker or crypto symbol you picked>",
  "score": <its composite score from the shortlist>,
  "risk_tier": "low" | "medium" | "high",
  "rationale": "<2-4 sentences on why this beat the field>",
  "red_flags": ["<any qualitative concerns you weighed, even if you still picked it>"],
{order_field}
}}\
"""


def build_prompt(shortlist_path: Path, result_path: Path, trade_date: str, trade_usd: float, dry_run: bool) -> str:
    if dry_run:
        mode_instructions = """\
This is a DRY RUN. Do NOT call any Robinhood or Coinbase MCP trade/order tool under any
circumstances — not even to "preview" an order. Read-only MCP calls (checking quotes, balances,
positions) are fine if you find them useful, but no order may be placed. Set "order" to null in
your result JSON."""
        result_schema = _result_schema(DRY_RUN_ORDER_FIELD)
    else:
        mode_instructions = f"""\
This is LIVE. After picking the winning candidate, place a real ${trade_usd:.2f} market buy
order for it:
- If you picked a stock, use the Robinhood Agentic Trading MCP server's order-placement tool.
- If you picked a crypto asset, use the Coinbase MCP server's order-placement tool, targeting the
  `coinbase_product_id` given for that candidate in the shortlist (not the raw CoinGecko symbol).
Populate the "order" field in your result JSON with the tool's response. If the trade tool call
fails or errors, do not fabricate order details — instead set "order" to null, note the failure in
"red_flags", and still write the result file so the failure is visible in the log."""
        result_schema = _result_schema(LIVE_ORDER_FIELD)

    return f"""\
You are the daily judgment step of an automated small-cap stock/crypto "gem picker". A deterministic
Python pipeline has already screened and scored today's candidates; your job is narrow: pick the
single best one across both asset classes, classify its risk, and (if live) execute the trade.

Read the shortlist at: {shortlist_path}

It contains up to two lists — "stocks" and "crypto" — each with candidates that already passed hard
quantitative filters (market cap range, liquidity, Coinbase-tradeability for crypto, etc.) and carry
a normalized 0-100 composite "score" plus a "score_breakdown" of the signals that produced it.

Your task:
1. Pick the single best candidate across BOTH lists combined (this system buys one asset per day,
   total — not one of each). Use each candidate's "score" as a strong prior, but you may weigh
   qualitative red flags the automated score can't see (e.g. recent bad news, a halted ticker, an
   obvious data-quality glitch) — if you override the top score for such a reason, say so explicitly
   in "red_flags".
   - A "market_cap_data_mismatch" flag means two data providers disagree materially on market cap.
     You have no way to verify which figure is right (no web access here) or why they disagree — a
     common real-world cause is a dual-class share structure, where the small figure is the tradeable
     public float and the large one is total company value across a founder/insider-held share class
     you cannot buy. If live, treat this flag as a strong reason to prefer a different candidate;
     only pick a flagged one if it's clearly the best of a bad field, and if you do, set
     risk_tier to "high" and say explicitly in "red_flags" that the tradeable float may be far
     smaller than the quoted score's market cap assumed.
2. If NO candidate exists in either list (empty shortlist), or all candidates in both lists have
   below-zero or clearly degenerate scores, you must still pick the least-bad available candidate —
   this system never skips a day. Only report a genuine error if BOTH lists are completely empty
   (nothing to pick from at all).
3. Assign a risk_tier using this rubric:
{RISK_TIER_GUIDANCE}

{mode_instructions}

Finally, write your result as a single JSON object to: {result_path}
Use exactly this shape:
{result_schema}

The "date" field must be exactly "{trade_date}". Write ONLY the result file — do not modify any
other files. Do not use Bash or WebSearch; everything you need is either in the shortlist file or
behind the Robinhood/Coinbase MCP tools.
"""
