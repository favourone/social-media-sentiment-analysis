# -*- coding: utf-8 -*-
"""Deterministic monitoring, event clustering, and alert generation for V2."""

from __future__ import annotations

import hashlib
import logging
import math
import re
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import jieba.analyse
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from crawler.adapters import CollectorError, CollectorPaused, get_collector
from services.delivery import deliver_webhook
from storage.monitor_store import get_monitor_store
from storage.product_store import get_product_store, utc_now


LOGGER = logging.getLogger(__name__)
MAX_CLUSTER_SIGNALS = 500
CLUSTER_THRESHOLD = 0.38


def _timestamp(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalized_terms(values):
    result = []
    for value in values or []:
        term = str(value).strip().lower()
        if term and term not in result:
            result.append(term)
    return result


def evaluate_post(post, monitor):
    """Return an auditable rule match or ``None`` for one collected post."""
    text = f"{post.get('text', '')} {post.get('topic', '')}".lower()
    keywords = _normalized_terms(monitor.get('keywords'))
    required = _normalized_terms(monitor.get('required_terms'))
    excluded = _normalized_terms(monitor.get('excluded_terms'))
    risk = _normalized_terms(monitor.get('risk_terms'))

    excluded_hits = [term for term in excluded if term in text]
    if excluded_hits:
        return None
    if required and not all(term in text for term in required):
        return None
    keyword_hits = [term for term in keywords if term in text]
    if keywords and not keyword_hits:
        return None
    risk_hits = [term for term in risk if term in text]
    matched = list(dict.fromkeys(keyword_hits + required))
    keyword_score = len(keyword_hits) / max(len(keywords), 1) if keywords else 1
    required_score = 1 if required else 0
    risk_bonus = min(len(risk_hits) * 0.08, 0.24)
    relevance = min(1, 0.72 * keyword_score + 0.2 * required_score + risk_bonus)
    return {
        'post_id': post['id'],
        'relevance_score': round(relevance * 100, 1),
        'matched_terms': matched,
        'risk_terms': risk_hits,
    }


def _extract_terms(text, limit=8):
    terms = jieba.analyse.extract_tags(text, topK=limit * 2, withWeight=False)
    cleaned = []
    for term in terms:
        value = re.sub(r'\s+', '', term).strip().lower()
        if len(value) >= 2 and value not in cleaned:
            cleaned.append(value)
        if len(cleaned) >= limit:
            break
    return cleaned


def _event_title(signals, top_terms):
    representative = max(
        signals,
        key=lambda item: (
            sum(int(value or 0) for value in item.get('engagement', {}).values()),
            len(item.get('text', '')),
        ),
    )
    text = re.sub(r'\s+', ' ', representative.get('text', '')).strip()
    sentence = re.split(r'[。！？!?；;\n]', text, maxsplit=1)[0].strip()
    if 8 <= len(sentence) <= 56:
        return sentence
    if top_terms:
        return ' · '.join(top_terms[:4])
    return text[:48] or '未命名事件'


def _event_metrics(signals, top_terms):
    timestamps = [
        _timestamp(item.get('published_at') or item.get('fetched_at'))
        for item in signals
    ]
    timestamps = [item for item in timestamps if item]
    reference = max(timestamps) if timestamps else datetime.now(timezone.utc)
    recent_start = reference - timedelta(hours=24)
    previous_start = reference - timedelta(hours=48)
    recent = sum(1 for item in timestamps if item >= recent_start)
    previous = sum(1 for item in timestamps if previous_start <= item < recent_start)
    if previous:
        growth_ratio = round(recent / previous, 2)
        trend = 'rising' if growth_ratio >= 1.5 else (
            'declining' if growth_ratio <= 0.67 else 'stable'
        )
    else:
        growth_ratio = None
        trend = 'emerging' if recent else 'stable'

    negative = sum(1 for item in signals if item.get('sentiment') == 0)
    platforms = sorted({item.get('platform') or 'unknown' for item in signals})
    engagement = sum(
        max(int(value or 0), 0)
        for item in signals
        for value in item.get('engagement', {}).values()
    )
    risk_terms = sorted({
        term for item in signals for term in item.get('risk_terms', [])
    })
    heat = min(
        100,
        round(
            math.log1p(len(signals)) * 18
            + len(platforms) * 10
            + math.log1p(engagement) * 5
            + min(recent, 10) * 2,
            1,
        ),
    )
    return {
        'sample_count': len(signals),
        'negative_count': negative,
        'negative_ratio': round(negative / max(len(signals), 1) * 100, 1),
        'source_count': len(platforms),
        'platforms': platforms,
        'total_engagement': engagement,
        'recent_24h': recent,
        'previous_24h': previous,
        'growth_ratio': growth_ratio,
        'trend': trend,
        'heat_score': heat,
        'top_terms': top_terms,
        'risk_terms': risk_terms,
        'evidence_ids': [item['id'] for item in signals[:5]],
        'confidence': 'limited' if len(signals) < 5 else 'moderate',
        'reference_time': reference.replace(microsecond=0).isoformat(),
    }


def cluster_signals(monitor_id, signals, threshold=CLUSTER_THRESHOLD):
    """Group related signals with deterministic character n-gram similarity."""
    signals = list(signals[:MAX_CLUSTER_SIGNALS])
    if not signals:
        return []
    texts = [
        re.sub(r'\s+', ' ', str(item.get('text', ''))).strip()
        for item in signals
    ]
    parents = list(range(len(signals)))

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left, right):
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parents[root_right] = root_left

    if len(signals) > 1 and any(texts):
        try:
            vectors = TfidfVectorizer(
                analyzer='char', ngram_range=(2, 4), min_df=1, max_features=6000
            ).fit_transform(texts)
            similarities = cosine_similarity(vectors)
            for left in range(len(signals)):
                for right in range(left + 1, len(signals)):
                    if similarities[left, right] >= threshold:
                        union(left, right)
        except ValueError:
            LOGGER.info('Event clustering fell back to singleton groups')

    groups = defaultdict(list)
    for index, signal in enumerate(signals):
        groups[find(index)].append(signal)

    events = []
    for grouped in groups.values():
        combined = '\n'.join(item.get('text', '') for item in grouped)
        top_terms = _extract_terms(combined)
        stable_material = '|'.join(top_terms[:5])
        if not stable_material:
            stable_material = '|'.join(
                sorted(f"{item.get('platform')}:{item.get('source_id')}" for item in grouped)
            )
        fingerprint = hashlib.sha256(stable_material.encode('utf-8')).hexdigest()[:20]
        event_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL, f'event:{monitor_id}:{fingerprint}'
        ))
        dates = [
            item.get('published_at') or item.get('fetched_at')
            for item in grouped
            if item.get('published_at') or item.get('fetched_at')
        ]
        metrics = _event_metrics(grouped, top_terms)
        title = _event_title(grouped, top_terms)
        summary = (
            f"聚合 {len(grouped)} 条相关信号，来自 {metrics['source_count']} 个来源；"
            f"负面筛查占比 {metrics['negative_ratio']}%，热度评分 {metrics['heat_score']}。"
        )
        events.append({
            'id': event_id,
            'fingerprint': fingerprint,
            'title': title,
            'summary': summary,
            'metrics': metrics,
            'post_ids': [item['id'] for item in grouped],
            'first_seen': min(dates) if dates else None,
            'last_seen': max(dates) if dates else None,
        })
    events.sort(
        key=lambda item: (
            item['metrics']['heat_score'],
            item.get('last_seen') or '',
        ),
        reverse=True,
    )
    return events


