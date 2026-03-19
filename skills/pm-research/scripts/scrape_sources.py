"""
scrape_sources.py — Source Scraper for pm-research skill

Fetches relevant content from Brave Search and RSS feeds for a given
prediction market. All external content is treated as untrusted data and
length-capped before any downstream processing.

SECURITY: This module enforces prompt injection defense.
  - All scraped text is capped at MAX_SOURCE_CHARS
  - Injection patterns are detected and the source is discarded
  - Content is NEVER inserted into system prompts

Reference repos:
  - github.com/ryanfrigo/kalshi-ai-trading-bot (news scraping patterns)
  - github.com/suislanchez/polymarket-kalshi-weather-bot (RSS feeds)
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parents[3] / ".env", override=False)

MAX_SOURCE_CHARS = 2000  # Hard cap per source — NEVER increase without security review

# Patterns that indicate possible prompt injection attempts
INJECTION_PATTERNS = [
    r"ignore (previous|above|all) instructions",
    r"you are now",
    r"disregard (your|all|previous)",
    r"system prompt",
    r"new instructions:",
    r"forget everything",
    r"act as",
]

INJECTION_RE = re.compile("|".join(INJECTION_PATTERNS), re.IGNORECASE)

# All RSS feeds from references/source-config.md
RSS_FEEDS = [
    # General news
    "https://feeds.reuters.com/reuters/topNews",
    "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
    "https://feeds.bbci.co.uk/news/rss.xml",
    # Politics / policy
    "https://rss.politico.com/politics-news.xml",
    "https://thehill.com/rss/syndicator/19110/feed/",
    # Finance / economics
    "https://feeds.bloomberg.com/markets/news.rss",
    "https://www.wsj.com/xml/rss/3_7085.xml",
    # Science / tech
    "https://feeds.arstechnica.com/arstechnica/index/",
]


# ---------------------------------------------------------------------------
# Data Model
# ---------------------------------------------------------------------------

@dataclass
class SourceResult:
    source: str          # "brave" | "rss"
    content: str         # Sanitized, length-capped text
    item_count: int      # Number of results/articles found
    error: str | None    # Error message if fetch failed


# ---------------------------------------------------------------------------
# Injection Defense
# ---------------------------------------------------------------------------

def sanitize_content(text: str, source_name: str) -> str | None:
    """
    Cap length and check for injection patterns.
    Returns None if injection detected (caller should discard the source).
    """
    if INJECTION_RE.search(text):
        print(
            f"[pm-research] INJECTION DETECTED in {source_name} — discarding source",
            file=sys.stderr,
        )
        return None
    return text[:MAX_SOURCE_CHARS]


# ---------------------------------------------------------------------------
# Brave Search Scraper
# ---------------------------------------------------------------------------

def scrape_brave(query: str) -> SourceResult:
    """
    Fetch web search results via Brave Search API.
    Requires BRAVE_API_KEY environment variable.

    Docs: https://api.search.brave.com/app/documentation/web-search
    Returns top 10 results, combining title + description per result.
    Falls back to LLM web search if Brave fails or quota is exhausted.
    """
    api_key = os.environ.get("BRAVE_API_KEY", "")
    if not api_key:
        print("[pm-research] BRAVE_API_KEY not set — using LLM fallback", file=sys.stderr)
        return _scrape_llm_fallback(query)

    try:
        import httpx

        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": api_key,
        }
        params = {
            "q": query,
            "count": 10,
            "search_lang": "en",
            "freshness": "pw",  # Past week — prioritize recent results
        }
        resp = httpx.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers=headers,
            params=params,
            timeout=10,
        )

        # 429 = rate limit, 402 = quota exhausted — fall back to LLM
        if resp.status_code in (402, 429):
            print(
                f"[pm-research] Brave API returned {resp.status_code} — using LLM fallback",
                file=sys.stderr,
            )
            return _scrape_llm_fallback(query)

        resp.raise_for_status()
        data = resp.json()

        web_results = data.get("web", {}).get("results", [])
        snippets = [
            f"{r.get('title', '')}: {r.get('description', '')}"
            for r in web_results
            if r.get("title") or r.get("description")
        ]

        if not snippets:
            print("[pm-research] Brave returned no results — using LLM fallback", file=sys.stderr)
            return _scrape_llm_fallback(query)

        combined = " ".join(snippets)
        clean = sanitize_content(combined, "brave")
        if clean is None:
            return SourceResult("brave", "", 0, "Injection pattern detected — discarded")
        return SourceResult("brave", clean, len(snippets), None)

    except Exception as e:
        print(f"[pm-research] Brave error: {e} — using LLM fallback", file=sys.stderr)
        return _scrape_llm_fallback(query)


# ---------------------------------------------------------------------------
# LLM Web Search Fallback
# ---------------------------------------------------------------------------

def _scrape_llm_fallback(query: str) -> SourceResult:
    """
    Ask Claude for a brief summary of recent news/events relevant to the query.
    Used when Brave Search is unavailable or quota-exhausted.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return SourceResult("llm_fallback", "", 0, "No LLM API key available for fallback")

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        prompt = (
            f"Provide a brief factual summary of recent news, events, and publicly known information "
            f"relevant to this prediction market question: '{query}'. "
            f"Focus on facts, recent developments, and context that would help assess the probability. "
            f"Be concise (2-3 paragraphs max). Do not make predictions."
        )
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",  # Haiku — fast and cheap for search fallback
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        content = message.content[0].text if message.content else ""
        if not content:
            return SourceResult("llm_fallback", "", 0, "LLM returned empty response")

        clean = sanitize_content(content, "llm_fallback")
        if clean is None:
            return SourceResult("llm_fallback", "", 0, "Injection pattern detected — discarded")

        print("[pm-research] LLM fallback search succeeded", file=sys.stderr)
        return SourceResult("llm_fallback", clean, 1, None)

    except Exception as e:
        return SourceResult("llm_fallback", "", 0, f"LLM fallback error: {e}")


