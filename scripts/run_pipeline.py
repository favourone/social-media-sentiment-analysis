# -*- coding: utf-8 -*-
"""
完整数据处理流水线
==================
执行顺序：
1. 生成/加载数据
2. 数据预处理（分词、特征提取）
3. 模型训练（TextCNN、LDA、SIR、ARIMA）
4. 启动 Web 应用

运行方式：python scripts/run_pipeline.py
"""

import os
import sys
import json
import time
from datetime import datetime, timezone

# Windows 终端 UTF-8 编码修复
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


def step1_generate_data():
    """步骤1：生成模拟数据"""
    print("\n" + "=" * 60)
    print("  步骤 1/5：生成模拟数据")
    print("=" * 60)

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from generate_data import generate_dataset

    posts_path = os.path.join(config.RAW_DATA_DIR, 'posts.json')
    if os.path.exists(posts_path):
        print("   数据已存在，跳过生成")
        with open(posts_path, 'r', encoding='utf-8') as f:
            posts = json.load(f)
        with open(os.path.join(config.RAW_DATA_DIR, 'comments.json'), 'r', encoding='utf-8') as f:
            comments = json.load(f)
        return posts, comments

    return generate_dataset()


def step2_text_processing(posts):
    """步骤2：文本预处理"""
    print("\n" + "=" * 60)
    print("  步骤 2/5：文本预处理")
    print("=" * 60)

    from processing.text_processor import TextProcessor

    processor = TextProcessor()
    texts = [p.get('text', '') for p in posts]

    # 分词
    print("\n   [2.1] jieba 分词...")
    tokenized = processor.tokenize_batch(texts)
    total_words = sum(len(t) for t in tokenized)
    print(f"   分词完成：{len(texts)} 篇文本，{total_words} 个词")

    # 构建词表
    print("\n   [2.2] 构建词表...")
    vocab = processor.build_vocab(texts)

    # TF-IDF
    print("\n   [2.3] 训练 TF-IDF...")
    tfidf_matrix = processor.fit_tfidf(texts)

    # 词频统计
    print("\n   [2.4] 词频统计...")
    word_freq = processor.get_word_freq(texts, top_k=30)
    print("   Top 10 高频词：")
    for word, count in word_freq[:10]:
        print(f"      {word}: {count}")

    return processor, tokenized, vocab


def prepare_textcnn_data(posts, random_state=None):
    """Split raw text first, then build a vocabulary from training text only."""
    from sklearn.model_selection import train_test_split
    import numpy as np
    from processing.text_processor import TextProcessor

    random_state = config.RANDOM_SEED if random_state is None else random_state
    texts = [post.get('text', '') for post in posts]
    labels = np.array([post.get('sentiment', 0) for post in posts])
    if len(texts) < 100:
        raise ValueError('TextCNN 评估至少需要 100 条数据')
    unique_labels, counts = np.unique(labels, return_counts=True)
    if len(unique_labels) < 2 or np.min(counts) < 3:
        raise ValueError('每个情感类别至少需要 3 条数据')

    train_texts, test_texts, y_train, y_test = train_test_split(
        texts,
        labels,
        test_size=0.2,
        random_state=random_state,
        stratify=labels,
    )
    train_texts, val_texts, y_train, y_val = train_test_split(
        train_texts,
        y_train,
        test_size=0.1,
        random_state=random_state,
        stratify=y_train,
    )

    processor = TextProcessor()
    processor.build_vocab(train_texts)
    return {
        'processor': processor,
        'train_texts': train_texts,
        'val_texts': val_texts,
        'test_texts': test_texts,
        'X_train': np.array(processor.texts_to_sequences(train_texts)),
        'X_val': np.array(processor.texts_to_sequences(val_texts)),
        'X_test': np.array(processor.texts_to_sequences(test_texts)),
        'y_train': np.array(y_train),
        'y_val': np.array(y_val),
        'y_test': np.array(y_test),
    }


