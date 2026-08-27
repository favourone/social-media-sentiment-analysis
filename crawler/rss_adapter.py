# -*- coding: utf-8 -*-
"""RSS/Atom collector used by V2 monitoring projects."""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

import config
from crawler.adapters import (
    CollectorAdapter,
    CollectorError,
    normalize_post,
    summarize_quality,
)
from services.network_safety import UnsafeURL, validate_outbound_url


def _local_name(tag):
    return str(tag).rsplit('}', 1)[-1].lower()


def _child_text(element, names):
    names = set(names)
    for child in list(element):
        if _local_name(child.tag) in names and child.text:
            return child.text.strip()
    return ''


def _entry_link(element):
    for child in list(element):
        if _local_name(child.tag) != 'link':
            continue
        href = child.attrib.get('href')
        rel = child.attrib.get('rel', 'alternate')
        if href and rel in {'alternate', ''}:
            return href.strip()
        if child.text:
            return child.text.strip()
    return ''


def parse_feed(payload, feed_url):
    """Parse a bounded RSS or Atom document into normalized source records."""
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise CollectorError('invalid_feed', 'RSS/Atom 内容不是有效 XML') from exc

    records = []
    for element in root.iter():
        if _local_name(element.tag) not in {'item', 'entry'}:
            continue
        title = _child_text(element, {'title'})
        description = _child_text(
            element, {'description', 'summary', 'content', 'encoded'}
        )
        clean_description = BeautifulSoup(description, 'html.parser').get_text(
            ' ', strip=True
        )
        text = title
        if clean_description and clean_description not in title:
            text = f'{title}。{clean_description}' if title else clean_description
        link = _entry_link(element)
        source_id = _child_text(element, {'guid', 'id'}) or link
        if not source_id and text:
            source_id = hashlib.sha256(
                f'{feed_url}|{text}'.encode('utf-8')
            ).hexdigest()[:32]
        author = _child_text(element, {'author', 'creator'})
        published = _child_text(
            element, {'pubdate', 'published', 'updated', 'date'}
        )
        if text:
            records.append({
                'id': source_id,
                'text': text,
                'author': author,
                'created_at': published,
                'url': link,
                'feed_url': feed_url,
                'title': title,
            })
    return records


class RSSFeedAdapter(CollectorAdapter):
    """Collect public RSS/Atom feeds with conservative network safeguards."""

    def collect(self, job, progress=None):
        urls = job.get('params', {}).get('feed_urls') or []
        if not isinstance(urls, list) or not urls:
            raise CollectorError('missing_feed_url', 'RSS 任务没有配置订阅地址')
        topic = (job.get('keywords') or ['RSS 监测'])[0]
        posts = []
        rejected = 0
        source_files = []
        parsed_records = 0
        for index, raw_url in enumerate(urls[:20]):
            try:
                url = validate_outbound_url(
                    raw_url, allow_private=config.ALLOW_PRIVATE_NETWORK_URLS
                )
                response = requests.get(
                    url,
                    timeout=config.RSS_TIMEOUT_SECONDS,
                    headers={'User-Agent': config.CRAWLER_HEADERS['User-Agent']},
                    allow_redirects=True,
                )
                response.raise_for_status()
                final_url = validate_outbound_url(
                    response.url,
                    allow_private=config.ALLOW_PRIVATE_NETWORK_URLS,
                )
                payload = response.content
                if len(payload) > config.RSS_MAX_BYTES:
                    raise CollectorError(
                        'feed_too_large', 'RSS/Atom 响应超过允许大小'
                    )
                records = parse_feed(payload, final_url)
                parsed_records += len(records)
                for record in records:
                    try:
                        posts.append(normalize_post(record, 'rss', topic))
                    except CollectorError:
                        rejected += 1
                source_files.append(final_url)
            except UnsafeURL as exc:
                raise CollectorError('unsafe_feed_url', str(exc)) from exc
            except requests.RequestException as exc:
                raise CollectorError(
                    'feed_request_failed', f'RSS 获取失败：{type(exc).__name__}'
                ) from exc
            if progress:
                progress(min(90, int((index + 1) / min(len(urls), 20) * 90)))
        if not posts:
            raise CollectorError('no_valid_records', 'RSS/Atom 中没有可用内容')
        return {
            'posts': posts,
            'comments': [],
            'rejected': rejected,
            'source_files': source_files,
            'quality_summary': summarize_quality(
                posts,
                rejected=rejected,
                source_files=source_files,
                extra={
                    'adapter': 'rss',
                    'feed_count': min(len(urls), 20),
                    'parsed_records': parsed_records,
                },
            ),
        }
