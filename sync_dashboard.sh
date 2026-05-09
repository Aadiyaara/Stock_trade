#!/bin/bash
# Syncs paper_trades.json to docs/ folder and pushes to GitHub
# Run after paper_trade.py close

DIR="$(cd "$(dirname "$0")" && pwd)"
cp "$DIR/paper_trades.json" "$DIR/docs/paper_trades.json"

cd "$DIR"
git add docs/paper_trades.json
git commit -m "update: daily paper trade results $(date +%Y-%m-%d)" 2>/dev/null
git push origin main 2>/dev/null
