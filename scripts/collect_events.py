#!/usr/bin/env python3
"""Bounded, auditable collection. Candidates never enter approved.json."""
import concurrent.futures
import datetime as dt
import json
import io
import re
import time
import unicodedata
import urllib.request
from collections import Counter
from pathlib import Path
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from pypdf import PdfReader
from event_model import event_id, today_jst, PREFECTURES

ROOT = Path(__file__).resolve().parents[1]
DATE = re.compile(r'(?:(20\d{2})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*日')
RANGE = re.compile(r'^\s*(?:[（(][^）)]*[）)])?\s*[～〜~－-]\s*(?:(20\d{2})\s*年\s*)?(?:(\d{1,2})\s*月\s*)?(\d{1,2})\s*日')
EXCLUDE = re.compile(r'スケートボード|スクーター|スクータリング|リンクホッケー|フィギュア|アイス|氷上')
COMPETITION = re.compile(r'大会|選手権|記録会|カップ|オープン|マラソン|ウルトラスケート|Championship', re.I)


def text(node):
    return ' '.join(unicodedata.normalize('NFKC', node.get_text(' ', strip=True)).split()) if node else ''


def dates(raw, year=None):
    raw = unicodedata.normalize('NFKC', raw)
    m = DATE.search(raw)
    if not m or not (m[1] or year):
        return None
    try:
        start = dt.date(int(m[1] or year), int(m[2]), int(m[3]))
        end = start
        tail = raw[m.end():]
        r = RANGE.search(tail)
        if r:
            ey = int(r[1] or start.year)
            em = int(r[2] or start.month)
            if not r[1] and em < start.month:
                ey += 1
            end = dt.date(ey, em, int(r[3]))
        elif re.match(r'\s*[・、]', tail):
            return None  # Discontinuous dates require human extraction, no invented range.
        if end < start:
            return None
        return start, end
    except ValueError:
        return None


def make_event(title, period, source, url=None, venue='', evidence=''):
    if not period or EXCLUDE.search(title) or not COMPETITION.search(title):
        return None
    start, end = period
    category = source.get('category', 'other')
    if re.search(r'マラソン|ウルトラスケート|Marathon', title, re.I):
        category = 'marathon'
    location = venue + ' ' + evidence
    prefecture = next((p for p in PREFECTURES if p in location), source.get('prefecture', ''))
    return {'id': event_id(title, start.isoformat()), 'title': title[:180],
            'date_start': start.isoformat(), 'date_end': end.isoformat(),
            'category': category, 'prefecture': prefecture, 'venue': venue,
            'official_url': url or source['url'], 'source_url': source['url'],
            'status': 'pending', 'discovered_at': today_jst().isoformat(),
            'evidence': evidence[:1000], 'source_ids': [source['id']]}


def parse_calendar(soup, source, warnings):
    events = []
    year = None
    domestic = True
    rows = soup.select('table tr')
    if not rows:
        raise ValueError('年間予定表のtable/trが見つかりません（構造変更の可能性）')
    for row in rows:
        cells = [text(c) for c in row.find_all(['td', 'th'], recursive=False)]
        if any('国際大会' in c for c in cells):
            domestic = False
        if not domestic or len(cells) < 5:
            continue
        date, title, venue, _, code = cells[:5]
        explicit = re.search(r'(20\d{2})年', date)
        if explicit:
            year = int(explicit[1])
        # Never borrow the sport code from an adjacent row.
        category = {'SPD': 'speed', 'IHC': 'hockey', 'IFS': 'freestyle', 'RFS': 'freestyle',
                    'RDB': 'other'}.get(code)
        if not category or EXCLUDE.search(title):
            continue
        period = dates(date, year)
        if not period:
            warnings.append({'title': title, 'date_text': date, 'reason': '日付未確定/非連続/不正。人による確認が必要'})
            continue
        event = make_event(title, period, {**source, 'category': category}, venue=venue, evidence=' | '.join(cells))
        if event:
            events.append(event)
    return events


