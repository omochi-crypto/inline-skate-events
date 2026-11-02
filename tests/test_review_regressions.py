"""Counterexamples from the second design review; no live network or event data."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import base64
import io
import contextlib
from email.message import Message
from urllib.error import HTTPError
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from test_improvements import Response, EVENT, SOURCE, ledger
from approve_event import add_approved
from polite_http import Client
from revisions import prepare_revision, check_replacement
from source_policy import validate_sources
from collection_state import validate_state, Ledger
from event_model import digest, validate
from review_changes import records, classify
import collect_events as ce


class ReviewRegressionTests(unittest.TestCase):
    def test_partial_revision_cannot_silently_drop_details(self):
        from event_model import digest
        previous = {**EVENT, 'details':['保持すべき競技案内'], 'registration_note':'当日受付'}
        partial = {**EVENT, 'venue':'架空会場B', 'replaces_digest':digest(previous)}
        payload = {'sender':{'login':'omochi-crypto'},'comment':{'id':2},'issue':{'number':42}}
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'approved.json';path.write_text(json.dumps([previous]))
            with self.assertRaises(ValueError): add_approved(partial,payload,path)
            self.assertEqual(json.loads(path.read_text()), [previous])

    def test_full_revision_preserves_details_and_explicit_removal_is_bound(self):
        previous={**EVENT,'details':['競技案内'],'registration_deadline':'2099-03-01'}
        full=prepare_revision(previous,{'venue':'架空会場B'})
        self.assertEqual(full['details'],previous['details'])
        self.assertEqual(full['registration_deadline'],'2099-03-01')
        self.assertIs(check_replacement(full,[previous]),previous)
        removed=prepare_revision(previous,{'venue':'架空会場B'},remove=['registration_deadline'])
        self.assertNotIn('registration_deadline',removed)
        self.assertEqual(removed['removed_fields'],['registration_deadline'])
        self.assertNotEqual(digest(full),digest(removed))
        with self.assertRaises(ValueError):prepare_revision(previous,{'registration_deadline':''})
        with self.assertRaises(ValueError):prepare_revision(previous,{},remove=['venue'])

    def test_publication_requires_venue(self):
        for value in ('',None):
            with self.assertRaises(ValueError):validate({**EVENT,'venue':value})

    def test_repeated_begin_cannot_reset_site_budget(self):
        opener=Mock(return_value=Response(b'User-agent: *'))
        client=Client({},opener=opener,clock=lambda:1000,sleeper=lambda _:None)
        client.begin(SOURCE);client.get(SOURCE['url']);client.get(SOURCE['url'])
        client.begin({**SOURCE,'id':'another-setting'})
        with self.assertRaises(ValueError):client.get(SOURCE['url'])
        self.assertEqual(opener.call_count,3)

    def test_changed_source_url_does_not_reuse_old_cache(self):
        value=ledger();value.reserve([SOURCE],1000)
        value.finish(SOURCE,[EVENT],{'ok':True,'url':SOURCE['url'],'extracted':1})
        events,status=value.cached({**SOURCE,'url':'https://other.example.org/'})
        self.assertEqual(events,[])
        self.assertFalse(status['ok'])

    def test_duplicate_configuration_rejected_and_host_deadline_survives_id_change(self):
        small={**SOURCE,'adapter':'articles','category':'speed','min_interval_seconds':604800}
        with self.assertRaises(ValueError):validate_sources([small,small])
        with self.assertRaises(ValueError):validate_sources([small,{**small,'id':'second','url':SOURCE['url']+'another'}])
        value=ledger();value.reserve([SOURCE],1000)
        self.assertEqual(value.reserve([{**SOURCE,'id':'renamed'}],1001),set())

    def test_malformed_state_and_excessive_state_size_fail_before_write(self):
        for data in ({'version':1,'sources':[],'hosts':{}},
                     {'version':1,'sources':{},'hosts':{'x':{'last_end':float('nan')}}}):
            with self.assertRaises(ValueError):validate_state(data)
        value=Ledger.__new__(Ledger);value.parent=None
        value.data={'version':1,'sources':{'x':{'reason':'x'*750001}},'hosts':{}}
        with patch('collection_state.api') as request:
            with self.assertRaises(ValueError):value.save('fixture')
            request.assert_not_called()

    def test_legacy_policy_upgrade_uses_quiet_period_instead_of_permanent_pause(self):
        from source_policy import fingerprint
        source={**SOURCE,'small_site':False,'min_interval_seconds':172800}
        value=ledger()
        value.data['sources'][source['id']]={'config':{
            k:v for k,v in fingerprint(source).items() if k not in ('small_site','min_interval_seconds')},'next_due':1001}
        self.assertEqual(value.reserve([source],1000),set())
        state=value.data['sources'][source['id']]
        self.assertNotIn('paused',state)
        self.assertEqual(state['next_due'],173800)
        self.assertEqual(value.reserve([source],173800),{source['id']})

    def test_expired_successful_cache_is_reference_only(self):
        value=ledger();value.reserve([SOURCE],1000)
        value.finish(SOURCE,[EVENT],{'ok':True,'fetched_at':'2000-01-01T00:00:00+00:00','extracted':1})
        events,status=value.cached(SOURCE)
        self.assertTrue(status['incomplete'])
        self.assertEqual(len(events),1)

    def test_shared_host_and_global_run_limits(self):
        now=[1000.0]
        def sleep(n):now[0]+=n
        opener=Mock(return_value=Response(b'User-agent: *'))
        client=Client({},opener=opener,clock=lambda:now[0],sleeper=sleep)
        for i in range(7):
            client.begin({'id':str(i),'url':'https://example.org/','small_site':False})
            client.get('https://example.org/'+str(i))
        self.assertEqual(opener.call_count,8) # robots + seven body requests
        client.begin({'id':'eighth','url':'https://example.org/','small_site':False})
        with self.assertRaises(ValueError):client.get('https://example.org/8')
        client.max_requests=len(client.metrics)
        client.begin({'id':'different','url':'https://other.example.org/','small_site':False})
        with self.assertRaises(ValueError):client.get('https://other.example.org/')

    def test_invalid_robots_and_access_challenge_stop(self):
        from polite_http import robot_rules
        for text in ('User-agent: \nDisallow: /','User-agent: *\nDisallow: private',
                     'User-agent: *\nDisallow: /\x00private'):
            with self.assertRaises(ValueError):robot_rules(text,SOURCE['url'])
        opener=Mock(side_effect=[Response(b'User-agent: *'),Response(b'<html>Verify you are human</html>')])
        client=Client({},opener=opener,clock=lambda:1000,sleeper=lambda _:None)
        client.begin(SOURCE)
        with self.assertRaises(ValueError):client.get(SOURCE['url'])
        self.assertIn('paused',client.hosts['example.org'])

    def test_unknown_partial_reads_keep_byte_reservation(self):
        # A timeout can hide bytes already read internally. Charge the full
        # bounded read instead of treating such a request as zero bytes.
        class TimeoutResponse(Response):
            def read(self,count):raise TimeoutError('fixture partial read')
        opener=Mock(return_value=TimeoutResponse(b''))
        client=Client({},opener=opener,clock=lambda:1000,sleeper=lambda _:None)
        client.begin({**SOURCE,'small_site':False})
        client.robots['example.org']='User-agent: *'
        client.max_bytes=100
        with self.assertRaises(TimeoutError):client.get(SOURCE['url'])
        self.assertEqual(client.byte_budget_used,100)
        with self.assertRaises(ValueError):client.get(SOURCE['url'])
        self.assertEqual(opener.call_count,1)

    def test_latest_issue_wins_independent_of_api_order(self):
        recent={'number':10,'state':'open','body':json.dumps({**EVENT,'venue':'架空会場B'})}
        old={'number':1,'state':'closed','body':json.dumps(EVENT)}
        for issues in ([recent,old],[old,recent]):
            known=records(issues,[])
            self.assertEqual(known[EVENT['id']]['issue'],10)

    def test_failed_cache_cannot_create_new_candidate(self):
        event={**EVENT,'source_ids':['a']}
        events,_=ce.collect([{}],fetcher=lambda _:([event],{
            'id':'a','ok':False,'cached':True,'incomplete':True,'error':'HTTP 429'}))
        self.assertEqual(classify(events[0],{})[0],'unverified_observation')

    def test_state_failure_still_writes_diagnostic_report(self):
        value=Mock();value.reserve.side_effect=OSError('fixture persistence failure')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'events').mkdir()
            (root/'events/sources.json').write_text(json.dumps({'sources':[{
                'id':'official','url':'https://example.org/','adapter':'cards','category':'speed','small_site':False}]}))
            (root/'events/candidates.json').write_text('[{"stale":true}]')
            with patch.object(ce,'ROOT',root),patch.object(ce,'Ledger',return_value=value):
                with contextlib.redirect_stdout(io.StringIO()):
                    with self.assertRaises(OSError):ce.main()
            report=json.loads((root/'reports/collection.json').read_text())
            self.assertFalse(report['safe_for_issue_write'])
            self.assertEqual(json.loads((root/'events/candidates.json').read_text()),[])


class MemoryGitHub:
    """Tiny immutable-commit fixture. Never invokes GitHub or an origin server."""
    def __init__(self):
        self.head=None;self.trees={};self.commits={};self.calls=[];self.fail_results=False
    def __call__(self,path,method='GET',data=None):
        self.calls.append((path,method))
        if path.startswith('/git/ref/heads/'):
            if self.head is None:raise HTTPError('fixture',404,'missing',Message(),None)
            return {'object':{'sha':self.head}}
        if path.startswith('/contents/state.json?ref='):
            sha=path.split('ref=',1)[1]
            self.assert_pinned=sha==self.head
            return {'content':base64.b64encode(self.commits[sha]['content'].encode()).decode()}
        if path=='/git/trees':
            if self.fail_results and self.head is not None:raise HTTPError('fixture',403,'fixture stop',Message(),None)
            sha='tree'+str(len(self.trees));self.trees[sha]=data['tree'][0]['content'];return {'sha':sha}
        if path=='/git/commits':
            sha='commit'+str(len(self.commits));self.commits[sha]={'content':self.trees[data['tree']], 'parents':data['parents']};return {'sha':sha}
        if path=='/git/refs':self.head=data['sha'];return {}
        if path.startswith('/git/refs/heads/'):
            if self.commits[data['sha']]['parents']!=[self.head]:raise HTTPError('fixture',422,'conflict',Message(),None)
            self.head=data['sha'];return {}
        raise AssertionError((path,method))


class CollectionIntegrationTests(unittest.TestCase):
    def run_fixture(self,fail_results=False):
        github=MemoryGitHub();github.fail_results=fail_results
        now=[1000.0]
        def sleep(n):now[0]+=n
        def origin(request,timeout):
            self.assertIsNotNone(github.head) # Durable reservation exists before GET.
            state=json.loads(github.commits[github.head]['content'])
            self.assertIn('pending',state['sources']['fixture'])
            if request.full_url.endswith('/robots.txt'):return Response(b'User-agent: *')
            return Response('<a class="detail_page_link" href="/race"><span class="compe_title">架空スピード記録会</span><span class="compe_date_span">2099年3月7日</span><span class="compe_stage">東京都架空会場</span></a>'.encode())
        opener=Mock(side_effect=origin)
        def client(hosts):return Client(hosts,opener=opener,clock=lambda:now[0],sleeper=sleep)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'events').mkdir()
            source={'id':'fixture','url':'https://example.org/list','category':'speed','adapter':'cards','small_site':False,'min_interval_seconds':172800}
            (root/'events/sources.json').write_text(json.dumps({'sources':[source]}))
            with patch.object(ce,'ROOT',root),patch.object(ce,'Client',side_effect=client),patch('collection_state.api',side_effect=github),contextlib.redirect_stdout(io.StringIO()):
                if fail_results:
                    with self.assertRaises(HTTPError):ce.main()
                    report=json.loads((root/'reports/collection.json').read_text())
                    self.assertFalse(report['safe_for_issue_write'])
                    self.assertEqual(report['phase'],'persist_results')
                    self.assertEqual(len(json.loads((root/'events/candidates.json').read_text())),1)
                    self.assertIn('pending',json.loads(github.commits[github.head]['content'])['sources']['fixture'])
                else:
                    ce.main()
                    self.assertEqual(len(github.commits),2)
                    self.assertEqual(opener.call_count,2)
                    ce.main() # Same input: no additional origin GET or state commit.
                    self.assertEqual(len(github.commits),2)
                    self.assertEqual(opener.call_count,2)
                    report=json.loads((root/'reports/collection.json').read_text())
                    self.assertTrue(report['safe_for_issue_write'])
                    self.assertTrue(report['sources'][0]['cached'])
                    self.assertTrue(github.assert_pinned)
    def test_end_to_end_reservation_collection_and_cached_replay(self):self.run_fixture()
    def test_results_save_failure_keeps_reservation_and_diagnostics(self):self.run_fixture(True)


if __name__=='__main__':unittest.main()