# ---------------------------------------------------------------------------
# RSS / News Scraper
# ---------------------------------------------------------------------------

def scrape_rss(query: str, feed_urls: list[str]) -> SourceResult:
    """
    Fetch and filter RSS feed entries matching the query.
    Feed URLs are configured in references/source-config.md.
    """
    try:
        import feedparser  # type: ignore
        import httpx

        articles = []
        query_word = query.lower().split()[0]
        for url in feed_urls:
            try:
                resp = httpx.get(url, timeout=8)
                feed = feedparser.parse(resp.text)
                for entry in feed.entries[:5]:
                    title = entry.get("title", "")
                    summary = entry.get("summary", "")
                    if query_word in (title + summary).lower():
                        articles.append(f"{title}: {summary}")
            except Exception:
                continue

        if not articles:
            return SourceResult("rss", "", 0, "No matching articles found")

        combined = " ".join(articles)
        clean = sanitize_content(combined, "rss")
        if clean is None:
            return SourceResult("rss", "", 0, "Injection pattern detected — discarded")
        return SourceResult("rss", clean, len(articles), None)

    except Exception as e:
        return SourceResult("rss", "", 0, str(e))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def scrape_all(market_title: str) -> dict[str, Any]:
    """Run all scrapers and return combined results."""
    # Use first 8 words of title as search query to keep it focused
    query = " ".join(market_title.split()[:8])

    results = [
        scrape_brave(query),
        scrape_rss(query, RSS_FEEDS),
    ]

    successful = [r for r in results if r.error is None and r.content]
    failed = [{"source": r.source, "error": r.error} for r in results if r.error]

    return {
        "query": query,
        "sources": [{"source": r.source, "content": r.content, "item_count": r.item_count} for r in successful],
        "failed_sources": failed,
        "source_count": len(successful),
    }


def main() -> None:
    args = sys.argv[1:]
    title = ""
    for i, arg in enumerate(args):
        if arg == "--title" and i + 1 < len(args):
            title = args[i + 1]

    if not title:
        print("Usage: scrape_sources.py --title 'Market title'", file=sys.stderr)
        sys.exit(1)

    result = scrape_all(title)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
