#!/bin/bash
# Autonomous strategy research loop with overfitting protection
# Tests on BOTH val (in-sample) AND test (out-of-sample) — rejects changes that overfit
# Auto-stops after MAX_STALE consecutive failures
# Usage: ./run_loop.sh [hours]  (default: 7)

DURATION_HOURS=${1:-7}
MAX_STALE=15  # Stop after this many consecutive non-improvements
END_TIME=$(($(date +%s) + DURATION_HOURS * 3600))
EXPERIMENT=0
STALE_COUNT=0
LOG_DIR="$(pwd)/experiment_logs"
mkdir -p "$LOG_DIR"

export PATH="$HOME/.local/bin:$PATH"
CLAUDE="$HOME/.local/bin/claude"

echo "=== Autonomous Research Loop (with overfitting guard) ==="
echo "Duration: ${DURATION_HOURS} hours (or $MAX_STALE consecutive failures)"
echo "End time: $(date -r $END_TIME)"
echo "Logs: $LOG_DIR/"
echo "Press Ctrl+C to stop"
echo ""

# Record starting scores on BOTH splits
echo "--- Getting baseline scores ---"
VAL_BASELINE=$(uv run backtest.py 2>&1 | grep "^score:" | awk '{print $2}')
OOS_BASELINE=$(uv run backtest_oos.py 2>&1 | grep "^score:" | awk '{print $2}')
echo "Val score:  $VAL_BASELINE"
echo "Test score: $OOS_BASELINE"
echo ""

BEST_VAL="$VAL_BASELINE"
BEST_OOS="$OOS_BASELINE"
RESULTS_FILE="$(pwd)/loop_results.tsv"
echo -e "exp\ttimestamp\tval_score\ttest_score\tbest_val\tbest_test\tstatus\treason" > "$RESULTS_FILE"

while [ $(date +%s) -lt $END_TIME ]; do
    EXPERIMENT=$((EXPERIMENT + 1))
    TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
    echo "=== Experiment $EXPERIMENT | $TIMESTAMP | stale: $STALE_COUNT/$MAX_STALE ==="

    LOGFILE="$LOG_DIR/exp_${EXPERIMENT}_$(date +%s).log"

    # Save current strategy as backup
    cp strategy.py strategy.py.backup

    # Run one experiment via Claude
    $CLAUDE --dangerously-skip-permissions -p "You are an autonomous quant researcher. Your job: improve strategy.py to get a higher backtest score.

CURRENT SCORES:
- Val (in-sample) best:  $BEST_VAL
- Test (out-of-sample) best: $BEST_OOS

CRITICAL RULES:
- ONLY modify strategy.py — no other files
- Only use numpy, pandas, scipy, stdlib
- Run: uv run backtest.py to get the val score
- If val score > $BEST_VAL → print KEEP and the new score
- If val score <= $BEST_VAL or error → revert with: git checkout strategy.py, print REVERT
- Try ONE focused, well-reasoned change per experiment
- DO NOT make random parameter tweaks. Think about WHY a change should help before implementing it.
- Prefer structural improvements (new signal logic, better exits) over parameter tuning

IMPORTANT: After backtesting, if you KEEP, commit with: git add strategy.py && git commit -m 'exp: description of change'

KNOWN BUG TO FIX FIRST:
- The BB signal (bb_pctile < 90) is TRUE ~90% of the time, making it a free vote that dilutes the ensemble.
  Try tightening to bb_pctile < 40 or < 50, which would make it a real compression filter.

HIGH PRIORITY IDEAS (research-backed, pick one):
- Fix BB signal: tighten threshold to 40-50, AND make it directional (bullish when price near lower band + compressed, bearish when near upper band)
- Dynamic ATR stops: scale ATR_STOP_MULT by vol_ratio so stops tighten in low-vol and widen in high-vol
- Volume confirmation: only enter when current bar volume > 1.5x the 24-bar average volume
- Funding rate as contrarian filter: when avg funding strongly positive, avoid new longs
- Stochastic RSI instead of raw RSI for overbought/oversold exits
- Time-in-trade stop tightening: after 48 bars in a trade, gradually reduce ATR_STOP_MULT to lock profits
- Composite RSI+BB exit: exit long only when RSI > 69 AND price > upper Bollinger Band
- Volatility regime detection: adapt MIN_VOTES and COOLDOWN by vol regime

