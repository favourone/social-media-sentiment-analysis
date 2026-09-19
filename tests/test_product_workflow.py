# -*- coding: utf-8 -*-

import io
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

import config
from crawler.adapters import (
    CollectorError, CollectorPaused, FileImportAdapter,
    MediaCrawlerWeiboAdapter, get_collector, normalize_post
)
from services.task_queue import QueueUnavailable, dispatch, queue_status
from storage.product_store import ProductStore, get_product_store, reset_store_cache
from web.app import app

_ORIGINAL_ENGINES = None


def setUpModule():
    """工作流回归覆盖词典基线行为，显式固定引擎，避免被 .env 配置影响。"""
    global _ORIGINAL_ENGINES
    _ORIGINAL_ENGINES = (config.SENTIMENT_ENGINE, config.CLUSTERING_ENGINE)
    config.SENTIMENT_ENGINE = 'lexicon'
    config.CLUSTERING_ENGINE = 'ngram'


def tearDownModule():
    config.SENTIMENT_ENGINE, config.CLUSTERING_ENGINE = _ORIGINAL_ENGINES


class ProductStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp.name) / 'product.db')
        self.store = ProductStore(self.db_path).initialize('admin', 'correct horse battery')

    def tearDown(self):
        self.temp.cleanup()

    def test_admin_password_is_hashed_and_can_change(self):
        self.assertIsNotNone(self.store.authenticate('admin', 'correct horse battery'))
        with self.store.connect() as connection:
            password_hash = connection.execute(
                'SELECT password_hash FROM admins WHERE username = ?', ('admin',)
            ).fetchone()[0]
        self.assertNotEqual(password_hash, 'correct horse battery')
        self.assertTrue(self.store.change_password(1, 'correct horse battery', 'a different secure password'))
        self.assertIsNone(self.store.authenticate('admin', 'correct horse battery'))
        self.assertIsNotNone(self.store.authenticate('admin', 'a different secure password'))

    def test_platform_source_id_is_unique(self):
        job = self.store.create_collection_job('weibo', 'file', ['测试'])
        post = normalize_post({'id': 'same-1', 'text': '这是一条测试微博'}, topic='测试')
        self.assertEqual(self.store.insert_posts(job['id'], [post]), 1)
        self.assertEqual(self.store.insert_posts(job['id'], [post]), 0)
        self.assertEqual(len(self.store.list_posts()), 1)

    def test_analysis_conditional_updates_preserve_cancellation(self):
        job = self.store.create_analysis_job('hybrid')
        cancelled = self.store.update_analysis_job(
            job['id'], expected_statuses={'pending', 'running'},
            status='cancelled',
        )
        self.assertEqual(cancelled['status'], 'cancelled')
        self.assertIsNone(self.store.update_analysis_job(
            job['id'], expected_statuses={'pending'}, status='running',
        ))
        self.assertIsNone(self.store.update_analysis_job(
            job['id'], expected_statuses={'running'}, status='succeeded',
        ))
        self.assertEqual(self.store.get_analysis_job(job['id'])['status'], 'cancelled')


