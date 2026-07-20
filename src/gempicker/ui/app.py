"""Local dashboard for Gem Picker. Run with:

    uv run streamlit run src/gempicker/ui/app.py

(the sys.path bootstrap below makes `gempicker` importable regardless of
this environment's flaky editable-install state, so no PYTHONPATH needed)
"""

import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[2]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from gempicker.config import PROJECT_ROOT, get_settings  # noqa: E402
from gempicker.data_sources.base import new_session  # noqa: E402
from gempicker.db import (  # noqa: E402
    add_manual_trade,
    delete_manual_trade,
    get_connection,
    get_manual_trades,
    get_pick_for_date,
    get_recent_picks,
    get_shortlist_for_date,
)
from gempicker.lock import lock_status  # noqa: E402
from gempicker.pricing import get_current_price, resolve_coingecko_id  # noqa: E402

st.set_page_config(page_title="Gem Picker", page_icon="💎", layout="wide")

settings = get_settings()
conn = get_connection(settings.db_path)

if "pipeline_running" not in st.session_state:
    st.session_state.pipeline_running = False


def run_pipeline_streaming(cmd: list[str], output_placeholder) -> int:
    """Streams the subprocess's combined stdout/stderr into `output_placeholder`
    line by line as it arrives, instead of buffering everything until exit —
    the pipeline can legitimately take minutes on a cold cache, and showing
    nothing the whole time makes it indistinguishable from actually hanging.
    Mirrors scripts/run_daily.sh's environment setup (PATH/PYTHONPATH) so
    this behaves identically to a cron-triggered run."""
    env = os.environ.copy()
    env["PATH"] = (
        f"{Path.home()}/.local/bin:"
        f"{Path.home()}/.nvm/versions/node/v20.19.4/bin:"
        f"{env.get('PATH', '/usr/bin:/bin')}"
    )
    env["PYTHONPATH"] = str(SRC_DIR)
    env["PYTHONUNBUFFERED"] = "1"  # otherwise child stdout stays block-buffered and "streaming" shows nothing until exit

    process = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    lines: list[str] = []
    for line in process.stdout:
        lines.append(line.rstrip("\n"))
        output_placeholder.code("\n".join(lines[-80:]), language="text")
    process.wait()
    return process.returncode


st.title("💎 Gem Picker")

tab_run, tab_dashboard, tab_trades = st.tabs(["▶ Run", "📊 Dashboard", "💰 My Trades"])

# ----------------------------------------------------------------------
# RUN
# ----------------------------------------------------------------------
with tab_run:
    st.subheader("Run the daily pipeline")
    st.caption(
        "Screen -> Claude judgment -> (if Live) real trade via the Robinhood/Coinbase MCP tools. "
        "A given (date, mode) can only run once — re-running the same day/mode is a safe no-op."
    )

    col1, col2 = st.columns([1, 2])
    with col1:
        run_date = st.date_input("Date", value=date.today())
    with col2:
        live = st.toggle(
            "Live (places a real $%.2f trade)" % settings.gempicker_trade_usd,
            value=False,
            help="Requires the Robinhood/Coinbase MCP servers connected and this workspace trusted. Leave off for a dry run.",
        )
        if live:
            st.warning(
                f"LIVE mode: if MCP is connected, this will place a real ${settings.gempicker_trade_usd:.2f} trade. "
                "Make sure that's actually what you want before clicking Run."
            )

    held_by = lock_status(settings.data_dir)
    if held_by and not st.session_state.pipeline_running:
        st.info(
            f"A gempicker run is already in progress (pid {held_by}) — started from another tab, the CLI, "
            "or cron. Buttons are disabled until it finishes."
        )
    busy = st.session_state.pipeline_running or held_by is not None

    run_clicked = st.button("▶ Run pipeline now", type="primary", disabled=busy)
    screen_clicked = st.button(
        "🔍 Quick screen only (no LLM, no trade — just see today's candidates)", disabled=busy
    )

    if (run_clicked or screen_clicked) and not busy:
        st.session_state.pipeline_running = True
        date_str = run_date.isoformat()
        if run_clicked:
            cmd = ["uv", "run", "python", "-m", "gempicker.cli", "run", "--date", date_str]
            if live:
                cmd.append("--live")
            spinner_text = "Running screeners + Claude judgment... this can take a few minutes, especially on a cold cache"
        else:
            cmd = ["uv", "run", "python", "-m", "gempicker.cli", "screen", "--date", date_str]
            spinner_text = "Screening today's candidates..."

        output_placeholder = st.empty()
        try:
            with st.spinner(spinner_text):
                returncode = run_pipeline_streaming(cmd, output_placeholder)
        finally:
            st.session_state.pipeline_running = False

        if returncode == 0:
            st.success("Done.")
            if run_clicked:
                st.info("Check the Dashboard tab for the pick and its reasoning.")
        else:
            st.error(f"Exited with code {returncode} — see output above.")

