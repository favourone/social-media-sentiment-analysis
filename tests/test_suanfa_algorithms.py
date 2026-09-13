# -*- coding: utf-8 -*-
"""算法升级 V3 的回归测试：BERT 情感、BERTopic 聚类、混合预测。

设计原则：
- 所有涉及引擎开关的用例都在 setUp/tearDown 中显式设置与恢复配置，
  不依赖 .env，保证本地与 CI 行为一致；
- 不在单元测试中下载任何模型：BERT 路径用注入的假分类器，
  BERTopic 路径用注入的确定性句向量；
- 依赖缺失（无 torch / 无 bertopic）时用 skipUnless 跳过对应用例。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

import config
from crawler.adapters import (
    SENTIMENT_ALGORITHM_VERSION,
    active_sentiment_algorithm_version,
    assess_record_quality,
    score_sentiment,
)
from models.bert_sentiment import (
    BERT_SENTIMENT_ALGORITHM_VERSION,
    BertSentiment,
    reset_bert_sentiment,
)
from services.intelligence import (
    BERTOPIC_CLUSTERING_ALGORITHM_VERSION,
    CLUSTERING_ALGORITHM_VERSION,
    active_clustering_algorithm_version,
    algorithm_versions,
    cluster_signals,
)

try:
    import bertopic  # noqa: F401

    BERTOPIC_AVAILABLE = True
except ImportError:
    BERTOPIC_AVAILABLE = False

try:
    from models.hybrid_predictor import (
        HAS_TORCH,
        HYBRID_TREND_ALGORITHM_VERSION,
        HybridPredictor,
    )
except ImportError:
    HAS_TORCH = False
    HYBRID_TREND_ALGORITHM_VERSION = 'hybrid_arima_sir_lstm_v3'

from services.tasks import _hybrid_analysis


class _FakeBert:
    """离线替身：验证 score_sentiment 的 BERT 分支与证据注入。"""

    def __init__(self, probability=0.93):
        self.probability = probability
        self.available = True

    def analyze(self, text):
        score = int(round(self.probability * 200 - 100))
        return {
            'label': 1 if self.probability >= 0.5 else 0,
            'score': score,
            'confidence': 'high' if abs(score) >= 45 else 'medium',
            'method': BERT_SENTIMENT_ALGORITHM_VERSION,
            'algorithm_version': BERT_SENTIMENT_ALGORITHM_VERSION,
            'model_path': 'fake://checkpoint',
            'positive_probability': self.probability,
        }


class _FakeEmbedder:
    """确定性句向量：食堂相关与网络故障相关分别落在两个正交方向。"""

    def encode(self, texts, **kwargs):
        vectors = []
        for index, text in enumerate(texts):
            vector = np.zeros(4)
            if any(word in text for word in ('食堂', '饭菜', '腹泻')):
                vector[0] = 1.0
            else:
                vector[1] = 1.0
            vector[2] = index * 1e-4
            vector[3] = 0.05
            vectors.append(vector)
        return np.asarray(vectors)


class SentimentEngineTest(unittest.TestCase):
    def setUp(self):
        self.original_engine = config.SENTIMENT_ENGINE

    def tearDown(self):
        config.SENTIMENT_ENGINE = self.original_engine
        reset_bert_sentiment()

    def test_lexicon_engine_stays_transparent(self):
        config.SENTIMENT_ENGINE = 'lexicon'
        result = score_sentiment('食堂饭菜变质，多名学生投诉')
        self.assertEqual(result['algorithm_version'], SENTIMENT_ALGORITHM_VERSION)
        self.assertTrue(result['evidence']['negative_terms'])
        self.assertEqual(active_sentiment_algorithm_version(), SENTIMENT_ALGORITHM_VERSION)

    def test_auto_engine_falls_back_without_checkpoint(self):
        config.SENTIMENT_ENGINE = 'auto'
        with tempfile.TemporaryDirectory() as temp:
            reset_bert_sentiment(BertSentiment(model_dir=temp))
            result = score_sentiment('食堂饭菜变质，多名学生投诉')
        self.assertEqual(result['algorithm_version'], SENTIMENT_ALGORITHM_VERSION)
        self.assertEqual(active_sentiment_algorithm_version(), SENTIMENT_ALGORITHM_VERSION)

    def test_forced_bert_engine_falls_back_without_checkpoint(self):
        config.SENTIMENT_ENGINE = 'bert'
        with tempfile.TemporaryDirectory() as temp:
            reset_bert_sentiment(BertSentiment(model_dir=temp))
            result = score_sentiment('食堂饭菜变质，多名学生投诉')
        self.assertEqual(result['algorithm_version'], SENTIMENT_ALGORITHM_VERSION)

    def test_bert_result_keeps_lexicon_cross_check(self):
        config.SENTIMENT_ENGINE = 'auto'
        reset_bert_sentiment(_FakeBert(probability=0.93))
        result = score_sentiment('今天食堂新菜品很好吃，值得点赞')
        self.assertEqual(result['algorithm_version'], BERT_SENTIMENT_ALGORITHM_VERSION)
        self.assertEqual(result['label'], 1)
        self.assertGreater(result['score'], 0)
        cross_check = result['evidence']['lexicon_cross_check']
        self.assertIn('positive_probability', result['evidence'])
        self.assertIsInstance(cross_check['lexicon_score'], int)
        self.assertIn('人工复核', result['caveats'][0])

        negative = _FakeBert(probability=0.05)
        reset_bert_sentiment(negative)
        result = score_sentiment('食堂饭菜变质，多名学生投诉')
        self.assertEqual(result['label'], 0)
        self.assertLess(result['score'], 0)

    def test_source_label_still_wins_over_bert(self):
        config.SENTIMENT_ENGINE = 'auto'
        reset_bert_sentiment(_FakeBert(probability=0.93))
        result = score_sentiment('原文内容', raw_value=0)
        self.assertEqual(result['method'], 'source_label')

    def test_quality_flags_distinguish_bert_method(self):
        post = {
            'text': '这是一条足够长的正文内容',
            'published_at': '2026-07-22T10:00:00+08:00',
            'author': '用户',
            'source_url': 'https://example.com/1',
            'raw': {'_algorithm': {'sentiment': {'method': BERT_SENTIMENT_ALGORITHM_VERSION}}},
        }
        self.assertIn('bert_sentiment_used', assess_record_quality(post))

    def test_algorithm_versions_report_active_engines(self):
        config.SENTIMENT_ENGINE = 'lexicon'
        config.CLUSTERING_ENGINE = 'ngram'
        versions = algorithm_versions()
        for key in ('sentiment', 'quality', 'matching', 'clustering', 'trend_forecast', 'alerting'):
            self.assertIn(key, versions)
        self.assertEqual(versions['clustering'], CLUSTERING_ALGORITHM_VERSION)
        self.assertEqual(versions['sentiment'], SENTIMENT_ALGORITHM_VERSION)


@unittest.skipUnless(BERTOPIC_AVAILABLE, 'bertopic 未安装')
class BertopicClusteringTest(unittest.TestCase):
    def setUp(self):
        self.original_engine = config.CLUSTERING_ENGINE

    def tearDown(self):
        config.CLUSTERING_ENGINE = self.original_engine

    def _signals(self):
        texts = [
            '食堂饭菜变质，多名学生腹泻投诉处理进展 {}',
            '校园食堂餐饮安全投诉调查 {}',
            '食堂后厨卫生问题引发学生不满 {}',
            '饭菜里发现异物，学生要求彻查 {}',
            '校园网系统故障，选课系统打不开 {}',
            '教务系统崩溃导致选课失败 {}',
            '校园网络瘫痪，图书馆系统无法访问 {}',
            '信息系统故障，学生抱怨网络卡顿 {}',
        ]
        return [
            {
                'id': index + 1,
                'platform': 'weibo',
                'source_id': f's-{index}',
                'text': text.format(index),
                'published_at': f'2026-07-22T1{index % 10}:00:00+08:00',
                'fetched_at': '2026-07-22T14:00:00+08:00',
                'sentiment': 0,
                'engagement': {'likes': index},
                'risk_terms': [],
                'raw': {'_algorithm': {'sentiment': {'method': 'transparent_lexicon_v2'}}},
            }
            for index, text in enumerate(texts)
        ]

    def test_semantic_grouping_is_deterministic_and_explained(self):
        config.CLUSTERING_ENGINE = 'auto'
        with patch('services.intelligence._sentence_embedder', return_value=_FakeEmbedder()):
            first = cluster_signals('monitor-bertopic', self._signals())
            second = cluster_signals('monitor-bertopic', self._signals())
        self.assertTrue(first)
        self.assertGreaterEqual(len(first), 2)
        for event in first:
            self.assertEqual(
                event['metrics']['algorithm_version'],
                BERTOPIC_CLUSTERING_ALGORITHM_VERSION,
            )
            self.assertEqual(event['metrics']['clustering_engine']['engine'], 'bertopic')
            self.assertIsNotNone(event['metrics']['cohesion_score'])
        self.assertEqual(
            [event['fingerprint'] for event in first],
            [event['fingerprint'] for event in second],
        )

    def test_engine_version_reporting(self):
        config.CLUSTERING_ENGINE = 'auto'
        self.assertEqual(
            active_clustering_algorithm_version(),
            BERTOPIC_CLUSTERING_ALGORITHM_VERSION,
        )
        config.CLUSTERING_ENGINE = 'ngram'
        self.assertEqual(
            active_clustering_algorithm_version(),
            CLUSTERING_ALGORITHM_VERSION,
        )


class BertopicFallbackTest(unittest.TestCase):
    def setUp(self):
        self.original_engine = config.CLUSTERING_ENGINE

    def tearDown(self):
        config.CLUSTERING_ENGINE = self.original_engine

    def test_bertopic_failure_falls_back_to_ngram(self):
        config.CLUSTERING_ENGINE = 'bertopic'
        signals = [
            {
                'id': index + 1,
                'platform': 'weibo',
                'source_id': f's-{index}',
                'text': f'校园食堂食品安全投诉事件调查处理进展 {index}',
                'published_at': f'2026-07-22T1{index}:00:00+08:00',
                'fetched_at': '2026-07-22T14:00:00+08:00',
                'sentiment': 0,
                'engagement': {'likes': index},
                'risk_terms': ['投诉'],
                'raw': {},
            }
            for index in range(9)
        ]
        with patch(
            'services.intelligence._group_by_bertopic',
            side_effect=RuntimeError('模拟依赖缺失'),
        ):
            events = cluster_signals('monitor-fallback', signals)
        self.assertEqual(len(events), 1)
        self.assertEqual(
            events[0]['metrics']['algorithm_version'],
            CLUSTERING_ALGORITHM_VERSION,
        )


@unittest.skipUnless(HAS_TORCH, 'PyTorch 未安装')
class HybridPredictorTest(unittest.TestCase):
    def _series(self, days=30):
        rng = np.random.default_rng(7)
        counts = []
        for day in range(days):
            base = 20 + 45 * np.exp(-((day - 9) ** 2) / 30.0) + rng.normal(0, 2)
            counts.append(max(float(base), 0.5))
        return counts

    def test_hybrid_forecast_includes_interval_and_baseline(self):
        counts = self._series()
        sentiment = [0.4 if value > 30 else 0.6 for value in counts]
        engagement = [value * 3 for value in counts]
        predictor = HybridPredictor(epochs=150)
        predictor.fit(counts, sentiment=sentiment, engagement=engagement)
        forecast = predictor.predict(steps=7)
        self.assertEqual(forecast['algorithm_version'], HYBRID_TREND_ALGORITHM_VERSION)
        self.assertEqual(len(forecast['forecast']), 7)
        for value, lower, upper in zip(
            forecast['forecast'], forecast['lower_bound'], forecast['upper_bound']
        ):
            self.assertGreaterEqual(value, 0)
            self.assertLessEqual(lower, value)
            self.assertGreaterEqual(upper, value)
        self.assertIn('R0', forecast)
        self.assertIn('arima', forecast['baseline'])
        self.assertIn('sir', forecast['baseline'])
        self.assertIn('claim_scope', forecast)

    def test_minimum_points_enforced(self):
        with self.assertRaises(ValueError):
            HybridPredictor().fit([1, 2, 3, 4, 5])

    def test_hybrid_analysis_with_daily_posts(self):
        posts = []
        for index, value in enumerate(self._series()):
            posts.append({
                'published_at': f'2026-07-{index + 1:02d}T10:00:00+08:00',
                'sentiment': 0 if value > 30 else 1,
                'engagement': {'likes': int(value)},
                'text': f'第 {index} 天的校园观察记录',
            })
        result = _hybrid_analysis(posts)
        self.assertTrue(result['available'])
        self.assertEqual(result['algorithm_version'], HYBRID_TREND_ALGORITHM_VERSION)

    def test_hybrid_analysis_requires_enough_history(self):
        posts = [
            {
                'published_at': f'2026-07-{index + 1:02d}T10:00:00+08:00',
                'sentiment': 1,
                'engagement': {'likes': index},
                'text': '记录',
            }
            for index in range(5)
        ]
        result = _hybrid_analysis(posts)
        self.assertFalse(result['available'])
        self.assertIn('10', result['reason'])


if __name__ == '__main__':
    unittest.main()
