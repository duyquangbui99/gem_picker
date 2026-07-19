# MCP tools discovered during setup

Fill this in during `scripts/setup_mcp.sh` Step 3, then use it to tighten
`.claude/settings.json` from server-level grants to specific tool names.

## robinhood-trading (`https://agent.robinhood.com/mcp/trading`)

| Tool name | Purpose | Key parameters |
|---|---|---|
| _(fill in)_ | | |

Order-placement tool to use in `judge/prompt_builder.py` live instructions: `_(fill in)_`

## coinbase

Actual endpoint URL used (replace the placeholder in `.mcp.json` first): `_(fill in)_`

| Tool name | Purpose | Key parameters |
|---|---|---|
| _(fill in)_ | | |

Order-placement tool to use in `judge/prompt_builder.py` live instructions: `_(fill in)_`
Confirm the parameter that identifies the product (should accept the `coinbase_product_id`, e.g. `"BTC-USD"`, exactly as CoinBase's own product list names it — this is what `coinbase_products.py` filters against).
