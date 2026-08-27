# -*- coding: utf-8 -*-
"""Compliant collector adapters and normalized record mapping.

MediaCrawler remains an external, separately licensed tool.  This project only
invokes a user-configured command and imports its exported files.  It does not
ship cookies, bypass challenges, or silently replace failures with mock data.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import config


class CollectorError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class CollectorPaused(CollectorError):
    """A recoverable condition that requires user action."""


class CollectorAdapter(ABC):
    @abstractmethod
    def collect(self, job, progress=None):
        """Return ``{'posts': [...], 'comments': [...]}``."""


def _first(record, *keys, default=None):
    for key in keys:
        value = record.get(key)
        if value not in (None, ''):
            return value
    return default


def _integer(value):
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _timestamp(value):
    if value in (None, ''):
        return None
    if isinstance(value, (int, float)) or str(value).isdigit():
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000
        try:
            return datetime.fromtimestamp(number, timezone.utc).isoformat()
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.isoformat()
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.isoformat()
    except (TypeError, ValueError, OverflowError):
        pass
    # Keep a bounded source timestamp when parsing is ambiguous.
    return text[:64]


POSITIVE_WORDS = {
    '支持', '喜欢', '优秀', '进步', '成功', '满意', '点赞', '期待', '改善', '及时',
    '负责', '放心', '顺利', '感谢', '好评', '安全', '解决', '稳定', '清晰', '便利'
}
NEGATIVE_WORDS = {
    '反对', '失望', '问题', '风险', '失败', '投诉', '愤怒', '质疑', '崩溃', '故障',
    '延误', '拥堵', '停水', '停电', '漏水', '异味', '变质', '腹泻', '诈骗', '冒充',
    '转账', '火灾', '打架', '丢失', '失联', '危险', '隐患', '无法', '不能', '打不开'
}
RISK_WORDS = {
    '中毒', '腹泻', '异味', '变质', '诈骗', '冒充', '转账', '火灾', '打架', '失联',
    '泄露', '坍塌', '爆炸', '聚集', '投诉', '举报', '恐慌', '谣言', '危险', '隐患'
}
NEGATION_WORDS = {'不', '没', '没有', '无', '未', '并非', '不是', '别'}
INTENSIFIERS = {'很', '非常', '特别', '严重', '大量', '持续', '反复', '多次', '集中'}
SENTIMENT_ALGORITHM_VERSION = 'transparent_lexicon_v2'
QUALITY_ALGORITHM_VERSION = 'record_quality_v1'


def _hits(text, words):
    return [word for word in sorted(words, key=len, reverse=True) if word in text]


def score_sentiment(text, raw_value=None):
    """Return a transparent sentiment screen while keeping binary compatibility."""
    value = str(text or '')
    if raw_value in (0, 1, '0', '1'):
        label = int(raw_value)
        return {
            'label': label,
            'score': -70 if label == 0 else 70,
            'confidence': 'high',
            'method': 'source_label',
            'algorithm_version': SENTIMENT_ALGORITHM_VERSION,
            'evidence': {
                'source_label': label,
                'positive_terms': [],
                'negative_terms': [],
                'risk_terms': [],
                'negation_terms': [],
                'intensifiers': [],
            },
            'caveats': ['优先采用来源提供的情感标签，仍需结合原文人工复核。'],
        }
    positive_terms = _hits(value, POSITIVE_WORDS)
    negative_terms = _hits(value, NEGATIVE_WORDS)
    risk_terms = _hits(value, RISK_WORDS)
    negation_terms = _hits(value, NEGATION_WORDS)
    intensifiers = _hits(value, INTENSIFIERS)
    positive = sum(value.count(word) for word in positive_terms)
    negative = sum(value.count(word) for word in negative_terms)
    risk = sum(value.count(word) for word in risk_terms)
    negation = min(sum(value.count(word) for word in negation_terms), 3)
    intensity = min(sum(value.count(word) for word in intensifiers), 4)
    raw_score = positive * 16 - negative * 18 - risk * 24
    if negative or risk:
        raw_score -= intensity * 6
    elif positive:
        raw_score += intensity * 4
    # In public-opinion screening, negation words often reduce the certainty of a
    # lexicon hit more safely than reversing polarity with no syntax parser.
    if negation and (negative or risk):
        raw_score += min(negation * 10, 24)
    score = max(-100, min(100, raw_score))
    label = 0 if score < 0 else 1
    absolute = abs(score)
    confidence = 'high' if absolute >= 45 else 'medium' if absolute >= 18 else 'low'
    method = SENTIMENT_ALGORITHM_VERSION if confidence != 'low' else f'{SENTIMENT_ALGORITHM_VERSION}_low_confidence'
    caveats = ['透明词典筛查只用于发现疑似风险，不代表人工标注或事实结论。']
    if confidence == 'low':
        caveats.append('词典证据较弱，建议优先查看原文语境。')
    return {
        'label': label,
        'score': int(score),
        'confidence': confidence,
        'method': method,
        'algorithm_version': SENTIMENT_ALGORITHM_VERSION,
        'evidence': {
            'positive_terms': positive_terms,
            'negative_terms': negative_terms,
            'risk_terms': risk_terms,
            'negation_terms': negation_terms,
            'intensifiers': intensifiers,
        },
        'caveats': caveats,
    }


def infer_sentiment(text, raw_value=None):
    result = score_sentiment(text, raw_value)
    return result['label'], result['method']


def _algorithm_bucket(record):
    raw = dict(record or {})
    bucket = raw.get('_algorithm')
    if not isinstance(bucket, dict):
        bucket = {}
    raw['_algorithm'] = bucket
    return raw, bucket


def assess_record_quality(post, source_record=None, generated_source_id=False):
    flags = []
    text = str(post.get('text') or '')
    if not post.get('published_at'):
        flags.append('missing_published_at')
    if not post.get('author'):
        flags.append('missing_author')
    if not post.get('source_url'):
        flags.append('missing_source_url')
    if len(text) < 12:
        flags.append('short_text')
    if generated_source_id:
        flags.append('generated_source_id')
    sentiment = (post.get('raw') or {}).get('_algorithm', {}).get('sentiment', {})
    method = sentiment.get('method') or post.get('sentiment_method')
    if method == 'source_label':
        flags.append('source_sentiment_label_used')
    elif method:
        flags.append('lexicon_sentiment_used')
    if source_record and source_record.get('feed_url') and not source_record.get('url'):
        flags.append('rss_missing_item_link')
    return flags


def summarize_quality(posts, rejected=0, duplicates=0, source_files=None, comments=None, extra=None):
    flag_counts = Counter()
    method_counts = Counter()
    confidence_counts = Counter()
    score_values = []
    for post in posts or []:
        algorithm = (post.get('raw') or {}).get('_algorithm') or {}
        flag_counts.update(algorithm.get('quality_flags') or [])
        sentiment = algorithm.get('sentiment') or {}
        method_counts.update([sentiment.get('method') or post.get('sentiment_method') or '未记录'])
        confidence_counts.update([sentiment.get('confidence') or '未记录'])
        if isinstance(sentiment.get('score'), (int, float)):
            score_values.append(sentiment['score'])
    summary = {
        'algorithm_version': QUALITY_ALGORITHM_VERSION,
        'valid_posts': len(posts or []),
        'valid_comments': len(comments or []),
        'rejected_records': int(rejected or 0),
        'duplicate_records': int(duplicates or 0),
        'source_file_count': len(source_files or []),
        'quality_flags': dict(flag_counts.most_common()),
        'sentiment_methods': dict(method_counts.most_common()),
        'sentiment_confidence': dict(confidence_counts.most_common()),
    }
    if score_values:
        summary['sentiment_score'] = {
            'min': min(score_values),
            'max': max(score_values),
            'average': round(sum(score_values) / len(score_values), 1),
        }
    if extra:
        summary.update(extra)
    return summary


def normalize_post(record, platform='weibo', topic=None):
    text = str(_first(
        record, 'text', 'content', 'desc', 'note_content', 'raw_text', default=''
    )).strip()
    source_id = str(_first(
        record, 'source_id', 'post_id', 'id', 'note_id', 'aweme_id', default=''
    )).strip()
    generated_source_id = False
    published_at = _timestamp(_first(
        record, 'published_at', 'created_at', 'publish_time', 'time', 'create_time'
    ))
    if not source_id and text:
        identity = f'{platform}|{published_at}|{text}'.encode('utf-8')
        source_id = hashlib.sha256(identity).hexdigest()[:24]
        generated_source_id = True
    if not source_id or not text:
        raise CollectorError('invalid_record', '采集记录缺少可识别的 ID 或正文')
    sentiment_detail = score_sentiment(text, record.get('sentiment'))
    raw, algorithm = _algorithm_bucket(record)
    algorithm['sentiment'] = sentiment_detail
    post = {
        'platform': platform,
        'source_id': source_id,
        'author': str(_first(
            record, 'author', 'user_name', 'nickname', 'screen_name', default=''
        ))[:200],
        'text': text,
        'published_at': published_at,
        'engagement': {
            'likes': _integer(_first(record, 'likes', 'liked_count', 'attitudes_count')),
            'comments': _integer(_first(record, 'comments', 'comment_count', 'comments_count')),
            'reposts': _integer(_first(record, 'reposts', 'shared_count', 'reposts_count')),
            'collects': _integer(_first(record, 'collects', 'collected_count')),
        },
        'fetched_at': datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        'source_url': _first(record, 'source_url', 'url', 'note_url', 'detail_url'),
        'topic': topic or _first(record, 'topic', 'keyword', default='未分类'),
        'sentiment': sentiment_detail['label'],
        'sentiment_method': sentiment_detail['method'],
        'raw': raw,
    }
    algorithm['quality_flags'] = assess_record_quality(
        post, record, generated_source_id=generated_source_id
    )
    return post


def normalize_comment(record, platform='weibo'):
    text = str(_first(record, 'text', 'content', 'comment_content', default='')).strip()
    source_id = str(_first(
        record, 'source_id', 'comment_id', 'id', default=''
    )).strip()
    post_source_id = str(_first(
        record, 'post_source_id', 'post_id', 'note_id', 'aweme_id', default=''
    )).strip()
    if not source_id or not post_source_id or not text:
        raise CollectorError('invalid_comment', '评论记录缺少评论 ID、帖子 ID 或正文')
    sentiment_detail = score_sentiment(text, record.get('sentiment'))
    raw, algorithm = _algorithm_bucket(record)
    algorithm['sentiment'] = sentiment_detail
    return {
        'platform': platform,
        'source_id': source_id,
        'post_source_id': post_source_id,
        'author': str(_first(record, 'author', 'user_name', 'nickname', default=''))[:200],
        'text': text,
        'published_at': _timestamp(_first(
            record, 'published_at', 'created_at', 'publish_time', 'create_time'
        )),
        'fetched_at': datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        'sentiment': sentiment_detail['label'],
        'raw': raw,
    }


def load_records(path):
    """Load JSON, JSONL or CSV without executing content."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == '.csv':
        with path.open('r', encoding='utf-8-sig', newline='') as handle:
            return list(csv.DictReader(handle))
    if suffix in {'.jsonl', '.ndjson'}:
        records = []
        with path.open('r', encoding='utf-8') as handle:
            for line in handle:
                if line.strip():
                    records.append(json.loads(line))
        return records
    if suffix == '.json':
        with path.open('r', encoding='utf-8') as handle:
            payload = json.load(handle)
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in ('data', 'items', 'posts', 'comments'):
                if isinstance(payload.get(key), list):
                    return payload[key]
            return [payload]
    raise CollectorError('unsupported_file', '仅支持 JSON、JSONL 和 CSV 文件')


