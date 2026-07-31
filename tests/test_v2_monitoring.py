# -*- coding: utf-8 -*-
"""Regression and product-flow tests for the V2 monitoring workspace."""

from __future__ import annotations

import io
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import config
import requests
from crawler.adapters import CollectorError
from crawler.rss_adapter import RSSFeedAdapter, parse_feed
from services.briefing import _extract_json, _llm_brief
from services.delivery import deliver_webhook
from services.intelligence import cluster_signals, evaluate_post
from services.network_safety import UnsafeURL, validate_outbound_url
from storage.monitor_store import MonitorStore
from storage.product_store import ProductStore, get_product_store, reset_store_cache
from web.app import app


class RSSAndNetworkSafetyTest(unittest.TestCase):
    def test_rss_and_atom_entries_keep_source_provenance(self):
        rss = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"><channel><title>Test</title>
          <item><guid>news-1</guid><title>校园食堂回应</title>
          <description><![CDATA[<p>公开说明与处理进展</p>]]></description>
          <link>https://news.example/item/1</link>
          <pubDate>Wed, 22 Jul 2026 10:30:00 +0800</pubDate></item>
        </channel></rss>""".encode()
        records = parse_feed(rss, 'https://news.example/feed.xml')
        self.assertEqual(records[0]['id'], 'news-1')
        self.assertIn('公开说明', records[0]['text'])
        self.assertEqual(records[0]['url'], 'https://news.example/item/1')

        atom = """<feed xmlns="http://www.w3.org/2005/Atom">
          <entry><id>tag:example,2026:2</id><title>后续通报</title>
          <summary>调查仍在继续</summary>
          <link rel="alternate" href="https://news.example/item/2"/>
          <updated>2026-07-22T12:00:00+08:00</updated></entry>
        </feed>""".encode()
        atom_records = parse_feed(atom, 'https://news.example/atom.xml')
        self.assertEqual(atom_records[0]['id'], 'tag:example,2026:2')
        self.assertIn('调查仍在继续', atom_records[0]['text'])

    def test_invalid_feed_and_private_targets_fail_explicitly(self):
        with self.assertRaises(CollectorError) as context:
            parse_feed(b'<not-closed>', 'https://news.example/feed.xml')
        self.assertEqual(context.exception.code, 'invalid_feed')

        loopback = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 80))]
        with patch('services.network_safety.socket.getaddrinfo', return_value=loopback):
            with self.assertRaises(UnsafeURL):
                validate_outbound_url('http://example.test/feed')
        with self.assertRaises(UnsafeURL):
            validate_outbound_url('file:///etc/passwd')

    def test_rss_adapter_does_not_turn_network_failure_into_empty_success(self):
        original = config.ALLOW_PRIVATE_NETWORK_URLS
        config.ALLOW_PRIVATE_NETWORK_URLS = True
        try:
            with patch(
                'crawler.rss_adapter.requests.get',
                side_effect=requests.ConnectionError('offline'),
            ):
                with self.assertRaises(CollectorError) as context:
                    RSSFeedAdapter().collect({
                        'keywords': ['校园'],
                        'params': {'feed_urls': ['https://news.example/feed.xml']},
                    })
            self.assertEqual(context.exception.code, 'feed_request_failed')
        finally:
            config.ALLOW_PRIVATE_NETWORK_URLS = original


class MonitoringLogicTest(unittest.TestCase):
    def test_rule_matching_is_auditable_and_exclusions_win(self):
        monitor = {
            'keywords': ['食堂', '餐饮'],
            'required_terms': ['校园'],
            'excluded_terms': ['招聘'],
            'risk_terms': ['投诉', '风险'],
        }
        matched = evaluate_post({
            'id': 7,
            'text': '校园食堂收到投诉，正在处理',
            'topic': '校园生活',
        }, monitor)
        self.assertEqual(matched['post_id'], 7)
        self.assertEqual(matched['matched_terms'], ['食堂', '校园'])
        self.assertEqual(matched['risk_terms'], ['投诉'])
        self.assertGreater(matched['relevance_score'], 60)
        self.assertIsNone(evaluate_post({
            'id': 8,
            'text': '校园食堂招聘启事',
            'topic': '',
        }, monitor))

    def test_similar_signals_cluster_with_metrics_and_evidence(self):
        signals = []
        for index in range(4):
            signals.append({
                'id': index + 1,
                'platform': 'rss' if index == 3 else 'weibo',
                'source_id': f's-{index}',
                'text': f'校园食堂食品安全投诉事件调查处理进展 {index}',
                'published_at': f'2026-07-22T1{index}:00:00+08:00',
                'fetched_at': '2026-07-22T14:00:00+08:00',
                'sentiment': 0,
                'engagement': {'likes': index},
                'risk_terms': ['投诉'],
            })
        events = cluster_signals('monitor-test', signals)
        self.assertEqual(len(events), 1)
        metrics = events[0]['metrics']
        self.assertEqual(metrics['sample_count'], 4)
        self.assertEqual(metrics['negative_ratio'], 100)
        self.assertEqual(metrics['source_count'], 2)
        self.assertEqual(metrics['evidence_ids'], [1, 2, 3, 4])

    def test_store_keeps_alert_lifecycle_and_redacts_delivery_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            product = ProductStore(str(Path(temp) / 'product.db')).initialize()
            store = MonitorStore(product)
            monitor = store.create_monitor(
                '食品安全',
                keywords=['食堂'],
                sources=[{'kind': 'database', 'name': '数据仓', 'url': ''}],
            )
            alert_id, created = store.upsert_alert(
                monitor['id'],
                None,
                'risk_keyword',
                'high',
                '风险词命中',
                '请核验',
                {'signal_ids': [1]},
                f"{monitor['id']}:risk",
            )
            self.assertTrue(created)
            alert = store.transition_alert(
                alert_id, 'investigating', note='已分配核验', actor='admin'
            )
            self.assertEqual(alert['status'], 'investigating')
            self.assertEqual(store.list_alert_actions(alert_id)[0]['note'], '已分配核验')

            delivery_id = store.create_delivery(
                monitor['id'],
                alert_id,
                'webhook',
                'https://hooks.example',
                {'title': '风险词命中'},
            )
            store.finish_delivery(delivery_id, 'succeeded', response_code=204)
            delivery = store.list_deliveries()[0]
            self.assertEqual(delivery['destination'], 'https://hooks.example')
            self.assertEqual(delivery['response_code'], 204)

    def test_webhook_delivery_records_success_and_hides_secret_path(self):
        with tempfile.TemporaryDirectory() as temp:
            product = ProductStore(str(Path(temp) / 'product.db')).initialize()
            store = MonitorStore(product)
            monitor = store.create_monitor(
                '食品安全',
                keywords=['食堂'],
                webhook_url='https://hooks.example/private/token-123',
            )
            alert_id, _ = store.upsert_alert(
                monitor['id'],
                None,
                'risk_keyword',
                'high',
                '风险词命中',
                '请核验',
                {'signal_ids': [1]},
                f"{monitor['id']}:delivery-risk",
            )
            alert = store.get_alert(alert_id)
            response = Mock(status_code=204)
            with (
                patch(
                    'services.delivery.validate_outbound_url',
                    return_value=monitor['webhook_url'],
                ),
                patch('services.delivery.requests.post', return_value=response) as post,
            ):
                deliver_webhook(store, monitor, alert)
            delivery = store.list_deliveries()[0]
            self.assertEqual(delivery['status'], 'succeeded')
            self.assertEqual(delivery['destination'], 'https://hooks.example')
            self.assertNotIn('token-123', json.dumps(delivery, ensure_ascii=False))
            self.assertEqual(post.call_args.kwargs['json']['alert']['id'], alert_id)

    def test_llm_brief_accepts_only_current_integer_evidence_ids(self):
        original = {
            'LLM_BASE_URL': config.LLM_BASE_URL,
            'LLM_API_KEY': config.LLM_API_KEY,
            'LLM_MODEL': config.LLM_MODEL,
        }
        config.LLM_BASE_URL = 'https://models.example/v1'
        config.LLM_API_KEY = 'unit-test-secret'
        config.LLM_MODEL = 'test-model'
        event = {
            'title': '食品安全事件',
            'metrics': {'sample_count': 2, 'negative_ratio': 50},
        }
        signals = [
            {
                'id': 11,
                'platform': 'rss',
                'published_at': '2026-07-22T10:00:00+08:00',
                'author': '公开来源',
                'text': '学校发布处理通报',
                'source_url': 'https://news.example/11',
            },
            {
                'id': 12,
                'platform': 'weibo',
                'published_at': '2026-07-22T11:00:00+08:00',
                'author': '公开用户',
                'text': '相关讨论仍在继续',
                'source_url': 'https://weibo.com/example/12',
            },
        ]
        result = {
            'headline': '食品安全事件',
            'summary': '当前有两条公开证据。',
            'key_points': ['学校发布通报'],
            'risks': [],
            'counterpoints': ['样本有限'],
            'recommended_actions': ['核验原文'],
            'uncertainty': '不代表全网。',
            'citations': [11, '12', 999, 'bad', 11],
        }
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            'choices': [{'message': {'content': f"```json\n{json.dumps(result, ensure_ascii=False)}\n```"}}]
        }
        try:
            with patch('services.briefing.requests.post', return_value=response) as post:
                brief = _llm_brief(event, signals)
            self.assertEqual(brief['citations'], [11, 12])
            self.assertEqual(brief['mode'], 'llm')
            self.assertEqual(
                post.call_args.kwargs['headers']['Authorization'],
                'Bearer unit-test-secret',
            )
            self.assertEqual(_extract_json('{"ok": true}'), {'ok': True})
        finally:
            for name, value in original.items():
                setattr(config, name, value)


class V2ApiWorkflowTest(unittest.TestCase):
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
        app.config.update(TESTING=True, MAX_CONTENT_LENGTH=1024 * 1024)
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
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()['data']['csrf_token']

    def import_related_posts(self, csrf):
        records = [{
            'id': f'food-{index}',
            'text': f'校园食堂食品安全投诉问题，学校正在调查处理进展 {index}',
            'author': f'user-{index}',
            'created_at': f'2026-07-22 {10 + index:02d}:00:00',
            'likes': index * 3,
            'source_url': f'https://weibo.com/example/food-{index}',
        } for index in range(6)]
        response = self.client.post(
            '/api/v1/imports',
            data={
                'topic': '校园食品安全',
                'file': (
                    io.BytesIO(json.dumps(records, ensure_ascii=False).encode()),
                    'food.json',
                ),
            },
            headers={'X-CSRF-Token': csrf},
            content_type='multipart/form-data',
        )
        self.assertEqual(response.status_code, 202, response.get_data(as_text=True))
        self.assertEqual(response.get_json()['data']['status'], 'succeeded')

    def test_auth_csrf_validation_and_complete_incident_flow(self):
        self.assertEqual(self.client.get('/api/v2/overview').status_code, 401)
        csrf = self.login()
        headers = {'X-CSRF-Token': csrf}
        self.assertEqual(self.client.post(
            '/api/v2/monitors',
            json={'name': '食品安全', 'keywords': ['食堂']},
        ).status_code, 403)
        invalid = self.client.post(
            '/api/v2/monitors',
            json={'name': '空规则', 'keywords': []},
            headers=headers,
        )
        self.assertEqual(invalid.status_code, 400)

        self.import_related_posts(csrf)
        response = self.client.post(
            '/api/v2/monitors',
            json={
                'name': '校园食品安全',
                'description': '识别公开投诉并回到原文核验',
                'keywords': ['食堂', '食品安全'],
                'risk_terms': ['投诉', '问题'],
                'negative_threshold': 50,
                'spike_threshold': 3,
                'interval_minutes': 60,
            },
            headers=headers,
        )
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        monitor = response.get_json()['data']

        response = self.client.post(
            f"/api/v2/monitors/{monitor['id']}/run",
            headers=headers,
        )
        self.assertEqual(response.status_code, 202, response.get_data(as_text=True))
        run = response.get_json()['data']
        self.assertEqual(run['status'], 'succeeded')
        self.assertEqual(run['stats']['matched_signals'], 6)
        self.assertGreaterEqual(run['stats']['events'], 1)
        self.assertGreaterEqual(run['stats']['new_alerts'], 1)

        signals = self.client.get(
            '/api/v2/signals',
            query_string={'monitor_id': monitor['id']},
        ).get_json()['data']
        self.assertEqual(len(signals), 6)
        self.assertTrue(all(item['matched_terms'] for item in signals))

        events = self.client.get(
            '/api/v2/events',
            query_string={'monitor_id': monitor['id']},
        ).get_json()['data']
        self.assertTrue(events)
        event_id = events[0]['id']
        event = self.client.get(f'/api/v2/events/{event_id}').get_json()['data']
        self.assertEqual(event['metrics']['sample_count'], 6)
        self.assertEqual(len(event['signals']), 6)

        brief_response = self.client.post(
            f'/api/v2/events/{event_id}/briefs',
            headers=headers,
        )
        self.assertEqual(brief_response.status_code, 202)
        brief = brief_response.get_json()['data']
        self.assertEqual(brief['status'], 'succeeded')
        valid_signal_ids = {item['id'] for item in event['signals']}
        self.assertTrue(set(brief['result']['citations']) <= valid_signal_ids)
        self.assertEqual(brief['result']['mode'], 'deterministic')

        alerts = self.client.get(
            '/api/v2/alerts',
            query_string={'monitor_id': monitor['id']},
        ).get_json()['data']
        self.assertTrue(alerts)
        transition = self.client.post(
            f"/api/v2/alerts/{alerts[0]['id']}/transition",
            json={'status': 'investigating', 'note': '已核验并开始调查'},
            headers=headers,
        )
        self.assertEqual(transition.status_code, 200)
        self.assertEqual(transition.get_json()['data']['status'], 'investigating')
        detail = self.client.get(
            f"/api/v2/alerts/{alerts[0]['id']}"
        ).get_json()['data']
        self.assertEqual(detail['actions'][0]['actor'], 'admin')

        overview = self.client.get('/api/v2/overview').get_json()['data']
        self.assertEqual(overview['active_monitors'], 1)
        self.assertEqual(overview['signals'], 6)
        self.assertGreaterEqual(overview['open_alerts'], 1)

    def test_paused_monitor_cannot_run_and_urls_are_validated(self):
        csrf = self.login()
        headers = {'X-CSRF-Token': csrf}
        response = self.client.post(
            '/api/v2/monitors',
            json={
                'name': '品牌观察',
                'keywords': ['品牌'],
                'feed_urls': ['file:///private/data'],
            },
            headers=headers,
        )
        self.assertEqual(response.status_code, 400)

        response = self.client.post(
            '/api/v2/monitors',
            json={
                'name': '品牌观察',
                'keywords': ['品牌'],
                'feed_urls': ['https://news.example/feed.xml'],
                'include_weibo': True,
            },
            headers=headers,
        )
        monitor = response.get_json()['data']
        updated = self.client.patch(
            f"/api/v2/monitors/{monitor['id']}",
            json={'include_weibo': False},
            headers=headers,
        )
        self.assertEqual(updated.status_code, 200)
        source_kinds = [
            item['kind'] for item in updated.get_json()['data']['sources']
        ]
        self.assertIn('rss', source_kinds)
        self.assertNotIn('weibo', source_kinds)
        paused = self.client.post(
            f"/api/v2/monitors/{monitor['id']}/status",
            json={'status': 'paused'},
            headers=headers,
        )
        self.assertEqual(paused.get_json()['data']['status'], 'paused')
        blocked = self.client.post(
            f"/api/v2/monitors/{monitor['id']}/run",
            headers=headers,
        )
        self.assertEqual(blocked.status_code, 409)


if __name__ == '__main__':
    unittest.main()