class CollectorNormalizationTest(unittest.TestCase):
    def test_missing_text_is_rejected_instead_of_mocked(self):
        with self.assertRaises(CollectorError) as context:
            normalize_post({'id': 'missing-text'})
        self.assertEqual(context.exception.code, 'invalid_record')

    def test_normalization_keeps_provenance_and_transparent_sentiment(self):
        post = normalize_post({
            'id': 'wb-1', 'content': '这项改进很优秀，值得支持',
            'nickname': '研究用户', 'publish_time': 1784678400,
            'liked_count': '12', 'url': 'https://weibo.com/example/wb-1'
        }, topic='产品')
        self.assertEqual(post['source_id'], 'wb-1')
        self.assertEqual(post['engagement']['likes'], 12)
        self.assertEqual(post['sentiment_method'], 'transparent_lexicon_v2')
        self.assertEqual(post['raw']['nickname'], '研究用户')

        weibo_date = normalize_post({
            'id': 'wb-date', 'text': '日期解析测试',
            'created_at': 'Wed Jul 22 10:30:00 +0800 2026'
        })
        self.assertTrue(weibo_date['published_at'].startswith('2026-07-22T10:30:00'))

    def test_file_adapter_supports_jsonl_and_rejects_unsafe_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original = config.IMPORT_DIR
            config.IMPORT_DIR = temp_dir
            try:
                path = Path(temp_dir) / 'records.jsonl'
                path.write_text(
                    '{"id":"one","text":"第一条公开记录"}\n'
                    '{"id":"two","text":"第二条公开记录"}\n', encoding='utf-8'
                )
                progress = []
                result = FileImportAdapter().collect({
                    'platform': 'weibo', 'keywords': ['导入'],
                    'params': {'import_path': str(path)}
                }, progress.append)
                self.assertEqual(len(result['posts']), 2)
                self.assertTrue(progress)
                with self.assertRaises(CollectorError) as context:
                    FileImportAdapter().collect({
                        'platform': 'weibo', 'keywords': ['导入'],
                        'params': {'import_path': str(Path(temp_dir).parent / 'outside.json')}
                    })
                self.assertEqual(context.exception.code, 'unsafe_import_path')
            finally:
                config.IMPORT_DIR = original

    def test_unknown_collector_is_rejected(self):
        with self.assertRaises(CollectorError) as context:
            get_collector('not-real')
        self.assertEqual(context.exception.code, 'unknown_adapter')


