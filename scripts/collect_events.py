#!/usr/bin/env python3
"""Collect candidate inline skating competitions; never publish automatically."""
import datetime as dt
import hashlib
import json
import os
import re
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
JST = ZoneInfo("Asia/Tokyo")
DATE = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
END_DAY = re.compile(r"(?:～|〜|~|－|-)\\s*(?:(\\d{1,2})\\s*月\\s*)?(\\d{1,2})\\s*日")
SPEED = re.compile(r"マラソン|スピード|ロードレース|トラックレース|スプリント|SPD", re.I)
INLINE = re.compile(r"インライン|ローラースケート|スピード|スラローム|スケートクロス|フリースタイル|ローラーフリースタイル|ホッケー|ダービー|ダウンヒル", re.I)
EXCLUDE = re.compile(r"スケートボード|スクーター|リンクホッケー")
class Parser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.lines = []
        self.href = None
        self.skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"): self.skip += 1
        if tag == "a": self.href = dict(attrs).get("href")
    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"): self.skip = max(0,self.skip-1)
        if tag == "a": self.href = None
    def handle_data(self, data):
        if self.skip: return
        value = " ".join(data.split())
        if value: self.lines.append((value, self.href))
def main():
    sources = json.loads((ROOT/"events/sources.json").read_text(encoding="utf-8"))["sources"]
    today = dt.datetime.now(JST).date()
    out = []
    errors = []
    for source in sources:
        try:
            req = urllib.request.Request(source["url"], headers={"User-Agent":"InlineEventCollector/0.1 (public event index; polite daily fetch)"})
            with urllib.request.urlopen(req, timeout=25) as res:
                html = res.read(2_000_000).decode("utf-8", "replace")
            p = Parser()
            p.feed(html)
            # Conservative extraction: date must be present on the same text fragment.
            # Uncertain candidates remain pending; no fabricated date or title.
            for idx, (line, href) in enumerate(p.lines):
                match = DATE.search(line)
                if not match: continue
                try: day = dt.date(*map(int, match.groups()))
                except ValueError: continue
                if day < today: continue
                context = " ".join(x[0] for x in p.lines[max(0,idx-2):min(len(p.lines),idx+3)])
                if not INLINE.search(context) or EXCLUDE.search(context): continue
                # The federation calendar contains artistic and rink-hockey events;
                # do not classify those as inline speed just because adjacent rows mention SPD.
                if source["id"] == "wsj-calendar":
                    if not SPEED.search(line) and not re.search(r"\\bSPD\\b", context): continue
                    if re.search(r"フィギュア|リンクホッケー|ART|RHC", line): continue
                title = re.sub(r"\s+", " ", context).strip()[:180]
                if len(title) < 8: continue
                end = day
                suffix = line[match.end():]
                range_match = END_DAY.search(suffix[:40])
                if range_match:
                    try:
                        end = dt.date(day.year, int(range_match.group(1) or day.month), int(range_match.group(2)))
                        if end < day: end = day
                    except ValueError: end = day
                key = hashlib.sha256((source["id"]+"|"+str(day)+"|"+title).encode()).hexdigest()[:16]
                out.append({"id":key,"title":title,"date_start":day.isoformat(),"date_end":end.isoformat(),"category":source["category"],"source_url":source["url"],"status":"pending","discovered_at":today.isoformat()})
        except Exception as exc:
            errors.append({"source":source["id"],"error":str(exc)[:200]})
    # Prefer speed/marathon review candidates in the daily issue quota.
    out.sort(key=lambda e: (0 if e["category"] == "speed" else 1, e["date_start"]))
    unique = {e["id"]:e for e in out}
    path = ROOT/"events/candidates.json"
    existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    for event in existing: unique.setdefault(event["id"],event)
    path.write_text(json.dumps(sorted(unique.values(),key=lambda x:x["date_start"]),ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"candidates":len(unique),"errors":errors},ensure_ascii=False))
if __name__ == "__main__": main()
