# Venture

Venture is a purple web search engine project. It now includes its own crawler, SQLite full-text index, search API, and custom results UI. DuckDuckGo is used only as a temporary fallback when Venture's own index has no matching result.

## Architecture

`crawler.py` crawls public HTML pages, respects `robots.txt`, extracts text and links, and stores pages in `venture.db`.

`server.py` serves the website on port `0826` and exposes `/api/search?q=...`, ranked with SQLite FTS5/BM25.

`app.js` searches Venture's own index first. If the local index is empty or has no match, it can temporarily use DuckDuckGo's public Instant Answer data while Venture's index grows.

## Start an index

Use a few useful seed sites first instead of trying to crawl the entire web at once:

```bash
python3 crawler.py https://en.wikipedia.org https://developer.mozilla.org https://www.python.org --limit 1000
```

For a controlled test that stays on the seed domains:

```bash
python3 crawler.py https://www.python.org --limit 100 --same-domain
```

The crawler defaults to a 1 second delay between requests and identifies itself as `VentureBot/0.1`. Do not remove robots.txt compliance or crawl-delay/politeness controls when scaling it up.

## Run Venture

```bash
python3 server.py
```

Then open `http://localhost:0826`.

## Current search stack

1. Venture crawler discovers public pages.
2. Extracted page title, description, body, domain, URL, and crawl timestamp are stored in SQLite.
3. SQLite FTS5 provides the searchable index.
4. BM25 ranks matching pages, with stronger weights for titles and descriptions.
5. Venture renders results in its own UI.
6. DDG Instant Answer data is only a temporary fallback for gaps in the young Venture index.

## Next steps

The first crawler is intentionally small and polite. Future work can add recrawling schedules, sitemaps, canonical-tag handling, duplicate-content clustering, PageRank-style link signals, language detection, better snippets, spam filtering, multiple crawler workers, a distributed index, and image/news indexes.
