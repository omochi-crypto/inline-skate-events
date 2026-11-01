import copy
import datetime as dt
from email.message import Message
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from polite_http import Client, robot_rules
from collection_state import Ledger
from event_model import event_id, digest
from review_changes import records, classify
from approve_event import add_approved, authorize
from review_issues import candidate_body
import collect_events as ce

SOURCE = {'id': 'small', 'url': 'https://example.org/', 'small_site': True}
EVENT = {'id': event_id('架空スピード記録会', '2099-03-07'), 'title': '架空スピード記録会',
         'date_start': '2099-03-07', 'date_end': '2099-03-07', 'category': 'speed',
         'prefecture': '東京都', 'venue': '架空会場A', 'official_url': 'https://example.org/race'}


def ledger():
    value = Ledger.__new__(Ledger)
    value.data = {'version': 1, 'sources': {}, 'hosts': {}}
    value.save = Mock()
    return value


class Response:
    status = 200
    def __init__(self, body):
        self.body = body
        self.headers = Message(); self.headers['Content-Type'] = 'text/plain; charset=utf-8'
    def read(self, count): return self.body[:count]
    def __enter__(self): return self
    def __exit__(self, *args): pass


class HttpTests(unittest.TestCase):
    def client(self, opener):
        now = [1000.0]
        def sleep(n): now[0] += n
        return Client({}, opener=opener, clock=lambda: now[0], sleeper=sleep)

    def test_restrictions_pause_host_without_retry(self):
        for status in (401, 403, 429, 503):
            headers = Message(); headers['Retry-After'] = '120'
            opener = Mock(side_effect=[Response(b'User-agent: *\nDisallow: /private'),
                                      HTTPError(SOURCE['url'], status, 'test', headers, None)])
            client = self.client(opener); client.begin(SOURCE)
            with self.assertRaises(HTTPError): client.get(SOURCE['url'])
            client.begin(SOURCE)
            with self.assertRaises(ValueError): client.get(SOURCE['url'])
            self.assertEqual(opener.call_count, 2) # robots once, body once
            self.assertEqual(client.hosts['example.org']['retry_after'], '120')

    def test_spacing_includes_robots_and_body(self):
        opener = Mock(side_effect=[Response(b'User-agent: *\nCrawl-delay: 15'), Response(b'body')])
        client = self.client(opener); client.begin(SOURCE)
        client.get(SOURCE['url'])
        self.assertEqual(client.clock(), 1015)

    def test_robots_wildcard_and_unavailable(self):
        with self.assertRaises(ValueError):
            robot_rules('User-agent: *\nDisallow: /*secret*', 'https://example.org/a-secret')
        self.assertEqual(robot_rules('User-agent: Other\nDisallow: /', SOURCE['url']), 10)
        with self.assertRaises(ValueError):
            robot_rules('User-agent: *\nDisallow: /secret', 'https://example.org/%73ecret')
        with self.assertRaises(ValueError):
            robot_rules('Temporarily unavailable', SOURCE['url'])
        with self.assertRaises(ValueError):
            robot_rules('User-agent: *\nCrawl-delay: NaN', SOURCE['url'])
        opener = Mock(return_value=Response(b'<html>not robots</html>'))
        client = self.client(opener); client.begin(SOURCE)
        with self.assertRaises(ValueError): client.get(SOURCE['url'])
        self.assertEqual(opener.call_count, 1)
        self.assertIn('paused', client.hosts['example.org'])

    def test_budget_redirect_and_cross_origin(self):
        opener = Mock(return_value=Response(b'User-agent: *'))
        client = self.client(opener); client.begin(SOURCE)
        client.get(SOURCE['url']); client.get(SOURCE['url'])
        with self.assertRaises(ValueError): client.get(SOURCE['url'])
        with self.assertRaises(ValueError): client.get('https://another.org/file.pdf')
        self.assertEqual(opener.call_count, 3)
        redirected = Mock(side_effect=HTTPError(SOURCE['url'], 301, 'redirect', Message(), None))
        client = self.client(redirected); client.begin(SOURCE)
        with self.assertRaises(HTTPError): client.get(SOURCE['url'])
        self.assertEqual(redirected.call_count, 1)


