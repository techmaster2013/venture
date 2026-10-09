#!/usr/bin/env python3
import json
import math
import os
import re
import sqlite3
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DB_PATH = "venture.db"
PORT = 826
PORT_TEXT = "0826"


def clean_terms(query):
    return [t.lower() for t in re.findall(r"[\w'-]+", query, flags=re.UNICODE) if t]


def authority_scores(conn, urls):
    """Small-machine authority signal derived from VentureBot's link graph.

    This intentionally avoids an expensive full PageRank pass on every search.
    Incoming links from distinct crawled pages/domains give a logarithmic boost,
    while the text index remains the dominant relevance signal.
    """
    if not urls:
        return {}
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='links'").fetchone():
        return {u: 0.0 for u in urls}

    placeholders = ",".join("?" for _ in urls)
    rows = conn.execute(f"""
        SELECT l.target_url AS url,
               COUNT(DISTINCT l.source_url) AS incoming,
               COUNT(DISTINCT p.domain) AS source_domains
        FROM links l
        LEFT JOIN pages p ON p.url=l.source_url
        WHERE l.target_url IN ({placeholders})
        GROUP BY l.target_url
    """, urls).fetchall()
    out = {u: 0.0 for u in urls}
    for row in rows:
        incoming = row["incoming"] or 0
        domains = row["source_domains"] or 0
        out[row["url"]] = math.log1p(incoming) + 1.35 * math.log1p(domains)
    return out


def make_snippet(body, description, terms, limit=260):
    text = " ".join((body or "").split())
    desc = " ".join((description or "").split())
    if not text:
        return desc[:limit]

    lower = text.lower()
    hits = [lower.find(t) for t in terms if len(t) > 1 and lower.find(t) >= 0]
    if hits:
        start = max(0, min(hits) - 90)
        end = min(len(text), start + limit)
        snippet = text[start:end].strip()
        if start:
            snippet = "…" + snippet
        if end < len(text):
            snippet += "…"
        return snippet
    return (desc or text)[:limit] + ("…" if len(desc or text) > limit else "")


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/search": return self.search(parsed)
        if parsed.path == "/api/stats": return self.stats()
        return super().do_GET()

    def send_json(self, data, status=200):
        payload = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def db(self):
        if not os.path.exists(DB_PATH): return None
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def search(self, parsed):
        q = parse_qs(parsed.query).get("q", [""])[0].strip()
        if not q: return self.send_json({"query":"", "results":[]})
        conn = self.db()
        if conn is None: return self.send_json({"query":q,"results":[],"indexed":0,"ranking":"venture-v2"})
        try:
            terms = clean_terms(q)
            fts = " AND ".join('"' + term.replace('"','""') + '"' for term in terms)
            if not fts:
                return self.send_json({"query":q,"results":[],"indexed":0,"ranking":"venture-v2"})

            # Pull a wider text-relevant candidate set, then rerank it using
            # VentureBot's own link graph. Lower SQLite bm25() is better.
            rows = conn.execute("""
                SELECT f.url,f.title,f.description,f.domain,p.body,
                       bm25(pages_fts,0,8,4,1,2) AS bm25_score
                FROM pages_fts f
                LEFT JOIN pages p ON p.url=f.url
                WHERE pages_fts MATCH ?
                ORDER BY bm25_score
                LIMIT 80
            """, (fts,)).fetchall()

            urls = [r["url"] for r in rows]
            authority = authority_scores(conn, urls)
            ranked = []
            seen_domains = {}
            qlower = q.lower()
            for row in rows:
                bm = float(row["bm25_score"] or 0)
                text_relevance = -bm
                title = row["title"] or ""
                domain = row["domain"] or ""
                exact_title = 2.0 if qlower in title.lower() else 0.0
                domain_match = 0.8 if any(t in domain.lower() for t in terms) else 0.0
                auth = authority.get(row["url"], 0.0)
                score = text_relevance + exact_title + domain_match + auth * 0.65
                ranked.append({
                    "url": row["url"],
                    "title": title or row["url"],
                    "description": make_snippet(row["body"], row["description"], terms),
                    "domain": domain,
                    "score": round(score, 5),
                    "authority": round(auth, 3),
                })

            ranked.sort(key=lambda x: x["score"], reverse=True)

            # Prevent one giant site from swallowing the first page.
            diverse = []
            overflow = []
            for item in ranked:
                d = item["domain"]
                n = seen_domains.get(d, 0)
                if n < 3:
                    diverse.append(item)
                    seen_domains[d] = n + 1
                else:
                    overflow.append(item)
            results = (diverse + overflow)[:20]
            total = conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
            return self.send_json({
                "query": q,
                "indexed": total,
                "ranking": "venture-v2",
                "results": results,
            })
        except sqlite3.Error as exc:
            return self.send_json({"error":str(exc),"results":[]},500)
        finally:
            conn.close()

    def stats(self):
        conn = self.db()
        if conn is None: return self.send_json({"indexed":0,"ranking":"venture-v2"})
        try:
            count = conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
            indexed = conn.execute("SELECT COUNT(*) FROM pages_fts").fetchone()[0]
            queued = conn.execute("SELECT COUNT(*) FROM frontier WHERE state='queued'").fetchone()[0] if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='frontier'").fetchone() else 0
            links = conn.execute("SELECT COUNT(*) FROM links").fetchone()[0] if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='links'").fetchone() else 0
            domains = conn.execute("SELECT COUNT(DISTINCT domain) FROM pages").fetchone()[0]
        finally:
            conn.close()
        self.send_json({"stored":count,"indexed":indexed,"queued":queued,"links":links,"domains":domains,"ranking":"venture-v2"})


if __name__ == "__main__":
    print(f"Venture at http://localhost:{PORT_TEXT}")
    print("Ranking: Venture v2 (BM25 + link authority + domain diversity)")
    print("Search API: /api/search?q=your+query")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
