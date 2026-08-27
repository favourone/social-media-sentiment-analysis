# -*- coding: utf-8 -*-
"""Evidence-preserving aggregates for the competition visualization story.

The module deliberately returns chart-ready values rather than chart markup.
Every aggregate can be traced back to stored signals, and demo records remain
explicitly labelled so that a presentation can never confuse them with
observed platform data.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from services.intelligence import algorithm_versions


SOURCE_LABELS = {
    'weibo': '微博公开内容',
    'rss': 'RSS / 媒体',
    'news': '新闻媒体',
    'campus_forum': '校园论坛',
    'demo': '演示数据',
}


def _parse_time(value):
    text = str(value or '').strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        try:
            parsed = datetime.strptime(text, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _signal_time(signal):
    return _parse_time(signal.get('published_at') or signal.get('fetched_at'))


def _engagement(signal):
    values = signal.get('engagement') or {}
    return sum(
        max(int(values.get(name, 0) or 0), 0)
        for name in ('likes', 'comments', 'reposts')
    )


def _bin_hours(start, end):
    span_hours = max((end - start).total_seconds() / 3600, 0)
    if span_hours <= 48:
        return 6
    if span_hours <= 24 * 21:
        return 24
    return 24 * 7


def _floor_time(value, hours):
    epoch = int(value.timestamp())
    size = hours * 3600
    return datetime.fromtimestamp(epoch - epoch % size, timezone.utc)


def _iso(value):
    return value.replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _timeline(signals):
    dated = [(signal, _signal_time(signal)) for signal in signals]
    dated = [(signal, when) for signal, when in dated if when is not None]
    if not dated:
        return [], None, None, None
    start = min(when for _, when in dated)
    end = max(when for _, when in dated)
    hours = _bin_hours(start, end)
    cursor = _floor_time(start, hours)
    finish = _floor_time(end, hours)
    bins = []
    by_start = {}
    while cursor <= finish:
        item = {
            'start': _iso(cursor),
            'end': _iso(cursor + timedelta(hours=hours)),
            'total': 0,
            'negative': 0,
            'risk': 0,
            'engagement': 0,
            'event_count': 0,
        }
        bins.append(item)
        by_start[cursor] = item
        cursor += timedelta(hours=hours)
    event_sets = defaultdict(set)
    for signal, when in dated:
        key = _floor_time(when, hours)
        item = by_start[key]
        item['total'] += 1
        item['negative'] += int(signal.get('sentiment') == 0)
        item['risk'] += int(bool(signal.get('risk_terms')))
        item['engagement'] += _engagement(signal)
        if signal.get('event_id'):
            event_sets[key].add(signal['event_id'])
    for key, values in event_sets.items():
        by_start[key]['event_count'] = len(values)
    return bins, start, end, hours


def _source_distribution(signals):
    grouped = defaultdict(lambda: {'count': 0, 'negative': 0, 'engagement': 0})
    for signal in signals:
        key = str(signal.get('platform') or 'unknown')
        grouped[key]['count'] += 1
        grouped[key]['negative'] += int(signal.get('sentiment') == 0)
        grouped[key]['engagement'] += _engagement(signal)
    total = max(len(signals), 1)
    return [
        {
            'platform': platform,
            'label': SOURCE_LABELS.get(platform, platform),
            'count': values['count'],
            'share': round(values['count'] / total * 100, 1),
            'negative_ratio': round(
                values['negative'] / max(values['count'], 1) * 100, 1
            ),
            'engagement': values['engagement'],
        }
        for platform, values in sorted(
            grouped.items(), key=lambda item: item[1]['count'], reverse=True
        )
    ]


def _event_views(events, signals, timeline):
    signals_by_event = defaultdict(list)
    for signal in signals:
        if signal.get('event_id'):
            signals_by_event[signal['event_id']].append(signal)
    bin_index = {item['start']: index for index, item in enumerate(timeline)}
    result = []
    for event in events:
        members = signals_by_event.get(event['id'], [])
        if not members:
            continue
        negative = sum(item.get('sentiment') == 0 for item in members)
        platforms = Counter(str(item.get('platform') or 'unknown') for item in members)
        risk_terms = Counter(
            term for item in members for term in (item.get('risk_terms') or [])
        )
        counts = [0] * len(timeline)
        for member in members:
            when = _signal_time(member)
            if when is None or not timeline:
                continue
            hours = max(
                int((_parse_time(timeline[0]['end']) - _parse_time(timeline[0]['start'])).total_seconds() / 3600),
                1,
            )
            key = _iso(_floor_time(when, hours))
            if key in bin_index:
                counts[bin_index[key]] += 1
        metrics = event.get('metrics') or {}
        result.append({
            'id': event['id'],
            'title': event.get('title') or '未命名事件',
            'status': event.get('status') or 'open',
            'first_seen': event.get('first_seen'),
            'last_seen': event.get('last_seen'),
            'sample_count': len(members),
            'negative_ratio': round(negative / max(len(members), 1) * 100, 1),
            'source_count': len(platforms),
            'sources': [
                {
                    'platform': platform,
                    'label': SOURCE_LABELS.get(platform, platform),
                    'count': count,
                }
                for platform, count in platforms.most_common()
            ],
            'risk_terms': [
                {'term': term, 'count': count}
                for term, count in risk_terms.most_common(5)
            ],
            'engagement': sum(_engagement(item) for item in members),
            'heat_score': float(metrics.get('heat_score', 0) or 0),
            'trend': metrics.get('trend', 'stable'),
            'cohesion_score': metrics.get('cohesion_score'),
            'representative_id': metrics.get('representative_id'),
            'anchor_terms': metrics.get('anchor_terms') or metrics.get('top_terms', [])[:6],
            'sentiment_method_counts': metrics.get('sentiment_method_counts', {}),
            'quality_warning_counts': metrics.get('quality_warning_counts', {}),
            'algorithm_version': metrics.get('algorithm_version'),
            'series': counts,
        })
    return sorted(
        result,
        key=lambda item: (item['heat_score'], item['sample_count']),
        reverse=True,
    )


def _evidence_graph(monitor, sources, event_views):
    root_id = f"monitor:{monitor['id']}"
    nodes = [{
        'id': root_id,
        'label': monitor.get('name') or '监测项目',
        'type': 'monitor',
        'value': sum(item['count'] for item in sources),
    }]
    edges = []
    for source in sources:
        node_id = f"source:{source['platform']}"
        nodes.append({
            'id': node_id,
            'label': source['label'],
            'type': 'source',
            'value': source['count'],
        })
        edges.append({'source': root_id, 'target': node_id, 'value': source['count']})
    for event in event_views[:6]:
        node_id = f"event:{event['id']}"
        nodes.append({
            'id': node_id,
            'label': event['title'],
            'type': 'event',
            'value': event['sample_count'],
            'event_id': event['id'],
            'heat_score': event['heat_score'],
        })
        for source in event['sources']:
            edges.append({
                'source': f"source:{source['platform']}",
                'target': node_id,
                'value': source['count'],
            })
    return {
        'nodes': nodes,
        'edges': edges,
        'meaning': '连线表示数据来源与聚合事件之间的证据归属，不代表转发或因果传播路径。',
    }


def _demo_mode(signals):
    demo_count = sum(
        (item.get('raw') or {}).get('data_kind') == 'synthetic_competition_demo'
        for item in signals
    )
    if demo_count and demo_count == len(signals):
        return 'demo'
    if demo_count:
        return 'mixed'
    return 'observed'


def _algorithm_meta(signal):
    return (signal.get('raw') or {}).get('_algorithm') or {}


def _quality_flags(signal):
    return [str(item) for item in (_algorithm_meta(signal).get('quality_flags') or [])]


def _sentiment_meta(signal):
    return _algorithm_meta(signal).get('sentiment') or {}


def _sentiment_score_distribution(signals):
    scores = []
    confidence = Counter()
    for signal in signals:
        sentiment = _sentiment_meta(signal)
        if isinstance(sentiment.get('score'), (int, float)):
            scores.append(sentiment['score'])
        confidence.update([sentiment.get('confidence') or '未记录'])
    result = {'confidence': dict(confidence.most_common())}
    if scores:
        result.update({
            'min': min(scores),
            'max': max(scores),
            'average': round(sum(scores) / len(scores), 1),
        })
    return result


def build_visual_story(monitor, signals, events, alerts, *, total_available=None):
    """Build an auditable, chart-ready story for one monitor."""
    timeline, start, end, bin_hours = _timeline(signals)
    sources = _source_distribution(signals)
    event_views = _event_views(events, signals, timeline)
    negative = sum(item.get('sentiment') == 0 for item in signals)
    nonnegative = sum(item.get('sentiment') == 1 for item in signals)
    unknown = len(signals) - negative - nonnegative
    risk = sum(bool(item.get('risk_terms')) for item in signals)
    method_counts = Counter(
        str(
            _sentiment_meta(item).get('method')
            or item.get('sentiment_method')
            or '未记录'
        ) for item in signals
    )
    quality_counts = Counter(flag for item in signals for flag in _quality_flags(item))
    score_distribution = _sentiment_score_distribution(signals)
    peak = max(timeline, key=lambda item: item['total'], default=None)
    open_alerts = [
        item for item in alerts
        if item.get('status') in {'new', 'acknowledged', 'investigating'}
    ]
    high_alerts = sum(
        item.get('severity') in {'high', 'critical'} for item in open_alerts
    )
    data_mode = _demo_mode(signals)
    total_available = int(total_available if total_available is not None else len(signals))
    insights = []
    if event_views:
        top = event_views[0]
        insights.append(
            f"当前最高热度事件为“{top['title']}”，纳入 {top['sample_count']} 条证据信号。"
        )
    if peak:
        insights.append(
            f"峰值时间窗记录 {peak['total']} 条信号，其中 {peak['negative']} 条被标记为负面筛查。"
        )
    if sources:
        insights.append(
            f"证据覆盖 {len(sources)} 类来源；占比最高的是{sources[0]['label']}（{sources[0]['share']}%）。"
        )
    insights.append(
        '热度、情感与事件聚合均为筛查指标，需要回到原始证据和样本边界进行人工核验。'
    )
    mode_note = {
        'demo': '当前全部数据为明确标记的合成竞赛演示数据，不代表真实校园事件。',
        'mixed': '当前视图混合了演示数据与观测数据，解读时应按来源分别核验。',
        'observed': '当前数据来自已入库或已授权采集范围，不代表全网总体。',
    }[data_mode]
    return {
        'monitor': {
            'id': monitor['id'],
            'name': monitor.get('name') or '监测项目',
            'description': monitor.get('description') or '',
        },
        'provenance': {
            'data_mode': data_mode,
            'data_mode_label': {
                'demo': '合成演示数据',
                'mixed': '混合数据',
                'observed': '观测范围数据',
            }[data_mode],
            'sample_count': len(signals),
            'total_available': total_available,
            'truncated': total_available > len(signals),
            'dated_count': sum(_signal_time(item) is not None for item in signals),
            'missing_time_count': sum(_signal_time(item) is None for item in signals),
            'observed_from': _iso(start) if start else None,
            'observed_to': _iso(end) if end else None,
            'bin_hours': bin_hours,
            'sentiment_methods': [
                {'method': method, 'count': count}
                for method, count in method_counts.most_common()
            ],
            'sentiment_method_counts': dict(method_counts.most_common()),
            'sentiment_score_distribution': score_distribution,
            'quality_warnings': [
                {'flag': flag, 'count': count}
                for flag, count in quality_counts.most_common()
            ],
            'algorithm_versions': algorithm_versions(),
            'note': mode_note,
        },
        'summary': {
            'signals': len(signals),
            'events': len(event_views),
            'negative': negative,
            'negative_ratio': round(negative / max(len(signals), 1) * 100, 1),
            'risk_signals': risk,
            'risk_ratio': round(risk / max(len(signals), 1) * 100, 1),
            'open_alerts': len(open_alerts),
            'high_alerts': high_alerts,
            'engagement': sum(_engagement(item) for item in signals),
        },
        'sentiment': {
            'negative': negative,
            'nonnegative': nonnegative,
            'unknown': unknown,
        },
        'timeline': timeline,
        'sources': sources,
        'events': event_views,
        'graph': _evidence_graph(monitor, sources, event_views),
        'insights': insights,
        'definitions': {
            'negative': '由每条记录的 sentiment 字段统计；具体方法见溯源信息。',
            'risk': '至少命中一个监测项目风险词的信号。',
            'heat': '由样本量、互动量、负面比例、来源数和增长趋势组成的 0–100 描述性评分，不是概率。',
            'sentiment_score': '透明词典或来源标签生成的 -100 至 100 筛查分，不替代人工情感标注。',
            'clustering': '事件使用字符 n-gram TF-IDF 相似度聚合；凝聚度表示样本间文本相似近似水平。',
            'quality': '质量提示来自字段缺失、短文本、自动生成 ID 等可审计规则，只提示核验优先级。',
            'network': '来源—事件证据归属网络，不代表社交平台转发链或因果关系。',
        },
    }
