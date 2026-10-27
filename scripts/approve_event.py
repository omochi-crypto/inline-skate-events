#!/usr/bin/env python3
"""Only an explicit owner comment bound to current data can authorize publication."""
import json
import os
import re
from pathlib import Path
from event_model import parse_body, digest, validate, today_jst
from github_api import api

ROOT = Path(__file__).resolve().parents[1]

def authorize(payload, issue):
    sender = payload.get('sender', {})
    owner = os.environ.get('GITHUB_REPOSITORY', '').split('/')[0]
    if sender.get('login') != owner or sender.get('type') != 'User':
        raise ValueError('承認にはリポジトリ所有者本人のコメントが必要です')
    if issue.get('state', '').lower() != 'open' or 'pull_request' in issue:
        raise ValueError('開いている大会候補Issueのみ承認できます')
    event = parse_body(issue.get('body') or '')
    command = payload.get('comment', {}).get('body', '').strip()
    if command != '/approve ' + digest(event):
        raise ValueError('承認コードが現在の大会内容と一致しません。/preview で再確認してください')
    return validate(event)

def add_approved(event, payload, path):
    data = json.loads(path.read_text())
    if any(e['id'] == event['id'] for e in data):
        raise ValueError('既に承認済みです。変更には別途レビューが必要です')
    event.update(status='approved', approved_by=payload['sender']['login'],
                 approval_comment_id=payload['comment']['id'],
                 approval_issue=payload['issue']['number'], approval_digest=digest(event),
                 approved_at=today_jst().isoformat())
    data.append(event)
    data.sort(key=lambda e: e['date_start'])
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n')

def main():
    payload = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    dry_run = os.getenv('DRY_RUN', 'false').lower() == 'true'
    number = int(os.getenv('ISSUE_NUMBER') or payload['issue']['number'])
    issue = api(f'/issues/{number}')
    event = parse_body(issue.get('body') or '')
    command = payload.get('comment', {}).get('body', '').strip()
    if dry_run or command == '/preview':
        token = digest(event)
        try:
            validate(event)
            status = '内容の形式検証OK（人間による公式情報との照合は別途必要）'
        except (ValueError, KeyError, TypeError) as exc:
            status = f'要修正: {exc}'
        message = f'{status}\n\n現在の内容の承認コード: `/approve {token}`\n\nこの確認処理では公開していません。'
        print(message)
        if not dry_run:
            api(f'/issues/{number}/comments', 'POST', {'body': message})
        if os.getenv('GITHUB_STEP_SUMMARY'):
            with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
                f.write(message)
        return
    try:
        event = authorize(payload, issue)
        add_approved(event, payload, ROOT/'events/approved.json')
    except (ValueError, KeyError, TypeError) as exc:
        api(f'/issues/{number}/comments', 'POST', {'body': f'公開しませんでした: {exc}'})
        raise
    if os.getenv('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
            f.write('changed=true\n')

if __name__ == '__main__':
    main()