def parse_cards(soup, source, warnings):
    cards = soup.select('a.detail_page_link')
    if not cards:
        raise ValueError('大会カードが見つかりません（構造変更の可能性）')
    events = []
    for card in cards:
        title = text(card.select_one('.compe_title'))
        period = dates(text(card.select_one('.compe_date_span')))
        if not period:
            warnings.append({'title': title, 'reason': '大会カードの日付を解析できません'})
        event = make_event(title, period, source, urljoin(source['url'], card.get('href', '')),
                           text(card.select_one('.compe_stage')), text(card))
        if event:
            events.append(event)
    return events


def parse_gia(soup, source, warnings):
    titles = soup.select('.entry_title')
    if not titles:
        raise ValueError('GIA記事が見つかりません')
    events = []
    for heading in titles:
        title = text(heading)
        body = heading.find_next(class_='entry_body')
        posted = text(heading.find_next(class_='entry_date'))
        if not body or re.search(r'リザルト|名簿|結果', title):
            continue
        raw = text(body)
        if not COMPETITION.search(raw):
            continue
        # A post year provides context, but publication date is never an event date.
        y = re.search(r'20\d{2}', posted)
        year = int(y[0]) if y else None
        candidates = []
        paragraphs = body.find_all('p')
        fragments = [text(p) for p in paragraphs] if paragraphs else re.split(r'[。!！\n]', raw)
        for fragment in fragments:
            if re.search(r'締切|エントリー|申し込み|申込', fragment):
                continue
            if not re.search(r'開催|会いしましょう|会場|日程', fragment):
                continue
            for m in DATE.finditer(fragment):
                p = dates(fragment[m.start():], year)
                if p:
                    candidates.append(p)
        for period in set(candidates):
            link = body.find('a', href=re.compile(r'inlineskate\.or\.jp/.*(?:ultra|htm)'))
            url = link.get('href') if link else source['url']
            name = 'ウルトラスケート長良川' + str(period[0].year) if 'ウルトラスケート' in raw else title
            article_url = body.select_one('[data-url]')
            actual_source = {**source, 'url': article_url['data-url']} if article_url else source
            event = make_event(name, period, actual_source, url, '海津市長良川リバーサイドプラザ' if 'リバーサイドプラザ' in raw else '', raw)
            if event:
                event['date_note'] = '記事の投稿年を開催年の文脈として使用。承認前に公式要項を照合してください。'
                events.append(event)
    return events


def parse_articles(soup, source, warnings):
    # Each announcement remains isolated; no neighbouring article or page-wide date guessing.
    nodes = soup.select(source.get('selector', 'article'))
    if not nodes:
        raise ValueError('記事セレクターに一致なし（構造変更の可能性）')
    events = []
    for node in nodes[:30]:
        heading = next((h for h in node.select('h1, h2, h3, .title, .article-title') if COMPETITION.search(text(h))), None)
        title = text(heading)
        if not COMPETITION.search(title) or EXCLUDE.search(title):
            continue
        body = node.select_one('.article-body, .post-body, .entry-content') or node
        raw = text(body)
        # Require a labelled event date, not article publication, update or entry deadline.
        m = re.search(r'(?:開催日(?:時)?|開催期日|期日|日程|日時)\s*[:：]?\s*((?:20\d{2})\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日[^。\n]{0,35})', raw)
        if not m:
            warnings.append({'title': title, 'reason': '本文に年付き開催日がない（PDF・画像を確認）'})
            continue
        link = heading.find('a') if heading else None
        event = make_event(title, dates(m[1]), source,
                           urljoin(source['url'], link['href']) if link else None, evidence=m[0])
        if event:
            events.append(event)
    return events


