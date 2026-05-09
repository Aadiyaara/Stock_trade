#!/usr/bin/env python3
"""Learning Engine - Analyzes past trades to identify what works and what doesn't.

Runs after market close to:
1. Assess readiness to go live
2. Identify winning/losing patterns
3. Auto-adjust scoring weights based on performance
"""
import json
from pathlib import Path
from datetime import datetime
from collections import defaultdict

TRADES_FILE = Path(__file__).parent / "paper_trades.json"
LEARNINGS_FILE = Path(__file__).parent / "learnings.json"

# Readiness criteria
MIN_DAYS = 20
MIN_WIN_RATE = 60.0
MIN_PROFIT_FACTOR = 1.5  # gross profit / gross loss
MAX_DRAWDOWN_PCT = 10.0  # max cumulative loss from peak


def load_trades():
    if TRADES_FILE.exists():
        return json.loads(TRADES_FILE.read_text())
    return {"trades": [], "summary": {"total_invested": 0, "total_pnl": 0, "days": 0}}


def load_learnings():
    if LEARNINGS_FILE.exists():
        return json.loads(LEARNINGS_FILE.read_text())
    return {"weight_adjustments": {}, "insights": [], "last_updated": None}


def save_learnings(data):
    data["last_updated"] = datetime.now().isoformat()
    LEARNINGS_FILE.write_text(json.dumps(data, indent=2))


def analyze_performance(trades_data: dict) -> dict:
    """Deep analysis of all closed trades."""
    trades = [t for t in trades_data["trades"] if t.get("pnl") is not None and t["ticker"] != "CASH"]
    
    if not trades:
        return {"status": "NO_DATA"}
    
    # Store raw trades for exit efficiency analysis
    result_trades = trades
    
    # Basic stats
    winners = [t for t in trades if t["pnl"] > 0]
    losers = [t for t in trades if t["pnl"] < 0]
    
    gross_profit = sum(t["pnl"] for t in winners)
    gross_loss = abs(sum(t["pnl"] for t in losers))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    
    # Win rate
    win_rate = len(winners) / len(trades) * 100 if trades else 0
    
    # Max drawdown
    cumulative = 0
    peak = 0
    max_dd = 0
    by_date = defaultdict(list)
    for t in trades:
        by_date[t["date"]].append(t)
    for d in sorted(by_date.keys()):
        day_pnl = sum(t["pnl"] for t in by_date[d])
        cumulative += day_pnl
        peak = max(peak, cumulative)
        dd = peak - cumulative
        max_dd = max(max_dd, dd)
    
    total_invested = trades_data["summary"]["total_invested"]
    max_dd_pct = (max_dd / total_invested * 100) if total_invested > 0 else 0
    
    # Per-confidence analysis
    by_confidence = defaultdict(list)
    for t in trades:
        by_confidence[t.get("confidence", "UNKNOWN")].append(t)
    
    confidence_stats = {}
    for conf, conf_trades in by_confidence.items():
        conf_winners = [t for t in conf_trades if t["pnl"] > 0]
        confidence_stats[conf] = {
            "count": len(conf_trades),
            "win_rate": len(conf_winners) / len(conf_trades) * 100,
            "avg_pnl": sum(t["pnl"] for t in conf_trades) / len(conf_trades),
        }
    
    # Per-ticker analysis
    by_ticker = defaultdict(list)
    for t in trades:
        by_ticker[t["ticker"]].append(t)
    
    ticker_stats = {}
    for ticker, ticker_trades in by_ticker.items():
        ticker_winners = [t for t in ticker_trades if t["pnl"] > 0]
        ticker_stats[ticker] = {
            "trades": len(ticker_trades),
            "win_rate": len(ticker_winners) / len(ticker_trades) * 100,
            "total_pnl": sum(t["pnl"] for t in ticker_trades),
            "avg_score": sum(t.get("composite_score", 0) for t in ticker_trades) / len(ticker_trades),
        }
    
    # Score bracket analysis (does higher score = better results?)
    score_brackets = {"40-55": [], "55-70": [], "70-85": [], "85+": []}
    for t in trades:
        s = t.get("composite_score", 0)
        if s >= 85:
            score_brackets["85+"].append(t)
        elif s >= 70:
            score_brackets["70-85"].append(t)
        elif s >= 55:
            score_brackets["55-70"].append(t)
        else:
            score_brackets["40-55"].append(t)
    
    bracket_stats = {}
    for bracket, b_trades in score_brackets.items():
        if b_trades:
            b_winners = [t for t in b_trades if t["pnl"] > 0]
            bracket_stats[bracket] = {
                "count": len(b_trades),
                "win_rate": len(b_winners) / len(b_trades) * 100,
                "avg_pnl": sum(t["pnl"] for t in b_trades) / len(b_trades),
            }
    
    days_traded = len(set(t["date"] for t in trades))
    
    return {
        "status": "OK",
        "_raw_trades": result_trades,
        "days_traded": days_traded,
        "total_trades": len(trades),
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "avg_winner": round(gross_profit / len(winners), 2) if winners else 0,
        "avg_loser": round(gross_loss / len(losers), 2) if losers else 0,
        "confidence_stats": confidence_stats,
        "ticker_stats": ticker_stats,
        "bracket_stats": bracket_stats,
    }


