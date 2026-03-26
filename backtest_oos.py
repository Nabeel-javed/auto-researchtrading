"""Run backtest on test split (out-of-sample). Used by run_loop.sh for overfitting guard."""
import time
import signal as sig
from prepare import load_data, run_backtest, compute_score, TIME_BUDGET

def timeout_handler(signum, frame):
    print("TIMEOUT")
    exit(1)

sig.signal(sig.SIGALRM, timeout_handler)
sig.alarm(TIME_BUDGET + 30)

from strategy import Strategy
strategy = Strategy()
data = load_data("test")
result = run_backtest(strategy, data)
score = compute_score(result)
print(f"score:              {score:.6f}")
print(f"sharpe:             {result.sharpe:.6f}")
print(f"max_drawdown_pct:   {result.max_drawdown_pct:.6f}")
