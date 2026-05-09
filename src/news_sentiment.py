"""News sentiment analysis using free sources."""
import requests
from bs4 import BeautifulSoup
from textblob import TextBlob


def fetch_finviz_news(ticker: str) -> list[str]:
    """Fetch recent news headlines from Finviz (free, no API key)."""
    url = f"https://finviz.com/quote.ashx?t={ticker}"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        news_table = soup.find(id="news-table")
        if not news_table:
            return []
        headlines = []
        for row in news_table.find_all("tr")[:15]:
            link = row.find("a")
            if link:
                headlines.append(link.text.strip())
        return headlines
    except Exception:
        return []


def score_sentiment(headlines: list[str]) -> dict:
    """Score sentiment of headlines using TextBlob. Returns -1 to 1 scale."""
    if not headlines:
        return {"sentiment_score": 0, "headline_count": 0, "avg_polarity": 0}
    
    polarities = [TextBlob(h).sentiment.polarity for h in headlines]
    avg = sum(polarities) / len(polarities)
    
    # Normalize to 0-20 score for bullish contribution
    # -1 to 1 -> 0 to 20
    news_score = int((avg + 1) * 10)
    
    return {
        "sentiment_score": min(news_score, 20),
        "headline_count": len(headlines),
        "avg_polarity": round(avg, 3),
    }


def get_news_score(ticker: str) -> dict:
    """Full pipeline: fetch news and score sentiment for a ticker."""
    headlines = fetch_finviz_news(ticker)
    return score_sentiment(headlines)
