#!/usr/bin/env python3
"""Paper Trading Bot - Mock $100/day investment at market open, report P/L at close.

Runs on a schedule:
  - 6:35 AM PT: Pick top stocks, record open prices (mock buy)
  - 1:05 PM PT: Record close prices, calculate P/L

Stores trades in a local JSON file for tracking history.
"""
import json
import sys
import time
from datetime import datetime, date
from pathlib import Path
from src.data_fetcher import get_sp500_tickers, fetch_ohlc
from src.screener import run_full_analysis
from src.exit_strategy import analyze_intraday_profile
import yfinance as yf

TRADES_FILE = Path(__file__).parent / "paper_trades.json"
DAILY_BUDGET = 100.0
TOP_PICKS = 5  # split $100 across top 5 = $20 each


def load_trades() -> dict:
    if TRADES_FILE.exists():
        return json.loads(TRADES_FILE.read_text())
    return {"trades": [], "summary": {"total_invested": 0, "total_pnl": 0, "days": 0}}


def save_trades(data: dict):
    TRADES_FILE.write_text(json.dumps(data, indent=2))


def morning_pick():
    """Run at market open: screen stocks, record buy prices."""
    today = date.today().isoformat()
    trades = load_trades()
    
    # Check if already bought today
    if any(t["date"] == today and t.get("open_price") for t in trades["trades"]):
        print(f"Already have picks for {today}. Skipping.")
        return
    
    print(f"[{datetime.now().strftime('%H:%M')}] Morning scan - picking top {TOP_PICKS} day trades...")
    
    # Get S&P 500 data
    tickers = get_sp500_tickers()
    ohlc_data = fetch_ohlc(tickers)
    
    # Run screener in daytrade mode
    results = run_full_analysis(ohlc_data, top_n=50, include_news=False, daytrade_mode=True)
    top = results[:TOP_PICKS]
    
    # Get today's actual open price using intraday 1-min bars
    picked_tickers = [r["ticker"] for r in top]
    live_prices = {}
    intraday = yf.download(picked_tickers, period="1d", interval="1m", progress=False)
    for ticker in picked_tickers:
        try:
            if len(picked_tickers) == 1:
                live_prices[ticker] = float(intraday["Open"].iloc[0])
            else:
                live_prices[ticker] = float(intraday["Open"][ticker].iloc[0])
        except (KeyError, IndexError):
            pass
    
    per_stock = DAILY_BUDGET / TOP_PICKS
    
    # Filter: only trade stocks with HIGH or MEDIUM confidence and minimum score
    MIN_SCORE = 40.0
    qualified = [r for r in top if r["composite_score"] >= MIN_SCORE and r["confidence"] in ("HIGH", "MEDIUM")]
    
    if not qualified:
        print(f"\n  ⚠️  NO TRADES TODAY — no stocks met minimum criteria")
        print(f"  (Need composite score >= {MIN_SCORE} and confidence >= MEDIUM)")
        print(f"  Sitting out is a valid strategy. Cash preserved.")
        trades["trades"].append({
            "date": today, "ticker": "CASH", "open_price": 0, "shares": 0,
            "invested": 0, "close_price": 0, "pnl": 0, "confidence": "SKIP",
            "composite_score": 0,
        })
        save_trades(trades)
        return
    
    # Use only qualified picks (may be fewer than TOP_PICKS)
    picks = qualified[:TOP_PICKS]
    per_stock = DAILY_BUDGET / len(picks)
    
    print(f"\n{'='*50}")
    print(f"  PAPER TRADES FOR {today}")
    print(f"  Budget: ${DAILY_BUDGET} (${per_stock:.0f} x {len(picks)} stocks)")
    if len(qualified) < TOP_PICKS:
        print(f"  ⚠️  Only {len(qualified)} stocks qualified (need score>={MIN_SCORE} + MEDIUM+ confidence)")
    print(f"{'='*50}\n")
    
    for r in picks:
        ticker = r["ticker"]
        open_price = live_prices.get(ticker, 0)
        
        if open_price <= 0:
            continue
        
        shares = per_stock / open_price
        trade = {
            "date": today,
            "ticker": ticker,
            "open_price": round(open_price, 2),
            "shares": round(shares, 6),
            "invested": per_stock,
            "close_price": None,
            "pnl": None,
            "confidence": r["confidence"],
            "composite_score": r["composite_score"],
        }
        trades["trades"].append(trade)
        print(f"  BUY {ticker:<6} {shares:.4f} shares @ ${open_price:.2f} (score: {r['composite_score']:.1f}, {r['confidence']})")
    
    save_trades(trades)
    print(f"\nPicks saved. Run 'python paper_trade.py close' after market close.")
    
    # Exit strategy recommendations
    print(f"\n{'='*50}")
    print(f"  EXIT PLAN (sell triggers)")
    print(f"{'='*50}")
    for r in picks:
        profile = analyze_intraday_profile(r["ticker"], days=20)
        if profile:
            target = profile["suggested_profit_target_pct"]
            trail = profile["suggested_trailing_stop_pct"]
            buy_price = next((t["open_price"] for t in trades["trades"] if t["date"] == today and t["ticker"] == r["ticker"]), 0)
            target_price = buy_price * (1 + target / 100) if buy_price else 0
            stop_price = buy_price * (1 - trail / 100) if buy_price else 0
            print(f"  {r['ticker']:<6} Strategy: {profile['strategy']}")
            print(f"         Take profit: +{target:.2f}% (${target_price:.2f})")
            print(f"         Trailing stop: -{trail:.2f}% from high")
            print(f"         Hard stop: ${stop_price:.2f}")


