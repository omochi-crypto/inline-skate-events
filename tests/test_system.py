"""Offline regression tests: extraction boundaries and human approval are critical."""
import copy
import datetime as dt
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from bs4 import BeautifulSoup
from collect_events import dates, parse_calendar, parse_cards, parse_gia, collect
from event_model import digest, event_id, parse_body, validate
from approve_event import authorize, add_approved
from review_issues import candidate_body
from build_site import build
from github_api import all_pages

SOURCE = {'id': 'test', 'url': 'https://example.org/', 'category': 'speed'}
EVENT = {'id': '1234567890abcdef', 'title': '第9回スプリント選手権大会',
         'date_start': '2099-03-07', 'date_end': '2099-03-07', 'category': 'speed',
         'prefecture': '東京都', 'official_url': 'https://example.org/event', 'source_url': 'https://example.org/'}

def soup(html):
    return BeautifulSoup(html, 'html.parser')

class DateTests(unittest.TestCase):
    def test_omitted_year_and_range(self):
        self.assertEqual(dates('4月4日～5日', 2026), (dt.date(2026,4,4), dt.date(2026,4,5)))
    def test_cross_month_and_year(self):
        self.assertEqual(dates('2026年12月31日〜1月2日')[1], dt.date(2027,1,2))
    def test_weekday_and_fullwidth(self):
        self.assertEqual(dates('２０２６年４月４日（土）～５日')[1], dt.date(2026,4,5))
    def test_uncertain_or_invalid_dates(self):
        for raw in ['11月', '2月30日', '2027年2月13・14・20・21日', '3月7日']:
            self.assertIsNone(dates(raw))
    def test_missing_year_never_defaults_to_current(self):
        self.assertIsNone(dates('3月7日'))

class ExtractTests(unittest.TestCase):
    def test_calendar_no_adjacent_sport_leak(self):
        html = '<table>' + ''.join('<tr>'+''.join('<td>'+v+'</td>' for v in row)+'</tr>' for row in [
            ['2026年3月1日','フィギュア選手権大会','東京都','主催','ART'],
            ['4月4日～5日','全日本スピード選手権大会','奈良県','主催','SPD'],
            ['4月6日','スケートボード大会','東京都','主催','SPD'],
            ['11月','全国ローラーマラソン大会','未定','主催','SPD'],
            ['国際大会','','','',''],
            ['2026年12月1日','海外スピード選手権大会','フランス','派遣','SPD']])+'</table>'
        warnings=[]
        result = parse_calendar(soup(html),SOURCE,warnings)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['date_end'],'2026-04-05')
        self.assertEqual(result[0]['prefecture'],'奈良県')
        self.assertEqual(len(warnings),1)
    def test_card_separates_title_date_venue(self):
        html = '''<a class="detail_page_link" href="/race"><span class="compe_title">全国インラインマラソン大会</span><span class="compe_date_span">2026年10月25日</span><span class="compe_stage">岐阜県長良川</span></a>'''
        event = parse_cards(soup(html),SOURCE,[])[0]
        self.assertEqual(event['title'],'全国インラインマラソン大会')
        self.assertEqual(event['category'],'marathon')
        self.assertEqual(event['official_url'],'https://example.org/race')
    def test_gia_post_date_is_not_competition_date(self):
        html = '''<div class="entry_title">ウルトラスケート会場についてのお知らせ</div><div class="entry_date">2026.08.27</div><div class="entry_body">ウルトラスケート長良川 エントリーは9月13日まで。10月11日は岐阜でお会いしましょう!<a href="http://www.inlineskate.or.jp/261011ultra.htm">申込</a></div>'''
        result = parse_gia(soup(html),SOURCE,[])
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['date_start'],'2026-10-11')
    def test_source_independent_identity(self):
        self.assertEqual(event_id('第９回 スプリント選手権大会','2027-03-07'),event_id('第9回スプリント選手権大会','2027-03-07'))
    def test_cross_source_dedup_and_error_counts(self):
        event = copy.deepcopy(EVENT)
        event['source_ids']=['a']
        records=[([event],{'id':'a','ok':True}),([copy.deepcopy(event)],{'id':'b','ok':True}),([],{'id':'c','ok':False,'error':'timeout'})]
        with patch('collect_events.fetch_source',side_effect=records):
            events,report=collect([{}, {}, {}],dt.date(2026,10,27))
        self.assertEqual(report['duplicates'],1)
        self.assertEqual(report['errors'],1)
        self.assertEqual(report['upcoming'],1)
    def test_pagination_reads_closed_history_past_first_page(self):
        with patch('github_api.api',side_effect=[[{}]*100,[{'state':'closed'}]]) as request:
            self.assertEqual(len(all_pages('/issues?state=all')),101)
            self.assertIn('page=2',request.call_args.args[0])

