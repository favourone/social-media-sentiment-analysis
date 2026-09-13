# -*- coding: utf-8 -*-
"""Fine-tuned Chinese BERT sentiment classifier with explicit degradation.

检查点必须由 ``scripts/finetune_bert_sentiment.py`` 训练产出，目录中包含
tokenizer、模型权重和 ``labels.json``。当检查点或深度学习依赖缺失时，
调用方回退到透明词典基线，而不是假装模型运行过。
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

import config

LOGGER = logging.getLogger(__name__)

BERT_SENTIMENT_ALGORITHM_VERSION = 'bert_finetuned_v3'
POSITIVE_LABEL = 1
NEGATIVE_LABEL = 0
_LOAD_LOCK = threading.Lock()
_INSTANCE_LOCK = threading.Lock()
_INSTANCE = None


def checkpoint_ready(model_dir=None):
    """A usable checkpoint means config plus weights, not a bare config file."""
    directory = Path(model_dir or config.SENTIMENT_BERT_MODEL_DIR)
    if not (directory / 'config.json').is_file():
        return False
    return any(
        (directory / name).is_file()
        for name in ('model.safetensors', 'pytorch_model.bin')
    )


class BertSentiment:
    """Lazy, thread-safe wrapper around a fine-tuned BERT classifier.

    输出字段与词典基线保持兼容：
    ``{'label': 0/1, 'score': -100~100, 'confidence': ..., 'method': ...}``
    """

    def __init__(self, model_dir=None, device=None, max_length=None):
        self.model_dir = str(model_dir or config.SENTIMENT_BERT_MODEL_DIR)
        self.max_length = int(max_length or config.SENTIMENT_BERT_MAX_LENGTH)
        self._device_choice = str(device or config.SENTIMENT_BERT_DEVICE)
        self._model = None
        self._tokenizer = None
        self._torch = None
        self._device = 'cpu'
        self._labels = {0: '负面', 1: '正面'}
        self._loaded = False
        self._error = None

    @property
    def available(self):
        if self._loaded:
            return self._error is None
        try:
            return checkpoint_ready(self.model_dir)
        except OSError:
            return False

    def _ensure_loaded(self):
        if self._loaded:
            return self._error is None
        with _LOAD_LOCK:
            if self._loaded:
                return self._error is None
            try:
                torch, model_cls, tokenizer_cls, import_error = _import_stack()
                if import_error is not None:
                    raise RuntimeError(
                        f'torch/transformers 依赖不可用：{import_error}'
                    ) from import_error
                if not checkpoint_ready(self.model_dir):
                    raise FileNotFoundError(
                        f'未找到已微调的情感模型检查点：{self.model_dir}，'
                        '请先运行 scripts/finetune_bert_sentiment.py'
                    )
                self._torch = torch
                self._tokenizer = tokenizer_cls.from_pretrained(self.model_dir)
                self._model = model_cls.from_pretrained(self.model_dir)
                self._model.eval()
                self._device = self._device_choice or (
                    'cuda' if torch.cuda.is_available() else 'cpu'
                )
                self._model.to(self._device)
                label_file = Path(self.model_dir) / 'labels.json'
                if label_file.is_file():
                    mapping = json.loads(label_file.read_text(encoding='utf-8'))
                    self._labels = {int(key): str(value) for key, value in mapping.items()}
                LOGGER.info(
                    'BERT 情感模型已加载：%s（device=%s）', self.model_dir, self._device
                )
            except Exception as exc:  # noqa: BLE001 - 明确降级而不是崩溃
                self._error = exc
                LOGGER.warning('BERT 情感模型加载失败，将回退词典：%s', exc)
            finally:
                self._loaded = True
        return self._error is None

    def _compose(self, positive_probability):
        probability = min(max(float(positive_probability), 0.0), 1.0)
        score = int(round(probability * 200 - 100))
        score = max(-100, min(100, score))
        label = POSITIVE_LABEL if probability >= 0.5 else NEGATIVE_LABEL
        absolute = abs(score)
        confidence = 'high' if absolute >= 45 else 'medium' if absolute >= 18 else 'low'
        return {
            'label': label,
            'score': score,
            'confidence': confidence,
            'method': (
                BERT_SENTIMENT_ALGORITHM_VERSION
                if confidence != 'low'
                else f'{BERT_SENTIMENT_ALGORITHM_VERSION}_low_confidence'
            ),
            'algorithm_version': BERT_SENTIMENT_ALGORITHM_VERSION,
            'model_path': self.model_dir,
            'positive_probability': round(probability, 4),
            'label_text': self._labels.get(label, str(label)),
        }

    def analyze_batch(self, texts):
        if not self._ensure_loaded():
            raise RuntimeError(self._error)
        cleaned = [str(text or '') for text in texts]
        if not cleaned:
            return []
        encoded = self._tokenizer(
            cleaned,
            truncation=True,
            max_length=self.max_length,
            padding=True,
            return_tensors='pt',
        )
        encoded = {key: value.to(self._device) for key, value in encoded.items()}
        with self._torch.no_grad():
            logits = self._model(**encoded).logits
        probabilities = self._torch.softmax(logits, dim=-1)
        return [self._compose(float(row[self._positive_index()])) for row in probabilities]

    def _positive_index(self):
        for key, value in self._labels.items():
            if value in ('正面', 'positive', 'pos', '1'):
                return key
        return POSITIVE_LABEL

    def analyze(self, text):
        results = self.analyze_batch([text])
        return results[0] if results else None


def _import_stack():
    try:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
    except ImportError as exc:  # pragma: no cover - depends on environment
        return None, None, None, exc
    return torch, AutoModelForSequenceClassification, AutoTokenizer, None


def get_bert_sentiment():
    global _INSTANCE
    if _INSTANCE is None:
        with _INSTANCE_LOCK:
            if _INSTANCE is None:
                _INSTANCE = BertSentiment()
    return _INSTANCE


def reset_bert_sentiment(instance=None):
    """Replace the process-wide singleton; intended for tests."""
    global _INSTANCE
    with _INSTANCE_LOCK:
        _INSTANCE = instance