Read strategy.py first, think about what would genuinely improve the strategy, make one change, test it, keep or revert." \
    --allowedTools "Bash,Read,Write,Edit,Glob,Grep" \
    > "$LOGFILE" 2>&1

    # === DUAL-SPLIT VALIDATION ===
    NEW_VAL=$(uv run backtest.py 2>&1 | grep "^score:" | awk '{print $2}')

    if [ -z "$NEW_VAL" ]; then
        echo "  ERROR: backtest crashed, reverting"
        cp strategy.py.backup strategy.py
        echo -e "$EXPERIMENT\t$TIMESTAMP\tERROR\tERROR\t$BEST_VAL\t$BEST_OOS\tREVERT\tcrash" >> "$RESULTS_FILE"
        STALE_COUNT=$((STALE_COUNT + 1))
    else
        VAL_IMPROVED=$(python3 -c "print('yes' if float('$NEW_VAL') > float('$BEST_VAL') else 'no')" 2>/dev/null)

        if [ "$VAL_IMPROVED" = "yes" ]; then
            # Val improved — now check out-of-sample
            NEW_OOS=$(uv run backtest_oos.py 2>&1 | grep "^score:" | awk '{print $2}')

            if [ -z "$NEW_OOS" ]; then
                echo "  Val improved ($BEST_VAL → $NEW_VAL) but OOS crashed, reverting"
                git checkout strategy.py 2>/dev/null
                echo -e "$EXPERIMENT\t$TIMESTAMP\t$NEW_VAL\tERROR\t$BEST_VAL\t$BEST_OOS\tREVERT\toos_crash" >> "$RESULTS_FILE"
                STALE_COUNT=$((STALE_COUNT + 1))
            else
                OOS_DEGRADED=$(python3 -c "
best = float('$BEST_OOS')
new = float('$NEW_OOS')
# Reject if OOS drops by more than 1.0 point (allows minor fluctuation)
print('yes' if new < best - 1.0 else 'no')
" 2>/dev/null)

                if [ "$OOS_DEGRADED" = "yes" ]; then
                    echo "  OVERFIT: val improved ($BEST_VAL → $NEW_VAL) but test DEGRADED ($BEST_OOS → $NEW_OOS), reverting"
                    git checkout strategy.py 2>/dev/null
                    echo -e "$EXPERIMENT\t$TIMESTAMP\t$NEW_VAL\t$NEW_OOS\t$BEST_VAL\t$BEST_OOS\tREVERT\toverfit" >> "$RESULTS_FILE"
                    STALE_COUNT=$((STALE_COUNT + 1))
                else
                    echo "  KEEP: val $BEST_VAL → $NEW_VAL | test $BEST_OOS → $NEW_OOS"
                    BEST_VAL="$NEW_VAL"
                    # Update OOS best if it also improved
                    OOS_ALSO_UP=$(python3 -c "print('yes' if float('$NEW_OOS') > float('$BEST_OOS') else 'no')" 2>/dev/null)
                    if [ "$OOS_ALSO_UP" = "yes" ]; then
                        BEST_OOS="$NEW_OOS"
                    fi
                    echo -e "$EXPERIMENT\t$TIMESTAMP\t$NEW_VAL\t$NEW_OOS\t$BEST_VAL\t$BEST_OOS\tKEEP\tok" >> "$RESULTS_FILE"
                    STALE_COUNT=0  # Reset stale counter on success
                fi
            fi
        else
            echo "  No val improvement: $NEW_VAL (best: $BEST_VAL)"
            echo -e "$EXPERIMENT\t$TIMESTAMP\t$NEW_VAL\t-\t$BEST_VAL\t$BEST_OOS\tREVERT\tno_improvement" >> "$RESULTS_FILE"
            git checkout strategy.py 2>/dev/null
            STALE_COUNT=$((STALE_COUNT + 1))
        fi
    fi

    rm -f strategy.py.backup

    echo "  Best: val=$BEST_VAL | test=$BEST_OOS | stale=$STALE_COUNT/$MAX_STALE"
    echo ""

    # Auto-stop if stuck
    if [ $STALE_COUNT -ge $MAX_STALE ]; then
        echo "=== STOPPING: $MAX_STALE consecutive experiments without improvement ==="
        echo "The strategy has likely converged. Further experiments risk overfitting."
        break
    fi

    sleep 3
done

echo ""
echo "=== Loop Complete ==="
echo "Ran $EXPERIMENT experiments"
echo "Final best: val=$BEST_VAL | test=$BEST_OOS"
echo "Results: $RESULTS_FILE"
echo "Logs: $LOG_DIR/"
