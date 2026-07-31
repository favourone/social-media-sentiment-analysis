# -*- coding: utf-8 -*-
"""Persistence for the V2 monitoring and incident workflow.

V1 tables remain untouched so existing installations can upgrade in place.
The V2 repository shares the same SQLite database and connection policy as
``ProductStore`` while keeping the monitoring domain independently testable.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from storage.product_store import JOB_STATUSES, _dump, _load, get_product_store, utc_now


MONITOR_STATUSES = {'active', 'paused', 'archived'}
ALERT_STATUSES = {'new', 'acknowledged', 'investigating', 'resolved', 'false_positive'}
EVENT_STATUSES = {'open', 'watching', 'resolved'}
ALERT_TRANSITIONS = {
    'new': {'acknowledged', 'investigating', 'resolved', 'false_positive'},
    'acknowledged': {'investigating', 'resolved', 'false_positive', 'new'},
    'investigating': {'resolved', 'false_positive', 'acknowledged'},
    'resolved': {'new'},
    'false_positive': {'new'},
}


def _future(minutes):
    return (
        datetime.now(timezone.utc) + timedelta(minutes=max(int(minutes), 1))
    ).replace(microsecond=0).isoformat()


class MonitorStore:
    """V2 repository backed by an initialized :class:`ProductStore`."""

    def __init__(self, product_store=None):
        self.product_store = product_store or get_product_store()
        self.initialize()

    @property
    def path(self):
        return self.product_store.path

    def connect(self):
        return self.product_store.connect()

    def initialize(self):
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS monitors (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    keywords_json TEXT NOT NULL DEFAULT '[]',
                    required_terms_json TEXT NOT NULL DEFAULT '[]',
                    excluded_terms_json TEXT NOT NULL DEFAULT '[]',
                    risk_terms_json TEXT NOT NULL DEFAULT '[]',
                    semantic_query TEXT NOT NULL DEFAULT '',
                    interval_minutes INTEGER NOT NULL DEFAULT 60,
                    status TEXT NOT NULL DEFAULT 'active',
                    negative_threshold REAL NOT NULL DEFAULT 50,
                    spike_threshold INTEGER NOT NULL DEFAULT 5,
                    webhook_url TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_run_at TEXT,
                    next_run_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_monitors_schedule
                    ON monitors(status, next_run_at);

                CREATE TABLE IF NOT EXISTS monitor_sources (
                    id TEXT PRIMARY KEY,
                    monitor_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    url TEXT NOT NULL DEFAULT '',
                    config_json TEXT NOT NULL DEFAULT '{}',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    UNIQUE(monitor_id, kind, url),
                    FOREIGN KEY(monitor_id) REFERENCES monitors(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_monitor_sources_monitor
                    ON monitor_sources(monitor_id, enabled);

                CREATE TABLE IF NOT EXISTS monitor_runs (
                    id TEXT PRIMARY KEY,
                    monitor_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    stats_json TEXT NOT NULL DEFAULT '{}',
                    error_code TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    FOREIGN KEY(monitor_id) REFERENCES monitors(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_monitor_runs_created
                    ON monitor_runs(monitor_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS monitor_items (
                    monitor_id TEXT NOT NULL,
                    post_id INTEGER NOT NULL,
                    relevance_score REAL NOT NULL DEFAULT 0,
                    matched_terms_json TEXT NOT NULL DEFAULT '[]',
                    risk_terms_json TEXT NOT NULL DEFAULT '[]',
                    linked_at TEXT NOT NULL,
                    PRIMARY KEY(monitor_id, post_id),
                    FOREIGN KEY(monitor_id) REFERENCES monitors(id) ON DELETE CASCADE,
                    FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_monitor_items_post
                    ON monitor_items(post_id);

                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    monitor_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'open',
                    first_seen TEXT,
                    last_seen TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(monitor_id, fingerprint),
                    FOREIGN KEY(monitor_id) REFERENCES monitors(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_events_monitor_seen
                    ON events(monitor_id, last_seen DESC);

                CREATE TABLE IF NOT EXISTS event_items (
                    event_id TEXT NOT NULL,
                    post_id INTEGER NOT NULL,
                    PRIMARY KEY(event_id, post_id),
                    FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE,
                    FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS incident_alerts (
                    id TEXT PRIMARY KEY,
                    monitor_id TEXT NOT NULL,
                    event_id TEXT,
                    kind TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'new',
                    title TEXT NOT NULL,
                    message TEXT NOT NULL,
                    evidence_json TEXT NOT NULL DEFAULT '{}',
                    dedupe_key TEXT NOT NULL UNIQUE,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    resolved_at TEXT,
                    FOREIGN KEY(monitor_id) REFERENCES monitors(id) ON DELETE CASCADE,
                    FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_incident_alerts_state
                    ON incident_alerts(status, severity, last_seen DESC);

                CREATE TABLE IF NOT EXISTS alert_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alert_id TEXT NOT NULL,
                    from_status TEXT,
                    to_status TEXT NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    actor TEXT NOT NULL DEFAULT 'system',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(alert_id) REFERENCES incident_alerts(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_alert_actions_alert
                    ON alert_actions(alert_id, created_at);

                CREATE TABLE IF NOT EXISTS deliveries (
                    id TEXT PRIMARY KEY,
                    monitor_id TEXT NOT NULL,
                    alert_id TEXT,
                    channel TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 1,
                    response_code INTEGER,
                    error_message TEXT,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    finished_at TEXT,
                    FOREIGN KEY(monitor_id) REFERENCES monitors(id) ON DELETE CASCADE,
                    FOREIGN KEY(alert_id) REFERENCES incident_alerts(id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_deliveries_monitor
                    ON deliveries(monitor_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS event_briefs (
                    id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'deterministic',
                    model TEXT,
                    result_json TEXT,
                    evidence_json TEXT NOT NULL DEFAULT '[]',
                    error_code TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    finished_at TEXT,
                    FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_event_briefs_event
                    ON event_briefs(event_id, created_at DESC);
                """
            )
        return self

    # -------------------- monitors and sources --------------------

    def create_monitor(
        self,
        name,
        description='',
        keywords=None,
        required_terms=None,
        excluded_terms=None,
        risk_terms=None,
        semantic_query='',
        interval_minutes=60,
        negative_threshold=50,
        spike_threshold=5,
        webhook_url=None,
        sources=None,
    ):
        monitor_id = str(uuid.uuid4())
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO monitors(
                    id, name, description, keywords_json, required_terms_json,
                    excluded_terms_json, risk_terms_json, semantic_query,
                    interval_minutes, status, negative_threshold, spike_threshold,
                    webhook_url, created_at, updated_at, next_run_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)""",
                (
                    monitor_id,
                    name,
                    description,
                    _dump(keywords or []),
                    _dump(required_terms or []),
                    _dump(excluded_terms or []),
                    _dump(risk_terms or []),
                    semantic_query,
                    int(interval_minutes),
                    float(negative_threshold),
                    int(spike_threshold),
                    webhook_url or None,
                    now,
                    now,
                    now,
                ),
            )
            normalized_sources = sources or [
                {'kind': 'database', 'name': '已入库数据', 'url': ''}
            ]
            for source in normalized_sources:
                connection.execute(
                    """INSERT INTO monitor_sources(
                        id, monitor_id, kind, name, url, config_json, enabled, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(uuid.uuid4()),
                        monitor_id,
                        source['kind'],
                        source.get('name') or source['kind'],
                        source.get('url', ''),
                        _dump(source.get('config', {})),
                        1 if source.get('enabled', True) else 0,
                        now,
                    ),
                )
        return self.get_monitor(monitor_id)

    def get_monitor(self, monitor_id):
        with self.connect() as connection:
            row = connection.execute(
                'SELECT * FROM monitors WHERE id = ?', (monitor_id,)
            ).fetchone()
            sources = connection.execute(
                """SELECT * FROM monitor_sources
                   WHERE monitor_id = ? ORDER BY kind, name""",
                (monitor_id,),
            ).fetchall()
        if row is None:
            return None
        return self._monitor(row, sources)

    def list_monitors(self, include_archived=False):
        where = '' if include_archived else "WHERE status != 'archived'"
        with self.connect() as connection:
            rows = connection.execute(
                f'SELECT * FROM monitors {where} ORDER BY created_at DESC'
            ).fetchall()
            sources = connection.execute(
                'SELECT * FROM monitor_sources ORDER BY kind, name'
            ).fetchall()
        grouped = {}
        for source in sources:
            grouped.setdefault(source['monitor_id'], []).append(source)
        return [self._monitor(row, grouped.get(row['id'], [])) for row in rows]

    @staticmethod
    def _monitor(row, sources):
        data = dict(row)
        for key in ('keywords', 'required_terms', 'excluded_terms', 'risk_terms'):
            data[key] = _load(data.pop(f'{key}_json'), [])
        data['sources'] = []
        for source in sources:
            item = dict(source)
            item['enabled'] = bool(item['enabled'])
            item['config'] = _load(item.pop('config_json'), {})
            data['sources'].append(item)
        return data

    def update_monitor(self, monitor_id, **fields):
        json_fields = {
            'keywords', 'required_terms', 'excluded_terms', 'risk_terms'
        }
        allowed = {
            'name', 'description', 'keywords', 'required_terms', 'excluded_terms',
            'risk_terms', 'semantic_query', 'interval_minutes', 'status',
            'negative_threshold', 'spike_threshold', 'webhook_url',
        }
        values = {}
        for key, value in fields.items():
            if key not in allowed:
                continue
            column = f'{key}_json' if key in json_fields else key
            values[column] = _dump(value) if key in json_fields else value
        if 'status' in values and values['status'] not in MONITOR_STATUSES:
            raise ValueError('invalid monitor status')
        if not values:
            return self.get_monitor(monitor_id)
        values['updated_at'] = utc_now()
        if 'interval_minutes' in values:
            values['next_run_at'] = _future(values['interval_minutes'])
        assignments = ', '.join(f'{key} = ?' for key in values)
        with self.connect() as connection:
            connection.execute(
                f'UPDATE monitors SET {assignments} WHERE id = ?',
                (*values.values(), monitor_id),
            )
        return self.get_monitor(monitor_id)

    def replace_sources(self, monitor_id, sources):
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                'DELETE FROM monitor_sources WHERE monitor_id = ?', (monitor_id,)
            )
            for source in sources:
                connection.execute(
                    """INSERT INTO monitor_sources(
                        id, monitor_id, kind, name, url, config_json, enabled, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(uuid.uuid4()),
                        monitor_id,
                        source['kind'],
                        source.get('name') or source['kind'],
                        source.get('url', ''),
                        _dump(source.get('config', {})),
                        1 if source.get('enabled', True) else 0,
                        now,
                    ),
                )
        return self.get_monitor(monitor_id)

    def list_due_monitors(self, limit=20):
        now = utc_now()
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT id FROM monitors
                   WHERE status = 'active'
                     AND (next_run_at IS NULL OR next_run_at <= ?)
                   ORDER BY next_run_at LIMIT ?""",
                (now, limit),
            ).fetchall()
        return [self.get_monitor(row['id']) for row in rows]

    def mark_monitor_ran(self, monitor_id):
        monitor = self.get_monitor(monitor_id)
        if not monitor:
            return None
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """UPDATE monitors SET last_run_at = ?, next_run_at = ?, updated_at = ?
                   WHERE id = ?""",
                (now, _future(monitor['interval_minutes']), now, monitor_id),
            )
        return self.get_monitor(monitor_id)

    # -------------------- runs and signal links --------------------

    def create_run(self, monitor_id):
        run_id = str(uuid.uuid4())
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO monitor_runs(
                    id, monitor_id, status, progress, stats_json, created_at, updated_at
                ) VALUES (?, ?, 'pending', 0, '{}', ?, ?)""",
                (run_id, monitor_id, now, now),
            )
        return self.get_run(run_id)

    def get_run(self, run_id):
        with self.connect() as connection:
            row = connection.execute(
                'SELECT * FROM monitor_runs WHERE id = ?', (run_id,)
            ).fetchone()
        return self._run(row)

    def list_runs(self, monitor_id=None, limit=100):
        if monitor_id:
            query = (
                'SELECT * FROM monitor_runs WHERE monitor_id = ? '
                'ORDER BY created_at DESC LIMIT ?'
            )
            params = (monitor_id, limit)
        else:
            query = 'SELECT * FROM monitor_runs ORDER BY created_at DESC LIMIT ?'
            params = (limit,)
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._run(row) for row in rows]

    def active_run(self, monitor_id):
        with self.connect() as connection:
            row = connection.execute(
                """SELECT * FROM monitor_runs
                   WHERE monitor_id = ? AND status IN ('pending', 'running')
                   ORDER BY created_at DESC LIMIT 1""",
                (monitor_id,),
            ).fetchone()
        return self._run(row)

    @staticmethod
    def _run(row):
        if row is None:
            return None
        data = dict(row)
        data['stats'] = _load(data.pop('stats_json'), {})
        return data

    def update_run(self, run_id, **fields):
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
            raise ValueError('invalid monitor run status')
        values['updated_at'] = utc_now()
        assignments = ', '.join(f'{key} = ?' for key in values)
        with self.connect() as connection:
            connection.execute(
                f'UPDATE monitor_runs SET {assignments} WHERE id = ?',
                (*values.values(), run_id),
            )
        return self.get_run(run_id)

    def replace_monitor_items(self, monitor_id, matches):
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                'DELETE FROM monitor_items WHERE monitor_id = ?', (monitor_id,)
            )
            for match in matches:
                connection.execute(
                    """INSERT INTO monitor_items(
                        monitor_id, post_id, relevance_score, matched_terms_json,
                        risk_terms_json, linked_at
                    ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        monitor_id,
                        int(match['post_id']),
                        float(match.get('relevance_score', 0)),
                        _dump(match.get('matched_terms', [])),
                        _dump(match.get('risk_terms', [])),
                        now,
                    ),
                )
        return len(matches)

    def list_signals(
        self,
        monitor_id,
        query=None,
        platform=None,
        sentiment=None,
        event_id=None,
        limit=100,
        offset=0,
    ):
        clauses = ['mi.monitor_id = ?']
        values = [monitor_id]
        if query:
            clauses.append('(p.text LIKE ? OR p.author LIKE ? OR p.topic LIKE ?)')
            pattern = f'%{query}%'
            values.extend([pattern, pattern, pattern])
        if platform:
            clauses.append('p.platform = ?')
            values.append(platform)
        if sentiment in (0, 1, '0', '1'):
            clauses.append('p.sentiment = ?')
            values.append(int(sentiment))
        if event_id:
            clauses.append('ei.event_id = ?')
            values.append(event_id)
        values.extend([min(max(int(limit), 1), 500), max(int(offset), 0)])
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT p.*, mi.relevance_score, mi.matched_terms_json,
                           mi.risk_terms_json, e.id AS event_id,
                           e.title AS event_title
                    FROM monitor_items mi
                    JOIN posts p ON p.id = mi.post_id
                    LEFT JOIN event_items ei
                      ON ei.post_id = p.id
                     AND EXISTS (
                        SELECT 1 FROM events linked_event
                        WHERE linked_event.id = ei.event_id
                          AND linked_event.monitor_id = mi.monitor_id
                     )
                    LEFT JOIN events e
                      ON e.id = ei.event_id AND e.monitor_id = mi.monitor_id
                    WHERE {' AND '.join(clauses)}
                    ORDER BY COALESCE(p.published_at, p.fetched_at) DESC
                    LIMIT ? OFFSET ?""",
                values,
            ).fetchall()
        return [self._signal(row) for row in rows]

    @staticmethod
    def _signal(row):
        data = dict(row)
        data['engagement'] = _load(data.pop('engagement_json'), {})
        data['raw'] = _load(data.pop('raw_json'), {})
        data['matched_terms'] = _load(data.pop('matched_terms_json'), [])
        data['risk_terms'] = _load(data.pop('risk_terms_json'), [])
        return data

    def signal_summary(self, monitor_id):
        with self.connect() as connection:
            row = connection.execute(
                """SELECT COUNT(*) AS total,
                          SUM(CASE WHEN p.sentiment = 0 THEN 1 ELSE 0 END) AS negative,
                          COUNT(DISTINCT p.platform) AS platforms
                   FROM monitor_items mi JOIN posts p ON p.id = mi.post_id
                   WHERE mi.monitor_id = ?""",
                (monitor_id,),
            ).fetchone()
        total = int(row['total'] or 0)
        negative = int(row['negative'] or 0)
        return {
            'total': total,
            'negative': negative,
            'negative_ratio': round(negative / max(total, 1) * 100, 1),
            'platforms': int(row['platforms'] or 0),
        }

    # -------------------- events --------------------

    def replace_events(self, monitor_id, events):
        now = utc_now()
        with self.connect() as connection:
            incoming_ids = {event['id'] for event in events}
            existing_rows = connection.execute(
                'SELECT id FROM events WHERE monitor_id = ?', (monitor_id,)
            ).fetchall()
            for event in events:
                connection.execute(
                    """INSERT INTO events(
                        id, monitor_id, fingerprint, title, summary, metrics_json,
                        status, first_seen, last_seen, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        title = excluded.title,
                        summary = excluded.summary,
                        metrics_json = excluded.metrics_json,
                        first_seen = excluded.first_seen,
                        last_seen = excluded.last_seen,
                        updated_at = excluded.updated_at""",
                    (
                        event['id'],
                        monitor_id,
                        event['fingerprint'],
                        event['title'],
                        event.get('summary', ''),
                        _dump(event.get('metrics', {})),
                        event.get('status', 'open'),
                        event.get('first_seen'),
                        event.get('last_seen'),
                        now,
                        now,
                    ),
                )
                connection.execute(
                    'DELETE FROM event_items WHERE event_id = ?', (event['id'],)
                )
                for post_id in event.get('post_ids', []):
                    connection.execute(
                        'INSERT INTO event_items(event_id, post_id) VALUES (?, ?)',
                        (event['id'], int(post_id)),
                    )
            for row in existing_rows:
                if row['id'] not in incoming_ids:
                    connection.execute(
                        'DELETE FROM events WHERE id = ?', (row['id'],)
                    )
        return len(events)

    def list_events(self, monitor_id, limit=100):
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT e.*, COUNT(ei.post_id) AS item_count
                   FROM events e LEFT JOIN event_items ei ON ei.event_id = e.id
                   WHERE e.monitor_id = ?
                   GROUP BY e.id
                   ORDER BY json_extract(e.metrics_json, '$.heat_score') DESC,
                            e.last_seen DESC LIMIT ?""",
                (monitor_id, limit),
            ).fetchall()
        return [self._event(row) for row in rows]

    def get_event(self, event_id):
        with self.connect() as connection:
            row = connection.execute(
                """SELECT e.*, COUNT(ei.post_id) AS item_count
                   FROM events e LEFT JOIN event_items ei ON ei.event_id = e.id
                   WHERE e.id = ? GROUP BY e.id""",
                (event_id,),
            ).fetchone()
        return self._event(row)

    @staticmethod
    def _event(row):
        if row is None:
            return None
        data = dict(row)
        data['metrics'] = _load(data.pop('metrics_json'), {})
        return data

    # -------------------- incident alerts --------------------

    def upsert_alert(
        self,
        monitor_id,
        event_id,
        kind,
        severity,
        title,
        message,
        evidence,
        dedupe_key,
    ):
        now = utc_now()
        alert_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'v2-alert:{dedupe_key}'))
        with self.connect() as connection:
            existing = connection.execute(
                'SELECT id FROM incident_alerts WHERE dedupe_key = ?',
                (dedupe_key,),
            ).fetchone()
            connection.execute(
                """INSERT INTO incident_alerts(
                    id, monitor_id, event_id, kind, severity, status, title,
                    message, evidence_json, dedupe_key, first_seen, last_seen,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, 'new', ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(dedupe_key) DO UPDATE SET
                    event_id = excluded.event_id,
                    severity = excluded.severity,
                    title = excluded.title,
                    message = excluded.message,
                    evidence_json = excluded.evidence_json,
                    last_seen = excluded.last_seen,
                    updated_at = excluded.updated_at""",
                (
                    alert_id,
                    monitor_id,
                    event_id,
                    kind,
                    severity,
                    title,
                    message,
                    _dump(evidence),
                    dedupe_key,
                    now,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                'SELECT id FROM incident_alerts WHERE dedupe_key = ?',
                (dedupe_key,),
            ).fetchone()
        return row['id'], existing is None

    def close_stale_alerts(self, monitor_id, active_keys):
        active_keys = set(active_keys)
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT id, status, dedupe_key FROM incident_alerts
                   WHERE monitor_id = ?
                     AND status IN ('new', 'acknowledged', 'investigating')""",
                (monitor_id,),
            ).fetchall()
            now = utc_now()
            closed = 0
            for row in rows:
                if row['dedupe_key'] in active_keys:
                    continue
                connection.execute(
                    """UPDATE incident_alerts
                       SET status = 'resolved', resolved_at = ?, updated_at = ?
                       WHERE id = ?""",
                    (now, now, row['id']),
                )
                connection.execute(
                    """INSERT INTO alert_actions(
                        alert_id, from_status, to_status, note, actor, created_at
                    ) VALUES (?, ?, 'resolved', ?, 'system', ?)""",
                    (
                        row['id'],
                        row['status'],
                        '触发条件已不再满足，系统自动解决',
                        now,
                    ),
                )
                closed += 1
        return closed

    def list_alerts(self, monitor_id=None, status=None, limit=200):
        clauses = []
        values = []
        if monitor_id:
            clauses.append('a.monitor_id = ?')
            values.append(monitor_id)
        if status:
            clauses.append('a.status = ?')
            values.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ''
        values.append(min(max(int(limit), 1), 500))
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT a.*, m.name AS monitor_name, e.title AS event_title
                    FROM incident_alerts a
                    JOIN monitors m ON m.id = a.monitor_id
                    LEFT JOIN events e ON e.id = a.event_id
                    {where}
                    ORDER BY
                      CASE a.status
                        WHEN 'new' THEN 0 WHEN 'investigating' THEN 1
                        WHEN 'acknowledged' THEN 2 ELSE 3 END,
                      CASE a.severity
                        WHEN 'critical' THEN 0 WHEN 'high' THEN 1
                        WHEN 'medium' THEN 2 ELSE 3 END,
                      a.last_seen DESC LIMIT ?""",
                values,
            ).fetchall()
        return [self._alert(row) for row in rows]

    def get_alert(self, alert_id):
        with self.connect() as connection:
            row = connection.execute(
                """SELECT a.*, m.name AS monitor_name, e.title AS event_title
                   FROM incident_alerts a
                   JOIN monitors m ON m.id = a.monitor_id
                   LEFT JOIN events e ON e.id = a.event_id
                   WHERE a.id = ?""",
                (alert_id,),
            ).fetchone()
        return self._alert(row)

    @staticmethod
    def _alert(row):
        if row is None:
            return None
        data = dict(row)
        data['evidence'] = _load(data.pop('evidence_json'), {})
        return data

    def transition_alert(self, alert_id, to_status, note='', actor='admin'):
        if to_status not in ALERT_STATUSES:
            raise ValueError('invalid alert status')
        with self.connect() as connection:
            row = connection.execute(
                'SELECT status FROM incident_alerts WHERE id = ?', (alert_id,)
            ).fetchone()
            if row is None:
                return None
            from_status = row['status']
            if to_status == from_status:
                return self.get_alert(alert_id)
            if to_status not in ALERT_TRANSITIONS[from_status]:
                raise ValueError(f'cannot transition {from_status} to {to_status}')
            now = utc_now()
            resolved_at = now if to_status in {'resolved', 'false_positive'} else None
            connection.execute(
                """UPDATE incident_alerts
                   SET status = ?, resolved_at = ?, updated_at = ? WHERE id = ?""",
                (to_status, resolved_at, now, alert_id),
            )
            connection.execute(
                """INSERT INTO alert_actions(
                    alert_id, from_status, to_status, note, actor, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)""",
                (alert_id, from_status, to_status, note[:1000], actor, now),
            )
        return self.get_alert(alert_id)

    def list_alert_actions(self, alert_id):
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM alert_actions
                   WHERE alert_id = ? ORDER BY created_at""",
                (alert_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    # -------------------- delivery audit --------------------

    def create_delivery(self, monitor_id, alert_id, channel, destination, payload):
        delivery_id = str(uuid.uuid4())
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO deliveries(
                    id, monitor_id, alert_id, channel, destination, status,
                    payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
                (
                    delivery_id,
                    monitor_id,
                    alert_id,
                    channel,
                    destination,
                    _dump(payload),
                    utc_now(),
                ),
            )
        return delivery_id

    def finish_delivery(
        self, delivery_id, status, response_code=None, error_message=None
    ):
        with self.connect() as connection:
            connection.execute(
                """UPDATE deliveries
                   SET status = ?, response_code = ?, error_message = ?, finished_at = ?
                   WHERE id = ?""",
                (
                    status,
                    response_code,
                    error_message[:1000] if error_message else None,
                    utc_now(),
                    delivery_id,
                ),
            )

    def list_deliveries(self, monitor_id=None, limit=100):
        if monitor_id:
            query = (
                'SELECT * FROM deliveries WHERE monitor_id = ? '
                'ORDER BY created_at DESC LIMIT ?'
            )
            params = (monitor_id, limit)
        else:
            query = 'SELECT * FROM deliveries ORDER BY created_at DESC LIMIT ?'
            params = (limit,)
        with self.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        result = []
        for row in rows:
            data = dict(row)
            data['payload'] = _load(data.pop('payload_json'), {})
            result.append(data)
        return result

    # -------------------- evidence briefs --------------------

    def create_brief(self, event_id, mode='deterministic', model=None):
        brief_id = str(uuid.uuid4())
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO event_briefs(
                    id, event_id, status, mode, model, evidence_json,
                    created_at, updated_at
                ) VALUES (?, ?, 'pending', ?, ?, '[]', ?, ?)""",
                (brief_id, event_id, mode, model, now, now),
            )
        return self.get_brief(brief_id)

    def get_brief(self, brief_id):
        with self.connect() as connection:
            row = connection.execute(
                'SELECT * FROM event_briefs WHERE id = ?', (brief_id,)
            ).fetchone()
        return self._brief(row)

    def latest_brief(self, event_id):
        with self.connect() as connection:
            row = connection.execute(
                """SELECT * FROM event_briefs WHERE event_id = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (event_id,),
            ).fetchone()
        return self._brief(row)

    @staticmethod
    def _brief(row):
        if row is None:
            return None
        data = dict(row)
        data['result'] = _load(data.pop('result_json'), None)
        data['evidence'] = _load(data.pop('evidence_json'), [])
        return data

    def update_brief(self, brief_id, **fields):
        allowed = {
            'status', 'mode', 'model', 'result_json', 'evidence_json',
            'error_code', 'error_message', 'finished_at'
        }
        values = {}
        for key, value in fields.items():
            if key not in allowed:
                continue
            values[key] = _dump(value) if key in {'result_json', 'evidence_json'} else value
        if 'status' in values and values['status'] not in JOB_STATUSES:
            raise ValueError('invalid brief status')
        values['updated_at'] = utc_now()
        assignments = ', '.join(f'{key} = ?' for key in values)
        with self.connect() as connection:
            connection.execute(
                f'UPDATE event_briefs SET {assignments} WHERE id = ?',
                (*values.values(), brief_id),
            )
        return self.get_brief(brief_id)


def get_monitor_store(product_store=None):
    return MonitorStore(product_store=product_store)
