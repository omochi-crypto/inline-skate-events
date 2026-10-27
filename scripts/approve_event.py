#!/usr/bin/env python3
"""Approve only a manually reviewed GitHub Issue; never publish raw candidates."""
import datetime as dt
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

root = Path(__file__).resolve().parents[1]
issue = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert issue.get("state") == "OPEN", "Issue must be open"
body = issue.get("body") or ""
match = re.search(r"\{", body)
assert match, "Issue body must contain an event JSON object"
event, _ = json.JSONDecoder().raw_decode(body[match.start():])
assert isinstance(event, dict), "Event must be an object"
assert re.fullmatch(r"[a-f0-9]{16}", str(event.get("id", ""))), "Candidate ID is required"
assert isinstance(event.get("title"), str) and 5 <= len(event["title"].strip()) <= 180
assert isinstance(event.get("category"), str) and event["category"] in {"speed", "freestyle", "hockey", "other", "multi"}
for key in ("date_start", "date_end"):
    assert isinstance(event.get(key), str), f"Missing {key}"
start, end = (dt.date.fromisoformat(event[k]) for k in ("date_start", "date_end"))
assert start <= end, "End date before start date"
assert end >= dt.date.today(), "Event has already ended (UTC check)"
assert isinstance(event.get("prefecture"), str) and event["prefecture"].strip(), "Set prefecture (or 全国)"
url = event.get("official_url")
assert isinstance(url, str) and urlparse(url).scheme == "https" and bool(urlparse(url).netloc), "Set an official https URL"
event["status"] = "approved"
event["title"] = event["title"].strip()
allowed = {"id", "title", "date_start", "date_end", "category", "prefecture", "official_url", "status", "discovered_at", "source_url", "registration_deadline"}
event = {k: v for k, v in event.items() if k in allowed}
path = root / "events/approved.json"
data = json.loads(path.read_text(encoding="utf-8"))
assert isinstance(data, list)
for idx, old in enumerate(data):
    if old.get("id") == event["id"]:
        if old == event:
            print("Already approved; no change")
            sys.exit(0)
        raise ValueError("Existing approved event differs: manual review needed")
data.append(event)
data.sort(key=lambda e: e["date_start"])
path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("Approved:", event["id"], event["title"])
