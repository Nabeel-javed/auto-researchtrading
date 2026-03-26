"""
Hyperliquid Paper Trading Executor
Runs strategy_live.py against real market data on Hyperliquid testnet.
Designed to run every hour via cron.

Usage:
    python executor.py              # Run one iteration (for cron)
    python executor.py --loop       # Run continuously (every hour)
    python executor.py --status     # Show current status
"""

import os
import sys
import json
import time
import logging
import argparse
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Directories
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "paper_trading"
LOG_DIR = DATA_DIR / "logs"
STATE_FILE = DATA_DIR / "state.json"
TRADE_LOG = DATA_DIR / "trades.csv"
EQUITY_LOG = DATA_DIR / "equity.csv"
CONFIG_FILE = BASE_DIR / "config.json"

# Hyperliquid API
TESTNET_API = "https://api.hyperliquid-testnet.xyz"
MAINNET_API = "https://api.hyperliquid.xyz"

# Strategy config
SYMBOLS = ["BTC", "ETH", "SOL"]
LOOKBACK_BARS = 500
INITIAL_CAPITAL = 700.0  # Your starting capital

# Coin mapping (Hyperliquid uses coin names without -PERP for API)
HL_COINS = {"BTC": "BTC", "ETH": "ETH", "SOL": "SOL"}

# ---------------------------------------------------------------------------
# Setup logging
# ---------------------------------------------------------------------------

def setup_logging():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    log_file = LOG_DIR / f"executor_{datetime.now().strftime('%Y%m%d')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(),
        ],
    )
    return logging.getLogger("executor")

# ---------------------------------------------------------------------------
# Hyperliquid Data Fetcher (no auth needed for market data)
# ---------------------------------------------------------------------------

