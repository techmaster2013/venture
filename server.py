#!/usr/bin/env python3
import json
import os
import sqlite3
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DB_PATH = "venture.db"
PORT = 826

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
        self.end_headers(); self.wfile.write(payload)

    def db(self):
        if not os.path.exists(DB_PATH): return None
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def search(self, parsed):
        q = parse_qs(parsed.query).get("q", [""])[0].strip()
        if not q: return self.send_json({"query":"", "results":[]})
        conn = self.db()
        if conn is None: return self.send_json({"query":q,"results":[],"indexed":0})
        try:
            # FTS5 query terms are quoted individually so ordinary punctuation
            # in user searches cannot become FTS syntax.
            terms = [term for term in q.split() if term]
            fts = " AND ".join('"' + term.replace('"','""') + '"' for term in terms)
            rows = conn.execute("""
              SELECT url,title,description,domain,bm25(pages_fts,0,8,4,1,2) AS score
              FROM pages_fts WHERE pages_fts MATCH ? ORDER BY score LIMIT 20
            """, (fts,)).fetchall() if fts else []
            total = conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
            return self.send_json({"query":q,"indexed":total,"results":[dict(r) for r in rows]})
        except sqlite3.Error as exc:
            return self.send_json({"error":str(exc),"results":[]},500)
        finally: conn.close()

    def stats(self):
        conn = self.db()
        if conn is None: return self.send_json({"indexed":0})
        try: count = conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
        finally: conn.close()
        self.send_json({"indexed":count})

if __name__ == "__main__":
    print(f"Venture at http://localhost:{PORT}")
    print("Search API: /api/search?q=your+query")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
