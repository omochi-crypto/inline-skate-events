"""Classify observations without updating any approved event or old Issue."""
from difflib import SequenceMatcher
from event_model import parse_body, event_id, digest, publish_fields, normalize


def records(issues, approved):
    found = {}
    for issue in issues:
        try:
            event = parse_body(issue.get('body') or '')
            keys = {event['id'], event_id(event['title'], event['date_start'])}
        except (ValueError, KeyError, TypeError):
            continue
        for key in keys:
            found[key] = {'event': event, 'issue': issue['number'], 'state': issue['state']}
    for event in approved:
        for key in {event['id'], event_id(event['title'], event['date_start'])}:
            found[key] = {'event': event, 'issue': event.get('approval_issue'), 'state': 'approved'}
    return found


def classify(event, known):
    prior = known.get(event['id']) or known.get(event_id(event['title'], event['date_start']))
    if event.get('conflicts'):
        return 'conflict', {'id': event['id'], 'conflicts': event['conflicts'], 'event': event}
    if prior:
        old = prior['event']
        # Omitted/empty extracted fields are not a request to delete existing data.
        changes = {key: {'before': old.get(key), 'after': value}
                   for key, value in publish_fields(event).items()
                   if key != 'id' and value not in (None, '', []) and value != old.get(key)}
        if changes:
            return 'important_change', {'id': old['id'], 'issue': prior['issue'],
                'state': prior['state'], 'before_digest': digest(old),
                'changes': changes, 'event': event,
                'note': 'Observation only: verify official evidence and obtain new explicit approval'}
        return 'duplicate', None
    # Only propose a relation; never auto-merge a renamed/rescheduled edition.
    for item in known.values():
        old = item['event']
        same_day = old.get('date_start') == event.get('date_start')
        title_similarity = SequenceMatcher(None, normalize(old['title']), normalize(event['title'])).ratio()
        same_title = normalize(old['title']) == normalize(event['title'])
        if (same_day and title_similarity >= 0.75) or (same_title and old['date_start'][:4] == event['date_start'][:4]):
            return 'possible_duplicate', {'id': event['id'], 'related_id': old['id'],
                'issue': item['issue'], 'event': event, 'note': 'Alias/reschedule needs human review'}
    return 'new', None