class HyperliquidData:
    """Fetches market data from Hyperliquid API (works without auth)."""

    def __init__(self, api_url=MAINNET_API):
        self.api_url = api_url
        self.info_url = f"{api_url}/info"

    def get_candles(self, coin: str, interval: str = "1h", lookback: int = LOOKBACK_BARS) -> pd.DataFrame:
        """Fetch historical candles."""
        end_ms = int(time.time() * 1000)
        # Request extra bars to ensure we have enough after filtering
        start_ms = end_ms - (lookback + 50) * 3600 * 1000

        body = {
            "type": "candleSnapshot",
            "req": {
                "coin": coin,
                "interval": interval,
                "startTime": start_ms,
                "endTime": end_ms,
            },
        }

        try:
            resp = requests.post(self.info_url, json=body, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logging.error(f"Failed to fetch candles for {coin}: {e}")
            return pd.DataFrame()

        if not data:
            return pd.DataFrame()

        rows = []
        for bar in data:
            rows.append({
                "timestamp": int(bar["t"]),
                "open": float(bar["o"]),
                "high": float(bar["h"]),
                "low": float(bar["l"]),
                "close": float(bar["c"]),
                "volume": float(bar["v"]),
            })

        df = pd.DataFrame(rows).sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
        return df.tail(lookback).reset_index(drop=True)

    def get_funding_history(self, coin: str, hours: int = LOOKBACK_BARS) -> pd.DataFrame:
        """Fetch funding rate history."""
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - hours * 3600 * 1000

        body = {
            "type": "fundingHistory",
            "coin": coin,
            "startTime": start_ms,
            "endTime": end_ms,
        }

        try:
            resp = requests.post(self.info_url, json=body, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logging.error(f"Failed to fetch funding for {coin}: {e}")
            return pd.DataFrame(columns=["timestamp", "funding_rate"])

        if not data:
            return pd.DataFrame(columns=["timestamp", "funding_rate"])

        rows = [{"timestamp": int(r["time"]), "funding_rate": float(r["fundingRate"])} for r in data]
        return pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)

    def get_mid_price(self, coin: str) -> float:
        """Get current mid price."""
        body = {"type": "allMids"}
        try:
            resp = requests.post(self.info_url, json=body, timeout=10)
            resp.raise_for_status()
            mids = resp.json()
            return float(mids.get(coin, 0))
        except Exception as e:
            logging.error(f"Failed to get mid price for {coin}: {e}")
            return 0.0

# ---------------------------------------------------------------------------
# Paper Trading Portfolio (local simulation — no exchange interaction)
# ---------------------------------------------------------------------------

class PaperPortfolio:
    """Simulates portfolio locally. No real orders placed."""

    def __init__(self, initial_capital: float, state_file: Path):
        self.state_file = state_file
        if state_file.exists():
            self._load()
        else:
            self.cash = initial_capital
            self.positions = {}       # symbol -> signed USD notional
            self.entry_prices = {}    # symbol -> entry price
            self.equity = initial_capital
            self.peak_equity = initial_capital
            self.total_trades = 0
            self.wins = 0
            self.losses = 0
            self.total_pnl = 0.0
            self.start_time = datetime.now(timezone.utc).isoformat()
            self._save()

    def _load(self):
        with open(self.state_file) as f:
            state = json.load(f)
        self.cash = state["cash"]
        self.positions = state["positions"]
        self.entry_prices = state["entry_prices"]
        self.equity = state["equity"]
        self.peak_equity = state["peak_equity"]
        self.total_trades = state["total_trades"]
        self.wins = state["wins"]
        self.losses = state["losses"]
        self.total_pnl = state["total_pnl"]
        self.start_time = state["start_time"]

    def _save(self):
        state = {
            "cash": self.cash,
            "positions": self.positions,
            "entry_prices": self.entry_prices,
            "equity": self.equity,
            "peak_equity": self.peak_equity,
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "total_pnl": self.total_pnl,
            "start_time": self.start_time,
        }
        with open(self.state_file, "w") as f:
            json.dump(state, f, indent=2)

    def update_equity(self, prices: dict):
        """Mark-to-market."""
        unrealized = 0.0
        for sym, notional in self.positions.items():
            if sym in prices and sym in self.entry_prices:
                entry = self.entry_prices[sym]
                current = prices[sym]
                if entry > 0:
                    pnl = notional * (current - entry) / entry
                    unrealized += pnl
        self.equity = self.cash + sum(abs(v) for v in self.positions.values()) + unrealized
        self.peak_equity = max(self.peak_equity, self.equity)
        self._save()

    def execute_signal(self, symbol: str, target_position: float, current_price: float,
                       fee_rate: float = 0.00045):
        """Execute a position change (paper trade)."""
        current_pos = self.positions.get(symbol, 0.0)
        delta = target_position - current_pos

        if abs(delta) < 1.0:
            return None

        # Apply fee
        fee = abs(delta) * fee_rate
        self.cash -= fee

        trade_info = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "side": "BUY" if delta > 0 else "SELL",
            "delta": delta,
            "target": target_position,
            "price": current_price,
            "fee": fee,
        }

        if target_position == 0:
            # Close position
            if symbol in self.entry_prices:
                entry = self.entry_prices[symbol]
                if entry > 0:
                    pnl = current_pos * (current_price - entry) / entry
                    self.cash += abs(current_pos) + pnl
                    self.total_pnl += pnl
                    trade_info["pnl"] = pnl
                    if pnl > 0:
                        self.wins += 1
                    else:
                        self.losses += 1
                del self.entry_prices[symbol]
            if symbol in self.positions:
                del self.positions[symbol]
        elif current_pos == 0:
            # Open new position
            self.cash -= abs(target_position)
            self.positions[symbol] = target_position
            self.entry_prices[symbol] = current_price
            trade_info["pnl"] = 0
        else:
            # Modify position
            if abs(target_position) < abs(current_pos):
                reduced = abs(current_pos) - abs(target_position)
                entry = self.entry_prices.get(symbol, current_price)
                if entry > 0:
                    pnl = (current_pos / abs(current_pos)) * reduced * (current_price - entry) / entry
                else:
                    pnl = 0
                self.cash += reduced + pnl
                self.total_pnl += pnl
                trade_info["pnl"] = pnl
            elif abs(target_position) > abs(current_pos):
                added = abs(target_position) - abs(current_pos)
                self.cash -= added
                old_notional = abs(current_pos)
                old_entry = self.entry_prices.get(symbol, current_price)
                if old_notional + added > 0:
                    new_entry = (old_entry * old_notional + current_price * added) / (old_notional + added)
                    self.entry_prices[symbol] = new_entry
                trade_info["pnl"] = 0
            # Handle position flip
            if (target_position > 0 and current_pos < 0) or (target_position < 0 and current_pos > 0):
                # Close old
                entry = self.entry_prices.get(symbol, current_price)
                if entry > 0:
                    pnl = current_pos * (current_price - entry) / entry
                    self.cash += abs(current_pos) + pnl
                    self.total_pnl += pnl
                    trade_info["pnl"] = pnl
                # Open new
                self.cash -= abs(target_position)
                self.entry_prices[symbol] = current_price

            self.positions[symbol] = target_position

        self.total_trades += 1
        self._save()
        return trade_info

    def get_portfolio_state(self):
        """Return state compatible with prepare.py's PortfolioState."""
        from prepare import PortfolioState
        return PortfolioState(
            cash=self.cash,
            positions=dict(self.positions),
            entry_prices=dict(self.entry_prices),
            equity=self.equity,
            timestamp=int(time.time() * 1000),
        )

