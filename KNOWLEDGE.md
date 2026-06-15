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
│  Every 30m 12-4AM ET ────▶  stock-build-cache     ──▶ ohlc_cache  │
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
- **Trigger:** Every 30 min from 12:00-4:00 AM ET (Mon-Fri) = 10 runs
- **What:** Fetches 1-year daily OHLC from Polygon.io for 50 tickers per run
- **Why:** Polygon free tier limits to 5 calls/min. 10 runs × 50 = 500 tickers cached daily
- **Output:** `s3://stock-trades-536697230325/cache/ohlc_cache.json`
- **Timeout:** 10 minutes

### `stock-recommend` — Pre-Market Screener
- **Trigger:** 4:30 AM ET (Mon-Fri)
- **What:** Screens cached OHLC data, picks top 10 qualified stocks with prev close as reference
- **Output:** `s3://stock-trades-536697230325/recommendations.json` (public)
- **Qualification:** composite score ≥ 40 AND confidence ≥ MEDIUM

### `stock-morning-buy` — Trade Executor
- **Trigger:** 9:35 AM ET (Mon-Fri, 5 min after market open)
- **What:** Reads 10 candidates from S3, applies 5 filters, buys best 5
- **Filters (in order):**
  1. Gap filter: skip if price moved >2% from prev close
  2. Earnings filter: skip if stock reported earnings in last 3 days
  3. Blacklist: skip DVA, ON (0% win rate historically)
  4. Sector ban: skip Semis, Industrials (0% win rate)
  5. Sector cap: max 2 picks per sector (prevents concentration)
- **Design:** Does NOT re-run analysis — uses pre-computed recs + Finnhub real-time data
- **Output:** Updates `paper_trades.json` in S3
- **Budget:** Paper=$100/day, Live=$1000/day (configurable via LIVE_DAILY_BUDGET)
- **Alpaca:** If ALPACA_API_KEY is set, places real market orders via Alpaca REST API

### `stock-close-and-learn` — Close + Analysis
- **Trigger:** 4:05 PM ET (Mon-Fri, 5 min after market close)
- **What:** Fetches close/high prices via Finnhub, calculates P/L, runs learning engine
- **Output:** Updates `paper_trades.json` and `learnings.json` in S3
- **Alpaca:** If ALPACA_API_KEY is set, closes all live positions at market close

## Alpaca Integration
- **API:** REST v2, using `requests` directly (no SDK needed)
- **Auth:** APCA-API-KEY-ID + APCA-API-SECRET-KEY headers
- **Orders:** Market orders with `notional` (dollar amount) for fractional shares
- **Close:** DELETE /v2/positions/{symbol} to close entire position
- **Toggle:** Set ALPACA_LIVE_TRADING=true + provide keys to enable
- **Paper mode:** Uses paper-api.alpaca.markets by default (safe testing)

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
- `optimal_pnl`: profit if sold at intraday high (uses Finnhub `h` field for real day high)
- `missed_pnl`: money left on table (optimal_pnl - pnl)

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

---

## How the Stock Picker Works — Step by Step (Beginner Guide)

### Step 1: Get the list of stocks (`src/data_fetcher.py`)

```
"What stocks should we look at?"
```

- Goes to Wikipedia, grabs the table of all S&P 500 companies (503 tickers like AAPL, MSFT, GOOG...)
- For each ticker, calls Polygon.io to download 1 year of daily price data:
  - **Open** — price when market opened
  - **High** — highest price that day
  - **Low** — lowest price that day
  - **Close** — price when market closed
  - **Volume** — how many shares traded

---

### Step 2: Quick filter to top 50 (`src/screener.py` → `quick_bullish_rank`)

```
"Which of these 503 stocks look bullish at a glance?"
```

Simple checks (no heavy math):
- Is price above its 50-day average? → good sign
- Has it been going up the last 20 days? → momentum
- Has it been going up the last 5 days? → recent strength
- Is volume increasing? → people are interested

Each stock gets a quick 0-100 score. Top 50 move to deep analysis.