def build_alert_specs(monitor, events, signals):
    """Create explainable, configurable alert candidates for current events."""
    by_id = {item['id']: item for item in signals}
    specs = []
    for event in events:
        metrics = event['metrics']
        evidence_signals = [
            by_id[item_id]
            for item_id in event['post_ids'][:5]
            if item_id in by_id
        ]
        evidence = {
            'event_id': event['id'],
            'signal_ids': [item['id'] for item in evidence_signals],
            'source_urls': [
                item['source_url'] for item in evidence_signals if item.get('source_url')
            ],
            'sample_count': metrics['sample_count'],
            'reference_time': metrics['reference_time'],
            'confidence': metrics['confidence'],
        }
        base = f"{monitor['id']}:{event['fingerprint']}"

        if metrics['risk_terms']:
            terms = '、'.join(metrics['risk_terms'][:6])
            specs.append({
                'event_id': event['id'],
                'kind': 'risk_keyword',
                'severity': 'high' if len(metrics['risk_terms']) >= 2 else 'medium',
                'title': f"风险词命中 · {event['title']}",
                'message': f'当前事件命中风险词：{terms}。请回到原文核实语境。',
                'evidence': {**evidence, 'risk_terms': metrics['risk_terms']},
                'dedupe_key': f'{base}:risk_keyword',
            })

        if (
            metrics['sample_count'] >= 3
            and metrics['negative_ratio'] >= monitor['negative_threshold']
        ):
            specs.append({
                'event_id': event['id'],
                'kind': 'negative_ratio',
                'severity': 'high' if metrics['negative_ratio'] >= 70 else 'medium',
                'title': f"负面比例异常 · {event['title']}",
                'message': (
                    f"当前 {metrics['sample_count']} 条样本中负面筛查占比 "
                    f"{metrics['negative_ratio']}%，达到项目阈值 "
                    f"{monitor['negative_threshold']}%。"
                ),
                'evidence': {**evidence, 'negative_ratio': metrics['negative_ratio']},
                'dedupe_key': f'{base}:negative_ratio',
            })

        is_spike = (
            metrics['recent_24h'] >= monitor['spike_threshold']
            and (
                metrics['previous_24h'] == 0
                or (metrics['growth_ratio'] or 0) >= 2
            )
        )
        if is_spike:
            specs.append({
                'event_id': event['id'],
                'kind': 'volume_spike',
                'severity': (
                    'high'
                    if metrics['recent_24h'] >= monitor['spike_threshold'] * 2
                    else 'medium'
                ),
                'title': f"讨论量突增 · {event['title']}",
                'message': (
                    f"最近 24 小时出现 {metrics['recent_24h']} 条相关信号，"
                    f"前一窗口为 {metrics['previous_24h']} 条。"
                ),
                'evidence': {
                    **evidence,
                    'recent_24h': metrics['recent_24h'],
                    'previous_24h': metrics['previous_24h'],
                    'growth_ratio': metrics['growth_ratio'],
                },
                'dedupe_key': f'{base}:volume_spike',
            })
    return specs


