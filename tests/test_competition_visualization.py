# -*- coding: utf-8 -*-
"""Competition visualization and deterministic demo regression tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import config
from services.visualization import build_visual_story
from storage.product_store import get_product_store, reset_store_cache
from web.app import app


class VisualStoryAggregateTest(unittest.TestCase):
    def test_story_preserves_totals_provenance_and_network_meaning(self):
        monitor = {'id': 'm-1', 'name': '校园观察', 'description': '测试'}
        signals = [
            {
                'id': 1,
                'event_id': 'e-1',
                'platform': 'weibo',
                'published_at': '2026-08-01T08:00:00+08:00',
                'sentiment': 0,
                'sentiment_method': 'scenario_label_for_demo',
                'risk_terms': ['投诉'],
                'engagement': {'likes': 10, 'comments': 2, 'reposts': 1},
                'raw': {'data_kind': 'synthetic_competition_demo'},
            },
            {
                'id': 2,
                'event_id': 'e-1',
                'platform': 'rss',
                'published_at': '2026-08-01T14:00:00+08:00',
                'sentiment': 1,
                'sentiment_method': 'scenario_label_for_demo',
                'risk_terms': [],
                'engagement': {},
                'raw': {'data_kind': 'synthetic_competition_demo'},
            },
            {
                'id': 3,
                'event_id': None,
                'platform': 'rss',
                'published_at': None,
                'fetched_at': None,
                'sentiment': None,
                'sentiment_method': None,
                'risk_terms': [],
                'engagement': {},
                'raw': {'data_kind': 'synthetic_competition_demo'},
            },
        ]
        events = [{
            'id': 'e-1',
            'title': '食品安全事件',
            'status': 'open',
            'first_seen': signals[0]['published_at'],
            'last_seen': signals[1]['published_at'],
            'metrics': {'heat_score': 62, 'trend': 'rising'},
        }]
        alerts = [{
            'status': 'new', 'severity': 'high', 'event_id': 'e-1'
        }]
        story = build_visual_story(
            monitor, signals, events, alerts, total_available=4
        )
        self.assertEqual(story['provenance']['data_mode'], 'demo')
        self.assertTrue(story['provenance']['truncated'])
        self.assertEqual(story['provenance']['missing_time_count'], 1)
        self.assertEqual(sum(item['total'] for item in story['timeline']), 2)
        self.assertEqual(sum(item['count'] for item in story['sources']), 3)
        self.assertEqual(story['sentiment']['unknown'], 1)
        self.assertEqual(story['summary']['high_alerts'], 1)
        self.assertIn('不代表转发或因果', story['graph']['meaning'])
        self.assertTrue(story['graph']['edges'])


class CompetitionDemoApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original = {
            name: getattr(config, name) for name in (
                'PRODUCT_DB_PATH',
                'REPORT_DIR',
                'IMPORT_DIR',
                'ADMIN_USERNAME',
                'ADMIN_PASSWORD',
                'APP_SECRET_KEY',
                'TASK_QUEUE_MODE',
                'COLLECTOR_LICENSE_ACCEPTED',
                'LLM_ENABLED',
            )
        }
        root = Path(self.temp.name)
        config.PRODUCT_DB_PATH = str(root / 'product.db')
        config.REPORT_DIR = str(root / 'reports')
        config.IMPORT_DIR = str(root / 'imports')
        config.ADMIN_USERNAME = 'admin'
        config.ADMIN_PASSWORD = 'test-password-123'
        config.APP_SECRET_KEY = 'test-secret'
        config.TASK_QUEUE_MODE = 'inline'
        config.COLLECTOR_LICENSE_ACCEPTED = False
        config.LLM_ENABLED = False
        reset_store_cache()
        get_product_store()
        app.config.update(TESTING=True)
        self.client = app.test_client()

    def tearDown(self):
        reset_store_cache()
        for name, value in self.original.items():
            setattr(config, name, value)
        self.temp.cleanup()

    def login(self):
        response = self.client.post('/api/v1/auth/login', json={
            'username': 'admin',
            'password': 'test-password-123',
        })
        self.assertEqual(response.status_code, 200)
        return response.get_json()['data']['csrf_token']

    def test_demo_is_idempotent_and_visual_story_is_complete(self):
        csrf = self.login()
        headers = {'X-CSRF-Token': csrf}
        first = self.client.post('/api/v2/demo/seed', headers=headers)
        self.assertEqual(first.status_code, 201, first.get_data(as_text=True))
        first_data = first.get_json()['data']
        self.assertTrue(first_data['synthetic'])
        self.assertEqual(first_data['records'], 54)
        self.assertEqual(first_data['inserted'], 54)
        self.assertEqual(first_data['run']['status'], 'succeeded')
        self.assertEqual(
            first_data['monitor']['name'],
            '高校校园安全与民生服务｜国赛演示',
        )
        monitor_id = first_data['monitor']['id']

        story_response = self.client.get(
            '/api/v2/visual-story', query_string={'monitor_id': monitor_id}
        )
        self.assertEqual(story_response.status_code, 200)
        story = story_response.get_json()['data']
        self.assertEqual(story['provenance']['data_mode'], 'demo')
        self.assertEqual(story['summary']['signals'], 54)
        self.assertEqual(sum(item['total'] for item in story['timeline']), 54)
        self.assertEqual(sum(item['count'] for item in story['sources']), 54)
        self.assertEqual(story['summary']['events'], 3)
        self.assertEqual(
            sorted(item['sample_count'] for item in story['events']),
            [12, 14, 28],
        )
        self.assertTrue(story['graph']['nodes'])
        self.assertTrue(story['graph']['edges'])

        analytics_response = self.client.get(
            '/api/v2/analytics', query_string={'monitor_id': monitor_id}
        )
        self.assertEqual(analytics_response.status_code, 200)
        analytics = analytics_response.get_json()['data']
        self.assertEqual(analytics['summary']['signals'], 54)
        self.assertEqual(analytics['readiness']['documents'], 54)
        self.assertTrue(analytics['readiness']['lda']['available'])
        self.assertTrue(analytics['top_words'])
        self.assertEqual(
            sorted(item['sample_count'] for item in analytics['events']),
            [12, 14, 28],
        )

        analysis_response = self.client.post(
            '/api/v1/analysis-jobs',
            json={'type': 'summary', 'monitor_id': monitor_id},
            headers=headers,
        )
        self.assertEqual(
            analysis_response.status_code,
            202,
            analysis_response.get_data(as_text=True),
        )
        analysis = analysis_response.get_json()['data']
        self.assertEqual(analysis['params']['monitor_id'], monitor_id)
        self.assertEqual(analysis['status'], 'succeeded')
        self.assertEqual(analysis['result']['summary']['sample_count'], 54)

        second = self.client.post('/api/v2/demo/seed', headers=headers)
        self.assertEqual(second.status_code, 201)
        second_data = second.get_json()['data']
        self.assertEqual(second_data['inserted'], 0)
        self.assertEqual(second_data['duplicates'], 54)
        self.assertEqual(second_data['monitor']['id'], monitor_id)

    def test_visual_story_requires_login_and_valid_monitor(self):
        self.assertEqual(
            self.client.get('/api/v2/visual-story').status_code, 401
        )
        self.assertEqual(
            self.client.get('/api/v2/analytics').status_code, 401
        )
        self.login()
        workspace = self.client.get('/workspace')
        self.assertEqual(workspace.status_code, 200)
        workspace_html = workspace.get_data(as_text=True)
        self.assertIn('校园舆情雷达', workspace_html)
        self.assertIn('校园安全与民生服务', workspace_html)
        self.assertIn('分析实验室', workspace_html)
        self.assertNotIn('让公共事件的', workspace_html)
        invalid = self.client.get(
            '/api/v2/visual-story', query_string={'monitor_id': 'missing'}
        )
        self.assertEqual(invalid.status_code, 400)
        invalid_analytics = self.client.get(
            '/api/v2/analytics', query_string={'monitor_id': 'missing'}
        )
        self.assertEqual(invalid_analytics.status_code, 400)


if __name__ == '__main__':
    unittest.main()