def afternoon_close():
    """Run at market close: record close prices, calculate P/L, compare exit strategies."""
    today = date.today().isoformat()
    trades = load_trades()
    
    # Find today's open trades
    today_trades = [t for t in trades["trades"] if t["date"] == today and t["close_price"] is None]
    
    if not today_trades:
        print(f"No open trades for {today}.")
        return
    
    tickers = [t["ticker"] for t in today_trades]
    prices = yf.download(tickers, period="1d", progress=False)
    
    print(f"\n{'='*60}")
    print(f"  PAPER TRADE RESULTS - {today}")
    print(f"{'='*60}\n")
    
    # Correct open prices with actual market data
    for t in today_trades:
        ticker = t["ticker"]
        try:
            if len(tickers) == 1:
                actual_open = float(prices["Open"].iloc[-1])
            else:
                actual_open = float(prices["Open"][ticker].iloc[-1])
            if actual_open > 0 and abs(actual_open - t["open_price"]) / t["open_price"] > 0.001:
                old_price = t["open_price"]
                t["open_price"] = round(actual_open, 2)
                t["shares"] = t["invested"] / actual_open  # recalculate shares
                print(f"  ⚠️  {ticker} open corrected: ${old_price:.2f} → ${actual_open:.2f}")
        except (KeyError, IndexError):
            pass
    
    print(f"\n  {'Ticker':<7}{'Open':>8}{'Close':>8}{'High':>8}{'P/L':>9}{'Optimal':>9}{'Missed':>8}")
    print(f"  {'-'*55}")
    
    day_pnl = 0
    day_optimal = 0
    for t in today_trades:
        ticker = t["ticker"]
        try:
            if len(tickers) == 1:
                close_price = float(prices["Close"].iloc[-1])
                high_price = float(prices["High"].iloc[-1])
            else:
                close_price = float(prices["Close"][ticker].iloc[-1])
                high_price = float(prices["High"][ticker].iloc[-1])
        except (KeyError, IndexError):
            close_price = t["open_price"]
            high_price = t["open_price"]
        
        pnl = t["shares"] * (close_price - t["open_price"])
        optimal_pnl = t["shares"] * (high_price - t["open_price"])
        missed = optimal_pnl - pnl
        
        # Update trade record
        t["close_price"] = round(close_price, 2)
        t["high_price"] = round(high_price, 2)
        t["pnl"] = round(pnl, 2)
        t["optimal_pnl"] = round(optimal_pnl, 2)
        t["missed_pnl"] = round(missed, 2)
        
        day_pnl += pnl
        day_optimal += optimal_pnl
        
        marker = "✓" if pnl > 0 else "✗"
        print(f"  {ticker:<7}${t['open_price']:>7.2f}${close_price:>7.2f}${high_price:>7.2f}  ${pnl:>+6.2f} ${optimal_pnl:>+6.2f} ${missed:>+5.2f} {marker}")
    
    # Update summary
    trades["summary"]["total_invested"] += DAILY_BUDGET
    trades["summary"]["total_pnl"] += round(day_pnl, 2)
    trades["summary"]["total_optimal_pnl"] = trades["summary"].get("total_optimal_pnl", 0) + round(day_optimal, 2)
    trades["summary"]["days"] += 1
    
    save_trades(trades)
    
    day_missed = day_optimal - day_pnl
    print(f"  {'-'*55}")
    print(f"  {'TODAY:':<7}{'':>8}{'':>8}{'':>8}  ${day_pnl:>+6.2f} ${day_optimal:>+6.2f} ${day_missed:>+5.2f}")
    print(f"\n  💡 You left ${day_missed:.2f} on the table by holding to close.")
    print(f"     Selling at intraday high would have earned ${day_optimal:.2f} instead of ${day_pnl:.2f}")
    
    total_missed = trades["summary"]["total_optimal_pnl"] - trades["summary"]["total_pnl"]
    print(f"\n  Running Total:")
    print(f"    Days traded: {trades['summary']['days']}")
    print(f"    Total invested: ${trades['summary']['total_invested']:.2f}")
    print(f"    Total P/L: ${trades['summary']['total_pnl']:+.2f}")
    print(f"    Optimal P/L (sold at high): ${trades['summary']['total_optimal_pnl']:+.2f}")
    print(f"    Total left on table: ${total_missed:.2f}")
    avg = trades['summary']['total_pnl'] / trades['summary']['days'] if trades['summary']['days'] > 0 else 0
    print(f"    Avg daily P/L: ${avg:+.2f}")


