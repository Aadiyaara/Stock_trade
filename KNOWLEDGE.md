# Stock Recommender — System Overview

## Purpose
A Python-based daily trading tool that screens S&P 500 stocks for bullish day-trading opportunities, paper trades $100/day, tracks performance, and auto-learns from results.

## Architecture

```
stock-recommender/
├── main.py              # CLI entry point for manual screening
├── paper_trade.py       # Paper trading bot (buy/close/history)
├── dashboard.py         # Web UI at localhost:8501
├── learn.py             # Learning engine (performance analysis + readiness report)
├── setup_schedule.sh    # Installs cron jobs for auto-trading
├── sync_dashboard.sh    # Pushes results to GitHub Pages
├── paper_trades.json    # Trade history (auto-generated)
├── learnings.json       # Learning engine output (auto-generated)
├── requirements.txt     # Python dependencies
├── docs/
│   └── index.html       # Static dashboard for GitHub Pages (shareable)
└── src/
    ├── data_fetcher.py    # S&P 500 ticker list + OHLC download
    ├── patterns.py        # 11 candlestick pattern detectors
    ├── indicators.py      # Technical indicators (RSI, MACD, BB, MA, volume)
    ├── news_sentiment.py  # Finviz headline scraping + TextBlob sentiment
    ├── screener.py        # Scoring engine + intraday momentum filter
    └── exit_strategy.py   # Optimal exit detection (profit target, trailing stop)
```

## Data Flow

```
1. Fetch S&P 500 tickers (Wikipedia)
2. Download 1-year OHLC via yfinance (batch, single API call)
3. Quick-screen all 500 → top 50 bullish (momentum + volume + intraday filter)
4. Deep-analyze top 50:
   a. 11 candlestick patterns (last 5 days)
   b. Technical indicators (RSI, MACD, Bollinger, MA alignment, golden cross, volume)
   c. News sentiment (Finviz headlines + TextBlob polarity)
   d. Intraday momentum (win rate, gap-fade rate, avg return)
5. Composite scoring → rank → output top picks
6. Exit strategy analysis → profit target + trailing stop per stock
```

## Scoring System

### Composite Score (Day Trading Mode)
- 35% Technical indicators (0-100)
- 20% Pattern confluence (0-30 normalized)
- 10% News sentiment (0-20)
- 35% Intraday momentum score

### Confidence Levels (Day Trading)
- **HIGH**: 4+ signals AND intraday win rate >= 60% AND gap-fade <= 30%
- **MEDIUM**: 3+ signals AND intraday win rate >= 50%
- **LOW**: Everything else

### Minimum Trade Threshold
- Composite score >= 40
- Confidence >= MEDIUM
- If nothing qualifies → skip the day (preserve capital)

## Intraday Momentum Filter
Solves the "NVDA problem" — stocks that gap up overnight but sell off during market hours.

Metrics:
- **intraday_win_rate**: % of days with positive open→close
- **gap_fade_rate**: % of days that gap up then close lower (penalized)
- **avg_intraday_pct**: average open→close return
- **intraday_ratio**: % of total gains happening during market hours

## Exit Strategy Module (`src/exit_strategy.py`)

Analyzes historical intraday behavior to recommend when to sell:

### Three Strategies
1. **PROFIT_TARGET**: Stock regularly gives back gains → sell at fixed % above open
2. **TRAILING_STOP**: Some giveback → sell if price drops X% from intraday high
3. **HOLD_TO_CLOSE**: Stock tends to close near its high → just hold

### How It Works
- Computes avg open-to-high run-up (how much it typically gains)
- Computes avg high-to-close giveback (how much it loses after peaking)
- Suggests profit target = 70% of avg run-up (conservative)
- Suggests trailing stop = 80% of avg giveback
- Backtests all 3 strategies over 20 days to prove which is best

### Close Report Tracks "Money Left on Table"
Each trade records:
- `close_price`: what we sold at (market close)
- `high_price`: intraday high (what we could have sold at)
- `optimal_pnl`: profit if sold at high
- `missed_pnl`: difference (money left on table)

## Paper Trading Bot

### Schedule (Pacific Time, Mon-Fri via cron)
- **6:35 AM**: Screen S&P 500, pick top 5 (or fewer), mock buy at open price, print exit plan
- **1:05 PM**: Record close prices + highs, calculate P/L + optimal P/L, run learning engine, sync to GitHub

