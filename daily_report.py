"""
Generate daily paper trading report. Builds PAPER_TRADING.md with full history.
Run by GitHub Actions daily at midnight UTC.
"""

import json
import csv
from datetime import datetime, timezone
from pathlib import Path
from collections import OrderedDict

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

    # Group equity snapshots by date, take last snapshot of each day
    daily_equity = OrderedDict()
    for row in equity_history:
        date_str = row["timestamp"][:10]
        daily_equity[date_str] = float(row["equity"])

    # Count realized PnL per day from trades
    daily_realized = {}
    for t in trades:
        date_str = t["timestamp"][:10]
        pnl = float(t.get("pnl", 0))
        daily_realized[date_str] = daily_realized.get(date_str, 0) + pnl

    # Build daily rows
    dates = list(daily_equity.keys())
    rows = []
    prev_equity = INITIAL_CAPITAL

    for i, date_str in enumerate(dates):
        day_num = i + 1
        equity = daily_equity[date_str]
        total_ret = (equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
        daily_pnl = equity - prev_equity
        daily_pct = (equity - prev_equity) / prev_equity * 100
        realized = daily_realized.get(date_str, 0)

        rows.append({
            "day": day_num,
            "date": date_str,
            "equity": equity,
            "total_ret": total_ret,
            "daily_pnl": daily_pnl,
            "daily_pct": daily_pct,
            "realized": realized,
        })

        prev_equity = equity

    # Current state
    equity = state["equity"]
    total_pnl = state["total_pnl"]
    peak = state["peak_equity"]
    dd_pct = (peak - equity) / peak * 100 if peak > 0 else 0
    total_ret = (equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    positions = state["positions"]
    start_time = state["start_time"]

    if positions:
        pos_str = ", ".join(f"{'SHORT' if v < 0 else 'LONG'} {k} ${abs(v):.0f}" for k, v in positions.items())
    else:
        pos_str = "Flat"

    # Build the report
    content = f"""# Paper Trading Results

Starting Capital: **$700** | Strategy: `strategy_live.py` | Started: {start_time[:10]}

## Daily Performance

| Day | Date | Equity | Daily P&L | Daily % | Total Return | Realized PnL |
|-----|------|--------|-----------|---------|-------------|-------------|
"""

    for r in rows:
        content += (
            f"| {r['day']} | {r['date']} | ${r['equity']:.2f} | "
            f"${r['daily_pnl']:+.2f} | {r['daily_pct']:+.2f}% | "
            f"{r['total_ret']:+.2f}% | ${r['realized']:+.4f} |\n"
        )

    content += f"""
## Current Status

| Metric | Value |
|--------|-------|
| Equity | **${equity:.2f}** |
| Total Return | **{total_ret:+.2f}%** |
| Realized PnL | ${total_pnl:.4f} |
| Peak Equity | ${peak:.2f} |
| Max Drawdown | {dd_pct:.2f}% |
| Positions | {pos_str} |
| Last Updated | {now.strftime('%Y-%m-%d %H:%M UTC')} |
"""

    REPORT_FILE.write_text(content)
    print(f"Report updated: {REPORT_FILE}")
    print(f"  Equity: ${equity:.2f} ({total_ret:+.2f}%)")


if __name__ == "__main__":
    generate_report()
