# -*- coding: utf-8 -*-
"""Deterministic monitoring, event clustering, and alert generation for V2."""

from __future__ import annotations

import hashlib
import logging
import math
import re
import threading
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import config
import numpy as np
import jieba.analyse
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from crawler.adapters import (
    CollectorError,
    CollectorPaused,
    QUALITY_ALGORITHM_VERSION,
    active_sentiment_algorithm_version,
    get_collector,
    summarize_quality,
)
from services.delivery import deliver_webhook
from storage.monitor_store import get_monitor_store
from storage.product_store import get_product_store, utc_now


LOGGER = logging.getLogger(__name__)
MAX_CLUSTER_SIGNALS = 500
CLUSTER_THRESHOLD = 0.38
MATCHING_ALGORITHM_VERSION = 'keyword_rule_v2'
CLUSTERING_ALGORITHM_VERSION = 'char_ngram_tfidf_v2'
BERTOPIC_CLUSTERING_ALGORITHM_VERSION = 'bertopic_v3'
ALERT_ALGORITHM_VERSION = 'explainable_alert_rules_v2'
BASELINE_TREND_ALGORITHM_VERSION = 'arima_sir_baseline_v2'
_SENTENCE_EMBEDDER = None
_SENTENCE_EMBEDDER_LOCK = threading.Lock()


def active_clustering_algorithm_version():
    """Report the clustering version the configured engine resolves to."""
    engine = str(getattr(config, 'CLUSTERING_ENGINE', 'ngram')).strip().lower()
    if engine in ('auto', 'bertopic'):
        try:
            import bertopic  # noqa: F401 - availability probe only

            return BERTOPIC_CLUSTERING_ALGORITHM_VERSION
        except ImportError:
            pass
    return CLUSTERING_ALGORITHM_VERSION


def active_trend_algorithm_version():
    try:
        from models.hybrid_predictor import HYBRID_TREND_ALGORITHM_VERSION

        return HYBRID_TREND_ALGORITHM_VERSION
    except ImportError:
        return BASELINE_TREND_ALGORITHM_VERSION


def algorithm_versions():
    return {
        'sentiment': active_sentiment_algorithm_version(),
        'quality': QUALITY_ALGORITHM_VERSION,
        'matching': MATCHING_ALGORITHM_VERSION,
        'clustering': active_clustering_algorithm_version(),
        'trend_forecast': active_trend_algorithm_version(),
        'alerting': ALERT_ALGORITHM_VERSION,
    }


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


def _algorithm_meta(item):
    return (item.get('raw') or {}).get('_algorithm') or {}


def _quality_flags(item):
    flags = _algorithm_meta(item).get('quality_flags') or []
    return [str(flag) for flag in flags if flag]


def _sentiment_meta(item):
    return _algorithm_meta(item).get('sentiment') or {}


def _representative_signal(signals):
    return max(
        signals,
        key=lambda item: (
            sum(int(value or 0) for value in item.get('engagement', {}).values()),
            float(item.get('relevance_score') or 0),
            len(item.get('text', '')),
            -int(item.get('id') or 0),
        ),
    )


def _signal_sort_key(item):
    return (
        item.get('published_at') or item.get('fetched_at') or '',
        item.get('platform') or '',
        item.get('source_id') or '',
        int(item.get('id') or 0),
    )


def _hours_between(start, end):
    if not start or not end:
        return None
    seconds = max((end - start).total_seconds(), 0)
    return round(seconds / 3600, 1)


def _source_mix_score(platforms, sample_count):
    if sample_count <= 1:
        return 0
    return round(min(len(platforms) / max(sample_count, 1), 1) * 100, 1)


def _count_sentiment_methods(signals):
    counter = Counter()
    for item in signals or []:
        sentiment = _sentiment_meta(item)
        counter.update([sentiment.get('method') or item.get('sentiment_method') or '未记录'])
    return dict(counter.most_common())


