# -*- coding: utf-8 -*-
"""Generate auditable CSV and PDF reports from normalized posts."""

from __future__ import annotations

import csv
import os
from collections import Counter
from pathlib import Path

import config


def _summary(posts):
    total = len(posts)
    negative = sum(1 for post in posts if post.get('sentiment') == 0)
    topics = Counter(post.get('topic') or '未分类' for post in posts)
    sources = Counter(post.get('platform') or 'unknown' for post in posts)
    return {
        'sample_count': total,
        'positive_count': total - negative,
        'negative_count': negative,
        'negative_ratio': round(negative / max(total, 1) * 100, 1),
        'topics': dict(topics.most_common(10)),
        'sources': dict(sources),
    }


def generate_csv(path, posts):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow([
            'platform', 'source_id', 'topic', 'author', 'published_at',
            'sentiment', 'sentiment_method', 'likes', 'comments', 'reposts',
            'source_url', 'text'
        ])
        for post in posts:
            engagement = post.get('engagement', {})
            writer.writerow([
                post.get('platform'), post.get('source_id'), post.get('topic'),
                post.get('author'), post.get('published_at'), post.get('sentiment'),
                post.get('sentiment_method'), engagement.get('likes', 0),
                engagement.get('comments', 0), engagement.get('reposts', 0),
                post.get('source_url'), post.get('text'),
            ])


def generate_pdf(path, posts, filters):
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.platypus import (
            PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
        )
    except ImportError as exc:
        raise RuntimeError('生成 PDF 需要安装 reportlab') from exc

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    styles = getSampleStyleSheet()
    base = ParagraphStyle(
        'ChineseBase', parent=styles['BodyText'], fontName='STSong-Light',
        fontSize=9, leading=14, alignment=TA_LEFT
    )
    title = ParagraphStyle(
        'ChineseTitle', parent=base, fontSize=20, leading=26, spaceAfter=10
    )
    heading = ParagraphStyle(
        'ChineseHeading', parent=base, fontSize=13, leading=18, spaceBefore=8,
        spaceAfter=6
    )
    document = SimpleDocTemplate(
        path, pagesize=A4, rightMargin=16 * mm, leftMargin=16 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title='舆情分析报告', author='舆情分析平台 V1'
    )
    summary = _summary(posts)
    story = [
        Paragraph('舆情分析报告', title),
        Paragraph('报告范围与来源', heading),
        Paragraph(
            f"关键词：{filters.get('topic') or '全部'}　"
            f"日期：{filters.get('start_date') or '不限'} 至 {filters.get('end_date') or '不限'}",
            base,
        ),
        Paragraph(
            '数据来自用户授权采集或显式文件导入。情感标签可能包含透明词典基线，'
            '仅用于筛查，不能替代人工判断。', base
        ),
        Spacer(1, 5 * mm),
        Paragraph('核心统计', heading),
    ]
    table_data = [
        ['样本量', '正面', '负面', '负面占比'],
        [str(summary['sample_count']), str(summary['positive_count']),
         str(summary['negative_count']), f"{summary['negative_ratio']}%"],
    ]
    table = Table(table_data, colWidths=[35 * mm] * 4)
    table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'STSong-Light'),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#123b63')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#ccd6e0')),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('PADDING', (0, 0), (-1, -1), 6),
    ]))
    story.extend([table, Spacer(1, 5 * mm), Paragraph('话题分布', heading)])
    for topic, count in summary['topics'].items():
        story.append(Paragraph(f'{topic}：{count} 条', base))
    story.extend([
        PageBreak(),
        Paragraph('代表性记录（最多 30 条）', heading),
    ])
    for index, post in enumerate(posts[:30], 1):
        text = str(post.get('text', '')).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        story.append(Paragraph(
            f"{index}. [{post.get('topic') or '未分类'}] {text[:300]}", base
        ))
        story.append(Spacer(1, 2 * mm))
    story.extend([
        Paragraph('解释边界', heading),
        Paragraph(
            '本报告只描述所选样本。采集结果会受到平台权限、时间窗口、关键词和采样机制影响；'
            '模型结果不构成因果判断，也不能代表整个平台用户。', base
        ),
    ])
    document.build(story)


def report_path(report_id, report_format):
    if report_format not in {'pdf', 'csv'}:
        raise ValueError('unsupported report format')
    return os.path.join(config.REPORT_DIR, f'{report_id}.{report_format}')
