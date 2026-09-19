# -*- coding: utf-8 -*-
"""SQLite persistence for the product workflow.

The legacy dashboard can continue to read JSON or MongoDB, while product
records, jobs and reports use a transactional local database.  Every method
opens a short-lived connection so the web process and one RQ worker can share
the same database safely in WAL mode.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

import config


JOB_STATUSES = {
    'pending', 'running', 'paused', 'succeeded', 'failed', 'cancelled'
}


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _load(value, default):
    if value in (None, ''):
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


class ProductStore:
    """Small, auditable persistence layer backed by SQLite."""

    def __init__(self, path=None):
        self.path = os.path.abspath(path or config.PRODUCT_DB_PATH)

    @contextmanager
    def connect(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys = ON')
        connection.execute('PRAGMA busy_timeout = 10000')
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self, admin_username=None, admin_password=None):
        with self.connect() as connection:
            connection.execute('PRAGMA journal_mode = WAL')
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS admins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_login TEXT
                );

                CREATE TABLE IF NOT EXISTS collection_jobs (
                    id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    adapter TEXT NOT NULL,
                    keywords_json TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    stats_json TEXT NOT NULL DEFAULT '{}',
                    error_code TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_collection_jobs_created
                    ON collection_jobs(created_at DESC);

                CREATE TABLE IF NOT EXISTS posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    author TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL,
                    published_at TEXT,
                    engagement_json TEXT NOT NULL DEFAULT '{}',
                    fetched_at TEXT NOT NULL,
                    job_id TEXT,
                    source_url TEXT,
                    topic TEXT,
                    sentiment INTEGER,
                    sentiment_method TEXT,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(platform, source_id),
                    FOREIGN KEY(job_id) REFERENCES collection_jobs(id)
                );
                CREATE INDEX IF NOT EXISTS idx_posts_topic_time
                    ON posts(topic, published_at DESC);

                CREATE TABLE IF NOT EXISTS comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    post_source_id TEXT NOT NULL,
                    author TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL,
                    published_at TEXT,
                    fetched_at TEXT NOT NULL,
                    job_id TEXT,
                    sentiment INTEGER,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(platform, source_id),
                    FOREIGN KEY(job_id) REFERENCES collection_jobs(id)
                );

                CREATE TABLE IF NOT EXISTS analysis_jobs (
                    id TEXT PRIMARY KEY,
                    analysis_type TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    result_json TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_analysis_jobs_created
                    ON analysis_jobs(created_at DESC);

                CREATE TABLE IF NOT EXISTS alerts (
                    id TEXT PRIMARY KEY,
                    level TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    message TEXT NOT NULL,
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    acknowledged INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_alerts_created
                    ON alerts(created_at DESC);

                CREATE TABLE IF NOT EXISTS reports (
                    id TEXT PRIMARY KEY,
                    format TEXT NOT NULL,
                    filters_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    file_path TEXT,
                    sample_count INTEGER NOT NULL DEFAULT 0,
                    error_code TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_reports_created
                    ON reports(created_at DESC);

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
        if admin_password:
            self.ensure_admin(admin_username or 'admin', admin_password)
        return self

    # -------------------- administrator --------------------

    def ensure_admin(self, username, password):
        now = utc_now()
        with self.connect() as connection:
            exists = connection.execute(
                'SELECT id FROM admins WHERE username = ?', (username,)
            ).fetchone()
            if exists:
                return False
            connection.execute(
                'INSERT INTO admins(username, password_hash, created_at, updated_at) '
                'VALUES (?, ?, ?, ?)',
                (username, generate_password_hash(password), now, now),
            )
        return True

    def admin_count(self):
        with self.connect() as connection:
            return connection.execute('SELECT COUNT(*) FROM admins').fetchone()[0]

    def authenticate(self, username, password):
        with self.connect() as connection:
            row = connection.execute(
                'SELECT * FROM admins WHERE username = ?', (username,)
            ).fetchone()
            if not row or not check_password_hash(row['password_hash'], password):
                return None
            now = utc_now()
            connection.execute(
                'UPDATE admins SET last_login = ?, updated_at = ? WHERE id = ?',
                (now, now, row['id']),
            )
            return {'id': row['id'], 'username': row['username'], 'last_login': now}

    def change_password(self, admin_id, current_password, new_password):
        with self.connect() as connection:
            row = connection.execute(
                'SELECT password_hash FROM admins WHERE id = ?', (admin_id,)
            ).fetchone()
            if not row or not check_password_hash(row['password_hash'], current_password):
                return False
            connection.execute(
                'UPDATE admins SET password_hash = ?, updated_at = ? WHERE id = ?',
                (generate_password_hash(new_password), utc_now(), admin_id),
            )
        return True

    # -------------------- collection jobs --------------------

    def create_collection_job(self, platform, adapter, keywords, params=None):
        job_id = str(uuid.uuid4())
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO collection_jobs(
                    id, platform, adapter, keywords_json, params_json,
                    status, progress, stats_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', 0, '{}', ?, ?)""",
                (job_id, platform, adapter, _dump(keywords), _dump(params or {}), now, now),
            )
        return self.get_collection_job(job_id)

    def get_collection_job(self, job_id):
        with self.connect() as connection:
            row = connection.execute(
                'SELECT * FROM collection_jobs WHERE id = ?', (job_id,)
            ).fetchone()
        return self._collection_job(row)

    def list_collection_jobs(self, limit=100):
        with self.connect() as connection:
            rows = connection.execute(
                'SELECT * FROM collection_jobs ORDER BY created_at DESC LIMIT ?',
                (limit,),
            ).fetchall()
        return [self._collection_job(row) for row in rows]

    def update_collection_job(self, job_id, **fields):
        allowed = {
            'status', 'progress', 'stats_json', 'error_code', 'error_message',
            'started_at', 'finished_at'
        }
        values = {}
        for key, value in fields.items():
            if key not in allowed:
                continue
            values[key] = _dump(value) if key == 'stats_json' else value
        if 'status' in values and values['status'] not in JOB_STATUSES:
            raise ValueError('invalid collection job status')
        values['updated_at'] = utc_now()
        assignments = ', '.join(f'{key} = ?' for key in values)
        with self.connect() as connection:
            connection.execute(
                f'UPDATE collection_jobs SET {assignments} WHERE id = ?',
                (*values.values(), job_id),
            )
        return self.get_collection_job(job_id)

    @staticmethod
    def _collection_job(row):
        if row is None:
            return None
        data = dict(row)
        data['keywords'] = _load(data.pop('keywords_json'), [])
        data['params'] = _load(data.pop('params_json'), {})
        data['stats'] = _load(data.pop('stats_json'), {})
        return data

    # -------------------- collected records --------------------

    def insert_posts(self, job_id, posts):
        inserted = 0
        with self.connect() as connection:
            for post in posts:
                cursor = connection.execute(
                    """INSERT OR IGNORE INTO posts(
                        platform, source_id, author, text, published_at,
                        engagement_json, fetched_at, job_id, source_url, topic,
                        sentiment, sentiment_method, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        post['platform'], post['source_id'], post.get('author', ''),
                        post['text'], post.get('published_at'),
                        _dump(post.get('engagement', {})),
                        post.get('fetched_at') or utc_now(), job_id,
                        post.get('source_url'), post.get('topic'),
                        post.get('sentiment'), post.get('sentiment_method'),
                        _dump(post.get('raw', {})),
                    ),
                )
                inserted += max(cursor.rowcount, 0)
        return inserted

    def insert_comments(self, job_id, comments):
        inserted = 0
        with self.connect() as connection:
            for comment in comments:
                cursor = connection.execute(
                    """INSERT OR IGNORE INTO comments(
                        platform, source_id, post_source_id, author, text,
                        published_at, fetched_at, job_id, sentiment, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        comment['platform'], comment['source_id'],
                        comment['post_source_id'], comment.get('author', ''),
                        comment['text'], comment.get('published_at'),
                        comment.get('fetched_at') or utc_now(), job_id,
                        comment.get('sentiment'), _dump(comment.get('raw', {})),
                    ),
                )
                inserted += max(cursor.rowcount, 0)
        return inserted

    def list_posts(self, topic=None, start_date=None, end_date=None, limit=10000):
        clauses = []
        values = []
        if topic and topic != 'all':
            clauses.append('topic = ?')
            values.append(topic)
        if start_date:
            clauses.append("substr(published_at, 1, 10) >= ?")
            values.append(start_date)
        if end_date:
            clauses.append("substr(published_at, 1, 10) <= ?")
            values.append(end_date)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ''
        values.append(limit)
        with self.connect() as connection:
            rows = connection.execute(
                f'SELECT * FROM posts {where} ORDER BY published_at DESC LIMIT ?', values
            ).fetchall()
        result = []
        for row in rows:
            data = dict(row)
            data['engagement'] = _load(data.pop('engagement_json'), {})
            data['raw'] = _load(data.pop('raw_json'), {})
            result.append(data)
        return result

    def list_legacy_posts(self, limit=200000):
        result = []
        for row in self.list_posts(limit=limit):
            engagement = row['engagement']
            result.append({
                'post_id': row['source_id'],
                'user_name': row['author'],
                'text': row['text'],
                'created_at': row['published_at'] or row['fetched_at'],
                'topic': row['topic'] or '未分类',
                'sentiment': row['sentiment'] if row['sentiment'] in (0, 1) else 1,
                'likes': int(engagement.get('likes', 0) or 0),
                'reposts': int(engagement.get('reposts', 0) or 0),
                'comment_count': int(engagement.get('comments', 0) or 0),
                'source': '微博（采集）' if row['platform'] == 'weibo' else row['platform'],
                'source_url': row['source_url'],
                'data_kind': 'observed_public_data',
                'sentiment_method': row['sentiment_method'],
            })
        return result

    def list_legacy_comments(self, limit=1000000):
        with self.connect() as connection:
            rows = connection.execute(
                'SELECT * FROM comments ORDER BY published_at DESC LIMIT ?', (limit,)
            ).fetchall()
        return [{
            'comment_id': row['source_id'],
            'post_id': row['post_source_id'],
            'user_name': row['author'],
            'text': row['text'],
            'created_at': row['published_at'] or row['fetched_at'],
            'sentiment': row['sentiment'] if row['sentiment'] in (0, 1) else 1,
            'source': '微博（采集）' if row['platform'] == 'weibo' else row['platform'],
            'data_kind': 'observed_public_data',
        } for row in rows]

    def daily_series(self, topic=None):
        clauses = ["published_at IS NOT NULL", "published_at != ''"]
        values = []
        if topic and topic != 'all':
            clauses.append('topic = ?')
            values.append(topic)
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT substr(published_at, 1, 10) AS day, topic, COUNT(*) AS value
                    FROM posts WHERE {' AND '.join(clauses)}
                    GROUP BY day, topic ORDER BY day""",
                values,
            ).fetchall()
        return [
            {'date': row['day'], 'topic': row['topic'] or '未分类', 'value': row['value']}
            for row in rows if row['day']
        ]

    # -------------------- analysis, alerts and reports --------------------

    def create_analysis_job(self, analysis_type, params=None):
        job_id = str(uuid.uuid4())
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO analysis_jobs(
                    id, analysis_type, params_json, status, progress,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'pending', 0, ?, ?)""",
                (job_id, analysis_type, _dump(params or {}), now, now),
            )
        return self.get_analysis_job(job_id)

    def get_analysis_job(self, job_id):
        with self.connect() as connection:
            row = connection.execute(
                'SELECT * FROM analysis_jobs WHERE id = ?', (job_id,)
            ).fetchone()
        return self._analysis_job(row)

    def list_analysis_jobs(self, limit=100):
        with self.connect() as connection:
            rows = connection.execute(
                'SELECT * FROM analysis_jobs ORDER BY created_at DESC LIMIT ?',
                (limit,),
            ).fetchall()
        return [self._analysis_job(row) for row in rows]

    def update_analysis_job(self, job_id, *, expected_statuses=None, **fields):
        allowed = {
            'status', 'progress', 'result_json', 'error_code', 'error_message',
            'started_at', 'finished_at'
        }
        values = {}
        for key, value in fields.items():
            if key not in allowed:
                continue
            values[key] = _dump(value) if key == 'result_json' else value
        if 'status' in values and values['status'] not in JOB_STATUSES:
            raise ValueError('invalid analysis job status')
        values['updated_at'] = utc_now()
        assignments = ', '.join(f'{key} = ?' for key in values)
        expected = tuple(expected_statuses) if expected_statuses is not None else None
        if expected is not None and not expected:
            return None
        where = 'id = ?'
        parameters = (*values.values(), job_id)
        if expected is not None:
            where += f" AND status IN ({', '.join('?' for _ in expected)})"
            parameters += expected
        with self.connect() as connection:
            cursor = connection.execute(
                f'UPDATE analysis_jobs SET {assignments} WHERE {where}',
                parameters,
            )
            updated = cursor.rowcount > 0
        if expected is not None and not updated:
            return None
        return self.get_analysis_job(job_id)

    @staticmethod
    def _analysis_job(row):
        if row is None:
            return None
        data = dict(row)
        data['params'] = _load(data.pop('params_json'), {})
        data['result'] = _load(data.pop('result_json'), None)
        return data

    def replace_topic_alert(self, topic, level, message, evidence):
        alert_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'alert:{topic}'))
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO alerts(
                    id, level, topic, message, evidence_json, acknowledged, created_at
                ) VALUES (?, ?, ?, ?, ?, 0, ?)
                ON CONFLICT(id) DO UPDATE SET
                    level = excluded.level,
                    topic = excluded.topic,
                    message = excluded.message,
                    evidence_json = excluded.evidence_json,
                    acknowledged = 0,
                    created_at = excluded.created_at""",
                (alert_id, level, topic, message, _dump(evidence), utc_now()),
            )
        return alert_id

    def list_alerts(self, limit=100):
        with self.connect() as connection:
            rows = connection.execute(
                'SELECT * FROM alerts ORDER BY created_at DESC LIMIT ?', (limit,)
            ).fetchall()
        result = []
        for row in rows:
            data = dict(row)
            data['evidence'] = _load(data.pop('evidence_json'), {})
            data['acknowledged'] = bool(data['acknowledged'])
            result.append(data)
        return result

    def create_report(self, report_format, filters=None):
        report_id = str(uuid.uuid4())
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO reports(
                    id, format, filters_json, status, created_at, updated_at
                ) VALUES (?, ?, ?, 'pending', ?, ?)""",
                (report_id, report_format, _dump(filters or {}), now, now),
            )
        return self.get_report(report_id)

    def get_report(self, report_id):
        with self.connect() as connection:
            row = connection.execute(
                'SELECT * FROM reports WHERE id = ?', (report_id,)
            ).fetchone()
        return self._report(row)

    def list_reports(self, limit=100):
        with self.connect() as connection:
            rows = connection.execute(
                'SELECT * FROM reports ORDER BY created_at DESC LIMIT ?', (limit,)
            ).fetchall()
        return [self._report(row) for row in rows]

    def update_report(self, report_id, **fields):
        allowed = {
            'status', 'file_path', 'sample_count', 'error_code',
            'error_message', 'finished_at'
        }
        values = {key: value for key, value in fields.items() if key in allowed}
        if 'status' in values and values['status'] not in JOB_STATUSES:
            raise ValueError('invalid report status')
        values['updated_at'] = utc_now()
        assignments = ', '.join(f'{key} = ?' for key in values)
        with self.connect() as connection:
            connection.execute(
                f'UPDATE reports SET {assignments} WHERE id = ?',
                (*values.values(), report_id),
            )
        return self.get_report(report_id)

    @staticmethod
    def _report(row):
        if row is None:
            return None
        data = dict(row)
        data['filters'] = _load(data.pop('filters_json'), {})
        return data


_stores = {}
_store_lock = threading.Lock()


def get_product_store(path=None):
    resolved = os.path.abspath(path or config.PRODUCT_DB_PATH)
    with _store_lock:
        store = _stores.get(resolved)
        if store is None:
            store = ProductStore(resolved).initialize(
                config.ADMIN_USERNAME,
                config.ADMIN_PASSWORD,
            )
            _stores[resolved] = store
    return store


def reset_store_cache():
    """Test helper; existing database files are not removed."""
    with _store_lock:
        _stores.clear()
