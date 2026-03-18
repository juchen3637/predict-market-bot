# Source Configuration

## Active Sources

| Source | Status | Rate Limit | Auth |
|--------|--------|------------|------|
| Brave Search API | Active | 2000 req/month (free tier) | `BRAVE_API_KEY` |
| Reuters RSS | Active | None | None |
| NYT RSS | Active | None | None |
| BBC RSS | Active | None | None |
| Politico RSS | Active | None | None |
| The Hill RSS | Active | None | None |
| Bloomberg RSS | Active | None | None |
| WSJ RSS | Active | None | None |
| Ars Technica RSS | Active | None | None |

## RSS Feed URLs

```
# General news
https://feeds.reuters.com/reuters/topNews
https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml
https://feeds.bbci.co.uk/news/rss.xml

# Politics / policy
https://rss.politico.com/politics-news.xml
https://thehill.com/rss/syndicator/19110/feed/

# Finance / economics
https://feeds.bloomberg.com/markets/news.rss
https://www.wsj.com/xml/rss/3_7085.xml

# Science / tech (for relevant market categories)
https://feeds.arstechnica.com/arstechnica/index/
```

## Blocked Domains

| Domain | Reason | Added |
|--------|--------|-------|
| (none yet) | | |

## Adding New Sources

1. Add feed URL to the appropriate section above
2. Test with: `python scripts/scrape_sources.py --title "test query"`
3. Verify content is not triggering injection detection
4. Document rate limits and any auth requirements