class FileImportAdapter(CollectorAdapter):
    def collect(self, job, progress=None):
        raw_path = job.get('params', {}).get('import_path')
        if not raw_path:
            raise CollectorError('missing_import', '导入任务缺少文件路径')
        path = Path(raw_path).resolve()
        import_root = Path(config.IMPORT_DIR).resolve()
        if path != import_root and import_root not in path.parents:
            raise CollectorError('unsafe_import_path', '导入文件不在受控目录中')
        if not path.is_file():
            raise CollectorError('missing_import', '待导入文件不存在')
        records = load_records(path)
        posts = []
        rejected = 0
        topic = (job.get('keywords') or ['未分类'])[0]
        for index, record in enumerate(records):
            try:
                posts.append(normalize_post(record, job['platform'], topic))
            except CollectorError:
                rejected += 1
            if progress and records:
                progress(min(90, int((index + 1) / len(records) * 90)))
        if not posts:
            raise CollectorError('no_valid_records', '文件中没有可导入的有效帖子')
        source_files = [str(path)]
        return {
            'posts': posts,
            'comments': [],
            'rejected': rejected,
            'source_files': source_files,
            'quality_summary': summarize_quality(
                posts, rejected=rejected, source_files=source_files,
                extra={'adapter': 'file', 'loaded_records': len(records)}
            ),
        }