class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{'GITHUB_REPOSITORY':'omochi-crypto/inline-skate-events'})
        self.env.start()
        self.event=copy.deepcopy(EVENT)
        self.issue={'number':42,'state':'open','body':candidate_body(self.event)}
        self.payload={'sender':{'login':'omochi-crypto','type':'User'},'issue':self.issue,
                      'comment':{'id':123,'body':'/approve '+digest(self.event)}}
    def tearDown(self):
        self.env.stop()
    def test_explicit_owner_approval(self):
        self.assertEqual(authorize(self.payload,self.issue),self.event)
    def test_bot_rejected_even_with_owner_login(self):
        self.payload['sender']['type']='Bot'
        with self.assertRaises(ValueError):authorize(self.payload,self.issue)
    def test_nonowner_rejected(self):
        self.payload['sender']['login']='other-user'
        with self.assertRaises(ValueError):authorize(self.payload,self.issue)
    def test_changed_payload_rejects_old_approval(self):
        self.event['date_start']='2099-03-06'
        self.issue['body']=candidate_body(self.event)
        with self.assertRaises(ValueError):authorize(self.payload,self.issue)
    def test_edited_pending_status_cannot_bypass_comment(self):
        self.payload['comment']['body']='/approve'
        with self.assertRaises(ValueError):authorize(self.payload,self.issue)
    def test_closed_and_pr_rejected(self):
        for extra in [{'state':'closed'},{'pull_request':{}}]:
            issue={**self.issue,**extra}
            # An empty PR marker also counts as a PR, as it does on GitHub payloads.
            if 'pull_request' in extra:issue['pull_request']={'url':'https://example.org/pr'}
            with self.assertRaises(ValueError):authorize(self.payload,issue)
    def test_missing_prefecture_and_javascript_url_rejected(self):
        for extra in [{'prefecture':''},{'official_url':'javascript:alert(1)'},{'date_end':'2000-01-01'}]:
            with self.assertRaises(ValueError):validate({**self.event,**extra})
    def test_candidate_body_roundtrip_and_legacy(self):
        self.assertEqual(parse_body(candidate_body(self.event)),self.event)
        self.assertEqual(parse_body('未承認\n'+json.dumps(self.event)),self.event)
    def test_approval_audit_and_build_excludes_candidates(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'events').mkdir();(root/'site').mkdir()
            path=root/'events/approved.json';path.write_text('[]')
            (root/'events/candidates.json').write_text('[{"secret":"pending"}]')
            (root/'site/index.html').write_text('<h1>test</h1>')
            add_approved(authorize(self.payload,self.issue),self.payload,path)
            build(root)
            data=json.loads((root/'public/approved.json').read_text())
            self.assertEqual(data[0]['approval_comment_id'],123)
            self.assertFalse((root/'public/candidates.json').exists())
            with self.assertRaises(ValueError):add_approved(copy.deepcopy(EVENT),self.payload,path)
    def test_build_rejects_unaudited_approved_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'events').mkdir()
            (root/'events/approved.json').write_text(json.dumps([{**self.event,'status':'approved'}]))
            with self.assertRaises(ValueError):build(root)

if __name__ == '__main__':unittest.main()
