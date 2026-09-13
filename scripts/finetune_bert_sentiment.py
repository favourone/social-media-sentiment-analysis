# -*- coding: utf-8 -*-
"""微调中文 BERT 情感分类器（算法升级 V3 · 必做项）。

用法示例（在 cv 环境中）::

    python scripts/finetune_bert_sentiment.py \
        --train data/sentiment/train.jsonl \
        --valid data/sentiment/valid.jsonl \
        --model-name hfl/chinese-roberta-wwm-ext \
        --epochs 4

数据格式：JSONL / JSON / CSV 每行一条记录，至少包含：
- 正文列：``text`` / ``content`` / ``desc``
- 标签列：``label`` / ``sentiment``，取值 0/1、负面/正面、negative/positive

标签约定：**0 = 负面，1 = 正面**，与运行时 ``score_sentiment`` 的输出一致。
训练完成后检查点写入 ``--output``（默认 models/saved/bert_sentiment），
并记录训练元数据，保证证据可复现。

首次运行会从 Hugging Face 下载预训练权重；离线环境请提前准备好
本地模型目录并通过 --model-name 指向该目录。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

import config  # noqa: E402
from crawler.adapters import load_records  # noqa: E402

TEXT_KEYS = ('text', 'content', 'desc')
LABEL_KEYS = ('label', 'sentiment')
POSITIVE_TEXTS = {'1', 'positive', 'pos', '正面', '积极'}
NEGATIVE_TEXTS = {'0', 'negative', 'neg', '负面', '消极'}
DEFAULT_MODEL = 'hfl/chinese-roberta-wwm-ext'


def parse_args():
    parser = argparse.ArgumentParser(description='微调中文 BERT 校园情感分类器')
    parser.add_argument('--train', required=True, help='训练集文件（JSON/JSONL/CSV）')
    parser.add_argument('--valid', default='', help='验证集文件；缺省时从训练集切分 10%')
    parser.add_argument('--model-name', default=DEFAULT_MODEL,
                        help='预训练模型，如 bert-base-chinese 或本地目录')
    parser.add_argument('--output', default=config.SENTIMENT_BERT_MODEL_DIR,
                        help='检查点输出目录')
    parser.add_argument('--epochs', type=int, default=4)
    parser.add_argument('--batch-size', type=int, default=32)
    parser.add_argument('--lr', type=float, default=2e-5)
    parser.add_argument('--max-length', type=int, default=config.SENTIMENT_BERT_MAX_LENGTH)
    parser.add_argument('--warmup-ratio', type=float, default=0.1)
    parser.add_argument('--seed', type=int, default=config.RANDOM_SEED)
    parser.add_argument('--valid-ratio', type=float, default=0.1,
                        help='未提供验证集时从训练集切分的比例')
    return parser.parse_args()


def extract_example(record):
    text = ''
    for key in TEXT_KEYS:
        value = str(record.get(key) or '').strip()
        if value:
            text = value
            break
    raw_label = None
    for key in LABEL_KEYS:
        if record.get(key) not in (None, ''):
            raw_label = record[key]
            break
    if not text or raw_label is None:
        return None
    label = str(raw_label).strip().lower()
    if label in POSITIVE_TEXTS:
        return {'text': text, 'label': 1}
    if label in NEGATIVE_TEXTS:
        return {'text': text, 'label': 0}
    return None


def load_dataset(path):
    records = load_records(path)
    examples = []
    skipped = 0
    for record in records:
        example = extract_example(record)
        if example is None:
            skipped += 1
        else:
            examples.append(example)
    return examples, skipped


def split_dataset(examples, ratio, seed):
    indexed = list(range(len(examples)))
    random.Random(seed).shuffle(indexed)
    valid_count = max(1, int(len(examples) * ratio)) if len(examples) >= 20 else 0
    valid_indexes = set(indexed[:valid_count])
    train = [item for i, item in enumerate(examples) if i not in valid_indexes]
    valid = [item for i, item in enumerate(examples) if i in valid_indexes]
    return train, valid


def evaluate(model, dataloader, device, torch):
    import numpy as np

    model.eval()
    predictions, labels = [], []
    with torch.no_grad():
        for batch in dataloader:
            batch = {key: value.to(device) for key, value in batch.items()}
            logits = model(**{k: v for k, v in batch.items() if k != 'labels'}).logits
            predictions.extend(torch.argmax(logits, dim=-1).cpu().tolist())
            labels.extend(batch['labels'].cpu().tolist())
    predictions = np.asarray(predictions)
    labels = np.asarray(labels)
    accuracy = float((predictions == labels).mean()) if len(labels) else 0.0
    f1_scores = []
    for cls in (0, 1):
        tp = int(((predictions == cls) & (labels == cls)).sum())
        fp = int(((predictions == cls) & (labels != cls)).sum())
        fn = int(((predictions != cls) & (labels == cls)).sum())
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1_scores.append(
            2 * precision * recall / (precision + recall) if precision + recall else 0.0
        )
    return {
        'accuracy': round(accuracy, 4),
        'f1_negative': round(f1_scores[0], 4),
        'f1_positive': round(f1_scores[1], 4),
        'macro_f1': round(sum(f1_scores) / 2, 4),
    }


def main():
    args = parse_args()
    try:
        import torch
        from torch.utils.data import DataLoader, Dataset
        from transformers import (
            AutoModelForSequenceClassification,
            AutoTokenizer,
        )
    except ImportError as exc:
        print(f'缺少深度学习依赖（{exc}）。请先安装 torch 与 transformers。')
        return 1

    random.seed(args.seed)
    torch.manual_seed(args.seed)

    train_examples, skipped = load_dataset(args.train)
    if args.valid:
        valid_examples, valid_skipped = load_dataset(args.valid)
    else:
        train_examples, valid_examples = split_dataset(
            train_examples, args.valid_ratio, args.seed
        )
        valid_skipped = 0
    if len(train_examples) < 20 or not valid_examples:
        print(
            f'数据不足：训练 {len(train_examples)} 条，验证 {len(valid_examples)} 条；'
            '至少需要 20 条训练样本和可用的验证样本。'
        )
        return 1
    labels = {item['label'] for item in train_examples}
    if labels != {0, 1}:
        print(f'训练集必须同时包含 0（负面）和 1（正面）标签，当前只有 {sorted(labels)}。')
        return 1

    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    print(f'设备：{device}；基座模型：{args.model_name}')
    print(
        f'样本：训练 {len(train_examples)} 条（跳过 {skipped} 条无效记录），'
        f'验证 {len(valid_examples)} 条（跳过 {valid_skipped} 条）'
    )

    tokenizer = AutoTokenizer.from_pretrained(args.model_name)
    model = AutoModelForSequenceClassification.from_pretrained(
        args.model_name, num_labels=2
    ).to(device)

    class SentimentDataset(Dataset):
        def __init__(self, examples):
            self.examples = examples

        def __len__(self):
            return len(self.examples)

        def __getitem__(self, index):
            return self.examples[index]

    def collate(batch):
        encoded = tokenizer(
            [item['text'] for item in batch],
            truncation=True,
            max_length=args.max_length,
            padding=True,
            return_tensors='pt',
        )
        encoded['labels'] = torch.tensor(
            [item['label'] for item in batch], dtype=torch.long
        )
        return encoded

    train_loader = DataLoader(
        SentimentDataset(train_examples), batch_size=args.batch_size,
        shuffle=True, collate_fn=collate,
    )
    valid_loader = DataLoader(
        SentimentDataset(valid_examples), batch_size=args.batch_size,
        shuffle=False, collate_fn=collate,
    )

    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr)
    total_steps = max(1, len(train_loader) * args.epochs)
    warmup_steps = max(1, int(total_steps * args.warmup_ratio))

    def lr_lambda(step):
        if step < warmup_steps:
            return step / warmup_steps
        return max(
            0.0,
            (total_steps - step) / max(1, total_steps - warmup_steps),
        )

    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda)
    best_macro_f1 = -1.0
    history = []
    for epoch in range(1, args.epochs + 1):
        model.train()
        running_loss = 0.0
        for batch in train_loader:
            batch = {key: value.to(device) for key, value in batch.items()}
            optimizer.zero_grad()
            outputs = model(**batch)
            loss = outputs.loss
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            running_loss += float(loss.item())
        metrics = evaluate(model, valid_loader, device, torch)
        metrics['epoch'] = epoch
        metrics['train_loss'] = round(running_loss / max(1, len(train_loader)), 4)
        history.append(metrics)
        print(
            f"Epoch {epoch}/{args.epochs}  "
            f"loss={metrics['train_loss']:.4f}  "
            f"val_acc={metrics['accuracy']:.4f}  "
            f"macro_f1={metrics['macro_f1']:.4f}"
        )
        if metrics['macro_f1'] >= best_macro_f1:
            best_macro_f1 = metrics['macro_f1']
            output_dir = Path(args.output)
            output_dir.mkdir(parents=True, exist_ok=True)
            model.save_pretrained(output_dir)
            tokenizer.save_pretrained(output_dir)
            (output_dir / 'labels.json').write_text(
                json.dumps({'0': '负面', '1': '正面'}, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )

    meta = {
        'algorithm_version': 'bert_finetuned_v3',
        'base_model': args.model_name,
        'train_samples': len(train_examples),
        'valid_samples': len(valid_examples),
        'epochs': args.epochs,
        'batch_size': args.batch_size,
        'learning_rate': args.lr,
        'max_length': args.max_length,
        'seed': args.seed,
        'device': device,
        'label_mapping': {'0': '负面', '1': '正面'},
        'history': history,
        'best_macro_f1': best_macro_f1,
        'finished_at': datetime.now(timezone.utc).isoformat(),
        'claim_scope': '准确率与 F1 来自当前验证集，不等于真实舆情全量准确率。',
    }
    output_dir = Path(args.output)
    (output_dir / 'training_meta.json').write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    print(f'最佳 macro-F1={best_macro_f1:.4f}；检查点已保存到 {output_dir}')
    print('运行时将按 SENTIMENT_ENGINE 配置自动加载该检查点。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
