#!/bin/zsh
# Invoked by launchd (see launchd/com.quangbui.gempicker.plist). launchd's
# environment is minimal -- no shell profile, no user PATH -- so everything
# this script needs is set explicitly rather than assumed.

set -euo pipefail

PROJECT_DIR="/Users/quangbui/Desktop/Gem Picker"
cd "$PROJECT_DIR"

export PATH="$HOME/.local/bin:$HOME/.nvm/versions/node/v20.19.4/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PYTHONPATH="$PROJECT_DIR/src"

TODAY="$(date +%F)"
LOG_FILE="$PROJECT_DIR/data/logs/${TODAY}.log"
mkdir -p "$PROJECT_DIR/data/logs"

{
    echo "=== gempicker run_daily.sh starting at $(date -u +%FT%TZ) ==="
    uv run python -m gempicker.cli run --date "$TODAY" --live
    echo "=== gempicker run_daily.sh finished at $(date -u +%FT%TZ) ==="
} >> "$LOG_FILE" 2>&1
