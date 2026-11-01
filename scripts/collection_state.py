"""Durable fetch reservations. Persist before contacting any source, fail closed.

Only the collection-state branch is changed; public approved data is never touched.
There is one reservation commit and one results commit per run with due sources.
"""
import base64
import copy
import json
import os
import time
import urllib.error
from urllib.parse import urlparse
from github_api import api

BRANCH = 'collection-state'


class Ledger:
    def __init__(self):
        self.parent = None
        try:
            ref = api('/git/ref/heads/' + BRANCH)
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            self.data = {'version': 1, 'sources': {}, 'hosts': {}}
        else:
            self.parent = ref['object']['sha']
            file = api('/contents/state.json?ref=' + BRANCH)
            self.data = json.loads(base64.b64decode(file['content']))
            if self.data.get('version') != 1:
                raise ValueError('Unknown collection state version; no source access')

    def save(self, message):
        tree = api('/git/trees', 'POST', {'tree': [
            {'path': 'state.json', 'mode': '100644', 'type': 'blob',
             'content': json.dumps(self.data, ensure_ascii=False, sort_keys=True)}]})
        commit = api('/git/commits', 'POST', {'message': message, 'tree': tree['sha'],
                     'parents': [self.parent] if self.parent else []})
        if self.parent:
            # Non-fast-forward rejection prevents overwriting a competing writer.
            api('/git/refs/heads/' + BRANCH, 'PATCH', {'sha': commit['sha'], 'force': False})
        else:
            api('/git/refs', 'POST', {'ref': 'refs/heads/' + BRANCH, 'sha': commit['sha']})
        self.parent = commit['sha']

    def reserve(self, sources, now=None):
        now = time.time() if now is None else now
        before = copy.deepcopy(self.data)
        due = set()
        for source in sources:
            state = self.data['sources'].setdefault(source['id'], {})
            # Lost/incomplete runs never silently resume a reserved source.
            if state.get('pending') or state.get('paused') or state.get('next_due', 0) > now:
                continue
            if self.data['hosts'].get(urlparse(source['url']).netloc, {}).get('paused'):
                continue
            # First use of small sites starts with a quiet week (including migration).
            if not state and source.get('small_site'):
                state['next_due'] = now + 7 * 86400
                state['reason'] = 'Initial quiet week; prior fetch history unavailable'
                continue
            state['pending'] = os.getenv('GITHUB_RUN_ID', 'local')
            state['next_due'] = now + max(source.get('min_interval_seconds', 172800),
                                          604800 if source.get('small_site') else 0)
            due.add(source['id'])
        # Save even a newly initialized quiet week. No requests if saving fails.
        if self.data != before:
            self.save('Reserve bounded source collection before network access')
        return due

    def finish(self, source, events, result):
        state = self.data['sources'][source['id']]
        state.pop('pending', None)
        if result.get('ok'):
            state.pop('last_error', None)
            state['events'] = copy.deepcopy(events)
            state['result'] = copy.deepcopy(result)
        else:
            state['last_error'] = result.get('error', 'collection failed')
            if result.get('manual_review'):
                state['paused'] = state['last_error']

    def cached(self, source):
        state = self.data['sources'][source['id']]
        from urllib.parse import urlparse
        host = self.data['hosts'].get(urlparse(source['url']).netloc, {})
        paused = bool(state.get('paused') or state.get('pending') or host.get('paused'))
        result = copy.deepcopy(state.get('result', {'extracted': 0, 'warnings': []}))
        result.update(id=source['id'], url=source['url'], cached=True,
                      next_due=state.get('next_due'), ok=not paused)
        result['incomplete'] = paused or 'events' not in state or bool(state.get('last_error'))
        if paused:
            result['error'] = state.get('paused') or host.get('paused') or 'Unfinished reservation: manual review required'
        elif state.get('last_error'):
            result['ok'] = False
            result['error'] = state['last_error']
        elif 'events' not in state:
            result['status_note'] = state.get('reason', 'Not due; no cached observation')
        return copy.deepcopy(state.get('events', [])), result
