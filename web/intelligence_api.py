# -*- coding: utf-8 -*-
"""V2 monitoring, event, incident, and evidence-brief APIs."""

from __future__ import annotations

from urllib.parse import urlparse

import config
from flask import Blueprint, request, session

from services.competition_demo import seed_competition_demo
from services.visualization import build_visual_story
from storage.monitor_store import ALERT_STATUSES, MONITOR_STATUSES, get_monitor_store
from storage.product_store import utc_now
from web.product_api import (
    _dispatch,
    _json_body,
    csrf_required,
    error,
    login_required_api,
    success,
)


intelligence = Blueprint('intelligence', __name__)


def _terms(value, name, maximum=20):
    if value is None:
        return []
    if isinstance(value, str):
        value = re_split_terms(value)
    if not isinstance(value, list):
        raise ValueError(f'{name} 必须是字符串或字符串数组')
    result = []
    for item in value:
        term = str(item).strip()
        if term and term not in result:
            result.append(term)
    if len(result) > maximum or any(len(item) > 50 for item in result):
        raise ValueError(f'{name} 最多 {maximum} 项，每项不超过 50 字')
    return result


def re_split_terms(value):
    normalized = str(value).replace('，', ',').replace('\n', ',')
    return [item.strip() for item in normalized.split(',') if item.strip()]


def _http_url(value, name, optional=True):
    text = str(value or '').strip()
    if not text and optional:
        return ''
    parsed = urlparse(text)
    if (
        parsed.scheme not in {'http', 'https'}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or len(text) > 2048
    ):
        raise ValueError(f'{name} 必须是有效的 http/https 地址，且不能包含登录凭据')
    return text


def _source_payload(payload):
    raw_urls = payload.get('feed_urls', [])
    if isinstance(raw_urls, str):
        raw_urls = [line.strip() for line in raw_urls.splitlines() if line.strip()]
    if not isinstance(raw_urls, list) or len(raw_urls) > 10:
        raise ValueError('RSS 地址必须是最多 10 项的数组或逐行文本')
    sources = [{'kind': 'database', 'name': '已入库数据', 'url': ''}]
    for index, raw_url in enumerate(raw_urls):
        url = _http_url(raw_url, 'RSS 地址', optional=False)
        if any(item['kind'] == 'rss' and item['url'] == url for item in sources):
            continue
        sources.append({
            'kind': 'rss',
            'name': f'RSS 订阅 {index + 1}',
            'url': url,
        })
    if bool(payload.get('include_weibo')):
        sources.append({'kind': 'weibo', 'name': '微博关键词采集', 'url': ''})
    return sources


def _monitor_fields(payload, partial=False):
    result = {}
    if not partial or 'name' in payload:
        name = str(payload.get('name', '')).strip()
        if not 2 <= len(name) <= 80:
            raise ValueError('监测项目名称需要 2–80 个字符')
        result['name'] = name
    if not partial or 'description' in payload:
        result['description'] = str(payload.get('description', '')).strip()[:500]
    for key, label in (
        ('keywords', '关注词'),
        ('required_terms', '必须词'),
        ('excluded_terms', '排除词'),
        ('risk_terms', '风险词'),
    ):
        if not partial or key in payload:
            result[key] = _terms(payload.get(key, []), label)
    if not partial or 'semantic_query' in payload:
        result['semantic_query'] = str(payload.get('semantic_query', '')).strip()[:500]
    if not partial or 'interval_minutes' in payload:
        try:
            interval = int(payload.get('interval_minutes', 60))
        except (TypeError, ValueError) as exc:
            raise ValueError('运行间隔必须是整数分钟') from exc
        if not 15 <= interval <= 10080:
            raise ValueError('运行间隔必须在 15 分钟到 7 天之间')
        result['interval_minutes'] = interval
    if not partial or 'negative_threshold' in payload:
        try:
            threshold = float(payload.get('negative_threshold', 50))
        except (TypeError, ValueError) as exc:
            raise ValueError('负面预警阈值必须是数字') from exc
        if not 10 <= threshold <= 100:
            raise ValueError('负面预警阈值必须在 10–100 之间')
        result['negative_threshold'] = threshold
    if not partial or 'spike_threshold' in payload:
        try:
            spike = int(payload.get('spike_threshold', 5))
        except (TypeError, ValueError) as exc:
            raise ValueError('突增预警条数必须是整数') from exc
        if not 2 <= spike <= 1000:
            raise ValueError('突增预警条数必须在 2–1000 之间')
        result['spike_threshold'] = spike
    if not partial or 'webhook_url' in payload:
        result['webhook_url'] = _http_url(
            payload.get('webhook_url'), 'Webhook', optional=True
        ) or None
    return result