def _count_quality_warnings(signals):
    counter = Counter()
    for item in signals or []:
        counter.update(_quality_flags(item))
    return dict(counter.most_common())


def _quality_summary_from_signals(signals):
    flag_counts = Counter()
    method_counts = Counter()
    confidence_counts = Counter()
    scores = []
    for item in signals or []:
        flag_counts.update(_quality_flags(item))
        sentiment = _sentiment_meta(item)
        method_counts.update([sentiment.get('method') or item.get('sentiment_method') or '未记录'])
        confidence_counts.update([sentiment.get('confidence') or '未记录'])
        if isinstance(sentiment.get('score'), (int, float)):
            scores.append(sentiment['score'])
    summary = {
        'algorithm_version': QUALITY_ALGORITHM_VERSION,
        'valid_posts': len(signals or []),
        'quality_flags': dict(flag_counts.most_common()),
        'sentiment_methods': dict(method_counts.most_common()),
        'sentiment_confidence': dict(confidence_counts.most_common()),
    }
    if scores:
        summary['sentiment_score'] = {
            'min': min(scores),
            'max': max(scores),
            'average': round(sum(scores) / len(scores), 1),
        }
    return summary


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
    quality_flags = _quality_flags(post)
    quality_penalty = 0
    if 'short_text' in quality_flags:
        quality_penalty += 0.08
    if 'missing_published_at' in quality_flags:
        quality_penalty += 0.04
    if 'missing_source_url' in quality_flags:
        quality_penalty += 0.03
    relevance = max(
        0,
        min(1, 0.72 * keyword_score + 0.2 * required_score + risk_bonus - quality_penalty),
    )
    return {
        'post_id': post['id'],
        'relevance_score': round(relevance * 100, 1),
        'matched_terms': matched,
        'risk_terms': risk_hits,
        'match_evidence': {
            'algorithm_version': MATCHING_ALGORITHM_VERSION,
            'keyword_hits': keyword_hits,
            'required_hits': required,
            'risk_hits': risk_hits,
            'quality_flags': quality_flags,
            'quality_penalty': round(quality_penalty * 100, 1),
            'score_components': {
                'keyword_score': round(keyword_score, 3),
                'required_score': required_score,
                'risk_bonus': round(risk_bonus, 3),
            },
        },
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


def _event_metrics(
    signals,
    top_terms,
    threshold=CLUSTER_THRESHOLD,
    cohesion_score=None,
    fingerprint_basis=None,
):
    timestamps = [
        _timestamp(item.get('published_at') or item.get('fetched_at'))
        for item in signals
    ]
    timestamps = [item for item in timestamps if item]
    reference = max(timestamps) if timestamps else datetime.now(timezone.utc)
    first_seen = min(timestamps) if timestamps else None
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
    representative = _representative_signal(signals)
    sentiment_method_counts = _count_sentiment_methods(signals)
    quality_warning_counts = _count_quality_warnings(signals)
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
    confidence = 'limited' if len(signals) < 5 else 'moderate'
    if quality_warning_counts and len(signals) < 10:
        confidence = 'limited'
    return {
        'algorithm_version': CLUSTERING_ALGORITHM_VERSION,
        'cluster_threshold': threshold,
        'cohesion_score': cohesion_score,
        'representative_id': representative.get('id'),
        'anchor_terms': top_terms[:6],
        'sample_count': len(signals),
        'negative_count': negative,
        'negative_ratio': round(negative / max(len(signals), 1) * 100, 1),
        'source_count': len(platforms),
        'platforms': platforms,
        'source_mix_score': _source_mix_score(platforms, len(signals)),
        'total_engagement': engagement,
        'recent_24h': recent,
        'previous_24h': previous,
        'growth_ratio': growth_ratio,
        'trend': trend,
        'heat_score': heat,
        'top_terms': top_terms,
        'risk_terms': risk_terms,
        'sentiment_method_counts': sentiment_method_counts,
        'quality_warning_counts': quality_warning_counts,
        'time_span_hours': _hours_between(first_seen, reference),
        'fingerprint_basis': fingerprint_basis,
        'evidence_ids': [item['id'] for item in signals[:5]],
        'confidence': confidence,
        'reference_time': reference.replace(microsecond=0).isoformat(),
    }


def _ngram_groups(texts, threshold):
    """Deterministic char n-gram TF-IDF grouping (transparent baseline)."""
    parents = list(range(len(texts)))
    similarities = None

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left, right):
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parents[root_right] = root_left

    if len(texts) > 1 and any(texts):
        try:
            vectors = TfidfVectorizer(
                analyzer='char', ngram_range=(2, 4), min_df=1, max_features=6000
            ).fit_transform(texts)
            similarities = cosine_similarity(vectors)
            for left in range(len(texts)):
                for right in range(left + 1, len(texts)):
                    if similarities[left, right] >= threshold:
                        union(left, right)
        except ValueError:
            LOGGER.info('Event clustering fell back to singleton groups')

    groups = defaultdict(list)
    for index in range(len(texts)):
        groups[find(index)].append(index)
    return list(groups.values()), similarities