**Day trade extras:**
- Did the stock just spike abnormally? → SKIP (it'll probably crash back)
- Does it gap up then fade? → PENALTY (bad for day trading)

---

### Step 3: Technical indicators (`src/indicators.py`)

```
"What are the math-based signals saying?"
```

Runs 6 classic trading indicators on each stock:

| Indicator | What it checks | Bullish when |
|-----------|---------------|--------------|
| **RSI** (Relative Strength Index) | Is it oversold? | RSI between 30-50 (bouncing back) |
| **MACD** | Is momentum shifting up? | MACD line crosses above signal line |
| **Bollinger Bands** | Is price near the bottom of its range? | Price near lower band (cheap) |
| **Moving Averages** (20/50/200) | Is the trend up? | Price > 20-day > 50-day (aligned bullish) |
| **Golden Cross** | Major trend shift? | 50-day crosses above 200-day |
| **Volume** | Is today's volume abnormal? | Volume > 1.5× the 20-day average |

Each signal that fires adds points. Output: **0-100 technical score**.

---

### Step 4: Candlestick patterns (`src/patterns.py`)

```
"Do the candle shapes suggest a reversal or continuation?"
```

Looks at the shape of the last 5 days of price candles. Each "candle" is one day's Open/High/Low/Close drawn as a bar.

Detects 11 bullish patterns:

| Pattern | What it looks like | What it means |
|---------|--------------------|---------------|
| **Hammer** | Small body, long lower shadow | Sellers tried to push down but buyers won |
| **Bullish Engulfing** | Big green candle swallows previous red | Buyers overwhelmed sellers |
| **Morning Star** | Red → tiny → green (3 days) | Reversal from downtrend |
| **Three White Soldiers** | 3 consecutive green candles, each closing higher | Strong uptrend |
| **Doji** | Tiny body (open ≈ close) | Indecision → potential reversal |

Output: count of patterns found in last 5 days. More patterns = stronger signal.

---

### Step 5: News sentiment (`src/news_sentiment.py`)

```
"What's the news saying about this stock?"
```

1. Goes to Finviz.com, scrapes the 15 most recent headlines for the ticker
2. Runs each headline through TextBlob (simple AI that scores text as positive/negative)
3. Averages the scores: -1 (very negative) to +1 (very positive)
4. Converts to a 0-20 scale for the composite score

Example: "Apple reports record earnings" → positive → score 16/20

---

### Step 6: Intraday momentum (`src/screener.py` → `intraday_momentum_score`)

```
"Does this stock actually go UP during market hours, or does it just gap overnight?"
```

Looks at the last 10 days and asks:
- **Win rate**: What % of days did close > open? (we want ≥60%)
- **Gap-fade rate**: What % of days did it gap up at open then close lower? (we want ≤30%)
- **Avg return**: What's the average open→close % gain?
- **Overextended**: Did it just have an abnormal spike? (skip if yes)

This prevents picking stocks like NVDA that gap up 3% overnight but then sell off all day — bad for day trading.

---

### Step 7: Combine scores (`src/screener.py` → `analyze_stock`)

```
"Put it all together — how bullish is this stock?"
```

Weighted formula (day trade mode):
```
Composite = (35% × Technical) + (20% × Patterns) + (10% × News) + (35% × Intraday)
```

Then assign confidence:
- **HIGH** — 4+ signals fired + win rate ≥60% + gap-fade ≤30%
- **MEDIUM** — 3+ signals + win rate ≥50%
- **LOW** — not traded

---

### Step 8: Pick winners (`lambda/handler.py`)

```
"Which stocks do we actually trade?"
```

Filters:
- Composite score ≥ 40
- Confidence = HIGH or MEDIUM
- Take top 5
- Split $100 equally ($20 per stock)

If nothing qualifies → sit out the day (cash is a position).

---

### Step 9: Track results (`src/exit_strategy.py` + `src/learn.py`)

```
"How did we do? Could we have done better?"
```

**Exit strategy** — for each stock, calculates:
- What % does it typically run up from open? (avg open → high)
- How much does it give back? (avg high → close)
- Should you sell early (profit target) or hold to close?

**Learning engine** — after 20+ days asks:
- Win rate > 60%? ✅ or ❌
- Profit factor > 1.5? (gross profit ÷ gross loss)
- Max drawdown < 10%?
- Are HIGH confidence picks actually beating LOW?
- Which score ranges perform best?

When all criteria pass → "Ready for real money."

---

### Visual Summary

```
Wikipedia (503 tickers)
    │
    ▼
Polygon.io (1yr OHLC data)
    │
    ▼
┌─────────────────────────────────┐
│  Quick Screen (all 503)         │  → Top 50
│  • Trend? Volume? Momentum?     │
└─────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────┐
│  Deep Analysis (top 50)         │
│  ├── indicators.py  (35%)       │
│  ├── patterns.py    (20%)       │  → Composite Score
│  ├── news_sentiment (10%)       │
│  └── intraday momentum (35%)    │
└─────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────┐
│  Filter                         │
│  • Score ≥ 40                   │  → Top 5 picks
│  • Confidence ≥ MEDIUM          │
└─────────────────────────────────┘
    │
    ▼
   BUY at open → CLOSE at end → LEARN from results
```