def save_model_metrics_report(
    metrics, history, y_test, predicted_labels, sample_sizes, posts=None
):
    """Persist an auditable offline evaluation report for the read-only API."""
    from sklearn.metrics import confusion_matrix, precision_score, recall_score, f1_score

    metadata = {}
    if os.path.exists(config.DATASET_METADATA_PATH):
        with open(config.DATASET_METADATA_PATH, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
    elif posts:
        identifiers = [str(post.get('post_id', '')) for post in posts]
        if identifiers and all(item.startswith(('post_', 'sim_')) for item in identifiers):
            metadata = {
                'dataset_type': 'synthetic',
                'evidence_status': 'inferred',
            }

    report = {
        'schema_version': 1,
        'evaluation_type': 'offline_holdout',
        'evaluated_at': datetime.now(timezone.utc).isoformat(),
        'random_seed': config.RANDOM_SEED,
        'dataset_type': metadata.get('dataset_type', 'unknown'),
        'dataset_evidence_status': metadata.get('evidence_status', 'unknown'),
        'sample_sizes': sample_sizes,
        'accuracy': round(metrics['accuracy'], 4),
        'loss': round(metrics['loss'], 4),
        'precision': round(precision_score(y_test, predicted_labels, zero_division=0), 4),
        'recall': round(recall_score(y_test, predicted_labels, zero_division=0), 4),
        'f1_score': round(f1_score(y_test, predicted_labels, zero_division=0), 4),
        'confusion_matrix': confusion_matrix(y_test, predicted_labels).tolist(),
        'train_loss': [round(item['train_loss'], 4) for item in history],
        'val_loss': [
            round(item['val_loss'], 4) if item['val_loss'] is not None else None
            for item in history
        ],
        'train_acc': [round(item['train_acc'], 4) for item in history],
        'val_acc': [round(item['val_acc'], 4) for item in history],
        'epochs': len(history),
        'claim_scope': '仅描述固定随机种子下的本地留出集结果；模拟或派生数据不能证明真实社交媒体效果。',
    }
    os.makedirs(config.PROCESSED_DATA_DIR, exist_ok=True)
    with open(config.MODEL_METRICS_PATH, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"   评估报告：{config.MODEL_METRICS_PATH}")
    return report


def step3_train_textcnn(posts, processor=None):
    """步骤3：训练 TextCNN 情感分类模型"""
    print("\n" + "=" * 60)
    print("  步骤 3/5：训练 TextCNN 情感分类模型")
    print("=" * 60)

    try:
        from models.textcnn import TextCNNModel
    except ImportError as e:
        print(f"   ⚠️  依赖缺失：{e}，跳过 TextCNN 训练")
        print("   提示：运行 pip install torch scikit-learn 安装依赖")
        return None

    # 先拆分原始文本，随后只使用训练文本构建词表，防止测试集泄漏。
    print("\n   [3.1] 分层拆分数据并由训练集构建词表...")
    prepared = prepare_textcnn_data(posts)
    processor = prepared['processor']
    X_train, X_val, X_test = prepared['X_train'], prepared['X_val'], prepared['X_test']
    y_train, y_val, y_test = prepared['y_train'], prepared['y_val'], prepared['y_test']
    print(f"   训练集：{len(X_train)}，验证集：{len(X_val)}，测试集：{len(X_test)}")

    # 构建模型
    print("\n   [3.2] 构建 TextCNN 模型...")
    model = TextCNNModel(vocab_size=len(processor.vocab))
    if model.build_model() is None:
        print("   ⚠️  TextCNN 不可用，未生成评估报告")
        return None

    # 训练
    print("\n   [3.3] 开始训练...")
    history = model.train(X_train, y_train, X_val, y_val)

    # 评估
    print("\n   [3.4] 模型评估...")
    metrics = model.evaluate(X_test, y_test)
    if not metrics:
        raise RuntimeError('TextCNN 测试集评估失败')
    print(f"   测试集准确率：{metrics['accuracy']:.4f}")
    print(f"   测试集损失：{metrics['loss']:.4f}")

    predictions = model.predict(X_test)
    if not predictions:
        raise RuntimeError('TextCNN 测试集预测失败')
    predicted_labels = [item['label'] for item in predictions]
    save_model_metrics_report(
        metrics,
        history or [],
        y_test,
        predicted_labels,
        {
            'train': len(X_train),
            'validation': len(X_val),
            'test': len(X_test),
            'total': len(posts),
        },
        posts=posts,
    )

    # 保存模型
    model.save()

    return model


def step4_topic_and_prediction(posts, tokenized):
    """步骤4：主题分析 + 传播预测"""
    print("\n" + "=" * 60)
    print("  步骤 4/5：主题分析 & 传播预测")
    print("=" * 60)

    import numpy as np

    # 4.1 LDA 主题分析
    print("\n   [4.1] LDA 主题分析...")
    from models.lda_model import LDAModel
    lda = LDAModel(num_topics=5)
    lda.fit(tokenized)
    lda.print_topics(top_k=5)

    # 4.2 SIR 传播预测
    print("\n   [4.2] SIR 传播预测...")
    from models.sir_model import SIRModel

    # 按话题统计每日讨论量
    from collections import defaultdict
    topic_daily = defaultdict(lambda: defaultdict(int))
    for p in posts:
        date = p.get('created_at', '')[:10]
        topic = p.get('topic', '')
        if date and topic:
            topic_daily[topic][date] += 1

    # 对第一个话题做 SIR 预测
    test_topic = list(topic_daily.keys())[0]
    daily_counts = sorted(topic_daily[test_topic].items())
    values = [c for _, c in daily_counts]

    sir = SIRModel()
    if len(values) > 5:
        sir_result = sir.fit(values[:30])
    else:
        sir_result = sir.simulate(100, 30)

    print(f"   话题「{test_topic}」传播预测：")
    print(f"      峰值时间：第 {sir_result['peak_day']:.1f} 天")
    print(f"      峰值讨论人数：{sir_result['peak_value']:.0f}")
    print(f"      R₀(基本再生数)：{sir_result['R0']:.2f}")

    # 4.3 ARIMA 时间序列预测
    print("\n   [4.3] ARIMA 时间序列预测...")
    from models.arima_model import ARIMAPredictor

    arima = ARIMAPredictor()
    if len(values) > 10:
        arima.fit(values)
        forecast = arima.predict(steps=7)
    else:
        print("   ⚠️  数据量不足，使用模拟数据")
        mock_data = [100, 150, 200, 500, 2000, 8000, 15000, 12000, 8000, 5000,
                     3000, 2000, 1500, 1200, 1000, 800, 700, 600, 500, 450]
        arima.fit(mock_data)
        forecast = arima.predict(steps=7)

    return {'sir': sir_result, 'arima': forecast}


def step5_import_to_mongo():
    """步骤5：导入数据到 MongoDB"""
    print("\n" + "=" * 60)
    print("  步骤 5/5：导入数据到 MongoDB")
    print("=" * 60)

    from storage.mongo_client import MongoStorage
    db = MongoStorage()
    if db.connect():
        db.import_all_data()
        db.close()
    else:
        print("   MongoDB 不可用，使用本地 JSON 文件存储")


def main():
    """主流程"""
    print("\n" + "🌟" * 30)
    print("  社交媒体舆情分析与热点事件预测系统")
    print("  完整数据处理流水线")
    print("🌟" * 30)

    start_time = time.time()

    # 执行流水线
    posts, comments = step1_generate_data()
    processor, tokenized, vocab = step2_text_processing(posts)
    textcnn_model = step3_train_textcnn(posts, processor)
    predictions = step4_topic_and_prediction(posts, tokenized)
    step5_import_to_mongo()

    elapsed = time.time() - start_time

    print("\n" + "=" * 60)
    print(f"  ✅ 流水线执行完成！耗时：{elapsed:.1f} 秒")
    print("=" * 60)
    print("\n  启动 Web 应用：python -m web.app")
    print(f"  访问地址：http://localhost:{config.WEB_PORT}")
    print()


if __name__ == '__main__':
    main()
