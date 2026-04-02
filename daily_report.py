"""
Generate daily paper trading report. Appends today's stats to PAPER_TRADING.md.
Run by GitHub Actions daily at midnight UTC.
"""

import json
import csv
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / "paper_trading" / "state.json"
EQUITY_LOG = BASE_DIR / "paper_trading" / "equity.csv"
TRADE_LOG = BASE_DIR / "paper_trading" / "trades.csv"
REPORT_FILE = BASE_DIR / "PAPER_TRADING.md"
INITIAL_CAPITAL = 700.0


def load_state():
    if not STATE_FILE.exists():
        return None
    with open(STATE_FILE) as f:
        return json.load(f)


def load_equity_history():
    if not EQUITY_LOG.exists():
        return []
    rows = []
    with open(EQUITY_LOG) as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def load_trades():
    if not TRADE_LOG.exists():
        return []
    rows = []
    with open(TRADE_LOG) as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def generate_report():
    state = load_state()
    equity_history = load_equity_history()
    trades = load_trades()
    now = datetime.now(timezone.utc)

    if not state:
        print("No state file found. Skipping report.")
        return

    equity = state["equity"]
    cash = state["cash"]
    peak = state["peak_equity"]
    total_pnl = state["total_pnl"]
    total_trades = state["total_trades"]
    wins = state["wins"]
    losses = state["losses"]
    positions = state["positions"]
    start_time = state["start_time"]

    ret_pct = (equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    dd_pct = (peak - equity) / peak * 100 if peak > 0 else 0
    win_rate = wins / max(total_trades, 1) * 100

    # Calculate today's return
    today_str = now.strftime("%Y-%m-%d")
    today_equities = [float(r["equity"]) for r in equity_history
                      if r["timestamp"].startswith(today_str)]
    yesterday_equities = []
    if len(equity_history) > 0:
        # Find last equity before today
        for r in reversed(equity_history):
            if not r["timestamp"].startswith(today_str):
                yesterday_equities.append(float(r["equity"]))
                break

    if yesterday_equities and today_equities:
        daily_ret = (today_equities[-1] - yesterday_equities[0]) / yesterday_equities[0] * 100
        daily_pnl = today_equities[-1] - yesterday_equities[0]
    else:
        daily_ret = 0.0
        daily_pnl = 0.0

    # Today's trades
    today_trades = [t for t in trades if t["timestamp"].startswith(today_str)]

    # Positions string
    if positions:
        pos_str = ", ".join(f"{'SHORT' if v < 0 else 'LONG'} {k} ${abs(v):.0f}" for k, v in positions.items())
    else:
        pos_str = "Flat"

    # Build today's row
    today_row = (
        f"| {today_str} | ${equity:.2f} | {ret_pct:+.2f}% | "
        f"${daily_pnl:+.2f} | {daily_ret:+.2f}% | "
        f"{dd_pct:.2f}% | {len(today_trades)} | {pos_str} |"
    )

    # Create or update the report file
    if REPORT_FILE.exists():
        content = REPORT_FILE.read_text()
        # Check if today's date already exists
        if today_str in content:
            # Replace today's row
            lines = content.split("\n")
            new_lines = []
            for line in lines:
                if line.startswith(f"| {today_str}"):
                    new_lines.append(today_row)
                else:
                    new_lines.append(line)
            content = "\n".join(new_lines)
        else:
            # Append today's row before the summary section
            if "## Summary" in content:
                parts = content.split("## Summary")
                parts[0] = parts[0].rstrip() + "\n" + today_row + "\n\n"
                content = parts[0] + "## Summary" + parts[1]
            else:
                content = content.rstrip() + "\n" + today_row + "\n"

        # Update summary section
        summary = f"""## Summary

| Metric | Value |
|--------|-------|
| Starting Capital | ${INITIAL_CAPITAL:.2f} |
| Current Equity | ${equity:.2f} |
| Total Return | {ret_pct:+.2f}% |
| Total PnL (realized) | ${total_pnl:.4f} |
| Peak Equity | ${peak:.2f} |
| Max Drawdown | {dd_pct:.2f}% |
| Total Trades | {total_trades} |
| Win Rate | {win_rate:.1f}% |
| Running Since | {start_time[:10]} |
| Last Updated | {now.strftime('%Y-%m-%d %H:%M UTC')} |
"""
        if "## Summary" in content:
            content = content.split("## Summary")[0] + summary
        else:
            content += "\n" + summary

    else:
        # Create new file
        content = f"""# Paper Trading Results

Capital: ${INITIAL_CAPITAL:.2f} | Strategy: strategy_live.py | Exchange: Hyperliquid (paper)

## Daily Performance

| Date | Equity | Total Return | Daily P&L | Daily % | Drawdown | Trades | Positions |
|------|--------|-------------|-----------|---------|----------|--------|-----------|
{today_row}

## Summary

| Metric | Value |
|--------|-------|
| Starting Capital | ${INITIAL_CAPITAL:.2f} |
| Current Equity | ${equity:.2f} |
| Total Return | {ret_pct:+.2f}% |
| Total PnL (realized) | ${total_pnl:.4f} |
| Peak Equity | ${peak:.2f} |
| Max Drawdown | {dd_pct:.2f}% |
| Total Trades | {total_trades} |
| Win Rate | {win_rate:.1f}% |
| Running Since | {start_time[:10]} |
| Last Updated | {now.strftime('%Y-%m-%d %H:%M UTC')} |
"""

    REPORT_FILE.write_text(content)
    print(f"Report updated: {REPORT_FILE}")
    print(f"  Equity: ${equity:.2f} ({ret_pct:+.2f}%)")
    print(f"  Today: ${daily_pnl:+.2f} ({daily_ret:+.2f}%)")
    print(f"  Positions: {pos_str}")


if __name__ == "__main__":
    generate_report()