def _collect_feed_source(product_store, monitor, source):
    job = product_store.create_collection_job(
        'rss',
        'rss',
        monitor['keywords'] or [monitor['name']],
        {'feed_urls': [source['url']], 'monitor_id': monitor['id']},
    )
    product_store.update_collection_job(
        job['id'], status='running', progress=1, started_at=utc_now()
    )
    try:
        result = get_collector('rss').collect(job)
        inserted = product_store.insert_posts(job['id'], result['posts'])
        stats = {
            'received_posts': len(result['posts']),
            'inserted_posts': inserted,
            'duplicate_posts': len(result['posts']) - inserted,
            'rejected_records': result.get('rejected', 0),
            'source_file_count': len(result.get('source_files', [])),
        }
        product_store.update_collection_job(
            job['id'],
            status='succeeded',
            progress=100,
            stats_json=stats,
            finished_at=utc_now(),
        )
        return stats
    except CollectorError as exc:
        product_store.update_collection_job(
            job['id'],
            status='paused' if isinstance(exc, CollectorPaused) else 'failed',
            error_code=exc.code,
            error_message=str(exc),
            finished_at=utc_now(),
        )
        raise


def _collect_weibo_source(product_store, monitor):
    job = product_store.create_collection_job(
        'weibo',
        'mediacrawler',
        monitor['keywords'] or [monitor['name']],
        {'max_pages': 3, 'monitor_id': monitor['id']},
    )
    from services.tasks import run_collection_job
    run_collection_job(job['id'])
    completed = product_store.get_collection_job(job['id'])
    if completed['status'] not in {'succeeded'}:
        code = completed.get('error_code') or 'collector_failed'
        message = completed.get('error_message') or '微博采集没有成功完成'
        if completed['status'] == 'paused':
            raise CollectorPaused(code, message)
        raise CollectorError(code, message)
    return completed['stats']


