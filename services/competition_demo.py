# -*- coding: utf-8 -*-
"""Deterministic, explicitly labelled data for an offline competition demo."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services.intelligence import run_monitor
from storage.monitor_store import get_monitor_store
from storage.product_store import get_product_store, utc_now


DATASET_ID = 'campus-public-opinion-v3'
DEMO_MONITOR_NAME = '校园公共事件｜国赛演示'
REFERENCE_TIME = datetime(2026, 8, 1, 8, 0, tzinfo=timezone(timedelta(hours=8)))

SCENARIO_CONTEXT = {
    'food': (
        '【校园餐饮议题】 阶段追踪。'
        '专题证据统一记录餐饮线索、部门回应、供应链核查、窗口整改、'
        '留样检测、复核公开、后续监督和处置反馈等阶段信息，'
        '用于演示事件演化。本条动态：'
    ),
    'network': (
        '【校园信息服务议题】 阶段追踪。'
        '专题证据统一记录服务异常、技术排查、设备切换、连接恢复、'
        '状态验证、进度公开、后续观察和处置反馈等阶段信息，'
        '用于演示事件演化。本条动态：'
    ),
    'fraud': (
        '【校园财务提醒议题】 阶段追踪。'
        '专题证据统一记录缴费提醒、信息核验、渠道通报、紧急处置、'
        '进度公开、后续防范和处置反馈等阶段信息，'
        '用于演示事件演化。本条动态：'
    ),
}


def _record(cluster, index, hour, platform, text, sentiment, engagement):
    published = REFERENCE_TIME + timedelta(hours=hour, minutes=(index * 7) % 50)
    return {
        'platform': platform,
        'source_id': f'{DATASET_ID}-{cluster}-{index:03d}',
        'author': {
            'weibo': '公开讨论用户',
            'rss': '校务公开信息',
            'news': '公开媒体',
            'campus_forum': '校园社区用户',
        }.get(platform, '公开来源'),
        'text': f"{SCENARIO_CONTEXT.get(cluster, '')}{text}",
        'published_at': published.isoformat(),
        'fetched_at': (published + timedelta(minutes=8)).isoformat(),
        'engagement': engagement,
        'source_url': f'https://example.com/competition-demo/{cluster}/{index}',
        'topic': '校园公共事件',
        'sentiment': sentiment,
        'sentiment_method': 'scenario_label_for_demo',
        'raw': {
            'data_kind': 'synthetic_competition_demo',
            'dataset_id': DATASET_ID,
            'scenario': cluster,
            'reference_time': REFERENCE_TIME.isoformat(),
            'notice': '合成演示记录，不代表真实人物、学校或事件',
        },
    }


def competition_demo_records():
    """Return the fixed source table used by the one-click offline demo."""
    records = []
    food_hours = [0, 4, 8, 12, 16, 20, 24, 26, 28, 30, 32, 34, 36, 38,
                  40, 42, 44, 46, 48, 50, 52, 54, 57, 60, 63, 66, 69, 72]
    food_variants = [
        '校园食堂食品安全投诉受到关注，同学反映餐品异味并等待核验',
        '校园食堂餐品异味问题持续讨论，食品安全情况需要公开说明',
        '学校回应食堂食品安全投诉，已对留样和供应链启动调查',
        '食堂食品安全事件公布阶段性通报，涉事窗口暂停并接受检测',
        '校园食堂整改进展发布，复检结果与后续监督措施等待公布',
        '食堂食品安全处置进入复检阶段，讨论热度逐步回落',
    ]
    for index, hour in enumerate(food_hours, 1):
        late = index > 20
        platform = ['weibo', 'campus_forum', 'weibo', 'rss', 'news'][index % 5]
        records.append(_record(
            'food', index, hour, platform,
            f"{food_variants[min((index - 1) // 5, len(food_variants) - 1)]}。证据序号 F{index:02d}",
            1 if late and index % 3 == 0 else 0,
            {
                'likes': max(4, 18 + index * 6 - max(index - 18, 0) * 10),
                'comments': max(1, 5 + index * 2 - max(index - 20, 0) * 3),
                'reposts': max(0, index // 3 - max(index - 22, 0)),
            },
        ))

    network_hours = [6, 10, 14, 18, 22, 26, 30, 34, 38, 42, 46, 52, 58, 64]
    network_variants = [
        '校园网络故障导致教学平台访问异常，信息中心正在排查',
        '宿舍区域校园网中断问题扩大，在线课程与认证服务受到影响',
        '信息中心发布网络故障通报，核心设备切换期间连接不稳定',
        '校园网络恢复后继续观察，受影响服务已逐项验证',
    ]
    for index, hour in enumerate(network_hours, 1):
        platform = ['campus_forum', 'weibo', 'rss', 'news'][index % 4]
        records.append(_record(
            'network', index, hour, platform,
            f"{network_variants[min((index - 1) // 4, len(network_variants) - 1)]}。证据序号 N{index:02d}",
            0 if index <= 10 else 1,
            {'likes': 8 + index * 3, 'comments': 3 + index, 'reposts': index // 4},
        ))

    fraud_hours = [15, 21, 27, 33, 39, 45, 49, 53, 57, 61, 65, 70]
    fraud_variants = [
        '校园群出现冒充辅导员的缴费诈骗提醒，请勿点击陌生链接',
        '多名同学反馈收到虚假缴费通知，疑似诈骗信息正在扩散',
        '保卫部门发布反诈通报，确认虚假缴费链接并给出核验渠道',
        '校园诈骗提醒持续转发，官方渠道公布受理与止付流程',
    ]
    for index, hour in enumerate(fraud_hours, 1):
        platform = ['weibo', 'campus_forum', 'rss'][index % 3]
        records.append(_record(
            'fraud', index, hour, platform,
            f"{fraud_variants[min((index - 1) // 3, len(fraud_variants) - 1)]}。证据序号 S{index:02d}",
            0 if index <= 9 else 1,
            {'likes': 12 + index * 4, 'comments': 2 + index, 'reposts': 2 + index // 2},
        ))
    return records


def seed_competition_demo(product_store=None, monitor_store=None):
    """Insert/reuse the demo dataset and execute its monitor synchronously."""
    product_store = product_store or get_product_store()
    monitor_store = monitor_store or get_monitor_store()
    records = competition_demo_records()
    job = product_store.create_collection_job(
        'demo', 'competition_demo', ['校园公共事件'],
        {'dataset_id': DATASET_ID, 'synthetic': True},
    )
    product_store.update_collection_job(
        job['id'], status='running', progress=20, started_at=utc_now()
    )
    inserted = product_store.insert_posts(job['id'], records)
    product_store.update_collection_job(
        job['id'],
        status='succeeded',
        progress=100,
        stats_json={
            'dataset_id': DATASET_ID,
            'synthetic': True,
            'received': len(records),
            'inserted': inserted,
            'duplicates': len(records) - inserted,
        },
        finished_at=utc_now(),
    )
    monitor = next(
        (
            item for item in monitor_store.list_monitors(include_archived=True)
            if item.get('semantic_query') == f'dataset:{DATASET_ID}'
        ),
        None,
    )
    fields = {
        'name': DEMO_MONITOR_NAME,
        'description': '合成案例：展示校园食品安全、网络故障与反诈提醒的发现、聚合和处置过程。',
        'keywords': ['食堂', '食品安全', '校园网', '网络故障', '诈骗', '缴费'],
        'required_terms': [],
        'excluded_terms': ['招聘', '广告'],
        'risk_terms': ['投诉', '异味', '故障', '中断', '诈骗', '虚假'],
        'semantic_query': f'dataset:{DATASET_ID}',
        'interval_minutes': 60,
        'negative_threshold': 55,
        'spike_threshold': 6,
        'status': 'active',
    }
    if monitor:
        monitor = monitor_store.update_monitor(monitor['id'], **fields)
    else:
        create_fields = dict(fields)
        create_fields.pop('status')
        monitor = monitor_store.create_monitor(
            **create_fields,
            sources=[{
                'kind': 'database',
                'name': '竞赛演示数据仓（合成）',
                'url': '',
                'config': {'dataset_id': DATASET_ID, 'synthetic': True},
            }],
        )
    run = monitor_store.create_run(monitor['id'])
    completed = run_monitor(run['id'])
    return {
        'dataset_id': DATASET_ID,
        'synthetic': True,
        'records': len(records),
        'inserted': inserted,
        'duplicates': len(records) - inserted,
        'monitor': monitor_store.get_monitor(monitor['id']),
        'run': completed,
        'notice': '这是固定合成案例，不代表真实人物、学校或事件。',
    }
