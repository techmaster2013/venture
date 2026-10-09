# Venture

Venture is a purple web search engine project with its own crawler, SQLite full-text index, search API, and custom results UI. DuckDuckGo is currently only a temporary fallback when Venture's own index has no matching result.

## VentureBot v2

`crawler.py` is designed to run continuously on a small always-on machine such as an older MacBook Pro.

It now has:

- a persistent SQLite crawl frontier, so discovered URLs survive restarts
- automatic crash recovery for interrupted crawl jobs
- `robots.txt` support
- `Crawl-delay` support with a minimum 2-second per-host delay by default
- sitemap discovery from `robots.txt`
- automatic link discovery across the public web
- URL normalization and common tracking-parameter removal
- crawl-trap limits for path depth, query parameters, queue size, and pages per host
- HTML-only indexing with response-size limits
- canonical URL handling
- `noindex` and `nofollow` handling
- duplicate-content suppression using SHA-256 hashes
- retries with exponential backoff and `Retry-After` handling
- weekly recrawling of stale pages by default
- a stored link graph for future PageRank-style ranking
- SQLite WAL mode so the crawler and Venture search server can use the index together
- graceful SIGINT/SIGTERM shutdown
- crawler event history and live statistics

The crawler identifies itself as:

```text
VentureBot/0.2 (+https://github.com/techmaster2013/venture)
```

## Recommended Mac setup

Clone or update Venture on the Mac:

```bash
git clone https://github.com/techmaster2013/venture.git
cd venture
```

If it is already cloned:

```bash
cd venture
git pull
```

Make sure Python 3 is installed:

```bash
python3 --version
```

Initialize the crawler database and starter seeds:

```bash
python3 crawler.py init
```

Do a small foreground test first:

```bash
python3 crawler.py once --max-pages 25
```

See what it indexed:

```bash
python3 crawler.py stats
```

Then install the always-on macOS LaunchAgent:

```bash
bash install-macos.sh
```

The installer uses `launchd` to start VentureBot at login, restart it if it exits, run it at low I/O priority, and use `caffeinate -i` so normal idle sleep does not stop the crawler while the Mac is awake.

Watch the crawler live:

```bash
tail -f logs/crawler.log
```

Check its index at any time:

```bash
python3 crawler.py stats
```

Stop and remove the automatic crawler without deleting the index:

```bash
bash install-macos.sh uninstall
```

## Important laptop sleep note

`caffeinate -i` prevents normal idle sleep, but closing a MacBook lid can still suspend the machine unless macOS is in a supported clamshell setup. For a dedicated Venture crawler, keep it connected to power and make sure the machine itself remains awake.

## Seeds

Starter seeds live in `seeds.txt`. Keep the list small and trustworthy; VentureBot discovers new pages from links and sitemaps automatically.

Add a seed immediately without editing the file:

```bash
python3 crawler.py seed https://example.com/
```

Or add one or more URLs to `seeds.txt`; the continuous crawler checks the seed file again periodically.

## Crawler commands

Run continuously in the foreground:

```bash
python3 crawler.py run
```

Run a limited test batch:

```bash
python3 crawler.py once --max-pages 100
```

Show statistics:

```bash
python3 crawler.py stats
```

Add seeds:

```bash
python3 crawler.py seed https://example.com/ https://example.org/
```

Useful tuning options:

```bash
python3 crawler.py run \
  --delay 2 \
  --recrawl-days 7 \
  --max-depth 6 \
  --per-host-limit 5000 \
  --queue-limit 250000
```

The defaults are intentionally conservative for an older always-on Mac. Do not remove robots.txt compliance or aggressively lower the per-host delay.

## Search architecture

1. VentureBot discovers and fetches public HTML pages.
2. Extracted title, description, body, domain, URL, and crawl timestamp are stored in `venture.db`.
3. SQLite FTS5 is the searchable text index.
4. The crawler stores page-to-page links for future authority ranking.
5. `server.py` exposes `/api/search?q=...` and ranks matches with BM25.
6. `app.js` searches Venture's own index first.
7. DuckDuckGo Instant Answer data currently fills gaps while Venture's own index grows.

## Run Venture locally

```bash
python3 server.py
```

Then open:

```text
http://localhost:0826
```

The search API is available at:

```text
http://localhost:0826/api/search?q=python
```

## Runtime files

These are intentionally excluded from Git:

- `venture.db`
- `venture.db-wal`
- `venture.db-shm`
- `logs/`

The database is the actual local Venture index. Back it up if the crawler machine becomes important.

## Next engine work

Good next steps are PageRank-style authority scoring, better snippets, language detection, spam/SEO filtering, host quality signals, recrawl prioritization based on change frequency, distributed crawlers, and dedicated image/news indexes.
