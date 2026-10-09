#!/usr/bin/env python3
"""VentureBot v2: a polite, persistent, single-machine web crawler.

Designed for an always-on Mac. Standard library only.

Features:
- persistent SQLite crawl frontier (crash-safe)
- SQLite FTS5 search index
- robots.txt + crawl-delay support
- sitemap discovery
- per-host rate limiting
- recrawling of stale pages
- duplicate-content suppression
- canonical URL support
- noindex/nofollow support
- bounded retries with exponential backoff
- crawl-trap filtering
- link graph storage for future ranking work
"""

import argparse
import gzip
import hashlib
import os
import re
import signal
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urldefrag, urljoin, urlparse, urlunparse
from urllib.request import Request, build_opener
from urllib.robotparser import RobotFileParser

VERSION = "0.2"
USER_AGENT = "VentureBot/0.2 (+https://github.com/techmaster2013/venture)"
DB_PATH = os.environ.get("VENTURE_DB", "venture.db")

DEFAULT_DELAY = 2.0
DEFAULT_RECRAWL_DAYS = 7
DEFAULT_MAX_DEPTH = 6
DEFAULT_PER_HOST_LIMIT = 5000
DEFAULT_QUEUE_LIMIT = 250000
MAX_HTML_BYTES = 2_500_000
MAX_SITEMAP_BYTES = 6_000_000
MAX_LINKS_PER_PAGE = 500
MAX_SITEMAP_URLS = 3000
MAX_URL_LENGTH = 2048
MAX_QUERY_PARAMS = 8
MAX_RETRIES = 4

TRACKING_PARAMS = {
    "fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid",
    "igshid", "ref_src", "ref_url", "spm",
}
SKIP_EXTENSIONS = {
    ".7z", ".avi", ".bin", ".bmp", ".bz2", ".css", ".dmg", ".doc", ".docx",
    ".exe", ".flac", ".gif", ".gz", ".ico", ".iso", ".jpeg", ".jpg", ".js",
    ".m4a", ".m4v", ".mkv", ".mov", ".mp3", ".mp4", ".mpeg", ".ogg", ".otf",
    ".pdf", ".png", ".ppt", ".pptx", ".rar", ".rss", ".svg", ".tar", ".tgz",
    ".tif", ".tiff", ".ttf", ".wav", ".webm", ".webp", ".woff", ".woff2",
    ".xls", ".xlsx", ".xml", ".zip",
}
STOP = False


def log(message):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), message, flush=True)


def handle_stop(signum, frame):
    global STOP
    STOP = True
    log("shutdown requested; finishing current page")


signal.signal(signal.SIGINT, handle_stop)
signal.signal(signal.SIGTERM, handle_stop)


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.description = ""
        self.links = []
        self.text = []
        self.canonical = None
        self.language = ""
        self.noindex = False
        self.nofollow = False
        self._in_title = False
        self._ignored = 0

    def handle_starttag(self, tag, attrs):
        attrs = {str(k).lower(): (v or "") for k, v in attrs}
        tag = tag.lower()

        if tag == "html" and attrs.get("lang"):
            self.language = attrs["lang"].strip()[:32]

        if tag == "title":
            self._in_title = True

        if tag in {"script", "style", "noscript", "svg", "template"}:
            self._ignored += 1

        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])

        if tag == "link":
            rel = {x.lower() for x in attrs.get("rel", "").split()}
            if "canonical" in rel and attrs.get("href"):
                self.canonical = attrs["href"]

        if tag == "meta":
            name = attrs.get("name", "").lower()
            if name == "description":
                self.description = attrs.get("content", "").strip()
            elif name in {"robots", "googlebot", "venturebot"}:
                directives = {
                    x.strip().lower()
                    for x in attrs.get("content", "").replace(";", ",").split(",")
                }
                self.noindex = "noindex" in directives or "none" in directives
                self.nofollow = "nofollow" in directives or "none" in directives

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        if tag in {"script", "style", "noscript", "svg", "template"} and self._ignored:
            self._ignored -= 1

    def handle_data(self, data):
        value = " ".join(data.split())
        if not value:
            return
        if self._in_title:
            self.title += (" " if self.title else "") + value
        if not self._ignored:
            self.text.append(value)


@dataclass
class RobotsInfo:
    parser: RobotFileParser
    delay: float
    sitemaps: list
    fetched_at: float