# ---------------------------------------------------------------------------
# Build bar_data matching prepare.py format
# ---------------------------------------------------------------------------

def build_bar_data(data_fetcher: HyperliquidData, symbols: list) -> dict:
    """Fetch live data and build bar_data dict matching strategy interface."""
    from prepare import BarData

    bar_data = {}
    for symbol in symbols:
        coin = HL_COINS[symbol]

        # Fetch candles
        candles = data_fetcher.get_candles(coin, "1h", LOOKBACK_BARS)
        if candles.empty or len(candles) < 50:
            logging.warning(f"{symbol}: insufficient candle data ({len(candles)} bars)")
            continue

        # Fetch funding
        funding = data_fetcher.get_funding_history(coin, LOOKBACK_BARS)

        # Merge funding into candles
        if not funding.empty:
            candles = pd.merge_asof(
                candles.sort_values("timestamp"),
                funding.sort_values("timestamp"),
                on="timestamp",
                direction="backward",
            )
        if "funding_rate" not in candles.columns:
            candles["funding_rate"] = 0.0
        candles["funding_rate"] = candles["funding_rate"].fillna(0.0)

        latest = candles.iloc[-1]
        bar_data[symbol] = BarData(
            symbol=symbol,
            timestamp=int(latest["timestamp"]),
            open=float(latest["open"]),
            high=float(latest["high"]),
            low=float(latest["low"]),
            close=float(latest["close"]),
            volume=float(latest["volume"]),
            funding_rate=float(latest["funding_rate"]),
            history=candles,
        )

    return bar_data

# ---------------------------------------------------------------------------
# Trade logger
# ---------------------------------------------------------------------------

def log_trade(trade_info: dict, filepath: Path):
    """Append trade to CSV log."""
    write_header = not filepath.exists()
    with open(filepath, "a") as f:
        if write_header:
            f.write("timestamp,symbol,side,delta,target,price,fee,pnl\n")
        f.write(
            f"{trade_info['timestamp']},"
            f"{trade_info['symbol']},"
            f"{trade_info['side']},"
            f"{trade_info['delta']:.4f},"
            f"{trade_info['target']:.4f},"
            f"{trade_info['price']:.2f},"
            f"{trade_info['fee']:.6f},"
            f"{trade_info.get('pnl', 0):.6f}\n"
        )

def log_equity(portfolio: PaperPortfolio, filepath: Path):
    """Append equity snapshot to CSV log."""
    write_header = not filepath.exists()
    now = datetime.now(timezone.utc).isoformat()
    dd = (portfolio.peak_equity - portfolio.equity) / portfolio.peak_equity * 100 if portfolio.peak_equity > 0 else 0
    positions_str = "|".join(f"{k}:{v:.2f}" for k, v in portfolio.positions.items()) or "flat"

    with open(filepath, "a") as f:
        if write_header:
            f.write("timestamp,equity,cash,drawdown_pct,positions,total_pnl,trades,wins,losses\n")
        f.write(
            f"{now},"
            f"{portfolio.equity:.4f},"
            f"{portfolio.cash:.4f},"
            f"{dd:.4f},"
            f"{positions_str},"
            f"{portfolio.total_pnl:.4f},"
            f"{portfolio.total_trades},"
            f"{portfolio.wins},"
            f"{portfolio.losses}\n"
        )