def show_history():
    """Show all paper trade history."""
    trades = load_trades()
    if not trades["trades"]:
        print("No trades yet. Run 'python paper_trade.py buy' first.")
        return
    
    print(f"\n{'='*60}")
    print(f"  PAPER TRADE HISTORY")
    print(f"{'='*60}\n")
    
    # Group by date
    by_date = {}
    for t in trades["trades"]:
        by_date.setdefault(t["date"], []).append(t)
    
    for d in sorted(by_date.keys()):
        day_trades = by_date[d]
        day_pnl = sum(t["pnl"] or 0 for t in day_trades)
        closed = all(t["close_price"] for t in day_trades)
        status = f"${day_pnl:+.2f}" if closed else "OPEN"
        tickers = ", ".join(t["ticker"] for t in day_trades)
        print(f"  {d}  {status:<10} [{tickers}]")
    
    s = trades["summary"]
    print(f"\n  {'='*40}")
    print(f"  Total: ${s['total_pnl']:+.2f} over {s['days']} days (${s['total_invested']:.0f} invested)")
    if s["days"] > 0:
        win_days = sum(1 for d, ts in by_date.items() if sum(t["pnl"] or 0 for t in ts) > 0)
        print(f"  Win rate: {win_days}/{s['days']} days")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python paper_trade.py buy     - Morning: pick stocks & mock buy at open")
        print("  python paper_trade.py close   - Afternoon: record close prices & P/L")
        print("  python paper_trade.py history - Show all paper trade history")
        sys.exit(0)
    
    cmd = sys.argv[1].lower()
    if cmd == "buy":
        morning_pick()
    elif cmd == "close":
        afternoon_close()
    elif cmd == "history":
        show_history()
    else:
        print(f"Unknown command: {cmd}. Use 'buy', 'close', or 'history'.")
