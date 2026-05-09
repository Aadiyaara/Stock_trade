# Stock Paper Trader — Knowledge Base

> Auto-updated with each commit. If this doc is stale, the pre-commit hook failed.

## What This Does

An automated paper trading system that:
1. Screens all S&P 500 stocks daily for bullish day-trading setups
2. Paper trades $100/day across the top 5 picks
3. Tracks performance (P/L, optimal exits, win rate)
4. Self-learns from results to improve over time
5. Serves a public dashboard showing live results

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        AWS (account: 536697230325)                   │
│                                                                     │
│  EventBridge (cron)           Lambda (Python 3.12)        S3        │
│  ─────────────────           ─────────────────────    ──────────    │
│  1AM, 2AM, 3AM ──────────▶  stock-build-cache     ──▶ ohlc_cache  │
│  4:30 AM ET    ──────────▶  stock-recommend       ──▶ recs.json   │
│  9:35 AM ET    ──────────▶  stock-morning-buy     ──▶ trades.json │
│  4:05 PM ET    ──────────▶  stock-close-and-learn ──▶ trades.json │
│                                                                     │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │
                    S3 public read │
                                   ▼
                    GitHub Pages Dashboard
                    (aadiyaara.github.io/Stock_trade)
```

## Project Structure

```
stock-recommender/
├── lambda/
│   └── handler.py            # All Lambda function handlers
├── infra/
│   ├── app.py                # CDK app entry point
│   ├── stack.py              # CDK stack (Lambda, EventBridge, S3, IAM)
│   ├── cdk.json              # CDK config
│   └── requirements.txt      # CDK Python deps
├── src/
│   ├── data_fetcher.py       # Polygon.io API: fetch OHLC, prices, caching
│   ├── screener.py           # Scoring engine + intraday momentum filter
│   ├── indicators.py         # Technical indicators (RSI, MACD, BB, MA, volume)
│   ├── patterns.py           # 11 candlestick pattern detectors
│   ├── news_sentiment.py     # Finviz headline scraping + TextBlob sentiment
│   ├── exit_strategy.py      # Optimal exit detection (profit target, trailing stop)
│   └── learn.py              # Performance analysis + readiness report
├── docs/
│   └── index.html            # Dashboard UI (reads from S3)
├── main.py                   # CLI screener (for local use)
├── paper_trade.py            # CLI paper trader (for local use)
├── deploy.sh                 # One-command CDK deploy
├── requirements.txt          # Python runtime deps
└── KNOWLEDGE.md              # ← You are here
```

## Lambda Functions

### `stock-build-cache` — Data Collector
- **Trigger:** 1:00 AM, 2:00 AM, 3:00 AM ET (Mon-Fri)
- **What:** Fetches 1-year daily OHLC from Polygon.io for ~60 tickers per run
- **Why:** Polygon free tier limits to 5 calls/min. 3 runs × 60 = ~180 tickers cached daily
- **Output:** `s3://stock-trades-536697230325/cache/ohlc_cache.json`
- **Note:** Cache accumulates over days. After ~3 days all 503 S&P tickers are fresh

### `stock-recommend` — Pre-Market Screener
- **Trigger:** 4:30 AM ET (Mon-Fri)
- **What:** Screens cached OHLC data, picks top 5 qualified stocks, includes prev close as reference price
- **Output:** `s3://stock-trades-536697230325/recommendations.json` (public)
- **Qualification:** composite score ≥ 40 AND confidence ≥ MEDIUM

### `stock-morning-buy` — Trade Executor
- **Trigger:** 9:35 AM ET (Mon-Fri, 5 min after market open)
- **What:** Gets actual open price from Polygon (1-min bars), records paper buy
- **Output:** Updates `paper_trades.json` in S3
- **Budget:** $100/day split across qualified picks ($20 each if 5 qualify)

### `stock-close-and-learn` — Close + Analysis
- **Trigger:** 4:05 PM ET (Mon-Fri, 5 min after market close)
- **What:** Fetches close/high prices, calculates P/L, runs learning engine
- **Output:** Updates `paper_trades.json` and `learnings.json` in S3

## Scoring System

