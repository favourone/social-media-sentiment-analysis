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


def step3_train_textcnn(posts, processor):
    """步骤3：训练 TextCNN 情感分类模型"""
    print("\n" + "=" * 60)
    print("  步骤 3/5：训练 TextCNN 情感分类模型")
    print("=" * 60)

    try:
        from models.textcnn import TextCNNModel
        from sklearn.model_selection import train_test_split
        import numpy as np
    except ImportError as e:
        print(f"   ⚠️  依赖缺失：{e}，跳过 TextCNN 训练")
        print("   提示：运行 pip install torch scikit-learn 安装依赖")
        return None

    texts = [p.get('text', '') for p in posts]
    labels = [p.get('sentiment', 0) for p in posts]

    # 文本转序列
    print("\n   [3.1] 文本转索引序列...")
    sequences = processor.texts_to_sequences(texts)
    X = np.array(sequences)
    y = np.array(labels)
    print(f"   数据形状：X={X.shape}, y={y.shape}")

    # 划分训练/验证/测试集
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    X_train, X_val, y_train, y_val = train_test_split(X_train, y_train, test_size=0.1, random_state=42)
    print(f"   训练集：{len(X_train)}，验证集：{len(X_val)}，测试集：{len(X_test)}")

    # 构建模型
    print("\n   [3.2] 构建 TextCNN 模型...")
    model = TextCNNModel(vocab_size=len(processor.vocab))
    model.build_model()

    # 训练
    print("\n   [3.3] 开始训练...")
    model.train(X_train, y_train, X_val, y_val)

    # 评估
    print("\n   [3.4] 模型评估...")
    metrics = model.evaluate(X_test, y_test)
    print(f"   测试集准确率：{metrics['accuracy']:.4f}")
    print(f"   测试集损失：{metrics['loss']:.4f}")

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