@intelligence.get('/api/v2/overview')
@login_required_api
def overview():
    store = get_monitor_store()
    monitors = store.list_monitors()
    summaries = [store.signal_summary(item['id']) for item in monitors]
    events = [
        event for monitor in monitors
        for event in store.list_events(monitor['id'], limit=100)
    ]
    alerts = store.list_alerts(limit=500)
    open_alerts = [
        item for item in alerts
        if item['status'] in {'new', 'acknowledged', 'investigating'}
    ]
    return success({
        'monitors': len(monitors),
        'active_monitors': sum(item['status'] == 'active' for item in monitors),
        'signals': sum(item['total'] for item in summaries),
        'events': len(events),
        'open_alerts': len(open_alerts),
        'high_alerts': sum(
            item['severity'] in {'high', 'critical'} for item in open_alerts
        ),
        'recent_alerts': open_alerts[:5],
        'recent_runs': store.list_runs(limit=5),
        'top_events': sorted(
            events,
            key=lambda item: item['metrics'].get('heat_score', 0),
            reverse=True,
        )[:5],
    })


@intelligence.get('/api/v2/visual-story')
@login_required_api
def visual_story():
    """Return traceable aggregates for the competition visualization view."""
    monitor_id = str(request.args.get('monitor_id', '')).strip()
    store = get_monitor_store()
    monitor = store.get_monitor(monitor_id) if monitor_id else None
    if not monitor:
        return error('invalid_monitor', '请选择有效的监测项目', 400)
    summary = store.signal_summary(monitor_id)
    cap = 3000
    signals = []
    while len(signals) < min(summary['total'], cap):
        page = store.list_signals(
            monitor_id,
            limit=min(500, cap - len(signals)),
            offset=len(signals),
        )
        if not page:
            break
        signals.extend(page)
    return success(build_visual_story(
        monitor,
        signals,
        store.list_events(monitor_id, limit=500),
        store.list_alerts(monitor_id=monitor_id, limit=500),
        total_available=summary['total'],
    ))


@intelligence.post('/api/v2/demo/seed')
@login_required_api
@csrf_required
def competition_demo_seed():
    """Load an explicit synthetic case for deterministic offline judging."""
    return success(seed_competition_demo(), 201)


@intelligence.get('/api/v2/settings')
@login_required_api
def v2_settings():
    return success({
        'llm': {
            'enabled': bool(
                config.LLM_ENABLED and config.LLM_BASE_URL and config.LLM_MODEL
            ),
            'model': config.LLM_MODEL or None,
            'base_url_configured': bool(config.LLM_BASE_URL),
        },
        'scheduler_interval_seconds': config.MONITOR_SCHEDULER_SECONDS,
        'private_network_urls_allowed': config.ALLOW_PRIVATE_NETWORK_URLS,
    })


@intelligence.get('/api/v2/monitors')
@login_required_api
def monitors():
    return success(get_monitor_store().list_monitors())


@intelligence.post('/api/v2/monitors')
@login_required_api
@csrf_required
def create_monitor():
    payload = _json_body()
    if not isinstance(payload, dict):
        return error('invalid_json', '请求体必须是 JSON 对象', 400)
    try:
        fields = _monitor_fields(payload)
        sources = _source_payload(payload)
    except ValueError as exc:
        return error('invalid_monitor', str(exc), 400)
    if not fields['keywords'] and not fields['required_terms']:
        return error(
            'missing_match_rule',
            '至少配置一个关注词或必须词；语义描述目前只用于辅助说明',
            400,
        )
    monitor = get_monitor_store().create_monitor(**fields, sources=sources)
    return success(monitor, 201)


