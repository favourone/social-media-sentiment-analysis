# -*- coding: utf-8 -*-
"""Background task implementations shared by inline mode and RQ workers."""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import date, timedelta

from crawler.adapters import CollectorError, CollectorPaused, get_collector
from services.reporting import generate_csv, generate_pdf, report_path
from storage.product_store import get_product_store, utc_now


LOGGER = logging.getLogger(__name__)


def _still_active(job):
    return job and job.get('status') != 'cancelled'


def _refresh_alerts(store, posts):
    grouped = defaultdict(list)
    for post in posts:
        grouped[post.get('topic') or '未分类'].append(post)
    for topic, records in grouped.items():
        negative = sum(1 for post in records if post.get('sentiment') == 0)
        ratio = negative / max(len(records), 1) * 100
        if ratio <= 40:
            continue
        level = 'high' if ratio > 50 else 'medium'
        store.replace_topic_alert(
            topic,
            level,
            f'话题「{topic}」负面样本占比 {ratio:.1f}%，请结合原文人工复核',
            {
                'sample_count': len(records),
                'negative_count': negative,
                'negative_ratio': round(ratio, 1),
                'sentiment_scope': '来源标签或透明词典基线，非人工标注结论',
            },
        )


def run_collection_job(job_id):
    store = get_product_store()
    job = store.get_collection_job(job_id)
    if not _still_active(job):
        return
    store.update_collection_job(
        job_id, status='running', progress=1, started_at=utc_now(),
        error_code=None, error_message=None
    )

    def progress(value):
        current = store.get_collection_job(job_id)
        if _still_active(current):
            store.update_collection_job(job_id, progress=max(1, min(int(value), 95)))

    try:
        result = get_collector(job['adapter']).collect(job, progress=progress)
        if not _still_active(store.get_collection_job(job_id)):
            return
        posts = result.get('posts', [])
        comments = result.get('comments', [])
        inserted_posts = store.insert_posts(job_id, posts)
        inserted_comments = store.insert_comments(job_id, comments)
        duplicate_posts = len(posts) - inserted_posts
        duplicate_comments = len(comments) - inserted_comments
        quality_summary = dict(result.get('quality_summary') or {})
        if quality_summary:
            quality_summary['duplicate_records'] = (
                int(quality_summary.get('duplicate_records') or 0) + duplicate_posts
            )
        stats = {
            'received_posts': len(posts),
            'inserted_posts': inserted_posts,
            'duplicate_posts': duplicate_posts,
            'received_comments': len(comments),
            'inserted_comments': inserted_comments,
            'duplicate_comments': duplicate_comments,
            'rejected_records': result.get('rejected', 0),
            'source_file_count': len(result.get('source_files', [])),
            'quality_summary': quality_summary,
        }
        _refresh_alerts(store, store.list_posts(limit=100000))
        store.update_collection_job(
            job_id, status='succeeded', progress=100, stats_json=stats,
            finished_at=utc_now()
        )
    except CollectorPaused as exc:
        store.update_collection_job(
            job_id, status='paused', error_code=exc.code,
            error_message=str(exc), finished_at=utc_now()
        )
    except CollectorError as exc:
        store.update_collection_job(
            job_id, status='failed', error_code=exc.code,
            error_message=str(exc), finished_at=utc_now()
        )
    except Exception:
        LOGGER.exception('Unhandled collection task failure: %s', job_id)
        store.update_collection_job(
            job_id, status='failed', error_code='internal_task_error',
            error_message='采集任务发生内部错误，请查看服务日志', finished_at=utc_now()
        )
        raise


def _summary_analysis(posts):
    from processing.text_processor import TextProcessor
    total = len(posts)
    negative = sum(1 for post in posts if post.get('sentiment') == 0)
    topics = Counter(post.get('topic') or '未分类' for post in posts)
    processor = TextProcessor()
    words = processor.get_word_freq([post.get('text', '') for post in posts], top_k=30)
    return {
        'sample_count': total,
        'positive_count': total - negative,
        'negative_count': negative,
        'negative_ratio': round(negative / max(total, 1) * 100, 1),
        'topic_counts': dict(topics.most_common(20)),
        'top_words': [{'word': word, 'count': count} for word, count in words],
        'sentiment_scope': '来源标签或透明词典基线，仅用于舆情筛查',
    }