def generate_insights(perf: dict) -> list[str]:
    """Generate actionable insights from performance data."""
    insights = []
    
    if perf["status"] == "NO_DATA":
        return ["No trades yet. Start paper trading to generate data."]
    
    # Confidence level effectiveness
    conf_stats = perf.get("confidence_stats", {})
    if "HIGH" in conf_stats and "LOW" in conf_stats:
        high_wr = conf_stats["HIGH"]["win_rate"]
        low_wr = conf_stats["LOW"]["win_rate"]
        if high_wr > low_wr + 10:
            insights.append(f"✅ HIGH confidence trades win {high_wr:.0f}% vs LOW at {low_wr:.0f}% — confidence filter is working")
        elif low_wr >= high_wr:
            insights.append(f"⚠️ LOW confidence trades ({low_wr:.0f}%) outperform HIGH ({high_wr:.0f}%) — confidence scoring needs recalibration")
    
    # Score brackets
    bracket_stats = perf.get("bracket_stats", {})
    if bracket_stats:
        best_bracket = max(bracket_stats.items(), key=lambda x: x[1]["avg_pnl"])
        worst_bracket = min(bracket_stats.items(), key=lambda x: x[1]["avg_pnl"])
        if best_bracket[1]["avg_pnl"] > 0:
            insights.append(f"✅ Best performing score range: {best_bracket[0]} (avg P/L: ${best_bracket[1]['avg_pnl']:+.2f})")
        if worst_bracket[1]["avg_pnl"] < 0:
            insights.append(f"⚠️ Worst score range: {worst_bracket[0]} (avg P/L: ${worst_bracket[1]['avg_pnl']:+.2f}) — consider raising minimum threshold")
    
    # Repeat losers
    ticker_stats = perf.get("ticker_stats", {})
    repeat_losers = [t for t, s in ticker_stats.items() if s["trades"] >= 2 and s["win_rate"] < 40]
    if repeat_losers:
        insights.append(f"🚫 Repeat losers (consider blocklist): {', '.join(repeat_losers)}")
    
    repeat_winners = [t for t, s in ticker_stats.items() if s["trades"] >= 2 and s["win_rate"] > 70]
    if repeat_winners:
        insights.append(f"⭐ Consistent winners: {', '.join(repeat_winners)}")
    
    # Profit factor
    if perf["profit_factor"] < 1.0:
        insights.append(f"🔴 Profit factor {perf['profit_factor']:.2f} (below 1.0 = losing money). Strategy needs adjustment.")
    elif perf["profit_factor"] < 1.5:
        insights.append(f"🟡 Profit factor {perf['profit_factor']:.2f} — marginal. Need more edge.")
    else:
        insights.append(f"🟢 Profit factor {perf['profit_factor']:.2f} — healthy.")
    
    # Exit efficiency
    trades_with_optimal = [t for t in perf.get("_raw_trades", []) if t.get("optimal_pnl") is not None]
    if trades_with_optimal:
        total_actual = sum(t["pnl"] for t in trades_with_optimal)
        total_optimal = sum(t["optimal_pnl"] for t in trades_with_optimal)
        if total_optimal > 0:
            efficiency = total_actual / total_optimal * 100
            insights.append(f"📊 Exit efficiency: {efficiency:.0f}% (capturing {efficiency:.0f}% of possible gains)")
            if efficiency < 60:
                insights.append(f"⚠️ Leaving too much on table — trailing stop would help capture more of the move")
    
    # Win/loss size asymmetry
    if perf["avg_winner"] and perf["avg_loser"]:
        ratio = perf["avg_winner"] / perf["avg_loser"]
        if ratio < 0.8:
            insights.append(f"⚠️ Avg winner (${perf['avg_winner']:.2f}) smaller than avg loser (${perf['avg_loser']:.2f}) — consider tighter stop-loss")
        elif ratio > 1.5:
            insights.append(f"✅ Winners are {ratio:.1f}x larger than losers — good risk/reward")
    
    return insights