def _sentence_embedder():  # pragma: no cover - CI 未安装 sentence-transformers
    global _SENTENCE_EMBEDDER
    if _SENTENCE_EMBEDDER is None:
        with _SENTENCE_EMBEDDER_LOCK:
            if _SENTENCE_EMBEDDER is None:
                from sentence_transformers import SentenceTransformer

                _SENTENCE_EMBEDDER = SentenceTransformer(
                    config.BERTOPIC_EMBEDDING_MODEL
                )
    return _SENTENCE_EMBEDDER


def _cosine_matrix(embeddings):  # pragma: no cover - 仅 BERTopic 路径使用
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    normalized = embeddings / np.maximum(norms, 1e-12)
    return normalized @ normalized.T


def _group_by_bertopic(texts, embeddings=None):  # pragma: no cover - CI 无 bertopic，由 cv 环境 test_suanfa_algorithms 覆盖
    """Semantic grouping via BERTopic; returns (groups, similarity, meta).

    ``embeddings`` 参数允许测试注入确定性向量，避免在 CI 中下载模型。
    ``language=None`` 是必需的：默认 ``english`` 会触发 BERTopic 内部的
    ASCII 清洗，把中文正文剥成空文档。
    """
    from bertopic import BERTopic
    from hdbscan import HDBSCAN
    from sklearn.feature_extraction.text import CountVectorizer
    from umap import UMAP

    texts = list(texts)
    if embeddings is None:
        embeddings = _sentence_embedder().encode(
            texts, show_progress_bar=False, normalize_embeddings=True
        )
    embeddings = np.asarray(embeddings, dtype=float)
    count = len(texts)
    umap_model = UMAP(
        n_neighbors=max(2, min(15, count - 1)),
        n_components=max(2, min(5, count - 1)),
        min_dist=0.0,
        metric='cosine',
        random_state=config.BERTOPIC_RANDOM_STATE,
    )
    hdbscan_model = HDBSCAN(
        min_cluster_size=config.BERTOPIC_MIN_TOPIC_SIZE,
        metric='euclidean',
        cluster_selection_method='eom',
        prediction_data=True,
    )
    vectorizer_model = CountVectorizer(tokenizer=jieba.lcut, token_pattern=None)
    topic_model = BERTopic(
        embedding_model=None,
        language=None,
        vectorizer_model=vectorizer_model,
        min_topic_size=config.BERTOPIC_MIN_TOPIC_SIZE,
        umap_model=umap_model,
        hdbscan_model=hdbscan_model,
        calculate_probabilities=False,
        verbose=False,
    )
    topics, _probabilities = topic_model.fit_transform(texts, embeddings=embeddings)

    grouped = defaultdict(list)
    for index, topic in enumerate(topics):
        grouped[int(topic)].append(index)
    groups = []
    outlier_count = 0
    for topic in sorted(grouped):
        members = grouped[topic]
        if topic == -1:
            # HDBSCAN 噪声点不强行归组，保持为独立事件（与基线行为一致）。
            outlier_count = len(members)
            groups.extend([member] for member in members)
        else:
            groups.append(members)
    meta = {
        'engine': 'bertopic',
        'embedding_model': config.BERTOPIC_EMBEDDING_MODEL,
        'min_topic_size': config.BERTOPIC_MIN_TOPIC_SIZE,
        'random_state': config.BERTOPIC_RANDOM_STATE,
        'n_topics': len([topic for topic in grouped if topic != -1]),
        'outlier_count': outlier_count,
    }
    try:
        topic_terms = {}
        for topic in sorted(grouped):
            if topic == -1:
                continue
            words = topic_model.get_topic(topic) or []
            topic_terms[str(topic)] = [word for word, _weight in words[:6]]
        meta['topic_terms'] = topic_terms
    except Exception:  # noqa: BLE001 - 主题词仅用于解释，失败不影响聚类结果
        pass
    return groups, _cosine_matrix(embeddings), meta


