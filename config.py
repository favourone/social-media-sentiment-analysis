# -*- coding: utf-8 -*-
"""
系统配置文件
============
所有模块共用的配置参数集中管理
"""

import os


def env_bool(name, default=False):
    """Parse a boolean environment variable without surprising truthiness."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {'1', 'true', 'yes', 'on'}

# ==================== 基础路径 ====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
RAW_DATA_DIR = os.path.join(DATA_DIR, 'raw')
PROCESSED_DATA_DIR = os.path.join(DATA_DIR, 'processed')
MODEL_DIR = os.path.join(BASE_DIR, 'models', 'saved')
DATASET_METADATA_PATH = os.path.join(RAW_DATA_DIR, 'metadata.json')
MODEL_METRICS_PATH = os.path.join(PROCESSED_DATA_DIR, 'model_metrics.json')

# ==================== MongoDB 配置 ====================
MONGO_ENABLED = env_bool('MONGO_ENABLED', False)
MONGO_HOST = os.environ.get('MONGO_HOST', 'localhost')
MONGO_PORT = int(os.environ.get('MONGO_PORT', 27017))
MONGO_DB = os.environ.get('MONGO_DB', 'sentiment_db')
MONGO_CONNECT_TIMEOUT_MS = int(os.environ.get('MONGO_CONNECT_TIMEOUT_MS', 3000))
MONGO_COLLECTION_POSTS = 'posts'           # 帖子/微博
MONGO_COLLECTION_COMMENTS = 'comments'      # 评论
MONGO_COLLECTION_TOPICS = 'topics'          # 话题
MONGO_COLLECTION_PREDICTIONS = 'predictions' # 预测结果

# ==================== Redis 配置 ====================
REDIS_HOST = 'localhost'
REDIS_PORT = 6379
REDIS_DB = 0
REDIS_CACHE_TTL = 3600  # 缓存过期时间（秒）

# ==================== 爬虫配置 ====================
CRAWLER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/120.0.0.0 Safari/537.36'
}
CRAWLER_DELAY = 2       # 请求间隔（秒）
CRAWLER_MAX_PAGES = 50  # 最大爬取页数

# ==================== NLP 配置 ====================
STOPWORDS_PATH = os.path.join(DATA_DIR, 'stopwords.txt')
MAX_VOCAB_SIZE = 50000   # 词表最大大小
MAX_SEQ_LENGTH = 200     # 文本最大序列长度
EMBEDDING_DIM = 128      # 词向量维度

# ==================== TextCNN 模型配置 ====================
TEXTCNN_FILTER_SIZES = [3, 4, 5]    # 卷积核大小
TEXTCNN_NUM_FILTERS = 128            # 每种卷积核数量
TEXTCNN_DROPOUT = 0.5                # Dropout 比率
TEXTCNN_LEARNING_RATE = 0.001        # 学习率
TEXTCNN_BATCH_SIZE = 64              # 批大小
TEXTCNN_EPOCHS = 20                  # 训练轮数
TEXTCNN_NUM_CLASSES = 2              # 分类数（正面/负面）
RANDOM_SEED = int(os.environ.get('RANDOM_SEED', 42))

# ==================== LDA 模型配置 ====================
LDA_NUM_TOPICS = 10      # 主题数量
LDA_PASSES = 20          # 训练遍数
LDA_ITERATIONS = 400     # 迭代次数

# ==================== SIR 模型配置 ====================
SIR_BETA = 0.3           # 传播率
SIR_GAMMA = 0.1          # 恢复率
SIR_TOTAL_USERS = 1000000  # 总用户数
SIR_TIME_SPAN = 30       # 预测时间跨度（天）

# ==================== ARIMA 模型配置 ====================
ARIMA_ORDER = (2, 1, 2)  # (p, d, q) 参数
ARIMA_FORECAST_DAYS = 7  # 预测天数

# ==================== Web 配置 ====================
WEB_HOST = '0.0.0.0'
WEB_PORT = int(os.environ.get('WEB_PORT', 5000))
WEB_DEBUG = env_bool('WEB_DEBUG', False)
CORS_ORIGINS = [
    origin.strip()
    for origin in os.environ.get('CORS_ORIGINS', '').split(',')
    if origin.strip()
]

# ==================== 情感标签 ====================
SENTIMENT_LABELS = {0: '负面', 1: '正面'}
SENTIMENT_COLORS = {0: '#ff4d4f', 1: '#52c41a'}