def parse_pdf_page(soup, source, warnings):
    link = soup.find('a', href=lambda h: h and h.endswith('kirokukai.pdf'))
    if not link:
        raise ValueError('記録会要項PDFへのリンクなし')
    url = urljoin(source['url'], link['href'])
    with urllib.request.urlopen(url, timeout=25) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError('PDF上限2MBを超えました')
    reader = PdfReader(io.BytesIO(raw))
    if len(reader.pages) > 10:
        raise ValueError('PDF上限10ページを超えました')
    content = unicodedata.normalize('NFKC', ' '.join(p.extract_text() or '' for p in reader.pages))
    match = re.search(r'(?:開催日(?:時)?|期日|日時)\s*[:：]?\s*((?:20\d{2})\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日[^。]{0,30})', content)
    if not match:
        warnings.append({'url': url, 'reason': 'PDFに解析可能な年付き開催日なし。画像PDFは人による確認が必要'})
        return []
    title_match = re.search(r'第\s*\d+\s*回[^\n]{0,50}(?:スピード記録会|記録会)', content)
    title = ' '.join(title_match[0].split()) if title_match else '伊丹スピード記録会'
    event = make_event(title, dates(match[1]), source, url, '伊丹市立ローラースケート場', match[0])
    return [event] if event else []


def fetch_source(source):
    started = time.monotonic()
    for attempt in range(2):
        try:
            req = urllib.request.Request(source['url'], headers={'User-Agent': 'InlineEvents/1.0 (+https://github.com/omochi-crypto/inline-skate-events)'})
            with urllib.request.urlopen(req, timeout=25) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ValueError('ページ上限2MBを超えました')
                charset = response.headers.get_content_charset()
                soup = BeautifulSoup(raw, 'html.parser', from_encoding=charset)
            for tag in soup(['script', 'style', 'noscript']):
                tag.decompose()
            warnings = []
            adapter = {'calendar': parse_calendar, 'cards': parse_cards, 'gia': parse_gia, 'articles': parse_articles, 'pdf_page': parse_pdf_page}[source['adapter']]
            events = adapter(soup, source, warnings)
            return events, {'id': source['id'], 'url': source['url'], 'ok': True,
                            'extracted': len(events), 'warnings': warnings,
                            'seconds': round(time.monotonic()-started, 2)}
        except Exception as exc:
            if attempt:
                return [], {'id': source['id'], 'url': source['url'], 'ok': False,
                            'extracted': 0, 'error': str(exc)[:300]}
            time.sleep(1)


def collect(sources, today=None):
    today = today or today_jst()
    all_events, results = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        for events, result in pool.map(fetch_source, sources):
            all_events.extend(events)
            results.append(result)
    # Same normalized title/date across sources deduplicates; retain all provenance.
    unique = {}
    for event in all_events:
        if event['id'] in unique:
            old = unique[event['id']]
            old['source_ids'] = sorted(set(old['source_ids'] + event['source_ids']))
        else:
            unique[event['id']] = event
    events = sorted(unique.values(), key=lambda e: ({'speed': 0, 'marathon': 1}.get(e['category'], 3), e['date_start']))
    report = {'collected_at': dt.datetime.now().astimezone().isoformat(), 'today_jst': today.isoformat(),
              'extracted': len(all_events), 'unique': len(events), 'duplicates': len(all_events)-len(events),
              'upcoming': sum(e['date_end'] >= today.isoformat() for e in events),
              'historical': sum(e['date_end'] < today.isoformat() for e in events),
              'by_category': dict(Counter(e['category'] for e in events)),
              'errors': sum(not r['ok'] for r in results), 'sources': results}
    return events, report


def main():
    sources = json.loads((ROOT/'events/sources.json').read_text())['sources']
    events, report = collect(sources)
    (ROOT/'events/candidates.json').write_text(json.dumps(events, ensure_ascii=False, indent=2)+'\n')
    (ROOT/'reports').mkdir(exist_ok=True)
    (ROOT/'reports/collection.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(report, ensure_ascii=False))

if __name__ == '__main__':
    main()
