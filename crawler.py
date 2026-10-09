#!/usr/bin/env python3
"""Venture's first-party web crawler and local search index.

Uses only Python's standard library. It respects robots.txt, stays within
configured limits, stores extracted page text in SQLite, and discovers links
breadth-first from seed URLs.
"""

import argparse
import hashlib
import re
import sqlite3
import time
from collections import deque
from html import unescape
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.request import Request, build_opener
from urllib.robotparser import RobotFileParser

USER_AGENT = "VentureBot/0.1 (+https://github.com/techmaster2013/venture)"
DB_PATH = "venture.db"

class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.description = ""
        self.links = []
        self.text = []
        self._in_title = False
        self._ignored = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "title": self._in_title = True
        if tag in {"script", "style", "noscript", "svg"}: self._ignored += 1
        if tag == "a" and attrs.get("href"): self.links.append(attrs["href"])
        if tag == "meta" and attrs.get("name", "").lower() == "description":
            self.description = attrs.get("content", "").strip()

    def handle_endtag(self, tag):
        if tag == "title": self._in_title = False
        if tag in {"script", "style", "noscript", "svg"} and self._ignored: self._ignored -= 1

    def handle_data(self, data):
        value = " ".join(data.split())
        if not value: return
        if self._in_title: self.title += (" " if self.title else "") + value
        if not self._ignored: self.text.append(value)

def init_db(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS pages (
      url TEXT PRIMARY KEY,
      title TEXT NOT NULL,
      description TEXT NOT NULL,
      body TEXT NOT NULL,
      domain TEXT NOT NULL,
      content_hash TEXT NOT NULL,
      crawled_at INTEGER NOT NULL
    );
    CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
      url UNINDEXED, title, description, body, domain,
      tokenize='porter unicode61'
    );
    """)

def canonical(url):
    url, _ = urldefrag(url)
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}: return None
    path = parsed.path or "/"
    return parsed._replace(path=path, fragment="").geturl()

def robots_for(url, cache):
    p = urlparse(url)
    root = f"{p.scheme}://{p.netloc}"
    if root not in cache:
        rp = RobotFileParser()
        rp.set_url(root + "/robots.txt")
        try: rp.read()
        except Exception: pass
        cache[root] = rp
    return cache[root]

def fetch(url, timeout=12):
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    with build_opener().open(req, timeout=timeout) as response:
        ctype = response.headers.get_content_type()
        if ctype not in {"text/html", "application/xhtml+xml"}: return None
        raw = response.read(2_000_000)
        charset = response.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")

def save_page(conn, url, parser):
    body = " ".join(parser.text)
    title = parser.title.strip()[:300] or urlparse(url).netloc
    description = parser.description.strip()[:600]
    if not description: description = body[:300]
    domain = urlparse(url).netloc.lower().removeprefix("www.")
    digest = hashlib.sha256(body.encode()).hexdigest()
    now = int(time.time())
    conn.execute("INSERT OR REPLACE INTO pages VALUES (?,?,?,?,?,?,?)", (url,title,description,body,domain,digest,now))
    conn.execute("DELETE FROM pages_fts WHERE url=?", (url,))
    conn.execute("INSERT INTO pages_fts(url,title,description,body,domain) VALUES (?,?,?,?,?)", (url,title,description,body,domain))
    conn.commit()

def crawl(seeds, limit, delay, same_domain):
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)
    queue = deque((canonical(seed), 0) for seed in seeds if canonical(seed))
    seen = set()
    robots = {}
    seed_domains = {urlparse(seed).netloc for seed in seeds}
    indexed = 0
    while queue and indexed < limit:
        url, depth = queue.popleft()
        if not url or url in seen: continue
        seen.add(url)
        if same_domain and urlparse(url).netloc not in seed_domains: continue
        try:
            rp = robots_for(url, robots)
            if not rp.can_fetch(USER_AGENT, url):
                print("robots skip", url); continue
            html = fetch(url)
            if html is None: continue
            parser = PageParser(); parser.feed(html)
            save_page(conn, url, parser)
            indexed += 1
            print(f"[{indexed}/{limit}] {url}")
            if depth < 4:
                for href in parser.links:
                    child = canonical(urljoin(url, unescape(href)))
                    if child and child not in seen: queue.append((child, depth + 1))
            time.sleep(delay)
        except (HTTPError, URLError, TimeoutError, UnicodeError) as exc:
            print("skip", url, exc)
        except KeyboardInterrupt:
            break
    print(f"Venture indexed {indexed} pages; discovered {len(seen) + len(queue)} URLs.")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Crawl pages into Venture's SQLite FTS index")
    ap.add_argument("seeds", nargs="+", help="Seed URLs, e.g. https://wikipedia.org")
    ap.add_argument("--limit", type=int, default=250)
    ap.add_argument("--delay", type=float, default=1.0, help="Delay between requests")
    ap.add_argument("--same-domain", action="store_true", help="Only crawl seed domains")
    args = ap.parse_args()
    crawl(args.seeds, max(1,args.limit), max(.2,args.delay), args.same_domain)