@intelligence.get('/api/v2/monitors/<monitor_id>')
@login_required_api
def monitor_detail(monitor_id):
    store = get_monitor_store()
    monitor = store.get_monitor(monitor_id)
    if not monitor:
        return error('not_found', '监测项目不存在', 404)
    return success({
        **monitor,
        'signal_summary': store.signal_summary(monitor_id),
        'recent_runs': store.list_runs(monitor_id, limit=10),
        'event_count': len(store.list_events(monitor_id, limit=500)),
    })


@intelligence.patch('/api/v2/monitors/<monitor_id>')
@login_required_api
@csrf_required
def update_monitor(monitor_id):
    store = get_monitor_store()
    current = store.get_monitor(monitor_id)
    if not current:
        return error('not_found', '监测项目不存在', 404)
    payload = _json_body()
    if not isinstance(payload, dict):
        return error('invalid_json', '请求体必须是 JSON 对象', 400)
    try:
        fields = _monitor_fields(payload, partial=True)
        prospective_keywords = fields.get('keywords', current['keywords'])
        prospective_required = fields.get(
            'required_terms', current['required_terms']
        )
        if not prospective_keywords and not prospective_required:
            raise ValueError('至少保留一个关注词或必须词')
        monitor = store.update_monitor(monitor_id, **fields)
        if 'feed_urls' in payload or 'include_weibo' in payload:
            source_update = dict(payload)
            source_update.setdefault(
                'feed_urls',
                [
                    source['url']
                    for source in current['sources']
                    if source['kind'] == 'rss'
                ],
            )
            source_update.setdefault(
                'include_weibo',
                any(source['kind'] == 'weibo' for source in current['sources']),
            )
            monitor = store.replace_sources(
                monitor_id, _source_payload(source_update)
            )
    except ValueError as exc:
        return error('invalid_monitor', str(exc), 400)
    return success(monitor)


@intelligence.post('/api/v2/monitors/<monitor_id>/status')
@login_required_api
@csrf_required
def monitor_status(monitor_id):
    payload = _json_body()
    status = str(payload.get('status', '')).strip() if isinstance(payload, dict) else ''
    if status not in MONITOR_STATUSES:
        return error('invalid_status', '状态必须是 active、paused 或 archived', 400)
    store = get_monitor_store()
    if not store.get_monitor(monitor_id):
        return error('not_found', '监测项目不存在', 404)
    return success(store.update_monitor(monitor_id, status=status))


@intelligence.post('/api/v2/monitors/<monitor_id>/run')
@login_required_api
@csrf_required
def run_monitor_now(monitor_id):
    store = get_monitor_store()
    monitor = store.get_monitor(monitor_id)
    if not monitor:
        return error('not_found', '监测项目不存在', 404)
    if monitor['status'] != 'active':
        return error('monitor_paused', '只有启用中的监测项目可以运行', 409)
    active = store.active_run(monitor_id)
    if active:
        return error('run_in_progress', '该监测项目已有运行中的任务', 409, run=active)
    run = store.create_run(monitor_id)
    if _dispatch('services.intelligence.run_monitor', run['id']) is None:
        store.update_run(
            run['id'],
            status='failed',
            progress=100,
            error_code='queue_unavailable',
            error_message='Redis/RQ 不可用',
            finished_at=utc_now(),
        )
        return error('queue_unavailable', '任务队列不可用', 503)
    return success(store.get_run(run['id']), 202)


@intelligence.get('/api/v2/monitor-runs')
@login_required_api
def monitor_runs():
    return success(get_monitor_store().list_runs(
        monitor_id=request.args.get('monitor_id') or None,
        limit=min(request.args.get('limit', 100, type=int) or 100, 200),
    ))


@intelligence.get('/api/v2/monitor-runs/<run_id>')
@login_required_api
def monitor_run_detail(run_id):
    run = get_monitor_store().get_run(run_id)
    return success(run) if run else error('not_found', '监测运行不存在', 404)