def _group_signals(texts, threshold):
    """Dispatch to the configured clustering engine with explicit fallback."""
    engine = str(getattr(config, 'CLUSTERING_ENGINE', 'ngram')).strip().lower()
    minimum_docs = max(
        int(getattr(config, 'BERTOPIC_MIN_DOCS', 8)),
        int(getattr(config, 'BERTOPIC_MIN_TOPIC_SIZE', 3)),
    )
    if engine in ('auto', 'bertopic') and len(texts) >= minimum_docs:
        try:
            groups, similarities, meta = _group_by_bertopic(texts)
            if groups:
                return (
                    groups,
                    similarities,
                    BERTOPIC_CLUSTERING_ALGORITHM_VERSION,
                    meta,
                )
        except Exception as exc:  # noqa: BLE001 - 聚类失败显式回退
            LOGGER.warning(
                'BERTopic 聚类不可用，回退到字符 n-gram 基线：%s', exc
            )
    groups, similarities = _ngram_groups(texts, threshold)
    return (
        groups,
        similarities,
        CLUSTERING_ALGORITHM_VERSION,
        {'engine': 'char_ngram_tfidf', 'threshold': threshold},
    )


def cluster_signals(monitor_id, signals, threshold=CLUSTER_THRESHOLD):
    """Group related signals into auditable events.

    引擎顺序：CLUSTERING_ENGINE 配置的语义聚类（BERTopic）> 字符 n-gram TF-IDF 基线。
    每个事件都会记录实际使用的算法版本，便于证据追溯。
    """
    signals = sorted(list(signals), key=_signal_sort_key)[:MAX_CLUSTER_SIGNALS]
    if not signals:
        return []
    texts = [
        re.sub(r'\s+', ' ', str(item.get('text', ''))).strip()
        for item in signals
    ]
    groups, similarities, algorithm_version, clustering_meta = _group_signals(
        texts, threshold
    )

    def group_cohesion(indexes):
        if len(indexes) <= 1:
            return 100.0
        if similarities is None:
            return None
        values = []
        for left_pos, left in enumerate(indexes):
            for right in indexes[left_pos + 1:]:
                values.append(float(similarities[left, right]))
        if not values:
            return None
        return round(sum(values) / len(values) * 100, 1)

    events = []
    for indexes in groups:
        indexes = sorted(indexes, key=lambda index: _signal_sort_key(signals[index]))
        grouped = [signals[index] for index in indexes]
        combined = '\n'.join(item.get('text', '') for item in grouped)
        top_terms = _extract_terms(combined)
        stable_material = '|'.join(top_terms[:5])
        fingerprint_basis = 'top_terms'
        if not stable_material:
            stable_material = '|'.join(
                sorted(f"{item.get('platform')}:{item.get('source_id')}" for item in grouped)
            )
            fingerprint_basis = 'platform_source_id'
        fingerprint = hashlib.sha256(stable_material.encode('utf-8')).hexdigest()[:20]
        event_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL, f'event:{monitor_id}:{fingerprint}'
        ))
        dates = [
            item.get('published_at') or item.get('fetched_at')
            for item in grouped
            if item.get('published_at') or item.get('fetched_at')
        ]
        metrics = _event_metrics(
            grouped,
            top_terms,
            threshold=threshold,
            cohesion_score=group_cohesion(indexes),
            fingerprint_basis=fingerprint_basis,
        )
        metrics['algorithm_version'] = algorithm_version
        metrics['clustering_engine'] = clustering_meta
        title = _event_title(grouped, top_terms)
        quality_note = ''
        if metrics['quality_warning_counts']:
            quality_note = '；存在数据质量提示，需结合原文复核'
        summary = (
            f"聚合 {len(grouped)} 条相关信号，来自 {metrics['source_count']} 个来源；"
            f"负面筛查占比 {metrics['negative_ratio']}%，热度评分 {metrics['heat_score']}；"
            f"聚类凝聚度 {metrics['cohesion_score']}%{quality_note}。"
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
            'algorithm_version': ALERT_ALGORITHM_VERSION,
            'clustering_algorithm_version': metrics.get('algorithm_version'),
            'sentiment_method_counts': metrics.get('sentiment_method_counts', {}),
            'quality_warning_counts': metrics.get('quality_warning_counts', {}),
            'sample_limit_note': '预警证据最多展示前 5 条信号，完整样本请查看事件详情。',
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
                'evidence': {
                    **evidence,
                    'trigger_rule': 'risk_keyword',
                    'risk_terms': metrics['risk_terms'],
                    'thresholds': {'min_risk_terms': 1},
                    'observed_values': {
                        'risk_term_count': len(metrics['risk_terms']),
                        'risk_terms': metrics['risk_terms'],
                        'sample_count': metrics['sample_count'],
                    },
                },
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
                'evidence': {
                    **evidence,
                    'trigger_rule': 'negative_ratio',
                    'negative_ratio': metrics['negative_ratio'],
                    'thresholds': {
                        'min_samples': 3,
                        'negative_ratio_percent': monitor['negative_threshold'],
                    },
                    'observed_values': {
                        'sample_count': metrics['sample_count'],
                        'negative_count': metrics['negative_count'],
                        'negative_ratio_percent': metrics['negative_ratio'],
                    },
                },
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
                    'trigger_rule': 'volume_spike',
                    'recent_24h': metrics['recent_24h'],
                    'previous_24h': metrics['previous_24h'],
                    'growth_ratio': metrics['growth_ratio'],
                    'thresholds': {
                        'recent_24h_min': monitor['spike_threshold'],
                        'growth_ratio_min': 2,
                    },
                    'observed_values': {
                        'recent_24h': metrics['recent_24h'],
                        'previous_24h': metrics['previous_24h'],
                        'growth_ratio': metrics['growth_ratio'],
                    },
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
        duplicate_posts = len(result['posts']) - inserted
        quality_summary = dict(result.get('quality_summary') or {})
        if quality_summary:
            quality_summary['duplicate_records'] = (
                int(quality_summary.get('duplicate_records') or 0) + duplicate_posts
            )
        stats = {
            'received_posts': len(result['posts']),
            'inserted_posts': inserted,
            'duplicate_posts': duplicate_posts,
            'rejected_records': result.get('rejected', 0),
            'source_file_count': len(result.get('source_files', [])),
            'quality_summary': quality_summary,
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
            'source_stats': source_stats,
            'source_errors': source_errors,
            'matched_signals': len(matches),
            'events': len(events),
            'active_alerts': len(specs),
            'new_alerts': len(created_alerts),
            'auto_resolved_alerts': auto_resolved,
            'quality_summary': _quality_summary_from_signals(signals),
            'algorithm_versions': algorithm_versions(),
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