def suggest_weight_adjustments(perf: dict) -> dict:
    """Suggest scoring weight changes based on what's working."""
    adjustments = {}
    
    if perf["status"] == "NO_DATA" or perf["days_traded"] < 5:
        return adjustments
    
    conf_stats = perf.get("confidence_stats", {})
    
    # If HIGH confidence isn't outperforming, intraday filter may need more weight
    if "HIGH" in conf_stats and conf_stats["HIGH"]["win_rate"] < 55:
        adjustments["intraday_weight"] = "increase from 35% to 45%"
        adjustments["reason_intraday"] = "HIGH confidence trades underperforming — intraday momentum needs more influence"
    
    # If low-score trades are winning, threshold might be too high
    bracket_stats = perf.get("bracket_stats", {})
    if "40-55" in bracket_stats and bracket_stats["40-55"].get("win_rate", 0) > 65:
        adjustments["min_score"] = "lower from 40 to 35"
        adjustments["reason_score"] = "Low-score trades winning at high rate — we're being too selective"
    
    # If high-score trades are losing, something is wrong with scoring
    if "70-85" in bracket_stats and bracket_stats["70-85"].get("win_rate", 0) < 45:
        adjustments["technical_weight"] = "decrease from 35% to 25%"
        adjustments["pattern_weight"] = "increase from 20% to 30%"
        adjustments["reason_rebalance"] = "High-score trades losing — technical indicators may be lagging"
    
    return adjustments


def readiness_report(perf: dict) -> dict:
    """Determine if strategy is ready for real money."""
    if perf["status"] == "NO_DATA":
        return {"ready": False, "reason": "No data yet", "progress": "0%"}
    
    checks = {
        "min_days": perf["days_traded"] >= MIN_DAYS,
        "win_rate": perf["win_rate"] >= MIN_WIN_RATE,
        "profit_factor": perf["profit_factor"] >= MIN_PROFIT_FACTOR,
        "max_drawdown": perf["max_drawdown_pct"] <= MAX_DRAWDOWN_PCT,
    }
    
    passed = sum(checks.values())
    total = len(checks)
    ready = all(checks.values())
    
    details = {
        f"Days traded ({MIN_DAYS} needed)": f"{perf['days_traded']} {'✅' if checks['min_days'] else '❌'}",
        f"Win rate (>{MIN_WIN_RATE}%)": f"{perf['win_rate']}% {'✅' if checks['win_rate'] else '❌'}",
        f"Profit factor (>{MIN_PROFIT_FACTOR})": f"{perf['profit_factor']} {'✅' if checks['profit_factor'] else '❌'}",
        f"Max drawdown (<{MAX_DRAWDOWN_PCT}%)": f"{perf['max_drawdown_pct']}% {'✅' if checks['max_drawdown'] else '❌'}",
    }
    
    return {
        "ready": ready,
        "progress": f"{passed}/{total} criteria met",
        "details": details,
        "verdict": "🟢 READY — Strategy has proven itself. Start small with real money."
                   if ready else
                   "🔴 NOT READY — Keep paper trading until all criteria are met.",
    }


def run_learning():
    """Full learning cycle: analyze, generate insights, suggest adjustments."""
    trades_data = load_trades()
    perf = analyze_performance(trades_data)
    
    if perf["status"] == "NO_DATA":
        print("No trades to analyze yet.")
        return
    
    insights = generate_insights(perf)
    adjustments = suggest_weight_adjustments(perf)
    readiness = readiness_report(perf)
    
    # Save learnings
    learnings = {
        "weight_adjustments": adjustments,
        "insights": insights,
        "performance": perf,
        "readiness": readiness,
        "last_updated": None,
    }
    save_learnings(learnings)
    
    # Print report
    print("=" * 60)
    print("  LEARNING ENGINE REPORT")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)
    
    print(f"\n📊 PERFORMANCE ({perf['days_traded']} days, {perf['total_trades']} trades)")
    print(f"   Win rate: {perf['win_rate']}%")
    print(f"   Profit factor: {perf['profit_factor']}")
    print(f"   Max drawdown: {perf['max_drawdown_pct']}%")
    print(f"   Avg winner: ${perf['avg_winner']:.2f} | Avg loser: ${perf['avg_loser']:.2f}")
    
    print(f"\n💡 INSIGHTS")
    for i in insights:
        print(f"   {i}")
    
    if adjustments:
        print(f"\n🔧 SUGGESTED ADJUSTMENTS")
        for key, val in adjustments.items():
            if not key.startswith("reason"):
                reason = adjustments.get(f"reason_{key.split('_')[0]}", "")
                print(f"   • {key}: {val}")
                if reason:
                    print(f"     ({reason})")
    
    print(f"\n🎯 READINESS TO GO LIVE")
    print(f"   {readiness['verdict']}")
    print(f"   Progress: {readiness['progress']}")
    for check, status in readiness["details"].items():
        print(f"   • {check}: {status}")


if __name__ == "__main__":
    run_learning()
