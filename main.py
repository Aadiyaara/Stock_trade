#!/usr/bin/env python3
"""Stock Recommender - S&P 500 bullish stock screener with multi-pattern confluence."""
import argparse
import sys
from datetime import datetime
from src.data_fetcher import get_sp500_tickers, fetch_ohlc
from src.screener import run_full_analysis


def print_header():
    print("=" * 70)
    print("  STOCK RECOMMENDER - Bullish Signal Scanner")
    print(f"  Run: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 70)


def print_results(results: list[dict], top_n: int = 20, daytrade_mode: bool = False):
    if daytrade_mode:
        print(f"\n{'Rank':<5}{'Ticker':<8}{'Score':<8}{'Conf':<8}{'WinRate':<8}{'GapFade':<8}{'AvgRet%':<8}{'Signals'}")
        print("-" * 75)
        for i, r in enumerate(results[:top_n], 1):
            im = r.get("intraday_stats", {})
            signals_str = ", ".join(
                list(r.get("patterns_detected", {}).keys())[:2] +
                list(r.get("technical_signals", {}).keys())[:2]
            )
            print(
                f"{i:<5}{r['ticker']:<8}{r['composite_score']:<8.1f}"
                f"{r['confidence']:<8}{im.get('intraday_win_rate',0):<8.0f}%"
                f"{im.get('gap_fade_rate',0):<8.0f}%"
                f"{im.get('avg_intraday_pct',0):<+7.3f}%"
                f" {signals_str[:30]}"
            )
    else:
        print(f"\n{'Rank':<5}{'Ticker':<8}{'Score':<8}{'Conf':<8}{'Tech':<6}{'Pat':<5}{'News':<6}{'Signals'}")
        print("-" * 70)
        for i, r in enumerate(results[:top_n], 1):
            signals_str = ", ".join(
                list(r.get("patterns_detected", {}).keys())[:3] +
                list(r.get("technical_signals", {}).keys())[:2]
            )
            print(
                f"{i:<5}{r['ticker']:<8}{r['composite_score']:<8.1f}"
                f"{r['confidence']:<8}{r['technical_score']:<6}"
                f"{r['pattern_score']:<5}{r['news_score']:<6}"
                f"{signals_str[:35]}"
            )
    
    # Detailed view of top 5
    print(f"\n{'=' * 70}")
    print("  TOP 5 DETAILED BREAKDOWN")
    print("=" * 70)
    for i, r in enumerate(results[:5], 1):
        im = r.get("intraday_stats", {})
        print(f"\n  #{i} {r['ticker']} — Composite: {r['composite_score']:.1f} [{r['confidence']}]")
        print(f"     Technical Score: {r['technical_score']}/100")
        if r.get("technical_signals"):
            for sig, val in r["technical_signals"].items():
                print(f"       • {sig}: {val}")
        print(f"     Pattern Score: {r['pattern_score']} (last 5 days)")
        if r.get("patterns_detected"):
            for pat, count in r["patterns_detected"].items():
                print(f"       • {pat}: {count}x")
        print(f"     News Sentiment: {r['news_score']}/20 (polarity: {r.get('news_polarity', 'N/A')})")
        if daytrade_mode:
            print(f"     Intraday Stats (10-day):")
            print(f"       • Win rate: {im.get('intraday_win_rate', 0):.0f}%")
            print(f"       • Avg return: {im.get('avg_intraday_pct', 0):+.3f}%")
            print(f"       • Gap-fade rate: {im.get('gap_fade_rate', 0):.0f}% (lower=better)")
            print(f"       • Intraday ratio: {im.get('intraday_ratio', 0):.0f}% of gains happen intraday")


def main():
    parser = argparse.ArgumentParser(description="S&P 500 Bullish Stock Screener")
    parser.add_argument("--top", type=int, default=50, help="Number of stocks to deep-analyze (default: 50)")
    parser.add_argument("--show", type=int, default=20, help="Number of results to display (default: 20)")
    parser.add_argument("--no-news", action="store_true", help="Skip news sentiment (faster)")
    parser.add_argument("--daytrade", action="store_true", help="Day trading mode: filters for intraday momentum, penalizes gap-and-fade stocks")
    parser.add_argument("--tickers", nargs="+", help="Analyze specific tickers instead of S&P 500")
    args = parser.parse_args()
    
    print_header()
    
    # Get tickers
    if args.tickers:
        tickers = [t.upper() for t in args.tickers]
        print(f"\nAnalyzing {len(tickers)} specified tickers...")
    else:
        print("\nFetching S&P 500 tickers...")
        tickers = get_sp500_tickers()
        print(f"Found {len(tickers)} tickers")
    
    # Fetch OHLC data
    print("Downloading 1-year OHLC data (this may take a few minutes)...")
    ohlc_data = fetch_ohlc(tickers)
    print(f"Got data for {len(ohlc_data)} stocks")
    
    if not ohlc_data:
        print("ERROR: No data fetched. Check your internet connection.")
        sys.exit(1)
    
    # Run analysis
    results = run_full_analysis(
        ohlc_data, 
        top_n=args.top, 
        include_news=not args.no_news,
        daytrade_mode=args.daytrade,
    )
    
    if not results:
        print("ERROR: Analysis produced no results.")
        sys.exit(1)
    
    # Display
    print_results(results, top_n=args.show, daytrade_mode=args.daytrade)
    
    print(f"\n{'=' * 70}")
    print("  DISCLAIMER: This is for educational purposes only. Not financial advice.")
    print("=" * 70)


if __name__ == "__main__":
    main()