class StateTests(unittest.TestCase):
    def test_weekly_quiet_period_then_reservation_and_pending_stop(self):
        value = ledger()
        self.assertEqual(value.reserve([SOURCE], 1000), set())
        self.assertEqual(value.reserve([SOURCE], 1000+604799), set())
        self.assertEqual(value.reserve([SOURCE], 1000+604800), {'small'})
        self.assertEqual(value.reserve([SOURCE], 1000+2*604800), set())

    def test_failed_state_save_aborts_before_network(self):
        value = ledger(); value.save.side_effect = OSError('save failed')
        official = {**SOURCE, 'small_site': False}
        with self.assertRaises(OSError): value.reserve([official], 1000)

    def test_cached_observations_and_host_pause(self):
        value = ledger(); value.reserve([SOURCE], 1000)
        result = {'ok': True, 'extracted': 1, 'fetched_at': 'fixture'}
        value.finish(SOURCE, [EVENT], result)
        events, status = value.cached(SOURCE)
        self.assertEqual(events, [EVENT]); self.assertTrue(status['cached'])
        value.data['hosts']['example.org'] = {'paused': 'HTTP 429'}
        self.assertFalse(value.cached(SOURCE)[1]['ok'])
        self.assertEqual(value.reserve([SOURCE], 1000+604800), set())

    def test_authorization_failure_is_not_empty_initial_state(self):
        with patch('collection_state.api', side_effect=HTTPError('fixture',403,'forbidden',Message(),None)):
            with self.assertRaises(HTTPError): Ledger()

    def test_no_unchanged_state_commits_and_failed_cached_observation(self):
        value = ledger(); value.reserve([SOURCE], 1000)
        value.save.reset_mock()
        self.assertEqual(value.reserve([SOURCE], 1001), set())
        value.save.assert_not_called()
        value.finish(SOURCE, [], {'ok':False,'error':'timeout'})
        self.assertFalse(value.cached(SOURCE)[1]['ok'])
        self.assertTrue(value.cached(SOURCE)[1]['incomplete'])


class ReviewTests(unittest.TestCase):
    def test_legacy_json_and_closed_history(self):
        issue = {'number': 1, 'state': 'closed', 'body': json.dumps(EVENT)}
        known = records([issue], [])
        self.assertEqual(classify(EVENT, known)[0], 'duplicate')

    def test_change_is_report_only_and_omission_is_not_deletion(self):
        known = records([], [EVENT])
        changed = {**EVENT, 'venue': '架空会場B'}
        kind, detail = classify(changed, known)
        self.assertEqual(kind, 'important_change')
        self.assertEqual(detail['changes']['venue']['before'], '架空会場A')
        self.assertEqual(classify({**EVENT, 'venue': ''}, known)[0], 'duplicate')
        self.assertEqual(EVENT['venue'], '架空会場A')

    def test_conflicting_sources_preserve_both_evidence(self):
        first = {**EVENT, 'source_ids': ['a']}
        second = {**EVENT, 'venue': '架空会場B', 'source_ids': ['b']}
        with patch('collect_events.fetch_source', side_effect=[([first], {'ok':True}),([second], {'ok':True})]):
            events, report = ce.collect([{}, {}], dt.date(2026,11,2))
        self.assertEqual(len(events[0]['observations']), 2)
        self.assertIn('venue', events[0]['conflicts'])
        self.assertEqual(classify(events[0], {})[0], 'conflict')

    def test_explicit_revision_archives_previous_and_rejects_stale_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'approved.json'
            previous = {**EVENT, 'status':'approved', 'approved_by':'omochi-crypto',
                        'approval_comment_id':1, 'approval_digest':digest(EVENT)}
            path.write_text(json.dumps([previous]))
            changed = {**EVENT, 'venue':'架空会場B', 'replaces_digest':digest(previous)}
            issue = {'number':42, 'state':'open', 'body':candidate_body(changed)}
            payload = {'sender':{'login':'omochi-crypto','type':'User'},'issue':issue,
                       'comment':{'id':2,'body':'/approve '+digest(changed)}}
            with patch.dict(os.environ, {'GITHUB_REPOSITORY':'omochi-crypto/inline-skate-events'}):
                add_approved(authorize(payload,issue),payload,path)
                result=json.loads(path.read_text())[0]
                self.assertEqual(result['venue'],'架空会場B')
                self.assertEqual(result['approval_history'][0]['venue'],'架空会場A')
                with self.assertRaises(ValueError): add_approved(changed,payload,path)
                payload['comment']['body']='/approve '+digest(EVENT)
                with self.assertRaises(ValueError): authorize(payload,issue)


if __name__ == '__main__': unittest.main()