# ----------------------------------------------------------------------
# DASHBOARD
# ----------------------------------------------------------------------
with tab_dashboard:
    st.subheader("Recent picks")
    history = get_recent_picks(conn, limit=30, dry_run=None)

    if not history:
        st.info("No picks yet — run the pipeline from the Run tab first.")
    else:
        hist_df = pd.DataFrame(
            [
                {
                    "Date": r["trade_date"],
                    "Mode": "LIVE" if not r["dry_run"] else "dry-run",
                    "Asset": r["asset_class"],
                    "Symbol": r["symbol"],
                    "Score": r["score"],
                    "Risk": r["risk_tier"],
                    "Status": r["order_status"],
                }
                for r in history
            ]
        )
        st.dataframe(hist_df, width="stretch", hide_index=True)

        st.divider()
        available_dates = sorted({r["trade_date"] for r in history}, reverse=True)
        selected_date = st.selectbox("Inspect a day", available_dates)

        pick = get_pick_for_date(conn, selected_date)
        shortlist = get_shortlist_for_date(conn, selected_date)

        if pick:
            st.markdown(f"### {pick['trade_date']} — picked **{pick['asset_class']} {pick['symbol']}**")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Score", pick["score"])
            m2.metric("Risk tier", pick["risk_tier"])
            m3.metric("Mode", "LIVE" if not pick["dry_run"] else "dry-run")
            m4.metric("Status", pick["order_status"])

            if pick["order_status"] in ("judge_error", "error", "lock_contention"):
                st.error("This attempt failed. Error details:")
                st.code(pick["error_message"] or "(no error message captured)", language="text")
                st.caption("This day's slot is retryable — click Run again on the Run tab.")

            st.markdown("**Rationale:**")
            st.write(pick["rationale"] or "_(none recorded)_")

            red_flags = json.loads(pick["red_flags_json"] or "[]")
            if red_flags:
                st.markdown("**Red flags Claude weighed:**")
                for flag in red_flags:
                    st.markdown(f"- {flag}")

            if not pick["dry_run"] and pick["order_status"] == "filled":
                st.markdown(
                    f"**Order:** {pick['filled_qty']} units @ ${pick['filled_price']} "
                    f"(${pick['filled_usd']:.2f} total, order_id `{pick['order_id']}`)"
                )

        if shortlist:
            st.markdown("**Full shortlist that day** (what the pick beat):")
            col_s, col_c = st.columns(2)
            for col, key, label in ((col_s, "stocks", "Stocks"), (col_c, "crypto", "Crypto")):
                with col:
                    st.markdown(f"_{label}_")
                    candidates = shortlist.get(key, [])
                    if not candidates:
                        st.caption("(none passed screening that day)")
                        continue
                    df = pd.DataFrame(
                        [{"Symbol": c["symbol"], "Score": c["score"], "Flags": ", ".join(c.get("flags", []))} for c in candidates]
                    ).sort_values("Score", ascending=False)
                    st.dataframe(df, width="stretch", hide_index=True)