class MediaCrawlerAdapterTest(unittest.TestCase):
    CONFIG_TEXT = """\
KEYWORDS = "old"
CRAWLER_MAX_NOTES_COUNT = 15
SAVE_DATA_OPTION = "jsonl"
SAVE_DATA_PATH = ""
ENABLE_GET_COMMENTS = True
ENABLE_GET_SUB_COMMENTS = False
MAX_CONCURRENCY_NUM = 1
"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.home = root / 'MediaCrawler'
        (self.home / 'config').mkdir(parents=True)
        (self.home / 'config' / 'base_config.py').write_text(
            self.CONFIG_TEXT, encoding='utf-8'
        )
        self.output = self.home / 'data'
        self.original = {
            name: getattr(config, name) for name in (
                'COLLECTOR_LICENSE_ACCEPTED', 'MEDIACRAWLER_HOME',
                'MEDIACRAWLER_OUTPUT_DIR', 'MEDIACRAWLER_COMMAND_JSON',
                'MEDIACRAWLER_TIMEOUT_SECONDS'
            )
        }
        config.COLLECTOR_LICENSE_ACCEPTED = True
        config.MEDIACRAWLER_HOME = str(self.home)
        config.MEDIACRAWLER_OUTPUT_DIR = str(self.output)
        config.MEDIACRAWLER_COMMAND_JSON = json.dumps([
            'uv', 'run', 'main.py', '--platform', 'wb', '--type', 'search'
        ])
        config.MEDIACRAWLER_TIMEOUT_SECONDS = 10
        self.job = {
            'platform': 'weibo', 'keywords': ['人工智能'],
            'params': {'max_pages': 1}
        }

    def tearDown(self):
        for name, value in self.original.items():
            setattr(config, name, value)
        self.temp.cleanup()

    def test_successful_external_export_is_normalized_and_config_restored(self):
        def fake_run(*args, **kwargs):
            self.output.mkdir(parents=True, exist_ok=True)
            (self.output / 'weibo_contents.jsonl').write_text(
                json.dumps({'id': 'wb-live-1', 'content': '公开微博内容'}, ensure_ascii=False) + '\n',
                encoding='utf-8'
            )
            (self.output / 'weibo_comments.jsonl').write_text(
                json.dumps({
                    'comment_id': 'comment-1', 'post_id': 'wb-live-1',
                    'content': '公开评论'
                }, ensure_ascii=False) + '\n', encoding='utf-8'
            )
            return subprocess.CompletedProcess(args[0], 0, 'ok', '')

        progress = []
        with patch('crawler.adapters.subprocess.run', side_effect=fake_run):
            result = MediaCrawlerWeiboAdapter().collect(self.job, progress.append)
        self.assertEqual(result['posts'][0]['source_id'], 'wb-live-1')
        self.assertEqual(result['comments'][0]['post_source_id'], 'wb-live-1')
        self.assertEqual(progress, [5, 80])
        restored = (self.home / 'config' / 'base_config.py').read_text(encoding='utf-8')
        self.assertEqual(restored, self.CONFIG_TEXT)

    def test_external_login_challenge_pauses_and_restores_config(self):
        completed = subprocess.CompletedProcess([], 1, '', 'captcha login required')
        with patch('crawler.adapters.subprocess.run', return_value=completed):
            with self.assertRaises(CollectorPaused) as context:
                MediaCrawlerWeiboAdapter().collect(self.job)
        self.assertEqual(context.exception.code, 'login_or_challenge_required')
        self.assertEqual(
            (self.home / 'config' / 'base_config.py').read_text(encoding='utf-8'),
            self.CONFIG_TEXT
        )

    def test_no_export_pauses_and_timeout_pauses(self):
        completed = subprocess.CompletedProcess([], 0, '', '')
        with patch('crawler.adapters.subprocess.run', return_value=completed):
            with self.assertRaises(CollectorPaused) as context:
                MediaCrawlerWeiboAdapter().collect(self.job)
        self.assertEqual(context.exception.code, 'no_new_export')

        with patch(
            'crawler.adapters.subprocess.run',
            side_effect=subprocess.TimeoutExpired(['uv'], 10)
        ):
            with self.assertRaises(CollectorPaused) as context:
                MediaCrawlerWeiboAdapter().collect(self.job)
        self.assertEqual(context.exception.code, 'collector_timeout')

    def test_missing_install_command_and_license_are_explicit(self):
        config.COLLECTOR_LICENSE_ACCEPTED = False
        with self.assertRaises(CollectorPaused) as context:
            MediaCrawlerWeiboAdapter().collect(self.job)
        self.assertEqual(context.exception.code, 'license_confirmation_required')

        config.COLLECTOR_LICENSE_ACCEPTED = True
        config.MEDIACRAWLER_HOME = str(self.home / 'missing')
        with self.assertRaises(CollectorPaused) as context:
            MediaCrawlerWeiboAdapter().collect(self.job)
        self.assertEqual(context.exception.code, 'collector_not_installed')

        config.MEDIACRAWLER_HOME = str(self.home)
        config.MEDIACRAWLER_COMMAND_JSON = ''
        with self.assertRaises(CollectorPaused) as context:
            MediaCrawlerWeiboAdapter().collect(self.job)
        self.assertEqual(context.exception.code, 'collector_not_configured')


class TaskQueueTest(unittest.TestCase):
    def test_local_background_dispatch_returns_before_task_finishes(self):
        original_mode = config.TASK_QUEUE_MODE
        started = Event()
        release = Event()
        finished = Event()

        def slow_task():
            started.set()
            release.wait(timeout=5)
            finished.set()

        try:
            config.TASK_QUEUE_MODE = 'inline'
            with patch('services.task_queue._resolve', return_value=slow_task):
                result = dispatch('fake.slow_task', background=True)
            self.assertEqual(result['mode'], 'local_background')
            self.assertTrue(started.wait(timeout=2))
            self.assertFalse(finished.is_set())
        finally:
            release.set()
            config.TASK_QUEUE_MODE = original_mode
            self.assertTrue(finished.wait(timeout=2))

    def test_inline_unknown_and_unavailable_rq_modes(self):
        original_mode = config.TASK_QUEUE_MODE
        original_url = config.REDIS_URL
        try:
            config.TASK_QUEUE_MODE = 'inline'
            self.assertEqual(dispatch('builtins.len', [1, 2])['mode'], 'inline')
            self.assertTrue(queue_status()['ready'])

            config.TASK_QUEUE_MODE = 'unknown'
            self.assertFalse(queue_status()['ready'])
            with self.assertRaises(QueueUnavailable):
                dispatch('builtins.len', [])

            config.TASK_QUEUE_MODE = 'rq'
            config.REDIS_URL = 'redis://127.0.0.1:1/0'
            with patch('redis.Redis.ping', side_effect=ConnectionError('offline')):
                self.assertFalse(queue_status()['ready'])
                with self.assertRaises(QueueUnavailable):
                    dispatch('builtins.len', [])
        finally:
            config.TASK_QUEUE_MODE = original_mode
            config.REDIS_URL = original_url


class ProductApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original = {
            name: getattr(config, name) for name in (
                'PRODUCT_DB_PATH', 'REPORT_DIR', 'IMPORT_DIR', 'ADMIN_USERNAME',
                'ADMIN_PASSWORD', 'APP_SECRET_KEY', 'TASK_QUEUE_MODE',
                'COLLECTOR_LICENSE_ACCEPTED'
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
            'username': 'admin', 'password': 'test-password-123'
        })
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()['data']['csrf_token']

    def import_posts(self, csrf, count=12):
        records = [{
            'id': f'wb-{index}',
            'text': ('支持优秀项目' if index % 3 else '质疑项目风险'),
            'author': f'user-{index}',
            'created_at': f'2026-07-{index % 4 + 1:02d} 12:00:00',
            'likes': index,
            'source_url': f'https://weibo.com/example/{index}',
        } for index in range(count)]
        response = self.client.post(
            '/api/v1/imports',
            data={
                'topic': '产品测试',
                'file': (io.BytesIO(json.dumps(records, ensure_ascii=False).encode()), 'posts.json'),
            },
            headers={'X-CSRF-Token': csrf},
            content_type='multipart/form-data',
        )
        self.assertEqual(response.status_code, 202, response.get_data(as_text=True))
        return response.get_json()['data']

    def test_protected_endpoints_require_login_and_csrf(self):
        root = self.client.get('/')
        self.assertEqual(root.status_code, 302)
        self.assertTrue(root.headers['Location'].endswith('/workspace'))
        legacy = self.client.get('/dashboard')
        self.assertEqual(legacy.status_code, 302)
        self.assertTrue(legacy.headers['Location'].endswith('/workspace#analytics'))
        self.assertEqual(self.client.get('/workspace').status_code, 302)
        self.assertEqual(self.client.get('/api/v1/collection-jobs').status_code, 401)
        csrf = self.login()
        self.assertEqual(self.client.get('/workspace').status_code, 200)
        response = self.client.post('/api/v1/analysis-jobs', json={'type': 'summary'})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(csrf)

    def test_health_settings_and_logout(self):
        csrf = self.login()
        live = self.client.get('/api/v1/health/live')
        ready = self.client.get('/api/v1/health/ready')
        self.assertEqual(live.status_code, 200)
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.get_json()['data']['status'], 'ready')
        settings = self.client.get('/api/v1/settings/status').get_json()['data']
        self.assertEqual(settings['collector']['platform'], 'weibo')
        response = self.client.post(
            '/api/v1/auth/logout', headers={'X-CSRF-Token': csrf}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/api/v1/auth/me').status_code, 401)

    def test_csv_import_template_is_downloadable(self):
        response = self.client.get('/static/templates/campus-opinion-import-template.csv')
        try:
            self.assertEqual(response.status_code, 200)
            self.assertIn(response.mimetype, {'text/csv', 'application/vnd.ms-excel'})
            self.assertEqual(
                response.get_data(as_text=True).strip(),
                'id,text,created_at,source_url,likes,comments,reposts',
            )
        finally:
            response.close()

    def test_collection_input_state_cancel_and_retry(self):
        csrf = self.login()
        headers = {'X-CSRF-Token': csrf}
        self.assertEqual(self.client.post(
            '/api/v1/collection-jobs', json={'keywords': []}, headers=headers
        ).status_code, 400)
        self.assertEqual(self.client.post(
            '/api/v1/collection-jobs',
            json={'keywords': ['测试'], 'max_pages': 99}, headers=headers
        ).status_code, 400)
        with patch('web.product_api._dispatch', return_value={'mode': 'rq'}):
            response = self.client.post(
                '/api/v1/collection-jobs',
                json={'keywords': ['测试'], 'max_pages': 1}, headers=headers
            )
        job = response.get_json()['data']
        self.assertEqual(job['status'], 'pending')
        cancelled = self.client.post(
            f"/api/v1/collection-jobs/{job['id']}/cancel", headers=headers
        )
        self.assertEqual(cancelled.get_json()['data']['status'], 'cancelled')
        with patch('web.product_api._dispatch', return_value={'mode': 'rq'}):
            retried = self.client.post(
                f"/api/v1/collection-jobs/{job['id']}/retry", headers=headers
            )
        self.assertEqual(retried.status_code, 202)
        self.assertEqual(retried.get_json()['data']['status'], 'pending')
        self.assertEqual(self.client.get(
            '/api/v1/collection-jobs/not-found'
        ).status_code, 404)

    def test_analysis_cancel_and_report_not_ready(self):
        csrf = self.login()
        headers = {'X-CSRF-Token': csrf}
        with patch('web.product_api._dispatch', return_value={'mode': 'rq'}):
            response = self.client.post(
                '/api/v1/analysis-jobs', json={'type': 'summary'}, headers=headers
            )
        job = response.get_json()['data']
        cancelled = self.client.post(
            f"/api/v1/analysis-jobs/{job['id']}/cancel", headers=headers
        )
        self.assertEqual(cancelled.get_json()['data']['status'], 'cancelled')
        self.assertEqual(self.client.post(
            f"/api/v1/analysis-jobs/{job['id']}/cancel", headers=headers
        ).status_code, 409)
        self.assertEqual(self.client.get(
            '/api/v1/analysis-jobs/not-found'
        ).status_code, 404)

        with patch('web.product_api._dispatch', return_value={'mode': 'rq'}):
            response = self.client.post(
                '/api/v1/reports', json={'format': 'pdf'}, headers=headers
            )
        report = response.get_json()['data']
        self.assertEqual(self.client.get(
            f"/api/v1/reports/{report['id']}.pdf"
        ).status_code, 409)
        self.assertEqual(self.client.get(
            f"/api/v1/reports/{report['id']}.csv"
        ).status_code, 404)

    def test_password_change_invalidates_session(self):
        csrf = self.login()
        response = self.client.post(
            '/api/v1/auth/password',
            json={
                'current_password': 'test-password-123',
                'new_password': 'new-password-456'
            },
            headers={'X-CSRF-Token': csrf},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/api/v1/auth/me').status_code, 401)
        response = self.client.post('/api/v1/auth/login', json={
            'username': 'admin', 'password': 'new-password-456'
        })
        self.assertEqual(response.status_code, 200)

    def test_file_import_deduplicates_and_analysis_handles_model_limits(self):
        csrf = self.login()
        first = self.import_posts(csrf)
        second = self.import_posts(csrf)
        self.assertEqual(first['status'], 'succeeded')
        self.assertEqual(first['stats']['inserted_posts'], 12)
        self.assertEqual(second['stats']['inserted_posts'], 0)
        self.assertEqual(second['stats']['duplicate_posts'], 12)

        response = self.client.post(
            '/api/v1/analysis-jobs',
            json={'type': 'full', 'topic': '产品测试'},
            headers={'X-CSRF-Token': csrf},
        )
        self.assertEqual(response.status_code, 202, response.get_data(as_text=True))
        job = response.get_json()['data']
        deadline = time.monotonic() + 10
        while job['status'] not in {'succeeded', 'failed'} and time.monotonic() < deadline:
            time.sleep(0.02)
            job = self.client.get(
                f"/api/v1/analysis-jobs/{job['id']}"
            ).get_json()['data']
        self.assertEqual(job['status'], 'succeeded')
        self.assertEqual(job['result']['summary']['sample_count'], 12)
        self.assertIn('available', job['result']['lda'])
        self.assertFalse(job['result']['arima']['available'])
        self.assertIn('至少需要', job['result']['arima']['reason'])
        self.assertFalse(job['result']['sir']['available'])
        self.assertIn('至少需要', job['result']['sir']['reason'])

    def test_unconfigured_live_collector_pauses_without_fake_data(self):
        csrf = self.login()
        response = self.client.post(
            '/api/v1/collection-jobs',
            json={'keywords': ['人工智能'], 'max_pages': 1},
            headers={'X-CSRF-Token': csrf},
        )
        self.assertEqual(response.status_code, 202)
        job = response.get_json()['data']
        self.assertEqual(job['status'], 'paused')
        self.assertEqual(job['error_code'], 'license_confirmation_required')
        self.assertEqual(job['stats'], {})

    def test_csv_report_can_be_generated_and_downloaded(self):
        csrf = self.login()
        self.import_posts(csrf, count=3)
        response = self.client.post(
            '/api/v1/reports', json={'format': 'csv', 'topic': '产品测试'},
            headers={'X-CSRF-Token': csrf},
        )
        self.assertEqual(response.status_code, 202, response.get_data(as_text=True))
        report = response.get_json()['data']
        self.assertEqual(report['status'], 'succeeded')
        download = self.client.get(f"/api/v1/reports/{report['id']}.csv")
        self.assertEqual(download.status_code, 200)
        self.assertTrue(download.data.startswith(b'\xef\xbb\xbf'))
        download.close()

    def test_pdf_report_can_be_generated_and_downloaded(self):
        csrf = self.login()
        self.import_posts(csrf, count=3)
        response = self.client.post(
            '/api/v1/reports', json={'format': 'pdf', 'topic': '产品测试'},
            headers={'X-CSRF-Token': csrf},
        )
        self.assertEqual(response.status_code, 202, response.get_data(as_text=True))
        report = response.get_json()['data']
        self.assertEqual(report['status'], 'succeeded')
        download = self.client.get(f"/api/v1/reports/{report['id']}.pdf")
        self.assertEqual(download.status_code, 200)
        self.assertTrue(download.data.startswith(b'%PDF-'))
        download.close()

    def test_negative_samples_create_explainable_alert(self):
        csrf = self.login()
        records = [{
            'id': f'negative-{index}', 'text': '用户投诉并质疑项目风险',
            'created_at': '2026-07-01 12:00:00'
        } for index in range(4)]
        response = self.client.post(
            '/api/v1/imports',
            data={
                'topic': '风险话题',
                'file': (io.BytesIO(json.dumps(records, ensure_ascii=False).encode()), 'negative.json'),
            },
            headers={'X-CSRF-Token': csrf},
            content_type='multipart/form-data',
        )
        self.assertEqual(response.status_code, 202)
        alerts = self.client.get('/api/v1/alerts').get_json()['data']
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]['level'], 'high')
        self.assertEqual(alerts[0]['evidence']['sample_count'], 4)
        self.assertIn('人工复核', alerts[0]['message'])

    def test_invalid_filter_and_weak_password_are_rejected(self):
        csrf = self.login()
        response = self.client.post(
            '/api/v1/analysis-jobs',
            json={'type': 'summary', 'start_date': 'bad-date'},
            headers={'X-CSRF-Token': csrf},
        )
        self.assertEqual(response.status_code, 400)
        response = self.client.post(
            '/api/v1/auth/password',
            json={'current_password': 'test-password-123', 'new_password': 'short'},
            headers={'X-CSRF-Token': csrf},
        )
        self.assertEqual(response.status_code, 400)


if __name__ == '__main__':
    unittest.main()
