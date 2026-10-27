"""Shared validation and content-bound human approval."""
import datetime as dt
import hashlib
import json
import re
import unicodedata
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

JST = ZoneInfo('Asia/Tokyo')
CATEGORIES = {'speed', 'marathon', 'freestyle', 'hockey', 'other', 'multi'}
FIELDS = ('id', 'title', 'date_start', 'date_end', 'category', 'prefecture',
          'official_url', 'source_url', 'venue', 'registration_deadline',
          'source_type', 'source_note', 'attachment_sha256', 'start_time', 'postponed_date',
          'registration_note', 'participation_fee', 'details', 'organizer')
PREFECTURES = ('北海道 青森県 岩手県 宮城県 秋田県 山形県 福島県 茨城県 栃木県 群馬県 '
               '埼玉県 千葉県 東京都 神奈川県 新潟県 富山県 石川県 福井県 山梨県 長野県 '
               '岐阜県 静岡県 愛知県 三重県 滋賀県 京都府 大阪府 兵庫県 奈良県 和歌山県 '
               '鳥取県 島根県 岡山県 広島県 山口県 徳島県 香川県 愛媛県 高知県 福岡県 '
               '佐賀県 長崎県 熊本県 大分県 宮崎県 鹿児島県 沖縄県').split()

def today_jst():
    return dt.datetime.now(JST).date()

def normalize(s):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', s)).casefold()

def event_id(title, date):
    return hashlib.sha256((date + '|' + normalize(title)).encode()).hexdigest()[:16]

def publish_fields(event):
    return {k: event[k] for k in FIELDS if k in event}

def digest(event):
    raw = json.dumps(publish_fields(event), ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

def parse_body(body):
    fenced = re.search(r'```json\s*(\{.*?\})\s*```', body, re.S)
    if fenced:
        return json.loads(fenced.group(1))
    start = body.find('{')  # Compatibility with original candidate Issues.
    if start < 0:
        raise ValueError('Issue本文に大会JSONがありません')
    return json.JSONDecoder().raw_decode(body[start:])[0]

def validate(event, today=None, require_future=True):
    def need(ok, msg):
        if not ok:
            raise ValueError(msg)
    need(isinstance(event, dict), '大会JSONはobjectが必要です')
    need(bool(re.fullmatch(r'[a-f0-9]{16}', str(event.get('id', '')))), 'IDが不正です')
    title = event.get('title')
    need(isinstance(title, str) and 5 <= len(title.strip()) <= 180, '大会名は5～180文字が必要です')
    need(event.get('category') in CATEGORIES, '種目が不正です')
    start, end = (dt.date.fromisoformat(event[k]) for k in ('date_start', 'date_end'))
    need(start <= end, '終了日が開始日より前です')
    if require_future:
        need(end >= (today or today_jst()), '開催済み大会は公開できません')
    need(event.get('prefecture') in PREFECTURES + ['全国'], '開催都道府県を確認してください')
    need(event.get('source_type') in (None, 'web', 'user_flyer'), '情報源の種類が不正です')
    if event.get('source_type') == 'user_flyer':
        note = event.get('source_note')
        need(isinstance(note, str) and 5 <= len(note.strip()) <= 200, '提供資料の説明を記録してください')
        need(bool(re.fullmatch(r'[a-f0-9]{64}', str(event.get('attachment_sha256', '')))),
             '提供チラシのSHA-256を記録してください')
    else:
        url = urlparse(str(event.get('official_url', '')))
        need(url.scheme in ('https', 'http') and bool(url.hostname) and not url.username and not url.password,
             '公式URLを確認してください')
    for key in ('registration_note', 'participation_fee', 'organizer'):
        if key in event:
            need(isinstance(event[key], str) and 0 < len(event[key].strip()) <= 300, f'{key}が不正です')
    if 'start_time' in event:
        need(bool(re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', str(event['start_time']))), '開始時刻が不正です')
    if event.get('postponed_date'):
        need(dt.date.fromisoformat(event['postponed_date']) >= end, '予備日が開催日より前です')
    if 'details' in event:
        need(isinstance(event['details'], list) and 1 <= len(event['details']) <= 10
             and all(isinstance(s, str) and 0 < len(s.strip()) <= 400 for s in event['details']),
             '大会詳細が不正です')
    if event.get('registration_deadline'):
        need(dt.date.fromisoformat(event['registration_deadline']) <= end, '申込締切が終了日より後です')
    return publish_fields(event)
