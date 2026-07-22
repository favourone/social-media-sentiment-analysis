# -*- coding: utf-8 -*-
"""Repeatable local benchmark for the V1 product workflow."""

import io
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


def percentile(values, fraction):
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return ordered[index]


def measure(client, path, repetitions=30):
    samples = []
    for _ in range(repetitions):
        started = time.perf_counter()
        response = client.get(path)
        elapsed = (time.perf_counter() - started) * 1000
        if response.status_code != 200:
            raise RuntimeError(f'{path} returned {response.status_code}')
        samples.append(elapsed)
    return {
        'runs': repetitions,
        'p50_ms': round(statistics.median(samples), 2),
        'p95_ms': round(percentile(samples, 0.95), 2),
        'min_ms': round(min(samples), 2),
        'max_ms': round(max(samples), 2),
    }


def main():
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        config.PRODUCT_DB_PATH = str(root / 'product.db')
        config.REPORT_DIR = str(root / 'reports')
        config.IMPORT_DIR = str(root / 'imports')
        config.ADMIN_USERNAME = 'benchmark'
        config.ADMIN_PASSWORD = 'benchmark-password-only'
        config.APP_SECRET_KEY = 'benchmark-secret-only'
        config.TASK_QUEUE_MODE = 'inline'

        from storage.product_store import get_product_store, reset_store_cache
        reset_store_cache()
        get_product_store()
        from web.app import app
        app.config['TESTING'] = True
        client = app.test_client()
        login = client.post('/api/v1/auth/login', json={
            'username': 'benchmark', 'password': 'benchmark-password-only'
        })
        csrf = login.get_json()['data']['csrf_token']
        records = [{
            'id': f'benchmark-{index}',
            'text': f'人工智能产品测试记录 {index} 支持透明分析',
            'author': f'user-{index % 50}',
            'created_at': f'2026-07-{index % 20 + 1:02d} 10:00:00',
            'likes': index % 100,
        } for index in range(1000)]
        body = json.dumps(records, ensure_ascii=False).encode('utf-8')
        started = time.perf_counter()
        imported = client.post(
            '/api/v1/imports',
            data={
                'topic': '性能夹具',
                'file': (io.BytesIO(body), 'benchmark.json'),
            },
            headers={'X-CSRF-Token': csrf},
            content_type='multipart/form-data',
        )
        import_ms = (time.perf_counter() - started) * 1000
        if imported.status_code != 202:
            raise RuntimeError(imported.get_data(as_text=True))
        job = imported.get_json()['data']
        output = {
            'environment': 'Windows, Conda cv, Python 3.10.20, Flask test_client, SQLite WAL',
            'fixture_records': len(records),
            'import': {
                'elapsed_ms': round(import_ms, 2),
                'status': job['status'],
                'inserted_posts': job['stats'].get('inserted_posts'),
                'duplicate_posts': job['stats'].get('duplicate_posts'),
            },
            'collection_jobs_api': measure(client, '/api/v1/collection-jobs'),
            'legacy_overview_api': measure(client, '/api/overview'),
            'limitations': [
                '单进程测试客户端，不含网络、Redis、RQ 和浏览器成本',
                '一次导入观察，不是稳定吞吐量或生产 SLA',
            ],
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