@intelligence.get('/api/v2/signals')
@login_required_api
def signals():
    monitor_id = str(request.args.get('monitor_id', '')).strip()
    store = get_monitor_store()
    if not monitor_id or not store.get_monitor(monitor_id):
        return error('invalid_monitor', '请选择有效的监测项目', 400)
    sentiment = request.args.get('sentiment')
    if sentiment not in (None, '', '0', '1'):
        return error('invalid_sentiment', 'sentiment 必须是 0 或 1', 400)
    return success(store.list_signals(
        monitor_id,
        query=request.args.get('q') or None,
        platform=request.args.get('platform') or None,
        sentiment=sentiment,
        event_id=request.args.get('event_id') or None,
        limit=min(request.args.get('limit', 100, type=int) or 100, 500),
        offset=max(request.args.get('offset', 0, type=int) or 0, 0),
    ))


@intelligence.get('/api/v2/events')
@login_required_api
def events():
    monitor_id = str(request.args.get('monitor_id', '')).strip()
    store = get_monitor_store()
    if not monitor_id or not store.get_monitor(monitor_id):
        return error('invalid_monitor', '请选择有效的监测项目', 400)
    return success(store.list_events(monitor_id, limit=200))


@intelligence.get('/api/v2/events/<event_id>')
@login_required_api
def event_detail(event_id):
    store = get_monitor_store()
    event = store.get_event(event_id)
    if not event:
        return error('not_found', '事件不存在', 404)
    return success({
        **event,
        'signals': store.list_signals(
            event['monitor_id'], event_id=event_id, limit=100
        ),
        'latest_brief': store.latest_brief(event_id),
    })


@intelligence.post('/api/v2/events/<event_id>/briefs')
@login_required_api
@csrf_required
def create_event_brief(event_id):
    store = get_monitor_store()
    if not store.get_event(event_id):
        return error('not_found', '事件不存在', 404)
    mode = (
        'llm'
        if config.LLM_ENABLED and config.LLM_BASE_URL and config.LLM_MODEL
        else 'deterministic'
    )
    brief = store.create_brief(
        event_id, mode=mode, model=config.LLM_MODEL if mode == 'llm' else None
    )
    if _dispatch('services.briefing.run_event_brief', brief['id']) is None:
        store.update_brief(
            brief['id'],
            status='failed',
            error_code='queue_unavailable',
            error_message='任务队列不可用',
            finished_at=utc_now(),
        )
        return error('queue_unavailable', '任务队列不可用', 503)
    return success(store.get_brief(brief['id']), 202)


@intelligence.get('/api/v2/briefs/<brief_id>')
@login_required_api
def brief_detail(brief_id):
    brief = get_monitor_store().get_brief(brief_id)
    return success(brief) if brief else error('not_found', '事件简报不存在', 404)


@intelligence.get('/api/v2/alerts')
@login_required_api
def incident_alerts():
    status = request.args.get('status') or None
    if status and status not in ALERT_STATUSES:
        return error('invalid_status', '预警状态无效', 400)
    return success(get_monitor_store().list_alerts(
        monitor_id=request.args.get('monitor_id') or None,
        status=status,
    ))


@intelligence.get('/api/v2/alerts/<alert_id>')
@login_required_api
def incident_alert_detail(alert_id):
    store = get_monitor_store()
    alert = store.get_alert(alert_id)
    if not alert:
        return error('not_found', '预警不存在', 404)
    return success({
        **alert,
        'actions': store.list_alert_actions(alert_id),
    })


@intelligence.post('/api/v2/alerts/<alert_id>/transition')
@login_required_api
@csrf_required
def transition_incident_alert(alert_id):
    payload = _json_body()
    if not isinstance(payload, dict):
        return error('invalid_json', '请求体必须是 JSON 对象', 400)
    status = str(payload.get('status', '')).strip()
    note = str(payload.get('note', '')).strip()
    store = get_monitor_store()
    if not store.get_alert(alert_id):
        return error('not_found', '预警不存在', 404)
    try:
        alert = store.transition_alert(
            alert_id,
            status,
            note=note,
            actor=session.get('username', 'admin'),
        )
    except ValueError as exc:
        return error('invalid_transition', str(exc), 409)
    return success(alert)


@intelligence.get('/api/v2/deliveries')
@login_required_api
def deliveries():
    return success(get_monitor_store().list_deliveries(
        monitor_id=request.args.get('monitor_id') or None,
        limit=100,
    ))


def register_intelligence_routes(app):
    app.register_blueprint(intelligence)