def _lda_analysis(posts):
    from models.lda_model import LDAModel
    from processing.text_processor import TextProcessor
    processor = TextProcessor()
    tokenized = [processor.tokenize(post.get('text', '')) for post in posts]
    tokenized = [tokens for tokens in tokenized if tokens]
    if len(tokenized) < 10:
        return {'available': False, 'reason': '至少需要 10 篇有效文本', 'available_documents': len(tokenized)}
    model = LDAModel(num_topics=min(5, max(2, len(tokenized) // 5)))
    try:
        model.fit(tokenized)
        topics = model.get_topics(top_k=8)
    except Exception as exc:
        LOGGER.warning('LDA component unavailable: %s', type(exc).__name__)
        return {
            'available': False,
            'reason': '样本文本多样性不足，LDA 无法稳定拟合',
            'available_documents': len(tokenized),
        }
    return {
        'available': True,
        'topics': topics,
        'claim_scope': '主题词是当前样本的无监督归纳，需要人工命名与复核',
    }


def _time_series_analysis(posts):
    daily = Counter(
        (post.get('published_at') or '')[:10]
        for post in posts if post.get('published_at')
    )
    values = [daily[day] for day in sorted(daily)]
    result = {'observed_points': len(values), 'dates': sorted(daily), 'values': values}
    if len(values) < 10:
        result.update({'available': False, 'reason': 'ARIMA 至少需要 10 个日观测点'})
        return result
    from models.arima_model import ARIMAPredictor
    predictor = ARIMAPredictor()
    try:
        predictor.fit(values)
        forecast = predictor.predict(steps=7)
    except Exception as exc:
        LOGGER.warning('ARIMA component unavailable: %s', type(exc).__name__)
        result.update({'available': False, 'reason': 'ARIMA 无法在当前序列上稳定拟合'})
        return result
    result.update({
        'available': True,
        'forecast': forecast,
        'claim_scope': '统计外推仅适用于当前时间序列，不证明因果关系',
    })
    return result


def _sir_analysis(posts):
    daily = Counter(
        (post.get('published_at') or '')[:10]
        for post in posts if post.get('published_at')
    )
    values = [daily[day] for day in sorted(daily)]
    if len(values) < 6:
        return {
            'available': False,
            'reason': 'SIR 情景模拟至少需要 6 个日观测点',
            'observed_points': len(values),
        }
    from models.sir_model import SIRModel
    model = SIRModel()
    try:
        model.fit(values[:30])
        forecast = model.predict(days=30)
    except Exception as exc:
        LOGGER.warning('SIR component unavailable: %s', type(exc).__name__)
        return {'available': False, 'reason': 'SIR 无法在当前序列上稳定拟合'}
    return {
        'available': True,
        'observed_points': len(values),
        'time': forecast['time'],
        'discussion_curve': forecast['I'],
        'peak_day': forecast['peak_day'],
        'peak_value': forecast['peak_value'],
        'R0': forecast['R0'],
        'fit_nrmse': model.fit_nrmse,
        'claim_scope': '基于讨论量曲线形状的传播情景，不代表真实用户规模或因果效果',
    }


def _hybrid_analysis(posts):
    """ARIMA/SIR 特征与 LSTM 预测融合（按连续日历日聚合讨论量）。"""
    daily_counts = Counter()
    daily_sentiment = defaultdict(list)
    daily_engagement = Counter()
    for post in posts:
        day = (post.get('published_at') or post.get('fetched_at') or '')[:10]
        try:
            parsed_day = date.fromisoformat(day)
        except ValueError:
            continue
        day = parsed_day.isoformat()
        daily_counts[day] += 1
        daily_sentiment[day].append(1 if post.get('sentiment') == 1 else 0)
        daily_engagement[day] += sum(
            int(value or 0)
            for value in (post.get('engagement') or {}).values()
        )
    if daily_counts:
        start = date.fromisoformat(min(daily_counts))
        end = date.fromisoformat(max(daily_counts))
        days = [
            (start + timedelta(days=offset)).isoformat()
            for offset in range((end - start).days + 1)
        ]
    else:
        days = []
    counts = [daily_counts[day] for day in days]
    sentiment = [
        sum(daily_sentiment[day]) / len(daily_sentiment[day])
        if daily_sentiment[day] else 0.5
        for day in days
    ]
    engagement = [daily_engagement[day] for day in days]
    result = {
        'observed_points': len(counts), 'active_days': len(daily_counts),
        'dates': days, 'counts': counts,
    }
    if len(daily_counts) < 10:
        result.update({
            'available': False,
            'reason': '混合预测至少需要 10 个有实际样本的日期',
        })
        return result
    try:
        from models.hybrid_predictor import (
            HAS_TORCH,
            HYBRID_TREND_ALGORITHM_VERSION,
            HybridPredictor,
        )
    except ImportError:
        result.update({'available': False, 'reason': '混合预测依赖不可用'})
        return result
    if not HAS_TORCH:
        result.update({
            'available': False,
            'reason': '未安装 PyTorch，混合预测不可用；ARIMA 与 SIR 基线仍可单独使用',
        })
        return result
    try:
        predictor = HybridPredictor()
        predictor.fit(counts, sentiment=sentiment, engagement=engagement)
        forecast = predictor.predict()
    except Exception as exc:
        LOGGER.warning('Hybrid component unavailable: %s', type(exc).__name__)
        result.update({'available': False, 'reason': '混合预测无法在当前序列上稳定拟合'})
        return result
    result.update({
        'available': True,
        'algorithm_version': HYBRID_TREND_ALGORITHM_VERSION,
        'forecast': forecast,
        'claim_scope': '混合预测是统计外推与传播情景的组合，不证明因果关系',
    })
    return result


def run_analysis_job(job_id):
    store = None
    try:
        store = get_product_store()
        job = store.update_analysis_job(
            job_id, expected_statuses={'pending'}, status='running', progress=5,
            started_at=utc_now(), error_code=None, error_message=None,
        )
        if job is None:
            return
        params = job.get('params', {})
        monitor_id = params.get('monitor_id')
        if monitor_id:
            from storage.monitor_store import get_monitor_store
            monitor_store = get_monitor_store(product_store=store)
            posts = []
            offset = 0
            while len(posts) < 100000:
                page = monitor_store.list_signals(
                    monitor_id,
                    limit=min(500, 100000 - len(posts)),
                    offset=offset,
                )
                if not page:
                    break
                posts.extend(page)
                offset += len(page)
            start_date = params.get('start_date')
            end_date = params.get('end_date')
            if start_date or end_date:
                posts = [
                    item for item in posts
                    if (
                        (item.get('published_at') or item.get('fetched_at') or '')[:10]
                        and (
                            not start_date
                            or (item.get('published_at') or item.get('fetched_at'))[:10]
                            >= start_date
                        )
                        and (
                            not end_date
                            or (item.get('published_at') or item.get('fetched_at'))[:10]
                            <= end_date
                        )
                    )
                ]
        else:
            posts = store.list_posts(
                topic=params.get('topic'), start_date=params.get('start_date'),
                end_date=params.get('end_date'), limit=100000
            )
        if not posts:
            store.update_analysis_job(
                job_id, expected_statuses={'running'}, status='failed', progress=100,
                error_code='insufficient_data', error_message='当前范围没有可分析的采集数据',
                finished_at=utc_now()
            )
            return
        analysis_type = job['analysis_type']
        result = {'summary': _summary_analysis(posts)}
        if store.update_analysis_job(job_id, expected_statuses={'running'}, progress=45) is None:
            return
        if analysis_type in {'full', 'lda'}:
            result['lda'] = _lda_analysis(posts)
        if store.update_analysis_job(job_id, expected_statuses={'running'}, progress=70) is None:
            return
        if analysis_type in {'full', 'arima'}:
            result['arima'] = _time_series_analysis(posts)
        if analysis_type in {'full', 'sir'}:
            result['sir'] = _sir_analysis(posts)
        if analysis_type in {'full', 'hybrid'}:
            if store.update_analysis_job(job_id, expected_statuses={'running'}, progress=85) is None:
                return
            result['hybrid'] = _hybrid_analysis(posts)
        if not _still_active(store.get_analysis_job(job_id)):
            return
        if not monitor_id:
            _refresh_alerts(store, posts)
        store.update_analysis_job(
            job_id, expected_statuses={'running'}, status='succeeded', progress=100,
            result_json=result,
            finished_at=utc_now()
        )
    except Exception:
        LOGGER.exception('Unhandled analysis task failure: %s', job_id)
        if store is not None:
            try:
                store.update_analysis_job(
                    job_id, expected_statuses={'running'}, status='failed',
                    progress=100, error_code='analysis_failed',
                    error_message='分析任务执行失败，请查看服务日志',
                    finished_at=utc_now(),
                )
            except Exception:
                LOGGER.exception('Could not mark failed analysis task: %s', job_id)
        raise


def run_report_job(report_id):
    store = get_product_store()
    report = store.get_report(report_id)
    if not _still_active(report):
        return
    store.update_report(report_id, status='running', error_code=None, error_message=None)
    try:
        filters = report.get('filters', {})
        posts = store.list_posts(
            topic=filters.get('topic'), start_date=filters.get('start_date'),
            end_date=filters.get('end_date'), limit=100000
        )
        if not posts:
            store.update_report(
                report_id, status='failed', error_code='insufficient_data',
                error_message='当前范围没有可生成报告的数据', finished_at=utc_now()
            )
            return
        path = report_path(report_id, report['format'])
        if report['format'] == 'csv':
            generate_csv(path, posts)
        else:
            generate_pdf(path, posts, filters)
        store.update_report(
            report_id, status='succeeded', file_path=path,
            sample_count=len(posts), finished_at=utc_now()
        )
    except Exception:
        LOGGER.exception('Unhandled report task failure: %s', report_id)
        store.update_report(
            report_id, status='failed', error_code='report_failed',
            error_message='报告生成失败，请查看服务日志', finished_at=utc_now()
        )
        raise
