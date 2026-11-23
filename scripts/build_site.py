"""Build only approved, audited event data. No candidate data is copied."""
import json
import os
import shutil
from datetime import date
from html import escape
from pathlib import Path
from event_model import validate, digest
ROOT = Path(__file__).resolve().parents[1]

def has_practice_approval(practice, owner):
    if practice.get('approved_by') == owner and type(practice.get('approval_comment_id')) is int:
        return True

    chat = practice.get('chat_approval')
    if not isinstance(chat, dict) or chat.get('decision') != 'approved':
        return False
    if not all(isinstance(chat.get(key), str) and chat[key].strip()
               for key in ('message', 'reviewed_item', 'date')):
        return False
    try:
        approved_on = date.fromisoformat(chat['date'])
    except ValueError:
        return False
    return approved_on <= date.fromisoformat(practice['date_start'])


def load_approved_sessions(root, filename, owner, ids):
    sessions = json.loads((root / filename).read_text())
    if not isinstance(sessions, list):
        raise ValueError(f'{filename} must be an array')
    for session in sessions:
        validate(session, require_future=False)
        if (session.get('status') != 'approved'
                or not has_practice_approval(session, owner)
                or session.get('approval_digest') != digest(session)
                or session['id'] in ids):
            raise ValueError(f'Missing/invalid human approval audit or duplicate ID in {filename}')
        ids.add(session['id'])
    return sessions


def japanese_day(value):
    day = date.fromisoformat(value)
    weekday = '月火水木金土日'[day.weekday()]
    return f'{day.year}年{day.month}月{day.day}日（{weekday}）'


def community_card(event):
    when = japanese_day(event['date_start'])
    if event['date_end'] != event['date_start']:
        when += ' ～ ' + japanese_day(event['date_end'])
    if event.get('start_time'):
        when += ' ・ ' + event['start_time'] + '開始'
    lines = [
        f'    <article class="item" data-date-end="{escape(event["date_end"])}">',
        f'      <h2>{escape(event["title"])}</h2>',
        f'      <p class="meta">{escape(when)} ・ {escape(event["prefecture"])}</p>',
        f'      <p class="meta">{escape(event["venue"])}</p>',
    ]
    if event.get('cancelled'):
        lines.append('      <p class="badge">開催中止</p>')
    for key, label in (('organizer', '主催'), ('registration_note', '参加方法'),
                       ('participation_fee', '費用')):
        if event.get(key):
            lines.append(f'      <p class="meta">{label}：{escape(event[key])}</p>')
    if event.get('registration_deadline'):
        deadline = japanese_day(event['registration_deadline'])
        lines.append(f'      <p class="meta">申込締切：{deadline}</p>')
    if event.get('details'):
        lines.append('      <ul class="details">')
        lines.extend(f'        <li>{escape(detail)}</li>' for detail in event['details'])
        lines.append('      </ul>')
    if event.get('official_url') and event.get('source_type') != 'user_flyer':
        lines.append(f'      <a class="official" href="{escape(event["official_url"])}">主催者の募集案内を見る →</a>')
    lines.append('    </article>')
    return '\n'.join(lines)


def build(root=ROOT):
    data = json.loads((root/'events/approved.json').read_text())
    if not isinstance(data, list):
        raise ValueError('approved.json must be an array')
    ids = set()
    owner = os.getenv('GITHUB_REPOSITORY', 'omochi-crypto/inline-skate-events').split('/')[0]
    for event in data:
        validate(event, require_future=False)
        if (event.get('status') != 'approved' or event.get('approved_by') != owner
                or not isinstance(event.get('approval_comment_id'), int)
                or event.get('approval_digest') != digest(event) or event['id'] in ids):
            raise ValueError('Missing/invalid human approval audit or duplicate event')
        ids.add(event['id'])
    practices = load_approved_sessions(root, 'events/practices-approved.json', owner, ids)
    community = load_approved_sessions(root, 'site/community-approved.json', owner, ids)
    template = (root / 'site/community.html').read_text()
    cards = '\n'.join(community_card(event) for event in sorted(community, key=lambda e: e['date_start']))
    public = root/'public'
    public.mkdir(exist_ok=True)
    shutil.copy(root/'site/index.html', public/'index.html')
    shutil.copy(root/'site/practices.html', public/'practices.html')
    (public/'community.html').write_text(template.replace('<!-- COMMUNITY_EVENTS -->', cards))
    # Explicit whitelist: candidates, evidence and operational reports are never deployed.
    (public/'approved.json').write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')
    public_practices = [
        {key: value for key, value in practice.items() if key != 'chat_approval'}
        for practice in practices
    ]
    (public/'practices-approved.json').write_text(json.dumps(public_practices, ensure_ascii=False, indent=2)+'\n')
    (public/'.nojekyll').touch()
    print(f'Built approved-only site: {len(data)} events')

if __name__ == '__main__':
    build()
