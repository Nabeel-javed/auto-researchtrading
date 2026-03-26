#!/bin/bash
# Setup/remove cron job for paper trading executor
# Usage: ./setup_cron.sh install    # Install hourly cron job
#        ./setup_cron.sh remove     # Remove cron job
#        ./setup_cron.sh status     # Check if cron is running

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
UV_PATH="$HOME/.local/bin/uv"
CRON_CMD="5 * * * * cd $SCRIPT_DIR && $UV_PATH run executor.py >> $SCRIPT_DIR/paper_trading/cron.log 2>&1"
CRON_MARKER="# autotrader-paper"

case "$1" in
    install)
        # Remove existing entry if any
        crontab -l 2>/dev/null | grep -v "$CRON_MARKER" | crontab -
        # Add new entry
        (crontab -l 2>/dev/null; echo "$CRON_CMD $CRON_MARKER") | crontab -
        echo "Cron job installed! The executor will run at minute :05 of every hour."
        echo "Logs: $SCRIPT_DIR/paper_trading/cron.log"
        echo ""
        echo "To check status anytime:  uv run executor.py --status"
        echo "To view live logs:        tail -f $SCRIPT_DIR/paper_trading/cron.log"
        echo "To remove:                ./setup_cron.sh remove"
        ;;
    remove)
        crontab -l 2>/dev/null | grep -v "$CRON_MARKER" | crontab -
        echo "Cron job removed."
        ;;
    status)
        if crontab -l 2>/dev/null | grep -q "$CRON_MARKER"; then
            echo "Cron job is ACTIVE"
            crontab -l 2>/dev/null | grep "$CRON_MARKER"
        else
            echo "Cron job is NOT installed"
            echo "Run: ./setup_cron.sh install"
        fi
        ;;
    *)
        echo "Usage: $0 {install|remove|status}"
        exit 1
        ;;
esac
