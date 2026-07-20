#!/bin/sh
# One-off, INTERACTIVE setup for connecting Robinhood + Coinbase agentic
# trading MCP servers to Claude Code. Not meant to run unattended or from
# cron -- run each step by hand from an interactive `claude` session in this
# project directory, since both require you to complete an OAuth login in a
# browser and fund a dedicated trading sub-account.
#
# After running this, record the exact tool names Claude discovers on each
# server in docs/mcp_tools_discovered.md, then tighten the mcp__* entries in
# .claude/settings.json from server-level grants down to the specific
# trade-tool names.

set -e

echo "Step 0: Trust this workspace"
echo "-----------------------------------"
echo "Verified live: headless 'claude -p' silently IGNORES all of .claude/settings.json's"
echo "permissions.allow/deny rules until this workspace has been explicitly trusted --"
echo "it prints 'Ignoring N permissions.allow entries ... this workspace has not been"
echo "trusted' and falls back to default behavior instead. This defeats the whole"
echo "purpose of the narrow MCP tool allow-list for unattended runs."
echo "Fix: run 'claude' interactively in this directory once and accept the trust"
echo "dialog (or set projects[\"<this path>\"].hasTrustDialogAccepted: true in"
echo "~/.claude.json yourself if you understand what that skips). Do this BEFORE"
echo "relying on any headless/cron run for real."
echo

echo "Step 1: Robinhood Agentic Trading"
echo "-----------------------------------"
echo "Already registered in .mcp.json as 'robinhood-trading'."
echo "Run inside Claude Code (interactively, in this project dir): /mcp"
echo "Follow the OAuth flow, then open/fund your Agentic sub-account on desktop"
echo "(mobile requires copying the onboarding URL into a browser)."
echo

echo "Step 2: Coinbase for Agents"
echo "-----------------------------------"
echo "The exact consumer MCP endpoint URL was not publicly documented as of"
echo "this project's creation. Find it via Coinbase's own onboarding flow"
echo "(check coinbase.com for 'Coinbase for Agents' / 'agentic trading' in"
echo "account settings), then replace the placeholder URL for the 'coinbase'"
echo "server in .mcp.json with the real one before running /mcp again."
echo

echo "Step 3: Discover exact tool names"
echo "-----------------------------------"
echo "In an interactive Claude Code session in this directory, ask Claude to"
echo "list the tools available on each of the robinhood-trading and coinbase"
echo "MCP servers. Record the exact names + parameters in"
echo "docs/mcp_tools_discovered.md."
echo

echo "Step 4: Tighten permissions"
echo "-----------------------------------"
echo "Edit .claude/settings.json: replace the broad 'mcp__robinhood-trading'"
echo "and 'mcp__coinbase' server-level grants with the specific trade-tool"
echo "names discovered in Step 3 (e.g. mcp__robinhood-trading__place_order),"
echo "so the unattended judge step can't invoke an unexpected tool (like a"
echo "transfer/withdraw tool) the server happens to also expose."
