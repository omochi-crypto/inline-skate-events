"""Create pending review Issues, preserve closed history, and record every count."""
import json
import os
import re
import hashlib
from pathlib import Path
from event_model import digest, today_jst
from github_api import api, all_pages
from review_changes import records, classify

ROOT = Path(__file__).resolve().parents[1]
MARKER = '<!-- inline-event:'

def candidate_body(event):
    token = digest(event)
    return (f'{MARKER}{event["id"]} -->\n'
            '**未承認・自動抽出情報。公式情報、開催日、都道府県を確認してください。**\n\n'
            '```json\n'+json.dumps(event, ensure_ascii=False, indent=2)+'\n```\n\n'
            '普段の承認はChatGPTのチャットで行えます。公式照合後に提示される大会について「公開する／しない」と答えるだけで、承認・公開操作を代行します。\n\n'
            '以下はGitHubで直接操作する場合の代替手段です。\n\n'
            f'`/approve {token}`\n\n'
            '内容を修正した場合、まず `/preview` とコメントすると、新しい承認コードを表示します。\n'
            'ラベル付け・Issueのクローズだけでは公開されません。botによる承認も受け付けません。\n'
            '都道府県が空欄の場合は、公式要項を確認してJSONに追記してから `/preview` を実行してください。')

def main():
    candidates = json.loads((ROOT/'events/candidates.json').read_text())
    report_path = ROOT/'reports/collection.json'
    report = json.loads(report_path.read_text())
    if report.get('safe_for_issue_write') is False:
        raise SystemExit('収集状態を確定できないためIssue操作を停止しました')
    issues = [i for i in all_pages('/issues?state=all') if 'pull_request' not in i]
    seen = set()
    for issue in issues:
        seen.update(re.findall(r'inline-event:([a-f0-9]{16})', issue.get('body') or ''))
        seen.update(re.findall(r'\[大会候補\]\s+([a-f0-9]{16})', issue.get('title', '')))
    counts = {'created': 0, 'existing_duplicates': 0, 'historical_skipped': 0, 'deferred': 0}
    approved = json.loads((ROOT/'events/approved.json').read_text())
    known = records(issues, approved)
    reviews = []
    for event in candidates:
        kind, detail = classify(event, known)
        if event['date_end'] < today_jst().isoformat():
            counts['historical_skipped'] += 1
        elif kind in ('important_change', 'possible_duplicate', 'conflict', 'unverified_observation'):
            reviews.append({'kind': kind, **detail})
            counts['existing_duplicates'] += int(kind == 'important_change')
        elif kind == 'duplicate':
            counts['existing_duplicates'] += 1
        elif event['id'] in seen:
            counts['existing_duplicates'] += 1
        elif counts['created'] >= 20:
            counts['deferred'] += 1
        else:
            api('/issues', 'POST', {'title': f'[大会候補] {event["id"]} {event["title"]}'[:240],
                                   'body': candidate_body(event)})
            seen.add(event['id'])
            known[event['id']] = {'event': event, 'issue': None, 'state': 'pending'}
            counts['created'] += 1
    report['issues'] = counts
    report['review_required'] = reviews
    (ROOT/'reports/review-required.json').write_text(json.dumps(reviews, ensure_ascii=False, indent=2)+'\n')
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    summary = ['## 大会情報収集結果', '',
        f'抽出 **{report["extracted"]}** / 一意 **{report["unique"]}** / 収集元間重複 **{report["duplicates"]}**',
        f'今後 **{report["upcoming"]}** / 開催済み **{report["historical"]}** / エラー **{report["errors"]}**',
        f'Issue新規 **{counts["created"]}** / 既存重複 **{counts["existing_duplicates"]}** / 次回繰越 **{counts["deferred"]}**',
        f'重要変更・重複疑い・情報衝突 **{len(reviews)}** / 未取得等 **{report.get("incomplete", 0)}**',
        '', '未承認の大会は公開していません。詳細JSONはArtifactsのcollection-reportから取得できます。', '',
        '|収集元|抽出件数|状態|日付等の要確認|', '|---|---:|---|---:|']
    for source in report['sources']:
        state = source.get('error') or ('未取得/保留' if source.get('incomplete') else '保存済み再利用' if source.get('cached') else 'OK')
        summary.append(f'|{source["id"]}|{source["extracted"]}|{state}|{len(source.get("warnings", []))}|')
    content = '\n'.join(summary)+'\n'
    (ROOT/'reports/summary.md').write_text(content)
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
            f.write(content)
    # Persistent operational health Issue; never erase prior runs or closed Issues.
    title = '[収集監視] 大会情報収集エラー'
    live = next((i for i in issues if i['title'] == title and i['state'] == 'open'), None)
    failures = [s for s in report['sources'] if not s['ok']]
    if failures:
        detail = json.dumps(failures, ensure_ascii=False, indent=2)
        fingerprint = hashlib.sha256(json.dumps(
            sorted((s['id'], s.get('error', 'ERROR')) for s in failures), ensure_ascii=False).encode()).hexdigest()[:16]
        marker = f'<!-- failure-digest:{fingerprint} -->'
        body = f'実行: {os.getenv("GITHUB_SERVER_URL", "https://github.com")}/{os.getenv("GITHUB_REPOSITORY")}/actions/runs/{os.getenv("GITHUB_RUN_ID")}\n\n```json\n{detail}\n```'
        if live:
            if marker not in (live.get('body') or ''):
                api(f'/issues/{live["number"]}/comments', 'POST', {'body': body})
                original = re.sub(r'<!-- failure-digest:[a-f0-9]+ -->', '', live.get('body') or '')
                api(f'/issues/{live["number"]}', 'PATCH', {'body': original.rstrip()+'\n\n'+marker})
        else:
            api('/issues', 'POST', {'title': title, 'body': body+'\n\n'+marker})
    elif live and not report.get('incomplete'):
        api(f'/issues/{live["number"]}/comments', 'POST', {'body': '全収集元の取得が正常に復旧しました。過去のエラー履歴は保持します。'})
        api(f'/issues/{live["number"]}', 'PATCH', {'state': 'closed'})
    if report['errors']:
        raise SystemExit('収集元エラーあり。レポートと監視Issueを確認してください')

if __name__ == '__main__':
    main()