### Commands
```bash
python paper_trade.py buy       # Morning: pick & buy + exit plan
python paper_trade.py close     # Afternoon: close & report P/L + missed gains
python paper_trade.py history   # Show cumulative results
```

### Trade Storage (paper_trades.json)
```json
{
  "trades": [
    {
      "date": "2026-05-07",
      "ticker": "GOOG",
      "open_price": 394.25,
      "shares": 0.0507,
      "invested": 20,
      "close_price": 398.04,
      "high_price": 400.10,
      "pnl": 0.19,
      "optimal_pnl": 0.30,
      "missed_pnl": 0.11,
      "confidence": "HIGH",
      "composite_score": 72.2
    }
  ],
  "summary": {
    "total_invested": 100,
    "total_pnl": 3.08,
    "total_optimal_pnl": 4.49,
    "days": 1
  }
}
```

## Learning Engine (`learn.py`)

Runs daily after close. Produces:

### 1. Performance Analysis
- Win rate, profit factor, max drawdown
- Per-confidence-level stats (does HIGH actually outperform LOW?)
- Per-ticker stats (repeat winners/losers)
- Per-score-bracket stats (does higher score = better results?)
- Exit efficiency (% of possible gains captured)

### 2. Auto-Generated Insights
- Validates confidence scoring is working
- Identifies best/worst score ranges
- Flags repeat losers for blocklist
- Highlights consistent winners
- Detects win/loss size asymmetry
- Tracks exit efficiency over time

### 3. Weight Adjustment Suggestions
- If HIGH confidence underperforms → increase intraday weight
- If low-score trades keep winning → lower minimum threshold
- If high-score trades keep losing → rebalance technical vs pattern weights

### 4. Readiness Report ("Can I go live with real money?")
Must pass ALL criteria:
- 20+ days traded
- Win rate > 60%
- Profit factor > 1.5
- Max drawdown < 10%

## CLI Screener Modes

```bash
# Day trading mode (filters for intraday momentum)
python main.py --daytrade

# Swing/position mode (original, no intraday filter)
python main.py

# Specific tickers
python main.py --tickers AAPL MSFT NVDA --daytrade

# Skip news (faster)
python main.py --no-news

# Control output
python main.py --top 50 --show 20

# Exit strategy analysis
python -m src.exit_strategy GOOG TSLA AMD
```

## Dashboard

### Local (real-time)
```bash
python dashboard.py   # → http://localhost:8501
```

### Shareable (GitHub Pages)
Static HTML at `docs/index.html` reads `docs/paper_trades.json`.
Auto-synced daily via `sync_dashboard.sh` after close.
Friends visit: `https://USERNAME.github.io/stock-recommender`

## Dependencies (all free/open-source)
- yfinance: OHLC data from Yahoo Finance
- pandas: Data manipulation
- numpy: Numerical operations
- pandas-ta: Technical analysis (available as fallback)
- requests + beautifulsoup4: Web scraping (Finviz news, Wikipedia tickers)
- textblob: NLP sentiment analysis
- Chart.js (CDN): Dashboard charts

## Candlestick Patterns Detected
1. Doji
2. Hammer
3. Inverted hammer
4. Bullish engulfing
5. Piercing line
6. Morning star
7. Three white soldiers
8. Bullish harami
9. Tweezer bottom
10. Dragonfly doji
11. Rising three methods

## Technical Indicators
- RSI (14-period) — oversold bounce detection
- MACD (12/26/9) — crossover and momentum
- Bollinger Bands (20-period) — mean reversion near lower band
- SMA 20/50/200 — trend alignment
- Golden cross — 50 SMA crossing above 200 SMA
- Volume surge — current vs 20-day average

## Key Design Decisions
1. **No TA-Lib dependency** — all patterns implemented in pure Python/pandas for easy install
2. **Intraday filter** — prevents picking stocks that only move overnight (gap-and-fade)
3. **Minimum threshold** — sits out on bad days rather than forcing trades
4. **Multi-signal confluence** — higher confidence when multiple independent signals agree
5. **Paper trading first** — no real money until strategy proves itself over 20+ days
6. **Exit strategy** — tracks optimal exit vs actual, learns how much is left on table
7. **Self-learning** — daily analysis of what works, suggests weight adjustments
8. **Batch data download** — single yfinance API call for all 500 tickers (fast)