### Pre-Screen (quick_bullish_rank) — All 503 tickers
Fast 0-100 score to filter top 50:
- Price > 50-day SMA → +25
- Positive 20-day momentum → up to +25
- Positive 5-day momentum → up to +25
- Volume increasing → +25
- Overextended (abnormal spike) → **hard skip**
- High gap-fade rate → -30

### Deep Analysis (analyze_stock) — Top 50 only
Composite score (weighted):

| Weight | Component | Source |
|--------|-----------|--------|
| 35% | Technical indicators | RSI, MACD, Bollinger, MA alignment, golden cross, volume surge |
| 20% | Candlestick patterns | 11 patterns checked over last 5 days |
| 10% | News sentiment | Finviz headlines scored via TextBlob |
| 35% | Intraday momentum | Win rate, avg return, gap-fade rate |

### Confidence Levels
- **HIGH**: ≥4 signals + intraday win ≥60% + gap-fade ≤30%
- **MEDIUM**: ≥3 signals + intraday win ≥50%
- **LOW**: Everything else (not traded)

## Data Sources

| Source | Used For | Auth |
|--------|----------|------|
| **Polygon.io** (free tier) | OHLC data, open/close prices | API key in Lambda env var |
| **Wikipedia** | S&P 500 ticker list | None (scrape) |
| **Finviz** | News headlines for sentiment | None (scrape) |

## Key Concepts

### Intraday Momentum Filter
Solves the "gap-and-fade" problem — stocks that gap up overnight but sell off during market hours are bad for day trading.

- **intraday_win_rate**: % of last 10 days with positive open→close
- **gap_fade_rate**: % of days that gap up then close lower (penalized heavily)
- **avg_intraday_pct**: mean open→close return
- **overextended**: abnormal recent spike → excluded entirely

### Exit Strategy Analysis
Each trade tracks what you'd have made selling at the high vs holding to close:
- `pnl`: actual profit (sold at close)
- `optimal_pnl`: profit if sold at intraday high
- `missed_pnl`: money left on table

### Learning Engine
After each day, analyzes:
- Win rate by confidence level (is HIGH actually better?)
- Win rate by score bracket (does higher score = better results?)
- Repeat winners/losers (blocklist candidates)
- Exit efficiency (% of possible gains captured)
- Readiness to go live (20+ days, >60% win rate, profit factor >1.5, drawdown <10%)

## Infrastructure

### AWS Resources (CDK managed)
- **S3 Bucket:** `stock-trades-536697230325` (versioned, public read on trades/recs)
- **Lambda Layer:** pandas, numpy, requests, beautifulsoup4, textblob, lxml
- **IAM Role:** Lambda → S3 read/write only
- **EventBridge Rules:** 7 scheduled triggers per weekday

### Deployment
```bash
cd stock-recommender
./deploy.sh          # Runs CDK deploy with elevatr profile
```

Or manually:
```bash
cd infra
source .venv/bin/activate
AWS_PROFILE=elevatr cdk deploy
```

### AWS Profile
```
Profile: elevatr
Account: 536697230325
Region: us-east-1
```

## Cost
**$0/month** — fits entirely within AWS free tier + Polygon free tier.

## Public URLs
- **Dashboard:** https://aadiyaara.github.io/Stock_trade/
- **Recommendations:** https://stock-trades-536697230325.s3.amazonaws.com/recommendations.json
- **Trade History:** https://stock-trades-536697230325.s3.amazonaws.com/paper_trades.json

## Local Development

```bash
# Run screener manually (uses Polygon API)
python main.py --daytrade

# Specific tickers
python main.py --tickers AAPL MSFT NVDA --daytrade

# Skip news (faster)
python main.py --no-news

# Paper trade locally
python paper_trade.py buy
python paper_trade.py close
python paper_trade.py history
```

## Technical Patterns Detected
1. Doji — indecision, reversal signal
2. Hammer — bullish reversal at support
3. Inverted hammer — bullish reversal
4. Bullish engulfing — strong reversal
5. Piercing line — bullish reversal
6. Morning star — 3-candle reversal
7. Three white soldiers — strong continuation
8. Bullish harami — inside bar reversal
9. Tweezer bottom — double bottom
10. Dragonfly doji — bullish at support
11. Rising three methods — bullish continuation
