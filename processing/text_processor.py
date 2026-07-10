# -*- coding: utf-8 -*-
"""
文本处理模块
============
功能：文本清洗、jieba分词、停用词过滤、特征提取（TF-IDF）

数学原理：
---------
TF-IDF（词频-逆文档频率）
  TF(t,d) = 词t在文档d中出现的次数 / 文档d的总词数
  IDF(t) = log(文档总数 / 包含词t的文档数 + 1)
  TF-IDF(t,d) = TF(t,d) × IDF(t)

  直觉：一个词在当前文档中出现越多（TF高），同时在其他文档中出现越少（IDF高），
       则该词对当前文档越重要。
"""

import re
import os
import sys
import json

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

import jieba
import numpy as np
from collections import Counter

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False


class TextProcessor:
    """文本处理器"""

    def __init__(self):
        self.stopwords = set()
        self.tfidf_vectorizer = None
        self.vocab = {}          # 词 -> 索引
        self.idx2word = {}       # 索引 -> 词
        self._load_stopwords()

    def _load_stopwords(self):
        """加载停用词表"""
        path = config.STOPWORDS_PATH
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                self.stopwords = set(line.strip() for line in f if line.strip())
        # 补充常见停用词
        self.stopwords.update([' ', '\n', '\t', '的', '了', '在', '是', '我',
                                '有', '和', '就', '不', '人', '都', '一', '一个'])

    # ==================== 文本清洗 ====================

    @staticmethod
    def clean_text(text):
        """
        文本清洗
        - 去除URL、@用户、话题标签
        - 去除特殊字符和多余空白
        """
        if not text:
            return ""
        # 去除URL
        text = re.sub(r'http[s]?://\S+', '', text)
        # 去除 @用户名
        text = re.sub(r'@[\w]+', '', text)
        # 去除话题标签 ## 内容
        text = re.sub(r'#.*?#', '', text)
        # 去除表情符号 [xxx]
        text = re.sub(r'\[.*?\]', '', text)
        # 去除HTML标签
        text = re.sub(r'<[^>]+>', '', text)
        # 去除多余空白
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    # ==================== 分词 ====================

    def tokenize(self, text):
        """
        jieba 分词 + 停用词过滤
        返回：词列表
        """
        text = self.clean_text(text)
        if not text:
            return []
        words = jieba.lcut(text)
        # 过滤停用词和单字
        words = [w for w in words if w not in self.stopwords and len(w) > 1]
        return words

    def tokenize_batch(self, texts):
        """批量分词"""
        return [self.tokenize(text) for text in texts]

    # ==================== 词表构建 ====================

    def build_vocab(self, texts, max_size=None):
        """
        构建词表
        参数：
            texts: 文本列表
            max_size: 词表最大大小
        返回：词表字典 {word: index}
        """
        max_size = max_size or config.MAX_VOCAB_SIZE
        counter = Counter()
        for text in texts:
            words = self.tokenize(text)
            counter.update(words)

        # 取词频最高的词
        most_common = counter.most_common(max_size)
        self.vocab = {word: idx + 2 for idx, (word, _) in enumerate(most_common)}
        self.vocab['<PAD>'] = 0   # 填充
        self.vocab['<UNK>'] = 1   # 未知词
        self.idx2word = {idx: word for word, idx in self.vocab.items()}

        print(f"   词表大小：{len(self.vocab)}")
        return self.vocab

    def text_to_sequence(self, text, max_len=None):
        """
        文本转索引序列（用于TextCNN输入）
        参数：
            text: 原始文本
            max_len: 序列最大长度
        返回：索引列表
        """
        max_len = max_len or config.MAX_SEQ_LENGTH
        words = self.tokenize(text)
        seq = [self.vocab.get(w, self.vocab['<UNK>']) for w in words]

        # 填充或截断
        if len(seq) < max_len:
            seq = seq + [self.vocab['<PAD>']] * (max_len - len(seq))
        else:
            seq = seq[:max_len]
        return seq

    def texts_to_sequences(self, texts, max_len=None):
        """批量文本转索引序列"""
        return [self.text_to_sequence(text, max_len) for text in texts]

    # ==================== TF-IDF ====================

    def fit_tfidf(self, texts):
        """
        训练 TF-IDF 模型
        数学公式：TF-IDF(t,d) = TF(t,d) × log(N / DF(t))
        """
        if not HAS_SKLEARN:
            print("⚠️  scikit-learn 未安装，跳过 TF-IDF")
            return None

        # 对文本进行分词，用空格连接
        tokenized_texts = [' '.join(self.tokenize(text)) for text in texts]

        self.tfidf_vectorizer = TfidfVectorizer(
            max_features=config.MAX_VOCAB_SIZE,
            max_df=0.95,    # 忽略出现在95%以上文档中的词
            min_df=2,        # 忽略出现少于2次的词
        )
        tfidf_matrix = self.tfidf_vectorizer.fit_transform(tokenized_texts)
        print(f"   TF-IDF 矩阵形状：{tfidf_matrix.shape}")
        return tfidf_matrix

    def get_tfidf_keywords(self, text, top_k=10):
        """提取文本的 TF-IDF 关键词"""
        if self.tfidf_vectorizer is None:
            return []

        tokenized = ' '.join(self.tokenize(text))
        tfidf_vec = self.tfidf_vectorizer.transform([tokenized])
        # 兼容不同版本的 sklearn
        if hasattr(self.tfidf_vectorizer, 'get_feature_names_out'):
            feature_names = self.tfidf_vectorizer.get_feature_names_out()
        else:
            feature_names = self.tfidf_vectorizer.get_feature_names()

        scores = tfidf_vec.toarray()[0]
        top_indices = scores.argsort()[-top_k:][::-1]
        keywords = [(feature_names[i], scores[i]) for i in top_indices if scores[i] > 0]
        return keywords

    # ==================== 词频统计 ====================

    def get_word_freq(self, texts, top_k=100):
        """统计词频（用于词云）"""
        counter = Counter()
        for text in texts:
            words = self.tokenize(text)
            counter.update(words)
        return counter.most_common(top_k)


# 全局实例
processor = TextProcessor()
