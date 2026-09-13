# -*- coding: utf-8 -*-
"""
系统配置文件
============
所有模块共用的配置参数集中管理
"""

import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # Docker supplies env_file directly; python-dotenv is only a local convenience.
    pass


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
RUNTIME_DIR = os.path.abspath(os.environ.get(
    'RUNTIME_DIR', os.path.join(BASE_DIR, 'runtime')
))
PRODUCT_DB_PATH = os.path.abspath(os.environ.get(
    'PRODUCT_DB_PATH', os.path.join(RUNTIME_DIR, 'sentiment.db')
))
REPORT_DIR = os.path.abspath(os.environ.get(
    'REPORT_DIR', os.path.join(RUNTIME_DIR, 'reports')
))
IMPORT_DIR = os.path.abspath(os.environ.get(
    'IMPORT_DIR', os.path.join(RUNTIME_DIR, 'imports')
))

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
REDIS_HOST = os.environ.get('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.environ.get('REDIS_PORT', 6379))
REDIS_DB = int(os.environ.get('REDIS_DB', 0))
REDIS_URL = os.environ.get(
    'REDIS_URL', f'redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}'
)
REDIS_CACHE_TTL = 3600  # 缓存过期时间（秒）
TASK_QUEUE_MODE = os.environ.get('TASK_QUEUE_MODE', 'inline').strip().lower()
RQ_QUEUE_NAME = os.environ.get('RQ_QUEUE_NAME', 'sentiment-jobs')
MONITOR_SCHEDULER_SECONDS = int(os.environ.get('MONITOR_SCHEDULER_SECONDS', 30))

# ==================== 爬虫配置 ====================
CRAWLER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/120.0.0.0 Safari/537.36'
}
CRAWLER_DELAY = 2       # 请求间隔（秒）
CRAWLER_MAX_PAGES = 50  # 最大爬取页数
MEDIACRAWLER_HOME = os.path.abspath(os.environ.get(
    'MEDIACRAWLER_HOME', os.path.join(BASE_DIR, 'external', 'MediaCrawler')
))
MEDIACRAWLER_COMMAND_JSON = os.environ.get('MEDIACRAWLER_COMMAND_JSON', '')
MEDIACRAWLER_OUTPUT_DIR = os.path.abspath(os.environ.get(
    'MEDIACRAWLER_OUTPUT_DIR', os.path.join(MEDIACRAWLER_HOME, 'data')
))
MEDIACRAWLER_TIMEOUT_SECONDS = int(os.environ.get(
    'MEDIACRAWLER_TIMEOUT_SECONDS', 1800
))
COLLECTOR_LICENSE_ACCEPTED = env_bool('COLLECTOR_LICENSE_ACCEPTED', False)
RSS_TIMEOUT_SECONDS = int(os.environ.get('RSS_TIMEOUT_SECONDS', 15))
RSS_MAX_BYTES = int(os.environ.get('RSS_MAX_BYTES', 2 * 1024 * 1024))
ALLOW_PRIVATE_NETWORK_URLS = env_bool('ALLOW_PRIVATE_NETWORK_URLS', False)
WEBHOOK_TIMEOUT_SECONDS = int(os.environ.get('WEBHOOK_TIMEOUT_SECONDS', 10))

# ==================== Optional evidence-grounded LLM ====================
LLM_ENABLED = env_bool('LLM_ENABLED', False)
LLM_BASE_URL = os.environ.get('LLM_BASE_URL', '').rstrip('/')
LLM_API_KEY = os.environ.get('LLM_API_KEY', '')
LLM_MODEL = os.environ.get('LLM_MODEL', '')
LLM_TIMEOUT_SECONDS = int(os.environ.get('LLM_TIMEOUT_SECONDS', 60))

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

# ==================== 算法升级 V3（suanfa 分支） ====================
# 情感分析引擎：auto=存在微调检查点时优先 BERT，否则回退透明词典；
# bert=强制 BERT（无检查点时报错并回退）；lexicon=只用词典。
SENTIMENT_ENGINE = os.environ.get('SENTIMENT_ENGINE', 'auto').strip().lower()
SENTIMENT_BERT_MODEL_DIR = os.path.abspath(os.environ.get(
    'SENTIMENT_BERT_MODEL_DIR', os.path.join(MODEL_DIR, 'bert_sentiment')
))
SENTIMENT_BERT_MAX_LENGTH = int(os.environ.get('SENTIMENT_BERT_MAX_LENGTH', 128))
SENTIMENT_BERT_DEVICE = os.environ.get('SENTIMENT_BERT_DEVICE', '').strip()

# 事件聚类引擎：ngram=字符 n-gram TF-IDF 基线（默认，稳定可复现）；
# auto=信号量足够且依赖可用时用 BERTopic，否则自动回退 ngram；bertopic=强制尝试。
CLUSTERING_ENGINE = os.environ.get('CLUSTERING_ENGINE', 'ngram').strip().lower()
BERTOPIC_EMBEDDING_MODEL = os.environ.get(
    'BERTOPIC_EMBEDDING_MODEL', 'paraphrase-multilingual-MiniLM-L12-v2'
)
BERTOPIC_MIN_TOPIC_SIZE = int(os.environ.get('BERTOPIC_MIN_TOPIC_SIZE', 3))
BERTOPIC_MIN_DOCS = int(os.environ.get('BERTOPIC_MIN_DOCS', 8))
BERTOPIC_RANDOM_STATE = int(os.environ.get('BERTOPIC_RANDOM_STATE', 42))

# 混合趋势预测：ARIMA/SIR 基线 + LSTM 残差修正（SIR 形状约束作为损失先验）。
HYBRID_FORECAST_DAYS = int(os.environ.get('HYBRID_FORECAST_DAYS', 7))
HYBRID_HIDDEN_SIZE = int(os.environ.get('HYBRID_HIDDEN_SIZE', 24))
HYBRID_EPOCHS = int(os.environ.get('HYBRID_EPOCHS', 300))
HYBRID_SIR_PRIOR_WEIGHT = float(os.environ.get('HYBRID_SIR_PRIOR_WEIGHT', 0.15))

# ==================== Web 配置 ====================
WEB_HOST = '0.0.0.0'
WEB_PORT = int(os.environ.get('WEB_PORT', 5000))
WEB_DEBUG = env_bool('WEB_DEBUG', False)
CORS_ORIGINS = [
    origin.strip()
    for origin in os.environ.get('CORS_ORIGINS', '').split(',')
    if origin.strip()
]
APP_SECRET_KEY = os.environ.get('APP_SECRET_KEY', '')
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin').strip() or 'admin'
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '')
SESSION_HOURS = int(os.environ.get('SESSION_HOURS', 8))
LOGIN_MAX_ATTEMPTS = int(os.environ.get('LOGIN_MAX_ATTEMPTS', 5))
LOGIN_WINDOW_SECONDS = int(os.environ.get('LOGIN_WINDOW_SECONDS', 300))
MAX_UPLOAD_BYTES = int(os.environ.get('MAX_UPLOAD_BYTES', 10 * 1024 * 1024))

# ==================== 情感标签 ====================
SENTIMENT_LABELS = {0: '负面', 1: '正面'}
SENTIMENT_COLORS = {0: '#ff4d4f', 1: '#52c41a'}