# ----------------------------------------------------------------------
# MY TRADES
# ----------------------------------------------------------------------
with tab_trades:
    st.subheader("Log a trade you placed manually")
    st.caption(
        "Since MCP execution isn't connected yet (or you're just choosing to buy by hand), log what you "
        "actually bought here to track real profit/loss against live prices."
    )

    with st.form("add_trade", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            t_date = st.date_input("Date bought", value=date.today())
            t_asset_class = st.selectbox("Asset class", ["stock", "crypto"])
        with c2:
            t_symbol = st.text_input("Symbol (e.g. AAPL or BTC)").strip().upper()
            t_amount = st.number_input("Amount spent ($)", min_value=0.01, value=5.00, step=0.01)
        with c3:
            t_price = st.number_input("Price paid per unit ($)", min_value=0.00000001, value=1.00, format="%.8f")
            t_notes = st.text_input("Notes (optional)")

        submitted = st.form_submit_button("➕ Add trade")
        if submitted:
            if not t_symbol:
                st.error("Symbol is required.")
            else:
                coingecko_id = None
                if t_asset_class == "crypto":
                    session = new_session("gempicker/0.1 (ui)")
                    coingecko_id = resolve_coingecko_id(session, settings.cache_dir, t_symbol)
                    if not coingecko_id:
                        st.warning(f"Couldn't resolve '{t_symbol}' on CoinGecko — trade saved, but live P&L won't work for it.")
                add_manual_trade(
                    conn,
                    trade_date=t_date.isoformat(),
                    asset_class=t_asset_class,
                    symbol=t_symbol,
                    amount_usd=t_amount,
                    price_paid=t_price,
                    coingecko_id=coingecko_id,
                    notes=t_notes or None,
                )
                st.success(f"Logged {t_symbol}.")
                st.rerun()

    st.divider()
    st.subheader("Your trades & P&L")

    trades = get_manual_trades(conn)
    if not trades:
        st.info("No manual trades logged yet.")
    else:
        if "price_cache" not in st.session_state:
            st.session_state.price_cache = {}

        if st.button("🔄 Refresh live prices"):
            st.session_state.price_cache = {}

        rows = []
        total_invested = 0.0
        total_current = 0.0
        for t in trades:
            cache_key = (t["asset_class"], t["symbol"], t["coingecko_id"])
            if cache_key not in st.session_state.price_cache:
                st.session_state.price_cache[cache_key] = get_current_price(
                    settings, t["asset_class"], t["symbol"], t["coingecko_id"]
                )
            current_price = st.session_state.price_cache[cache_key]

            current_value = current_price * t["quantity"] if current_price else None
            pnl = (current_value - t["amount_usd"]) if current_value is not None else None
            pnl_pct = (pnl / t["amount_usd"] * 100) if pnl is not None else None

            total_invested += t["amount_usd"]
            if current_value is not None:
                total_current += current_value

            rows.append(
                {
                    "id": t["id"],
                    "Date": t["trade_date"],
                    "Asset": t["asset_class"],
                    "Symbol": t["symbol"],
                    "Spent ($)": round(t["amount_usd"], 2),
                    "Price paid": t["price_paid"],
                    "Qty": t["quantity"],
                    "Current price": current_price,
                    "Current value ($)": round(current_value, 2) if current_value is not None else None,
                    "P&L ($)": round(pnl, 2) if pnl is not None else None,
                    "P&L (%)": round(pnl_pct, 1) if pnl_pct is not None else None,
                    "Notes": t["notes"] or "",
                }
            )

        df = pd.DataFrame(rows)

        m1, m2, m3 = st.columns(3)
        m1.metric("Total invested", f"${total_invested:,.2f}")
        m2.metric("Current value", f"${total_current:,.2f}")
        total_pnl = total_current - total_invested
        m3.metric(
            "Total P&L",
            f"${total_pnl:,.2f}",
            delta=f"{(total_pnl / total_invested * 100):.1f}%" if total_invested else None,
        )

        st.dataframe(df.drop(columns=["id"]), width="stretch", hide_index=True)

        with st.expander("Delete a trade"):
            to_delete = st.selectbox(
                "Select a trade to delete",
                options=[None] + [t["id"] for t in trades],
                format_func=lambda i: "—" if i is None else next(f"{t['trade_date']} {t['symbol']}" for t in trades if t["id"] == i),
            )
            if to_delete and st.button("🗑 Delete", type="secondary"):
                delete_manual_trade(conn, to_delete)
                st.rerun()
