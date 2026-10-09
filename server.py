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

def clean_terms(query): return [t.lower() for t in re.findall(r"[\w'-]+",query,flags=re.UNICODE) if t]
def table_exists(conn,name): return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone())
def authority_scores(conn,urls):
    if not urls or not table_exists(conn,"links"): return {u:0.0 for u in urls}
    placeholders=",".join("?" for _ in urls)
    rows=conn.execute(f"SELECT l.target_url url,COUNT(DISTINCT l.source_url) incoming,COUNT(DISTINCT p.domain) source_domains FROM links l LEFT JOIN pages p ON p.url=l.source_url WHERE l.target_url IN ({placeholders}) GROUP BY l.target_url",urls).fetchall();out={u:0.0 for u in urls}
    for r in rows: out[r["url"]]=math.log1p(r["incoming"] or 0)+1.35*math.log1p(r["source_domains"] or 0)
    return out
def make_snippet(body,description,terms,limit=260):
    text=" ".join((body or "").split());desc=" ".join((description or "").split())
    if not text:return desc[:limit]
    lower=text.lower();hits=[lower.find(t) for t in terms if len(t)>1 and lower.find(t)>=0]
    if hits:
        start=max(0,min(hits)-90);end=min(len(text),start+limit);snippet=text[start:end].strip()
        return ("…" if start else "")+snippet+("…" if end<len(text) else "")
    value=desc or text;return value[:limit]+("…" if len(value)>limit else "")

class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed=urlparse(self.path)
        if parsed.path=="/api/search":return self.search(parsed)
        if parsed.path=="/api/stats":return self.stats()
        if parsed.path=="/api/crawl-status":return self.crawl_status()
        return super().do_GET()
    def send_json(self,data,status=200):
        payload=json.dumps(data,ensure_ascii=False).encode();self.send_response(status);self.send_header("Content-Type","application/json; charset=utf-8");self.send_header("Cache-Control","no-store");self.send_header("Content-Length",str(len(payload)));self.end_headers();self.wfile.write(payload)
    def db(self):
        if not os.path.exists(DB_PATH):return None
        conn=sqlite3.connect(DB_PATH);conn.row_factory=sqlite3.Row;return conn
    def counts(self,conn):
        return {"stored":conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0],"indexed":conn.execute("SELECT COUNT(*) FROM pages_fts").fetchone()[0],"queued":conn.execute("SELECT COUNT(*) FROM frontier WHERE state='queued'").fetchone()[0] if table_exists(conn,"frontier") else 0,"links":conn.execute("SELECT COUNT(*) FROM links").fetchone()[0] if table_exists(conn,"links") else 0,"domains":conn.execute("SELECT COUNT(DISTINCT domain) FROM pages").fetchone()[0]}
    def search(self,parsed):
        q=parse_qs(parsed.query).get("q",[""])[0].strip()
        if not q:return self.send_json({"query":"","results":[]})
        conn=self.db()
        if conn is None:return self.send_json({"query":q,"results":[],"indexed":0,"ranking":"venture-v2"})
        try:
            terms=clean_terms(q);fts=" AND ".join('"'+t.replace('"','""')+'"' for t in terms)
            if not fts:return self.send_json({"query":q,"results":[],"indexed":0,"ranking":"venture-v2"})
            rows=conn.execute("SELECT f.url,f.title,f.description,f.domain,p.body,bm25(pages_fts,0,8,4,1,2) bm25_score FROM pages_fts f LEFT JOIN pages p ON p.url=f.url WHERE pages_fts MATCH ? ORDER BY bm25_score LIMIT 80",(fts,)).fetchall();auth=authority_scores(conn,[r["url"] for r in rows]);ranked=[];qlower=q.lower()
            for r in rows:
                title=r["title"] or "";domain=r["domain"] or "";a=auth.get(r["url"],0.0);score=-float(r["bm25_score"] or 0)+(2.0 if qlower in title.lower() else 0)+(0.8 if any(t in domain.lower() for t in terms) else 0)+a*.65
                ranked.append({"url":r["url"],"title":title or r["url"],"description":make_snippet(r["body"],r["description"],terms),"domain":domain,"score":round(score,5),"authority":round(a,3)})
            ranked.sort(key=lambda x:x["score"],reverse=True);diverse=[];overflow=[];seen={}
            for item in ranked:
                d=item["domain"];n=seen.get(d,0)
                if n<3:diverse.append(item);seen[d]=n+1
                else:overflow.append(item)
            return self.send_json({"query":q,"indexed":self.counts(conn)["stored"],"ranking":"venture-v2","results":(diverse+overflow)[:20]})
        except sqlite3.Error as exc:return self.send_json({"error":str(exc),"results":[]},500)
        finally:conn.close()
    def stats(self):
        conn=self.db()
        if conn is None:return self.send_json({"indexed":0,"ranking":"venture-v2"})
        try:data=self.counts(conn);data["ranking"]="venture-v2"
        finally:conn.close()
        self.send_json(data)
    def crawl_status(self):
        conn=self.db()
        if conn is None:return self.send_json({"indexed":0,"domains":0,"links":0,"queued":0,"events":[],"recent":[],"ranking":"venture-v2"})
        try:
            data=self.counts(conn);data["ranking"]="venture-v2"
            data["events"]=[dict(r) for r in conn.execute("SELECT at,url,event,detail FROM crawl_events ORDER BY id DESC LIMIT 8").fetchall()] if table_exists(conn,"crawl_events") else []
            data["recent"]=[dict(r) for r in conn.execute("SELECT url,title,domain,crawled_at FROM pages ORDER BY crawled_at DESC LIMIT 8").fetchall()]
        except sqlite3.Error as exc:return self.send_json({"error":str(exc)},500)
        finally:conn.close()
        self.send_json(data)

if __name__=="__main__":
    print(f"Venture at http://localhost:{PORT_TEXT}");print("Dashboard: /dashboard.html");print("Ranking: Venture v2 (BM25 + link authority + domain diversity)");ThreadingHTTPServer(("127.0.0.1",PORT),Handler).serve_forever()