def db_connect(path=DB_PATH):
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


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

    CREATE TABLE IF NOT EXISTS frontier (
      url TEXT PRIMARY KEY,
      depth INTEGER NOT NULL DEFAULT 0,
      priority INTEGER NOT NULL DEFAULT 0,
      discovered_at INTEGER NOT NULL,
      next_fetch_at INTEGER NOT NULL,
      attempts INTEGER NOT NULL DEFAULT 0,
      state TEXT NOT NULL DEFAULT 'queued',
      source_url TEXT
    );

    CREATE INDEX IF NOT EXISTS frontier_ready
      ON frontier(state, next_fetch_at, priority DESC, depth, discovered_at);

    CREATE TABLE IF NOT EXISTS hosts (
      host TEXT PRIMARY KEY,
      last_fetch_at REAL NOT NULL DEFAULT 0,
      pages_fetched INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS links (
      source_url TEXT NOT NULL,
      target_url TEXT NOT NULL,
      PRIMARY KEY(source_url, target_url)
    );

    CREATE INDEX IF NOT EXISTS links_target ON links(target_url);

    CREATE TABLE IF NOT EXISTS sitemaps (
      url TEXT PRIMARY KEY,
      processed_at INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS crawl_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      at INTEGER NOT NULL,
      url TEXT,
      event TEXT NOT NULL,
      detail TEXT
    );
    """)
    conn.commit()


def event(conn, url, kind, detail=""):
    conn.execute(
        "INSERT INTO crawl_events(at,url,event,detail) VALUES(?,?,?,?)",
        (int(time.time()), url, kind, detail[:500]),
    )


def canonicalize(raw_url):
    if not raw_url:
        return None

    raw_url = unescape(raw_url.strip())
    raw_url, _ = urldefrag(raw_url)

    try:
        p = urlparse(raw_url)
    except ValueError:
        return None

    scheme = p.scheme.lower()
    if scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        return None

    host = p.hostname.lower().rstrip(".")
    try:
        port = p.port
    except ValueError:
        return None

    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        port = None

    netloc = host if port is None else f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", p.path or "/")

    lower_path = path.lower()
    if any(lower_path.endswith(ext) for ext in SKIP_EXTENSIONS):
        return None

    params = []
    for key, value in parse_qsl(p.query, keep_blank_values=True):
        lk = key.lower()
        if lk.startswith("utm_") or lk in TRACKING_PARAMS:
            continue
        params.append((key, value))

    if len(params) > MAX_QUERY_PARAMS:
        return None

    params.sort()
    query = urlencode(params, doseq=True)
    url = urlunparse((scheme, netloc, path, "", query, ""))

    if len(url) > MAX_URL_LENGTH:
        return None

    segments = [s for s in path.split("/") if s]
    if len(segments) > 24:
        return None

    counts = {}
    for segment in segments:
        counts[segment] = counts.get(segment, 0) + 1
        if counts[segment] > 5:
            return None

    return url


def hostname(url):
    return (urlparse(url).hostname or "").lower()


def queue_size(conn):
    return conn.execute(
        "SELECT COUNT(*) FROM frontier WHERE state IN ('queued','fetching')"
    ).fetchone()[0]


def host_page_count(conn, host):
    row = conn.execute("SELECT pages_fetched FROM hosts WHERE host=?", (host,)).fetchone()
    return row["pages_fetched"] if row else 0


def enqueue(conn, url, depth=0, priority=0, source_url=None,
            per_host_limit=DEFAULT_PER_HOST_LIMIT,
            queue_limit=DEFAULT_QUEUE_LIMIT):
    url = canonicalize(url)
    if not url:
        return False

    host = hostname(url)
    if not host or host_page_count(conn, host) >= per_host_limit:
        return False

    if queue_size(conn) >= queue_limit:
        return False

    now = int(time.time())
    cur = conn.execute(
        """INSERT OR IGNORE INTO frontier
           (url,depth,priority,discovered_at,next_fetch_at,attempts,state,source_url)
           VALUES(?,?,?,?,?,0,'queued',?)""",
        (url, depth, priority, now, now, source_url),
    )
    return cur.rowcount > 0


def seed_from_file(conn, path, per_host_limit, queue_limit):
    added = 0
    if not os.path.exists(path):
        return 0

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if enqueue(conn, line, 0, 100, None, per_host_limit, queue_limit):
                added += 1
    conn.commit()
    return added


def recover_frontier(conn):
    n = conn.execute(
        "UPDATE frontier SET state='queued' WHERE state='fetching'"
    ).rowcount
    if n:
        log(f"recovered {n} interrupted frontier item(s)")
    conn.commit()


def schedule_recrawls(conn, days):
    if days <= 0:
        return 0
    cutoff = int(time.time()) - days * 86400
    now = int(time.time())
    rows = conn.execute(
        "SELECT url FROM pages WHERE crawled_at <= ? LIMIT 5000", (cutoff,)
    ).fetchall()
    changed = 0
    for row in rows:
        cur = conn.execute(
            """UPDATE frontier
               SET state='queued', next_fetch_at=?, attempts=0, priority=20
               WHERE url=? AND state!='fetching'""",
            (now, row["url"]),
        )
        changed += cur.rowcount
    conn.commit()
    return changed


def next_item(conn):
    now = int(time.time())
    row = conn.execute(
        """SELECT url,depth,priority,attempts,source_url
           FROM frontier
           WHERE state='queued' AND next_fetch_at <= ?
           ORDER BY priority DESC, depth ASC, discovered_at ASC
           LIMIT 1""",
        (now,),
    ).fetchone()

    if row:
        conn.execute(
            "UPDATE frontier SET state='fetching', attempts=attempts+1 WHERE url=?",
            (row["url"],),
        )
        conn.commit()
    return row


def next_wakeup(conn):
    row = conn.execute(
        "SELECT MIN(next_fetch_at) AS t FROM frontier WHERE state='queued'"
    ).fetchone()
    return row["t"] if row and row["t"] else None


def fetch_bytes(url, max_bytes, timeout=15, accept="*/*"):
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": accept,
            "Accept-Encoding": "gzip",
        },
    )
    with build_opener().open(req, timeout=timeout) as response:
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                if int(content_length) > max_bytes:
                    raise ValueError("response too large")
            except ValueError:
                pass

        raw = response.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ValueError("response too large")

        if response.headers.get("Content-Encoding", "").lower() == "gzip":
            raw = gzip.decompress(raw)

        return raw, response.headers, response.geturl()


def fetch_html(url):
    raw, headers, final_url = fetch_bytes(
        url,
        MAX_HTML_BYTES,
        accept="text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
    )
    ctype = headers.get_content_type()
    if ctype not in {"text/html", "application/xhtml+xml"}:
        return None, headers, final_url

    charset = headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace"), headers, final_url


def load_robots(root, cache):
    now = time.time()
    cached = cache.get(root)
    if cached and now - cached.fetched_at < 3600:
        return cached

    robots_url = root + "/robots.txt"
    rp = RobotFileParser()
    rp.set_url(robots_url)
    sitemaps = []
    lines = []

    try:
        raw, _, _ = fetch_bytes(robots_url, 512_000, timeout=10, accept="text/plain,*/*;q=0.1")
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        for line in lines:
            if line.lower().startswith("sitemap:"):
                sm = line.split(":", 1)[1].strip()
                if sm:
                    sitemaps.append(sm)
    except HTTPError as exc:
        if exc.code != 404:
            log(f"robots HTTP {exc.code}: {root}")
    except Exception as exc:
        log(f"robots unavailable: {root} ({exc})")

    rp.parse(lines)
    delay = rp.crawl_delay(USER_AGENT) or rp.crawl_delay("*") or DEFAULT_DELAY
    try:
        delay = max(DEFAULT_DELAY, float(delay))
    except (TypeError, ValueError):
        delay = DEFAULT_DELAY

    info = RobotsInfo(rp, min(delay, 60.0), sitemaps, now)
    cache[root] = info
    return info


def respect_host_delay(conn, host, delay):
    row = conn.execute(
        "SELECT last_fetch_at FROM hosts WHERE host=?", (host,)
    ).fetchone()
    if not row:
        return
    remaining = delay - (time.time() - row["last_fetch_at"])
    if remaining > 0:
        time.sleep(remaining)


def mark_host_fetch(conn, host):
    now = time.time()
    conn.execute(
        """INSERT INTO hosts(host,last_fetch_at,pages_fetched)
           VALUES(?,?,1)
           ON CONFLICT(host) DO UPDATE SET
             last_fetch_at=excluded.last_fetch_at,
             pages_fetched=hosts.pages_fetched+1""",
        (host, now),
    )


def process_sitemap(conn, sitemap_url, per_host_limit, queue_limit):
    if conn.execute("SELECT 1 FROM sitemaps WHERE url=?", (sitemap_url,)).fetchone():
        return 0

    try:
        raw, _, _ = fetch_bytes(
            sitemap_url,
            MAX_SITEMAP_BYTES,
            timeout=20,
            accept="application/xml,text/xml,*/*;q=0.1",
        )
        if sitemap_url.lower().endswith(".gz"):
            raw = gzip.decompress(raw)

        root = ET.fromstring(raw)
        added = 0
        count = 0

        for elem in root.iter():
            if elem.tag.rsplit("}", 1)[-1].lower() != "loc" or not elem.text:
                continue
            loc = elem.text.strip()
            if loc.lower().endswith(".xml") or loc.lower().endswith(".xml.gz"):
                if count < 50:
                    process_sitemap(conn, loc, per_host_limit, queue_limit)
            else:
                if count >= MAX_SITEMAP_URLS:
                    break
                if enqueue(conn, loc, 0, 40, sitemap_url, per_host_limit, queue_limit):
                    added += 1
                count += 1

        conn.execute(
            "INSERT OR REPLACE INTO sitemaps(url,processed_at) VALUES(?,?)",
            (sitemap_url, int(time.time())),
        )
        conn.commit()
        if added:
            log(f"sitemap added {added} URL(s): {sitemap_url}")
        return added
    except Exception as exc:
        log(f"sitemap skip: {sitemap_url} ({exc})")
        conn.execute(
            "INSERT OR REPLACE INTO sitemaps(url,processed_at) VALUES(?,?)",
            (sitemap_url, int(time.time())),
        )
        conn.commit()
        return 0


def parse_x_robots(headers):
    raw = headers.get("X-Robots-Tag", "")
    directives = {
        x.strip().lower()
        for x in raw.replace(";", ",").split(",")
        if x.strip()
    }
    return (
        "noindex" in directives or "none" in directives,
        "nofollow" in directives or "none" in directives,
    )


def save_page(conn, requested_url, final_url, parser, headers):
    body = re.sub(r"\s+", " ", " ".join(parser.text)).strip()
    if len(body) < 80:
        return False, requested_url

    final_url = canonicalize(final_url) or requested_url
    canonical = canonicalize(urljoin(final_url, parser.canonical)) if parser.canonical else None
    storage_url = canonical or final_url

    title = parser.title.strip()[:300] or hostname(storage_url)
    description = re.sub(r"\s+", " ", parser.description).strip()[:600]
    if not description:
        description = body[:320]

    domain = hostname(storage_url)
    if domain.startswith("www."):
        domain = domain[4:]
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    now = int(time.time())

    header_noindex, _ = parse_x_robots(headers)
    noindex = parser.noindex or header_noindex

    conn.execute(
        """INSERT OR REPLACE INTO pages
           (url,title,description,body,domain,content_hash,crawled_at)
           VALUES(?,?,?,?,?,?,?)""",
        (storage_url, title, description, body, domain, digest, now),
    )

    conn.execute("DELETE FROM pages_fts WHERE url IN (?,?)", (requested_url, storage_url))

    duplicate = conn.execute(
        "SELECT url FROM pages WHERE content_hash=? AND url!=? LIMIT 1",
        (digest, storage_url),
    ).fetchone()

    if not noindex and not duplicate:
        conn.execute(
            """INSERT INTO pages_fts(url,title,description,body,domain)
               VALUES(?,?,?,?,?)""",
            (storage_url, title, description, body, domain),
        )

    return not noindex and not duplicate, storage_url


def fail_item(conn, url, attempts, detail):
    attempts_after = attempts + 1
    now = int(time.time())

    if attempts_after >= MAX_RETRIES:
        conn.execute(
            "UPDATE frontier SET state='failed', next_fetch_at=? WHERE url=?",
            (now, url),
        )
        event(conn, url, "failed", detail)
    else:
        backoff = min(3600, 30 * (2 ** max(0, attempts_after - 1)))
        conn.execute(
            "UPDATE frontier SET state='queued', next_fetch_at=? WHERE url=?",
            (now + backoff, url),
        )
        event(conn, url, "retry", detail)

    conn.commit()


def complete_item(conn, url):
    conn.execute(
        "UPDATE frontier SET state='done', next_fetch_at=? WHERE url=?",
        (int(time.time()), url),
    )


def crawl_one(conn, item, robots_cache, args):
    url = item["url"]
    depth = item["depth"]
    attempts = item["attempts"]
    host = hostname(url)
    parsed = urlparse(url)
    root = f"{parsed.scheme}://{parsed.netloc}"

    if host_page_count(conn, host) >= args.per_host_limit:
        complete_item(conn, url)
        conn.commit()
        return False

    robots = load_robots(root, robots_cache)

    if not robots.parser.can_fetch(USER_AGENT, url):
        log(f"robots skip {url}")
        complete_item(conn, url)
        event(conn, url, "robots_skip")
        conn.commit()
        return False

    for sitemap in robots.sitemaps[:10]:
        process_sitemap(conn, sitemap, args.per_host_limit, args.queue_limit)

    respect_host_delay(conn, host, max(args.delay, robots.delay))

    try:
        html, headers, final_url = fetch_html(url)
        mark_host_fetch(conn, host)

        if html is None:
            complete_item(conn, url)
            event(conn, url, "non_html")
            conn.commit()
            return False

        parser = PageParser()
        parser.feed(html)
        indexed, storage_url = save_page(conn, url, final_url, parser, headers)

        _, header_nofollow = parse_x_robots(headers)
        nofollow = parser.nofollow or header_nofollow

        unique_links = set()
        if not nofollow and depth < args.max_depth:
            for href in parser.links:
                child = canonicalize(urljoin(final_url, href))
                if not child or child == url:
                    continue
                unique_links.add(child)
                if len(unique_links) >= MAX_LINKS_PER_PAGE:
                    break

            for child in unique_links:
                conn.execute(
                    "INSERT OR IGNORE INTO links(source_url,target_url) VALUES(?,?)",
                    (storage_url, child),
                )
                enqueue(
                    conn,
                    child,
                    depth + 1,
                    max(0, 30 - depth * 3),
                    storage_url,
                    args.per_host_limit,
                    args.queue_limit,
                )

        complete_item(conn, url)
        event(conn, url, "indexed" if indexed else "crawled", f"links={len(unique_links)}")
        conn.commit()

        action = "indexed" if indexed else "crawled"
        log(f"{action}: {storage_url} (+{len(unique_links)} links)")
        return True

    except HTTPError as exc:
        if 400 <= exc.code < 500 and exc.code not in {408, 429}:
            complete_item(conn, url)
            event(conn, url, "http_skip", str(exc.code))
            conn.commit()
            log(f"HTTP {exc.code}: {url}")
        else:
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            fail_item(conn, url, attempts, f"HTTP {exc.code}")
            if retry_after and retry_after.isdigit():
                conn.execute(
                    "UPDATE frontier SET next_fetch_at=? WHERE url=?",
                    (int(time.time()) + min(int(retry_after), 86400), url),
                )
                conn.commit()
            log(f"retry HTTP {exc.code}: {url}")
        return False

    except (URLError, TimeoutError, UnicodeError, ValueError, OSError) as exc:
        fail_item(conn, url, attempts, str(exc))
        log(f"retry: {url} ({exc})")
        return False

    except Exception as exc:
        fail_item(conn, url, attempts, f"{type(exc).__name__}: {exc}")
        log(f"unexpected error: {url} ({type(exc).__name__}: {exc})")
        return False


def run_crawler(args, one_batch=False):
    conn = db_connect(args.db)
    init_db(conn)
    recover_frontier(conn)

    added = seed_from_file(conn, args.seeds_file, args.per_host_limit, args.queue_limit)
    if added:
        log(f"seeded {added} new URL(s)")

    if args.seed:
        for seed in args.seed:
            enqueue(conn, seed, 0, 100, None, args.per_host_limit, args.queue_limit)
        conn.commit()

    recrawled = schedule_recrawls(conn, args.recrawl_days)
    if recrawled:
        log(f"scheduled {recrawled} stale page(s) for recrawl")

    robots_cache = {}
    successful = 0
    processed = 0
    last_recrawl_check = time.time()

    log(
        f"VentureBot {VERSION} started | DB={args.db} | "
        f"delay>={args.delay:.1f}s | max-depth={args.max_depth}"
    )

    while not STOP:
        if args.max_pages and successful >= args.max_pages:
            break

        item = next_item(conn)
        if item:
            processed += 1
            if crawl_one(conn, item, robots_cache, args):
                successful += 1
            continue

        if one_batch:
            break

        if time.time() - last_recrawl_check >= 3600:
            schedule_recrawls(conn, args.recrawl_days)
            seed_from_file(conn, args.seeds_file, args.per_host_limit, args.queue_limit)
            last_recrawl_check = time.time()

        wake = next_wakeup(conn)
        sleep_for = max(1, min(30, wake - int(time.time()))) if wake else 15
        time.sleep(sleep_for)

    conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    conn.close()
    log(f"VentureBot stopped | processed={processed} successful={successful}")


def print_stats(path):
    conn = db_connect(path)
    init_db(conn)
    stats = {
        "indexed_pages": conn.execute("SELECT COUNT(*) FROM pages_fts").fetchone()[0],
        "stored_pages": conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0],
        "queued": conn.execute("SELECT COUNT(*) FROM frontier WHERE state='queued'").fetchone()[0],
        "fetching": conn.execute("SELECT COUNT(*) FROM frontier WHERE state='fetching'").fetchone()[0],
        "done": conn.execute("SELECT COUNT(*) FROM frontier WHERE state='done'").fetchone()[0],
        "failed": conn.execute("SELECT COUNT(*) FROM frontier WHERE state='failed'").fetchone()[0],
        "hosts": conn.execute("SELECT COUNT(*) FROM hosts").fetchone()[0],
        "links": conn.execute("SELECT COUNT(*) FROM links").fetchone()[0],
    }
    for key, value in stats.items():
        print(f"{key:14} {value:,}")
    conn.close()


def build_parser():
    ap = argparse.ArgumentParser(description="VentureBot persistent web crawler")
    sub = ap.add_subparsers(dest="command")

    def common(p):
        p.add_argument("--db", default=DB_PATH)
        p.add_argument("--seeds-file", default="seeds.txt")
        p.add_argument("--seed", action="append", default=[], help="add a seed URL")
        p.add_argument("--delay", type=float, default=DEFAULT_DELAY)
        p.add_argument("--recrawl-days", type=int, default=DEFAULT_RECRAWL_DAYS)
        p.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH)
        p.add_argument("--per-host-limit", type=int, default=DEFAULT_PER_HOST_LIMIT)
        p.add_argument("--queue-limit", type=int, default=DEFAULT_QUEUE_LIMIT)
        p.add_argument("--max-pages", type=int, default=0, help="0 = unlimited")

    run = sub.add_parser("run", help="run continuously")
    common(run)

    once = sub.add_parser("once", help="crawl until the current queue drains or max-pages is hit")
    common(once)

    seed = sub.add_parser("seed", help="add one or more URLs to the persistent frontier")
    seed.add_argument("urls", nargs="+")
    seed.add_argument("--db", default=DB_PATH)
    seed.add_argument("--per-host-limit", type=int, default=DEFAULT_PER_HOST_LIMIT)
    seed.add_argument("--queue-limit", type=int, default=DEFAULT_QUEUE_LIMIT)

    stats = sub.add_parser("stats", help="show crawler/index statistics")
    stats.add_argument("--db", default=DB_PATH)

    init = sub.add_parser("init", help="create/upgrade the database and load seeds.txt")
    init.add_argument("--db", default=DB_PATH)
    init.add_argument("--seeds-file", default="seeds.txt")
    init.add_argument("--per-host-limit", type=int, default=DEFAULT_PER_HOST_LIMIT)
    init.add_argument("--queue-limit", type=int, default=DEFAULT_QUEUE_LIMIT)

    return ap


def main():
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 2

    if args.command == "stats":
        print_stats(args.db)
        return 0

    if args.command == "seed":
        conn = db_connect(args.db)
        init_db(conn)
        added = 0
        for url in args.urls:
            if enqueue(conn, url, 0, 100, None, args.per_host_limit, args.queue_limit):
                added += 1
        conn.commit()
        conn.close()
        print(f"added {added} new seed URL(s)")
        return 0

    if args.command == "init":
        conn = db_connect(args.db)
        init_db(conn)
        recover_frontier(conn)
        added = seed_from_file(conn, args.seeds_file, args.per_host_limit, args.queue_limit)
        conn.close()
        print(f"database ready; added {added} seed URL(s)")
        return 0

    args.delay = max(0.5, args.delay)
    args.max_depth = max(0, args.max_depth)
    args.per_host_limit = max(1, args.per_host_limit)
    args.queue_limit = max(100, args.queue_limit)
    args.recrawl_days = max(0, args.recrawl_days)
    args.max_pages = max(0, args.max_pages)

    run_crawler(args, one_batch=(args.command == "once"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
