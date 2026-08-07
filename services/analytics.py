# -*- coding: utf-8 -*-
"""监测项目级分析实验室聚合。

所有指标都限定在一个监测项目已经匹配的信号集合内，避免旧版 JSON
数据、其他监测项目或合成案例之间相互串扰。
"""

from __future__ import annotations

from collections import Counter

from processing.text_processor import TextProcessor
from services.visualization import build_visual_story


def build_monitor_analytics(
    monitor,
    signals,
    events,
    *,
    total_available=None,
):
    """返回词频、情感趋势、事件排行和模型就绪条件。"""
    story = build_visual_story(
        monitor,
        signals,
        events,
        [],
        total_available=total_available,
    )
    processor = TextProcessor()
    words = processor.get_word_freq(
        [item.get('text', '') for item in signals],
        top_k=40,
    )
    dated_days = Counter(
        (item.get('published_at') or item.get('fetched_at') or '')[:10]
        for item in signals
        if item.get('published_at') or item.get('fetched_at')
    )
    documents = sum(bool(str(item.get('text') or '').strip()) for item in signals)
    observed_days = len([day for day in dated_days if day])
    return {
        'monitor': story['monitor'],
        'provenance': story['provenance'],
        'summary': story['summary'],
        'sentiment': story['sentiment'],
        'timeline': story['timeline'],
        'sources': story['sources'],
        'events': story['events'],
        'top_words': [
            {'word': word, 'count': int(count)} for word, count in words
        ],
        'readiness': {
            'documents': documents,
            'observed_days': observed_days,
            'lda': {
                'available': documents >= 10,
                'minimum': 10,
                'note': '至少需要 10 篇有效文本，并且文本应具有足够多样性。',
            },
            'arima': {
                'available': observed_days >= 10,
                'minimum': 10,
                'note': '至少需要 10 个不同日期的观测点。',
            },
            'sir': {
                'available': observed_days >= 6,
                'minimum': 6,
                'note': '至少需要 6 个不同日期的观测点。',
            },
        },
        'definitions': {
            **story['definitions'],
            'word_frequency': '仅统计当前监测项目样本文本中的分词频次，不代表全校总体关注度。',
            'model_scope': 'LDA、ARIMA 与 SIR 只使用当前监测项目的匹配信号；结果仍需人工核验。',
        },
    }
