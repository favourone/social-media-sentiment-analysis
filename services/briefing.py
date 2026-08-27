# -*- coding: utf-8 -*-
"""Evidence-grounded event briefs with an optional OpenAI-compatible model."""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import urlparse

import requests

import config
from storage.monitor_store import get_monitor_store
from storage.product_store import utc_now


LOGGER = logging.getLogger(__name__)


def _deterministic_brief(event, signals):
    metrics = event['metrics']
    citations = [item['id'] for item in signals[:5]]
    quality_warnings = metrics.get('quality_warning_counts') or {}
    sentiment_methods = metrics.get('sentiment_method_counts') or {}
    quality_line = '、'.join(
        f'{name} {count} 条' for name, count in quality_warnings.items()
    ) or '未发现明显字段质量提示'
    method_line = '、'.join(
        f'{name} {count} 条' for name, count in sentiment_methods.items()
    ) or '未记录'
    key_points = [
        (
            f"共收录 {metrics.get('sample_count', len(signals))} 条相关信号，"
            f"覆盖 {metrics.get('source_count', 0)} 个来源。"
        ),
        (
            f"负面筛查占比 {metrics.get('negative_ratio', 0)}%；"
            f"情感方法分布：{method_line}。"
        ),
        (
            f"聚类凝聚度 {metrics.get('cohesion_score', '—')}%，"
            f"代表样本 #{metrics.get('representative_id', '—')}，"
            f"锚定词：{'、'.join(metrics.get('anchor_terms') or metrics.get('top_terms', [])[:6]) or '未提取'}。"
        ),
        (
            f"最近 24 小时 {metrics.get('recent_24h', 0)} 条，"
            f"前一窗口 {metrics.get('previous_24h', 0)} 条，"
            f"趋势标记为 {metrics.get('trend', 'unknown')}。"
        ),
        f"数据质量提示：{quality_line}。",
    ]
    return {
        'headline': event['title'],
        'summary': event.get('summary') or key_points[0],
        'key_points': key_points,
        'risks': (
            [f"命中风险词：{'、'.join(metrics.get('risk_terms', []))}"]
            if metrics.get('risk_terms') else []
        ),
        'counterpoints': ['当前未进行立场聚类，需人工检查代表性原文中的相反观点。'],
        'recommended_actions': [
            '打开引用原文核实语境与发布时间',
            '确认来源是否独立，排除转载造成的虚假扩散',
            '在预警中心记录判断结果',
        ],
        'uncertainty': (
            '这是基于当前采集范围的描述性摘要，不代表全网总体，'
            '情感、聚类和质量提示均为算法筛查证据，不证明因果关系。'
        ),
        'citations': citations,
        'mode': 'deterministic',
    }


def _extract_json(text):
    value = str(text or '').strip()
    fenced = re.search(r'```(?:json)?\s*(.*?)```', value, re.DOTALL | re.IGNORECASE)
    if fenced:
        value = fenced.group(1).strip()
    return json.loads(value)


def _llm_brief(event, signals):
    parsed = urlparse(config.LLM_BASE_URL)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        raise ValueError('LLM_BASE_URL 必须是有效的 http/https 地址')
    evidence = [{
        'id': item['id'],
        'platform': item['platform'],
        'published_at': item.get('published_at'),
        'author': item.get('author', ''),
        'text': item.get('text', '')[:600],
        'url': item.get('source_url'),
        'sentiment': (item.get('raw') or {}).get('_algorithm', {}).get('sentiment', {}),
        'quality_flags': (item.get('raw') or {}).get('_algorithm', {}).get('quality_flags', []),
    } for item in signals[:20]]
    schema = {
        'headline': 'string',
        'summary': 'string',
        'key_points': ['string'],
        'risks': ['string'],
        'counterpoints': ['string'],
        'recommended_actions': ['string'],
        'uncertainty': 'string',
        'citations': [123],
    }
    prompt = (
        "你是舆情分析助手。只能根据给定证据形成简报，禁止补充外部事实。"
        "每条关键结论必须能由 citations 中的整数信号 ID 支持；证据不足时明确写入 uncertainty。"
        "不要输出思维过程，只返回一个 JSON 对象，结构严格匹配："
        f"{json.dumps(schema, ensure_ascii=False)}\n"
        f"事件指标：{json.dumps(event['metrics'], ensure_ascii=False)}\n"
        f"证据：{json.dumps(evidence, ensure_ascii=False)}"
    )
    headers = {'Content-Type': 'application/json'}
    if config.LLM_API_KEY:
        headers['Authorization'] = f'Bearer {config.LLM_API_KEY}'
    response = requests.post(
        f"{config.LLM_BASE_URL}/chat/completions",
        json={
            'model': config.LLM_MODEL,
            'messages': [
                {'role': 'system', 'content': '输出可核验、克制的中文舆情简报。'},
                {'role': 'user', 'content': prompt},
            ],
            'temperature': 0.1,
        },
        headers=headers,
        timeout=config.LLM_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    content = payload['choices'][0]['message']['content']
    result = _extract_json(content)
    allowed = {item['id'] for item in evidence}
    citations = []
    for value in result.get('citations', []):
        try:
            identifier = int(value)
        except (TypeError, ValueError):
            continue
        if identifier in allowed and identifier not in citations:
            citations.append(identifier)
    if not citations:
        raise ValueError('模型简报没有提供有效的证据信号 ID')
    for key in (
        'headline', 'summary', 'key_points', 'risks', 'counterpoints',
        'recommended_actions', 'uncertainty'
    ):
        if key not in result:
            raise ValueError(f'模型简报缺少字段：{key}')
    result['citations'] = citations
    result['mode'] = 'llm'
    return result


def run_event_brief(brief_id):
    store = get_monitor_store()
    brief = store.get_brief(brief_id)
    if not brief:
        return None
    event = store.get_event(brief['event_id'])
    if not event:
        store.update_brief(
            brief_id,
            status='failed',
            error_code='event_not_found',
            error_message='事件不存在',
            finished_at=utc_now(),
        )
        return None
    signals = store.list_signals(
        event['monitor_id'], event_id=event['id'], limit=100
    )
    store.update_brief(brief_id, status='running')
    try:
        if config.LLM_ENABLED and config.LLM_BASE_URL and config.LLM_MODEL:
            result = _llm_brief(event, signals)
            mode = 'llm'
            model = config.LLM_MODEL
        else:
            result = _deterministic_brief(event, signals)
            mode = 'deterministic'
            model = None
        return store.update_brief(
            brief_id,
            status='succeeded',
            mode=mode,
            model=model,
            result_json=result,
            evidence_json=result['citations'],
            finished_at=utc_now(),
        )
    except Exception as exc:
        LOGGER.exception('Event brief failed: %s', brief_id)
        return store.update_brief(
            brief_id,
            status='failed',
            error_code='brief_generation_failed',
            error_message=f'事件简报生成失败：{type(exc).__name__}',
            finished_at=utc_now(),
        )