def run_monitor(run_id):
    """Execute one monitor from source acquisition through alert delivery."""
    monitor_store = get_monitor_store()
    product_store = get_product_store()
    run = monitor_store.get_run(run_id)
    if not run:
        return None
    monitor = monitor_store.get_monitor(run['monitor_id'])
    if not monitor:
        monitor_store.update_run(
            run_id,
            status='failed',
            progress=100,
            error_code='monitor_not_found',
            error_message='监测项目不存在',
            finished_at=utc_now(),
        )
        return None
    monitor_store.update_run(
        run_id, status='running', progress=5, started_at=utc_now()
    )
    source_stats = []
    source_errors = []
    try:
        enabled_sources = [
            item for item in monitor['sources'] if item.get('enabled')
        ]
        for source in enabled_sources:
            try:
                if source['kind'] == 'rss':
                    source_stats.append({
                        'source_id': source['id'],
                        'kind': 'rss',
                        **_collect_feed_source(product_store, monitor, source),
                    })
                elif source['kind'] == 'weibo':
                    source_stats.append({
                        'source_id': source['id'],
                        'kind': 'weibo',
                        **_collect_weibo_source(product_store, monitor),
                    })
            except CollectorError as exc:
                source_errors.append({
                    'source_id': source['id'],
                    'kind': source['kind'],
                    'code': exc.code,
                    'message': str(exc),
                })
        monitor_store.update_run(run_id, progress=35)

        posts = product_store.list_posts(limit=20000)
        matches = []
        for post in posts:
            match = evaluate_post(post, monitor)
            if match:
                matches.append(match)
        monitor_store.replace_monitor_items(monitor['id'], matches)
        monitor_store.update_run(run_id, progress=55)

        signals = monitor_store.list_signals(
            monitor['id'], limit=MAX_CLUSTER_SIGNALS
        )
        events = cluster_signals(monitor['id'], signals)
        monitor_store.replace_events(monitor['id'], events)
        monitor_store.update_run(run_id, progress=75)

        active_keys = []
        created_alerts = []
        specs = build_alert_specs(monitor, events, signals)
        for spec in specs:
            active_keys.append(spec['dedupe_key'])
            alert_id, created = monitor_store.upsert_alert(
                monitor_id=monitor['id'], **spec
            )
            if created:
                created_alerts.append(monitor_store.get_alert(alert_id))
        auto_resolved = monitor_store.close_stale_alerts(
            monitor['id'], active_keys
        )
        for alert in created_alerts:
            if monitor.get('webhook_url'):
                deliver_webhook(monitor_store, monitor, alert)

        stats = {
            'sources_total': len(enabled_sources),
            'sources_succeeded': len(source_stats),
            'source_errors': source_errors,
            'matched_signals': len(matches),
            'events': len(events),
            'active_alerts': len(specs),
            'new_alerts': len(created_alerts),
            'auto_resolved_alerts': auto_resolved,
        }
        status = 'succeeded'
        error_code = None
        error_message = None
        if source_errors and not matches:
            status = 'paused'
            error_code = 'sources_need_attention'
            error_message = '数据源需要人工处理，且当前没有匹配信号'
        monitor_store.update_run(
            run_id,
            status=status,
            progress=100,
            stats_json=stats,
            error_code=error_code,
            error_message=error_message,
            finished_at=utc_now(),
        )
        return monitor_store.get_run(run_id)
    except Exception:
        LOGGER.exception('Unhandled monitor run failure: %s', run_id)
        monitor_store.update_run(
            run_id,
            status='failed',
            progress=100,
            stats_json={'source_stats': source_stats, 'source_errors': source_errors},
            error_code='monitor_run_failed',
            error_message='监测运行发生内部错误，请查看服务日志',
            finished_at=utc_now(),
        )
        raise
    finally:
        monitor_store.mark_monitor_ran(monitor['id'])
