# -*- coding: utf-8 -*-
"""
LDA 主题模型
============
数学原理：
---------
LDA（Latent Dirichlet Allocation，潜在狄利克雷分配）
是一种概率主题模型，假设每篇文档是多个主题的混合，每个主题是多个词的概率分布。

生成过程：
  对每篇文档 d：
    1. 从 Dirichlet(α) 采样主题分布 θ_d
    2. 对文档中的每个词位置：
       a. 从 Multinomial(θ_d) 采样主题 z
       b. 从 Multinomial(φ_z) 采样词 w

参数：
  α = 文档-主题分布的先验参数
  β = 主题-词分布的先验参数
  K = 主题数量
  θ_d = 文档 d 的主题分布（K维向量）
  φ_k = 主题 k 的词分布（V维向量）

推断方法：Gibbs 采样或变分推断
"""

import os
import sys

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

try:
    from gensim import corpora, models
    HAS_GENSIM = True
except ImportError:
    HAS_GENSIM = False


class LDAModel:
    """
    LDA 主题模型

    用法：
        lda = LDAModel()
        lda.fit(tokenized_texts)
        topics = lda.get_topics(top_k=10)
    """

    def __init__(self, num_topics=None):
        self.num_topics = num_topics or config.LDA_NUM_TOPICS
        self.dictionary = None
        self.corpus = None
        self.model = None

    def fit(self, tokenized_texts):
        """
        训练 LDA 模型

        参数：
            tokenized_texts: 分词后的文本列表，如 [['AI', '大模型', '突破'], ...]
        """
        if not HAS_GENSIM:
            print("⚠️  gensim 未安装")
            return None

        # 构建词典
        self.dictionary = corpora.Dictionary(tokenized_texts)
        # 过滤极端词
        self.dictionary.filter_extremes(no_below=5, no_above=0.5)
        print(f"   词典大小：{len(self.dictionary)}")

        # 构建语料库（词袋模型）
        self.corpus = [self.dictionary.doc2bow(text) for text in tokenized_texts]

        # 训练 LDA
        self.model = models.LdaModel(
            corpus=self.corpus,
            id2word=self.dictionary,
            num_topics=self.num_topics,
            passes=config.LDA_PASSES,
            iterations=config.LDA_ITERATIONS,
            random_state=42,
            alpha='auto',      # 自动学习 α
            eta='auto',        # 自动学习 β（gensim中叫eta）
        )

        print(f"   ✅ LDA 模型训练完成（{self.num_topics} 个主题）")
        return self.model

    def get_topics(self, top_k=10):
        """
        获取所有主题及其关键词

        返回：
            [{topic_id, keywords: [(word, weight), ...]}, ...]
        """
        if self.model is None:
            return []

        topics = []
        for topic_id in range(self.num_topics):
            word_weights = self.model.show_topic(topic_id, topn=top_k)
            topics.append({
                'topic_id': topic_id,
                'keywords': [(word, float(weight)) for word, weight in word_weights],
                'label': self._auto_label(word_weights),
            })
        return topics

    def get_document_topics(self, tokenized_text):
        """
        获取文档的主题分布

        参数：
            tokenized_text: 分词后的文本
        返回：
            [(topic_id, probability), ...]
        """
        if self.model is None or self.dictionary is None:
            return []

        bow = self.dictionary.doc2bow(tokenized_text)
        doc_topics = self.model.get_document_topics(bow, minimum_probability=0.01)
        return [(tid, float(prob)) for tid, prob in doc_topics]

    def get_topic_trends(self, tokenized_texts, dates):
        """
        分析主题随时间的变化趋势

        参数：
            tokenized_texts: 分词后的文本列表
            dates: 对应的日期列表
        返回：
            {topic_id: {date: count}}
        """
        from collections import defaultdict

        topic_trends = defaultdict(lambda: defaultdict(int))
        topic_counts = defaultdict(int)

        for text, date in zip(tokenized_texts, dates):
            topics = self.get_document_topics(text)
            if topics:
                # 取概率最大的主题
                main_topic = max(topics, key=lambda x: x[1])
                topic_trends[main_topic[0]][date] += 1
                topic_counts[main_topic[0]] += 1

        return dict(topic_trends), dict(topic_counts)

    def _auto_label(self, word_weights):
        """自动为主题生成标签（取前3个关键词）"""
        top_words = [w for w, _ in word_weights[:3]]
        return '/'.join(top_words)

    def print_topics(self, top_k=10):
        """打印所有主题"""
        topics = self.get_topics(top_k)
        print(f"\n📋 发现 {len(topics)} 个主题：")
        print("-" * 60)
        for topic in topics:
            keywords_str = ', '.join([f"{w}({p:.3f})" for w, p in topic['keywords'][:5]])
            print(f"   主题 {topic['topic_id']}: {topic['label']}")
            print(f"   关键词: {keywords_str}")
            print()
