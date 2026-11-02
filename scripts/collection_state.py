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
import math
import datetime as dt
from github_api import api
from source_policy import fingerprint, host_key

BRANCH = 'collection-state'


def validate_state(data):
    if not isinstance(data, dict) or data.get('version') != 1:
        raise ValueError('Unknown collection state version; no source access')
    for group in ('sources', 'hosts', 'sites'):
        values = data.get(group, {})
        if not isinstance(values, dict) or any(not isinstance(v, dict) for v in values.values()):
            raise ValueError('Invalid collection state structure; no source access')
        for state in values.values():
            for field in ('next_due', 'last_end', 'delay'):
                if field in state and (type(state[field]) not in (int,float)
                        or not math.isfinite(state[field]) or state[field] < 0):
                    raise ValueError('Invalid persisted time/interval; no source access')
    if not all(group in data for group in ('sources','hosts')):
        raise ValueError('Incomplete collection state; no source access')


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
            file = api('/contents/state.json?ref=' + self.parent)
            self.data = json.loads(base64.b64decode(file['content']))
        validate_state(self.data)

    def save(self, message):
        validate_state(self.data)
        content = json.dumps(self.data, ensure_ascii=False, sort_keys=True, allow_nan=False)
        if len(content.encode('utf-8')) > 750_000:
            raise ValueError('State size exceeds bounded GitHub storage; no further collection')
        tree = api('/git/trees', 'POST', {'tree': [
            {'path': 'state.json', 'mode': '100644', 'type': 'blob',
             'content': content}]})
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
        sites = self.data.setdefault('sites', {})
        for source in sources:
            state = self.data['sources'].setdefault(source['id'], {})
            new_source = not state
            expected = fingerprint(source)
            site = sites.setdefault(host_key(source['url']), {})
            old_config = state.get('config')
            legacy_keys = {'url','adapter','selector','category','prefecture'}
            if (isinstance(old_config, dict) and set(old_config) == legacy_keys
                    and all(old_config[k] == expected[k] for k in legacy_keys)):
                # Upgrade the previous policy fingerprint without permanently
                # stranding healthy sources. Do not guess when it last fetched.
                interval = max(source.get('min_interval_seconds',172800),
                               604800 if source.get('small_site') else 172800)
                state['config'] = expected
                state['next_due'] = max(state.get('next_due',0), now+interval)
                state['reason'] = 'Legacy policy upgraded; conservative quiet period'
                if source.get('small_site'):
                    site['next_due'] = max(site.get('next_due',0),state['next_due'])
            old_url = state.get('result', {}).get('url')
            if (state.get('config') is not None and state['config'] != expected) or (old_url and old_url != source['url']):
                state['paused'] = 'Source configuration changed; review cache and policy before resuming'
                continue
            state['config'] = expected
            # Lost/incomplete runs never silently resume a reserved source.
            if (state.get('pending') or state.get('paused') or state.get('next_due', 0) > now
                    or (source.get('small_site') and site.get('next_due', 0) > now)):
                continue
            if self.data['hosts'].get(host_key(source['url']), {}).get('paused'):
                continue
            # First use of small sites starts with a quiet week (including migration).
            if new_source and source.get('small_site'):
                state['next_due'] = now + 7 * 86400
                site['next_due'] = max(site.get('next_due', 0), state['next_due'])
                state['reason'] = 'Initial quiet week; prior fetch history unavailable'
                continue
            state['pending'] = os.getenv('GITHUB_RUN_ID', 'local')
            state['next_due'] = now + max(source.get('min_interval_seconds', 172800),
                                          604800 if source.get('small_site') else 0)
            if source.get('small_site'):
                site['next_due'] = state['next_due']
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
        if (state.get('config') is not None and state['config'] != fingerprint(source)) or (
                state.get('result', {}).get('url') not in (None, source['url'])):
            return [], {'id':source['id'], 'url':source['url'], 'ok':False,
                'extracted':0, 'incomplete':True, 'cached':False,
                'error':'Source configuration changed; cached data not reusable'}
        host = self.data['hosts'].get(host_key(source['url']), {})
        paused = bool(state.get('paused') or state.get('pending') or host.get('paused'))
        result = copy.deepcopy(state.get('result', {'extracted': 0, 'warnings': []}))
        result.update(id=source['id'], url=source['url'], cached=True,
                      next_due=state.get('next_due'), ok=not paused)
        result['incomplete'] = paused or 'events' not in state or bool(state.get('last_error'))
        if 'events' in state:
            try:
                observed = dt.datetime.fromisoformat(str(result.get('fetched_at')))
                if observed.tzinfo is None:
                    raise ValueError('Unzoned cache timestamp')
                age = time.time()-observed.timestamp()
                fresh = -300 <= age <= source.get('min_interval_seconds',
                    604800 if source.get('small_site') else 172800)
            except (ValueError,TypeError,OverflowError):
                fresh = False
            if not fresh:
                result['incomplete'] = True
                result['status_note'] = 'Cached observation age unknown/expired; reference only'
        if paused:
            result['error'] = state.get('paused') or host.get('paused') or 'Unfinished reservation: manual review required'
        elif state.get('last_error'):
            result['ok'] = False
            result['error'] = state['last_error']
        elif 'events' not in state:
            result['status_note'] = state.get('reason', 'Not due; no cached observation')
        return copy.deepcopy(state.get('events', [])), result