# ---------------------------------------------------------------------------
# Main execution
# ---------------------------------------------------------------------------

def run_once(logger):
    """Run one iteration of the strategy."""
    logger.info("=" * 60)
    logger.info("Starting executor iteration")

    # Initialize components
    data_fetcher = HyperliquidData(MAINNET_API)  # Use mainnet for real price data
    portfolio = PaperPortfolio(INITIAL_CAPITAL, STATE_FILE)

    # Fetch live market data
    logger.info("Fetching market data...")
    bar_data = build_bar_data(data_fetcher, SYMBOLS)

    if not bar_data:
        logger.error("No market data available, skipping iteration")
        return

    logger.info(f"Got data for: {list(bar_data.keys())}")
    for sym, bd in bar_data.items():
        logger.info(f"  {sym}: close=${bd.close:.2f}, {len(bd.history)} bars")

    # Update portfolio equity with current prices
    prices = {sym: bd.close for sym, bd in bar_data.items()}
    portfolio.update_equity(prices)

    # Build portfolio state for strategy
    port_state = portfolio.get_portfolio_state()

    # Run strategy
    logger.info("Running strategy...")
    from strategy_live import Strategy
    strategy = Strategy()

    # Load strategy state if exists
    strategy_state_file = DATA_DIR / "strategy_state.json"
    if strategy_state_file.exists():
        with open(strategy_state_file) as f:
            saved = json.load(f)
        strategy.entry_prices = saved.get("entry_prices", {})
        strategy.peak_prices = saved.get("peak_prices", {})
        strategy.atr_at_entry = saved.get("atr_at_entry", {})
        strategy.btc_momentum = saved.get("btc_momentum", 0.0)
        strategy.pyramided = saved.get("pyramided", {})
        strategy.peak_equity = saved.get("peak_equity", INITIAL_CAPITAL)
        strategy.exit_bar = saved.get("exit_bar", {})
        strategy.entry_bar = saved.get("entry_bar", {})
        strategy.bar_count = saved.get("bar_count", 0)
        strategy.day_start_equity = saved.get("day_start_equity", INITIAL_CAPITAL)
        strategy.last_day = saved.get("last_day", -1)
        strategy.halted = saved.get("halted", False)

    signals = strategy.on_bar(bar_data, port_state)

    # Save strategy state
    with open(strategy_state_file, "w") as f:
        json.dump({
            "entry_prices": strategy.entry_prices,
            "peak_prices": strategy.peak_prices,
            "atr_at_entry": strategy.atr_at_entry,
            "btc_momentum": strategy.btc_momentum,
            "pyramided": strategy.pyramided,
            "peak_equity": strategy.peak_equity,
            "exit_bar": strategy.exit_bar,
            "entry_bar": strategy.entry_bar,
            "bar_count": strategy.bar_count,
            "day_start_equity": strategy.day_start_equity,
            "last_day": strategy.last_day,
            "halted": strategy.halted,
        }, f, indent=2)

    # Execute signals
    if signals:
        logger.info(f"Strategy generated {len(signals)} signal(s)")
        for sig in signals:
            # Enforce $10 minimum order size
            if sig.target_position != 0 and abs(sig.target_position) < 10:
                sig_target = 10.0 if sig.target_position > 0 else -10.0
            else:
                sig_target = sig.target_position

            price = bar_data[sig.symbol].close
            trade = portfolio.execute_signal(sig.symbol, sig_target, price)

            if trade:
                logger.info(
                    f"  TRADE: {trade['side']} {sig.symbol} "
                    f"delta=${trade['delta']:.2f} @ ${trade['price']:.2f} "
                    f"fee=${trade['fee']:.4f} pnl=${trade.get('pnl', 0):.4f}"
                )
                log_trade(trade, TRADE_LOG)
    else:
        logger.info("No signals generated")

    # Update equity after trades
    portfolio.update_equity(prices)
    log_equity(portfolio, EQUITY_LOG)

    # Print summary
    dd = (portfolio.peak_equity - portfolio.equity) / portfolio.peak_equity * 100
    win_rate = portfolio.wins / max(portfolio.total_trades, 1) * 100
    ret_pct = (portfolio.equity - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100

    logger.info(f"--- Portfolio Summary ---")
    logger.info(f"  Equity:     ${portfolio.equity:.2f} ({ret_pct:+.2f}%)")
    logger.info(f"  Cash:       ${portfolio.cash:.2f}")
    logger.info(f"  Positions:  {portfolio.positions or 'flat'}")
    logger.info(f"  Drawdown:   {dd:.2f}%")
    logger.info(f"  Total PnL:  ${portfolio.total_pnl:.4f}")
    logger.info(f"  Trades:     {portfolio.total_trades} (win rate: {win_rate:.1f}%)")
    logger.info("=" * 60)


def show_status():
    """Show current paper trading status."""
    if not STATE_FILE.exists():
        print("No paper trading data found. Run 'python executor.py' first.")
        return

    with open(STATE_FILE) as f:
        state = json.load(f)

    ret_pct = (state["equity"] - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    dd = (state["peak_equity"] - state["equity"]) / state["peak_equity"] * 100
    win_rate = state["wins"] / max(state["total_trades"], 1) * 100

    print(f"\n{'='*50}")
    print(f"  PAPER TRADING STATUS")
    print(f"{'='*50}")
    print(f"  Started:    {state['start_time'][:19]}")
    print(f"  Capital:    ${INITIAL_CAPITAL:.2f}")
    print(f"  Equity:     ${state['equity']:.2f} ({ret_pct:+.2f}%)")
    print(f"  Cash:       ${state['cash']:.2f}")
    print(f"  Peak:       ${state['peak_equity']:.2f}")
    print(f"  Drawdown:   {dd:.2f}%")
    print(f"  Total PnL:  ${state['total_pnl']:.4f}")
    print(f"  Trades:     {state['total_trades']}")
    print(f"  Win rate:   {win_rate:.1f}%")
    print(f"  Positions:  {state['positions'] or 'flat'}")
    print(f"{'='*50}\n")

    # Show recent equity if available
    if EQUITY_LOG.exists():
        df = pd.read_csv(EQUITY_LOG)
        if len(df) > 0:
            print(f"  Last {min(10, len(df))} equity snapshots:")
            for _, row in df.tail(10).iterrows():
                print(f"    {row['timestamp'][:19]}  ${row['equity']:.2f}  dd={row['drawdown_pct']:.2f}%  {row['positions']}")
            print()


def main():
    parser = argparse.ArgumentParser(description="Paper Trading Executor")
    parser.add_argument("--loop", action="store_true", help="Run continuously (every hour)")
    parser.add_argument("--status", action="store_true", help="Show current status")
    parser.add_argument("--reset", action="store_true", help="Reset paper trading (start fresh)")
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    if args.reset:
        for f in [STATE_FILE, TRADE_LOG, EQUITY_LOG, DATA_DIR / "strategy_state.json"]:
            if f.exists():
                f.unlink()
        print("Paper trading reset. Run 'python executor.py' to start fresh.")
        return

    logger = setup_logging()

    if args.loop:
        logger.info("Starting continuous loop (Ctrl+C to stop)")
        while True:
            try:
                run_once(logger)
            except Exception as e:
                logger.error(f"Iteration failed: {e}", exc_info=True)

            # Wait until the next hour
            now = datetime.now(timezone.utc)
            minutes_to_next_hour = 60 - now.minute
            seconds_to_wait = minutes_to_next_hour * 60 - now.second
            logger.info(f"Next run in {minutes_to_next_hour} minutes...")
            time.sleep(max(seconds_to_wait, 60))
    else:
        try:
            run_once(logger)
        except Exception as e:
            logger.error(f"Execution failed: {e}", exc_info=True)
            sys.exit(1)


if __name__ == "__main__":
    main()
