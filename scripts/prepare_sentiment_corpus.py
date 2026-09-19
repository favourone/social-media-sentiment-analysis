# -*- coding: utf-8 -*-
"""准备 BERT 情感微调语料（算法升级 V3）。

把公开标注语料统一转换为 ``{text, label, source}`` JSONL，并按固定种子
切分训练/验证集。约定 **0 = 负面，1 = 正面**，与运行时 score_sentiment 一致。

数据源（公开研究语料，仅用于非商业教学与竞赛，来源需在证据中引用）：
1. weibo_senti_100k  — 微博帖子约 11.99 万条（ChineseNlpCorpus 收录，HF 镜像）
2. ChnSentiCorp      — 酒店/图书/电脑评论约 7766 条
3. waimai_10k        — 外卖评论约 1.2 万条（餐饮场景，与校园食品安全相关）

运行::

    python scripts/prepare_sentiment_corpus.py            # 使用已下载的 CSV
    python scripts/prepare_sentiment_corpus.py --download # 先经代理下载再转换

产物写入 ``data/processed/sentiment/``（该目录被 Git 忽略）：
``train.jsonl`` / ``valid.jsonl`` / ``stats.json``。
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

RAW_DIR = BASE_DIR / 'data' / 'raw' / 'sentiment'
OUT_DIR = BASE_DIR / 'data' / 'processed' / 'sentiment'

SOURCES = {
    'weibo_senti_100k': {
        'file': RAW_DIR / 'weibo_senti_100k.csv',
        'urls': [
            'https://huggingface.co/datasets/dirtycomputer/weibo_senti_100k/resolve/main/weibo_senti_100k.csv',
            'https://raw.githubusercontent.com/SophonPlus/ChineseNlpCorpus/master/datasets/weibo_senti_100k/weibo_senti_100k.csv',
        ],
        'text_key': 'review',
        'label_key': 'label',
    },
    'ChnSentiCorp': {
        'file': RAW_DIR / 'ChnSentiCorp_htl_all.csv',
        'urls': [
            'https://raw.githubusercontent.com/SophonPlus/ChineseNlpCorpus/master/datasets/ChnSentiCorp_htl_all/ChnSentiCorp_htl_all.csv',
        ],
        'text_key': 'review',
        'label_key': 'label',
    },
    'waimai_10k': {
        'file': RAW_DIR / 'waimai_10k.csv',
        'urls': [
            'https://raw.githubusercontent.com/SophonPlus/ChineseNlpCorpus/master/datasets/waimai_10k/waimai_10k.csv',
        ],
        'text_key': 'review',
        'label_key': 'label',
    },
}

PROXY = 'http://127.0.0.1:7890'


def download(sources):
    import requests

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for name, meta in sources.items():
        target = meta['file']
        if target.is_file() and target.stat().st_size > 10_000:
            print(f'[skip] {name} 已存在：{target}')
            continue
        for url in meta['urls']:
            try:
                print(f'[down] {name} <- {url[:80]}...')
                response = requests.get(
                    url, timeout=120, proxies={'http': PROXY, 'https': PROXY},
                    headers={'User-Agent': 'Mozilla/5.0 campus-sentiment/1.0'},
                )
                response.raise_for_status()
                if len(response.content) < 10_000:
                    raise RuntimeError(f'响应过小（{len(response.content)} 字节）')
                target.write_bytes(response.content)
                print(f'  -> {len(response.content) / 1e6:.1f} MB')
                break
            except Exception as exc:
                print(f'  失败：{exc}')
        else:
            print(f'[warn] {name} 全部地址失败，跳过')


def normalize_text(value):
    text = str(value or '').replace('\ufeff', ' ').strip()
    return ' '.join(text.split())


def load_source(name, meta):
    """读取一个语料 CSV，返回清洗后的 [{text, label, source}] 列表。"""
    records = []
    with meta['file'].open('r', encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            text = normalize_text(row.get(meta['text_key']))
            raw_label = str(row.get(meta['label_key']) or '').strip()
            if not text or raw_label not in {'0', '1'}:
                continue
            if len(text) < 4:
                continue
            records.append({'text': text, 'label': int(raw_label), 'source': name})
    return records


def main():
    parser = argparse.ArgumentParser(description='准备情感微调语料')
    parser.add_argument('--download', action='store_true', help='先经本地代理下载缺失的 CSV')
    parser.add_argument('--valid-ratio', type=float, default=0.05)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--max-per-source', type=int, default=0,
                        help='大于 0 时限制每个语料的样本数（快速试验用）')
    args = parser.parse_args()

    if args.download:
        download(SOURCES)

    random.seed(args.seed)
    all_records = []
    per_source = {}
    for name, meta in SOURCES.items():
        if not meta['file'].is_file():
            print(f'[warn] 缺少 {meta["file"]}，跳过 {name}')
            continue
        records = load_source(name, meta)
        if args.max_per_source and len(records) > args.max_per_source:
            random.shuffle(records)
            records = records[: args.max_per_source]
        labels = Counter(item['label'] for item in records)
        per_source[name] = {'total': len(records), 'positive': labels[1], 'negative': labels[0]}
        print(f'[{name}] {len(records)} 条（正 {labels[1]} / 负 {labels[0]}）')
        all_records.extend(records)

    if not all_records:
        print('没有任何可用语料。请先运行 --download 或手动放置 CSV。')
        return 1

    seen = set()
    deduped = []
    for item in all_records:
        key = item['text'][:120]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    random.shuffle(deduped)

    valid_count = max(1, int(len(deduped) * args.valid_ratio))
    valid, train = deduped[:valid_count], deduped[valid_count:]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for filename, rows in (('train.jsonl', train), ('valid.jsonl', valid)):
        with (OUT_DIR / filename).open('w', encoding='utf-8') as handle:
            for item in rows:
                handle.write(json.dumps(item, ensure_ascii=False) + '\n')

    labels = Counter(item['label'] for item in deduped)
    stats = {
        'algorithm_version': 'bert_finetuned_v3',
        'sources': per_source,
        'combined': {
            'total': len(deduped),
            'duplicates_removed': len(all_records) - len(deduped),
            'positive': labels[1],
            'negative': labels[0],
            'train': len(train),
            'valid': len(valid),
        },
        'valid_ratio': args.valid_ratio,
        'seed': args.seed,
        'label_convention': {'0': '负面', '1': '正面'},
        'prepared_at': datetime.now(timezone.utc).isoformat(),
        'note': '公开研究语料，仅用于非商业教学竞赛；来源以 ChineseNlpCorpus 收录页为准。',
    }
    (OUT_DIR / 'stats.json').write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    print(
        f'合并去重 {len(deduped)} 条（正 {labels[1]} / 负 {labels[0]}）；'
        f'训练 {len(train)} / 验证 {len(valid)}，输出到 {OUT_DIR}'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
