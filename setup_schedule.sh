#!/bin/bash
# Paper Trading Scheduler
# Adds cron jobs to auto-run paper trading at market open/close (Pacific Time)
#
# Market hours in PT: 6:30 AM - 1:00 PM
# Buy at 6:45 AM PT (15 min after open for price to settle)
# Close at 1:05 PM PT (5 min after close for final price)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON=$(which python3 || which python)
LOG_FILE="$SCRIPT_DIR/paper_trade.log"

echo "Setting up paper trading cron jobs..."
echo "Script dir: $SCRIPT_DIR"
echo "Python: $PYTHON"
echo ""

# Create cron entries (only on weekdays Mon-Fri)
CRON_BUY="45 6 * * 1-5 cd $SCRIPT_DIR && $PYTHON paper_trade.py buy >> $LOG_FILE 2>&1"
CRON_CLOSE="5 13 * * 1-5 cd $SCRIPT_DIR && $PYTHON paper_trade.py close >> $LOG_FILE 2>&1 && $PYTHON learn.py >> $LOG_FILE 2>&1 && $SCRIPT_DIR/sync_dashboard.sh >> $LOG_FILE 2>&1"

# Check if already installed
EXISTING=$(crontab -l 2>/dev/null || echo "")

if echo "$EXISTING" | grep -q "paper_trade.py"; then
    echo "Cron jobs already exist. Replacing..."
    EXISTING=$(echo "$EXISTING" | grep -v "paper_trade.py")
fi

# Install
(echo "$EXISTING"; echo "$CRON_BUY"; echo "$CRON_CLOSE") | crontab -

echo "Cron jobs installed:"
echo "  BUY:   6:35 AM PT (Mon-Fri)"
echo "  CLOSE: 1:05 PM PT (Mon-Fri)"
echo ""
echo "View logs: tail -f $LOG_FILE"
echo "Check cron: crontab -l"
echo "Remove: crontab -l | grep -v paper_trade | crontab -"