class MediaCrawlerWeiboAdapter(CollectorAdapter):
    """Run a separately installed MediaCrawler and import its latest exports."""

    _lock = threading.Lock()

    def collect(self, job, progress=None):
        if not self._lock.acquire(blocking=False):
            raise CollectorPaused(
                'collector_busy', '采集器正在执行另一个任务，请稍后重试'
            )
        try:
            return self._collect(job, progress)
        finally:
            self._lock.release()

    def _collect(self, job, progress=None):
        if not config.COLLECTOR_LICENSE_ACCEPTED:
            raise CollectorPaused(
                'license_confirmation_required',
                '请先确认仅用于非商业学习研究，并设置 COLLECTOR_LICENSE_ACCEPTED=true',
            )
        home = Path(config.MEDIACRAWLER_HOME).resolve()
        if not home.is_dir():
            raise CollectorPaused(
                'collector_not_installed',
                '未找到外部 MediaCrawler；请按设置页说明完成独立安装',
            )
        if not config.MEDIACRAWLER_COMMAND_JSON:
            raise CollectorPaused(
                'collector_not_configured',
                'MEDIACRAWLER_COMMAND_JSON 尚未配置',
            )
        try:
            command = json.loads(config.MEDIACRAWLER_COMMAND_JSON)
        except json.JSONDecodeError as exc:
            raise CollectorError('invalid_collector_command', '采集命令 JSON 无效') from exc
        if not isinstance(command, list) or not command or not all(
            isinstance(part, str) and part for part in command
        ):
            raise CollectorError('invalid_collector_command', '采集命令必须是非空字符串数组')

        keywords = ','.join(job.get('keywords') or [])
        max_pages = min(max(int(job.get('params', {}).get('max_pages', 3)), 1), 10)
        replacements = {'{keywords}': keywords, '{max_pages}': str(max_pages)}
        expanded = []
        for part in command:
            for placeholder, value in replacements.items():
                part = part.replace(placeholder, value)
            expanded.append(part)

        # MediaCrawler reads keywords and limits from its Python config rather
        # than CLI flags.  Apply a narrow, reversible override and restore the
        # original file even when the collector times out or fails.
        config_path = home / 'config' / 'base_config.py'
        if not config_path.is_file():
            raise CollectorError('collector_layout_changed', '外部采集器缺少 config/base_config.py')
        original_config = config_path.read_text(encoding='utf-8')
        output_root = Path(config.MEDIACRAWLER_OUTPUT_DIR).resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        overrides = {
            'KEYWORDS': repr(keywords),
            'CRAWLER_MAX_NOTES_COUNT': str(max_pages * 15),
            'SAVE_DATA_OPTION': repr('jsonl'),
            'SAVE_DATA_PATH': repr(str(output_root)),
            'ENABLE_GET_COMMENTS': 'True',
            'ENABLE_GET_SUB_COMMENTS': 'False',
            'MAX_CONCURRENCY_NUM': '1',
        }
        temporary_config = original_config
        for name, value in overrides.items():
            temporary_config, count = re.subn(
                rf'(?m)^{name}\s*=\s*.*$', f'{name} = {value}', temporary_config, count=1
            )
            if count != 1:
                raise CollectorError(
                    'collector_layout_changed', f'外部采集器配置项 {name} 不存在'
                )
        config_path.write_text(temporary_config, encoding='utf-8')

        started = time.time()
        if progress:
            progress(5)
        try:
            try:
                completed = subprocess.run(
                    expanded,
                    cwd=str(home),
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    timeout=config.MEDIACRAWLER_TIMEOUT_SECONDS,
                    shell=False,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise CollectorPaused('collector_timeout', '采集超时，任务已暂停，可调整范围后重试') from exc
            except OSError as exc:
                raise CollectorError('collector_start_failed', f'外部采集器启动失败：{exc}') from exc
        finally:
            config_path.write_text(original_config, encoding='utf-8')

        output = f'{completed.stdout}\n{completed.stderr}'.lower()
        if completed.returncode != 0:
            if any(word in output for word in ('captcha', 'verify', 'login', '二维码', '验证码')):
                raise CollectorPaused('login_or_challenge_required', '采集器需要重新登录或人工完成验证')
            raise CollectorError(
                'collector_failed',
                f'外部采集器退出码为 {completed.returncode}，请查看采集器日志',
            )

        if progress:
            progress(80)
        if not output_root.is_dir():
            raise CollectorError('missing_collector_output', '采集器未生成可读取的输出目录')
        candidates = [
            path for path in output_root.rglob('*')
            if path.is_file()
            and path.suffix.lower() in {'.json', '.jsonl', '.ndjson', '.csv'}
            and path.stat().st_mtime >= started - 5
        ]
        if not candidates:
            raise CollectorPaused(
                'no_new_export',
                '采集器已结束但没有发现本次任务的新导出文件，请检查外部配置',
            )

        posts, comments, rejected = [], [], 0
        topic = (job.get('keywords') or ['未分类'])[0]
        for path in sorted(candidates):
            try:
                records = load_records(path)
            except (OSError, json.JSONDecodeError, CollectorError):
                rejected += 1
                continue
            is_comment_file = 'comment' in path.name.lower() or '评论' in path.name
            for record in records:
                try:
                    if is_comment_file:
                        comments.append(normalize_comment(record, 'weibo'))
                    else:
                        posts.append(normalize_post(record, 'weibo', topic))
                except CollectorError:
                    rejected += 1
        if not posts:
            raise CollectorError('no_valid_records', '采集输出中没有可识别的微博帖子')
        source_files = [str(path) for path in candidates]
        return {
            'posts': posts,
            'comments': comments,
            'rejected': rejected,
            'source_files': source_files,
            'quality_summary': summarize_quality(
                posts,
                rejected=rejected,
                source_files=source_files,
                comments=comments,
                extra={
                    'adapter': 'mediacrawler',
                    'export_files': len(source_files),
                    'loaded_records': len(posts) + len(comments) + rejected,
                },
            ),
        }


def get_collector(name):
    from crawler.rss_adapter import RSSFeedAdapter

    adapters = {
        'file': FileImportAdapter,
        'mediacrawler': MediaCrawlerWeiboAdapter,
        'rss': RSSFeedAdapter,
    }
    adapter = adapters.get(name)
    if adapter is None:
        raise CollectorError('unknown_adapter', f'不支持的采集适配器：{name}')
    return adapter()
