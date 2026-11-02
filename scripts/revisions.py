"""Prepare complete, content-bound revisions without implicit field deletion."""
import copy
from event_model import FIELDS, digest, publish_fields, validate

CONTROL = {'replaces_digest', 'removed_fields'}
REQUIRED = {'id', 'title', 'date_start', 'date_end', 'category', 'prefecture', 'venue'}


def check_replacement(event, approved):
    previous = next((old for old in approved if old['id'] == event['id']), None)
    if previous is None:
        if any(key in event for key in CONTROL):
            raise ValueError('更新対象の承認済み大会がありません')
        return None
    if event.get('replaces_digest') != digest(previous):
        raise ValueError('既に承認済みです。現在のdigestを指定した再承認が必要です')
    removed = event.get('removed_fields', [])
    if (not isinstance(removed, list) or any(not isinstance(k, str) for k in removed)
            or len(set(removed)) != len(removed) or not set(removed) <= set(FIELDS) - REQUIRED - CONTROL):
        raise ValueError('削除項目の指定が不正です')
    missing = set(publish_fields(previous)) - set(event) - CONTROL
    if missing != set(removed):
        raise ValueError('既存項目の暗黙削除を拒否しました。全項目を保持するかremoved_fieldsで明示してください')
    for key, old in publish_fields(previous).items():
        if key not in CONTROL and key in event and old not in (None, '', []) and event[key] in (None, '', []):
            raise ValueError('空欄による暗黙削除を拒否しました。削除はremoved_fieldsで明示してください')
    old_content = {k:v for k,v in publish_fields(previous).items() if k not in CONTROL}
    new_content = {k:v for k,v in publish_fields(event).items() if k not in CONTROL}
    if old_content == new_content:
        raise ValueError('公開内容に変更がありません')
    return previous


def prepare_revision(previous, changes, remove=()):
    """Use before showing the full proposal/digest to the owner, never after approval."""
    if set(changes) - (set(FIELDS) - CONTROL):
        raise ValueError('更新項目に未対応のキーがあります')
    if 'id' in changes and changes['id'] != previous['id']:
        raise ValueError('大会IDは更新で変更できません')
    event = copy.deepcopy(publish_fields(previous))
    for key in CONTROL:
        event.pop(key, None)
    for key in remove:
        if key in REQUIRED or key in CONTROL or key not in event:
            raise ValueError('削除できない項目です')
        event.pop(key)
    event.update(copy.deepcopy(changes))
    event['replaces_digest'] = digest(previous)
    if remove:
        event['removed_fields'] = list(remove)
    validate(event)
    check_replacement(event, [previous])
    return event
