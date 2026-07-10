# -*- coding: utf-8 -*-
"""
Flask Web 应用
==============
舆情监控大屏后端，提供 RESTful API 和页面渲染
支持时间范围筛选和话题筛选
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

from flask import Flask, render_template, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)


def filter_posts(posts, start_date=None, end_date=None, topic=None):
    """过滤帖子数据"""
    filtered = []
    for p in posts:
        date = p.get('created_at', '')[:10]
        p_topic = p.get('topic', '')

        if start_date and date and date < start_date:
            continue
        if end_date and date and date > end_date:
            continue
        if topic and topic != 'all' and p_topic != topic:
            continue

        filtered.append(p)
    return filtered


def filter_time_series(series, start_date=None, end_date=None, topic=None):
    """过滤时间序列数据"""
    filtered = []
    for s in series:
        date = s.get('date', '')[:10]
        s_topic = s.get('topic', '')

        if start_date and date and date < start_date:
            continue
        if end_date and date and date > end_date:
            continue
        if topic and topic != 'all' and s_topic != topic:
            continue

        filtered.append(s)
    return filtered


@app.route('/')
def index():
    """舆情监控大屏首页"""
    return render_template('index.html')


@app.route('/api/overview')
def api_overview():
    """
    获取总览数据
    参数：start_date, end_date, topic
    返回：帖子总数、评论总数、话题数、情感分布
    """
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    topic = request.args.get('topic', 'all')

    from storage.mongo_client import get_db
    db = get_db()
    posts = db.get_all_posts(limit=200000)
    comments = db._load_from_json('comments.json')

    posts = filter_posts(posts, start_date, end_date, topic)

    total_posts = len(posts)
    total_comments = len(comments)
    positive = sum(1 for p in posts if p.get('sentiment') == 1)
    negative = total_posts - positive

    topics = set(p.get('topic', '') for p in posts)

    platforms = {}
    for p in posts:
        src = p.get('source', '未知')
        platforms[src] = platforms.get(src, 0) + 1

    result = {
        'total_posts': total_posts,
        'total_comments': total_comments,
        'total_topics': len(topics),
        'positive_count': positive,
        'negative_count': negative,
        'positive_ratio': round(positive / max(total_posts, 1) * 100, 1),
        'negative_ratio': round(negative / max(total_posts, 1) * 100, 1),
        'platforms': platforms,
    }
    return jsonify(result)


@app.route('/api/topic_stats')
def api_topic_stats():
    """获取各话题统计数据"""
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    from storage.mongo_client import get_db
    db = get_db()
    posts = db.get_all_posts(limit=200000)

    posts = filter_posts(posts, start_date, end_date)

    stats = {}
    for p in posts:
        t = p.get('topic', '未知')
        if t not in stats:
            stats[t] = {'_id': t, 'topic': t, 'count': 0, 'positive': 0, 'negative': 0,
                        'total_reposts': 0, 'likes_sum': 0}
        stats[t]['count'] += 1
        stats[t]['positive'] += 1 if p.get('sentiment') == 1 else 0
        stats[t]['negative'] += 1 if p.get('sentiment') == 0 else 0
        stats[t]['total_reposts'] += p.get('reposts', 0)
        stats[t]['likes_sum'] += p.get('likes', 0)

    result = []
    for t, s in stats.items():
        s['avg_likes'] = round(s['likes_sum'] / max(s['count'], 1), 0)
        s['positive_ratio'] = round(s['positive'] / max(s['count'], 1) * 100, 1)
        result.append(s)

    return jsonify(sorted(result, key=lambda x: x['count'], reverse=True))


@app.route('/api/sentiment_trend')
def api_sentiment_trend():
    """获取情感趋势数据"""
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    topic = request.args.get('topic', 'all')

    from storage.mongo_client import get_db
    db = get_db()
    posts = db.get_all_posts(limit=200000)

    posts = filter_posts(posts, start_date, end_date, topic)

    daily = {}
    for p in posts:
        date = p.get('created_at', '')[:10]
        if not date:
            continue
        if date not in daily:
            daily[date] = {'positive': 0, 'negative': 0}
        if p.get('sentiment') == 1:
            daily[date]['positive'] += 1
        else:
            daily[date]['negative'] += 1

    sorted_dates = sorted(daily.keys())
    result = {
        'dates': sorted_dates,
        'positive': [daily[d]['positive'] for d in sorted_dates],
        'negative': [daily[d]['negative'] for d in sorted_dates],
    }
    return jsonify(result)


@app.route('/api/word_freq')
def api_word_freq():
    """获取词频数据（用于词云）"""
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    topic = request.args.get('topic', 'all')

    from storage.mongo_client import get_db
    from processing.text_processor import TextProcessor
    db = get_db()
    processor = TextProcessor()
    posts = db.get_all_posts(limit=50000)

    posts = filter_posts(posts, start_date, end_date, topic)

    texts = [p.get('text', '') for p in posts]
    word_freq = processor.get_word_freq(texts, top_k=100)
    result = [{'name': w, 'value': c} for w, c in word_freq]
    return jsonify(result)


@app.route('/api/sir_prediction')
def api_sir_prediction():
    """
    SIR 传播预测
    参数：topic（可选）、days（预测天数）、start_date、end_date
    """
    topic = request.args.get('topic', 'AI人工智能')
    days = int(request.args.get('days', 30))
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    from storage.mongo_client import get_db
    from models.sir_model import SIRModel
    db = get_db()

    series_data = db._load_from_json('time_series.json', topic=topic)
    if not series_data:
        series_data = db._load_from_json('time_series.json')

    series_data = filter_time_series(series_data, start_date, end_date, topic)

    values = [d['value'] for d in series_data if d.get('topic') == topic]
    if not values:
        values = [100, 150, 200, 500, 2000, 8000, 15000, 12000, 8000, 5000,
                  3000, 2000, 1500, 1200, 1000, 800, 700, 600, 500, 450]

    sir = SIRModel()
    sir.fit(values[:30])
    forecast = sir.predict(days=days)

    return jsonify({
        'topic': topic,
        'time': forecast['time'],
        'S': forecast['S'],
        'I': forecast['I'],
        'R': forecast['R'],
        'peak_day': forecast['peak_day'],
        'peak_value': forecast['peak_value'],
        'R0': forecast['R0'],
        'phase': sir.get_status(len(values), values)['phase'],
        'risk_level': sir.get_status(len(values), values)['risk_level'],
    })


@app.route('/api/arima_prediction')
def api_arima_prediction():
    """
    ARIMA 时间序列预测
    参数：topic（可选）、steps（预测步数）、start_date、end_date
    """
    topic = request.args.get('topic', 'AI人工智能')
    steps = int(request.args.get('steps', 7))
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    from storage.mongo_client import get_db
    from models.arima_model import ARIMAPredictor
    db = get_db()

    series_data = db._load_from_json('time_series.json', topic=topic)
    if not series_data:
        series_data = db._load_from_json('time_series.json')

    series_data = filter_time_series(series_data, start_date, end_date, topic)

    values = [d['value'] for d in series_data if d.get('topic') == topic]
    if not values:
        values = [100, 150, 200, 500, 2000, 8000, 15000, 12000, 8000, 5000,
                  3000, 2000, 1500, 1200, 1000, 800, 700, 600, 500, 450]

    arima = ARIMAPredictor()
    arima.fit(values)
    prediction = arima.predict(steps=steps)

    dates = [d['date'] for d in series_data if d.get('topic') == topic]
    history_dates = dates[-30:] if len(dates) >= 30 else dates
    history_values = values[-30:] if len(values) >= 30 else values

    return jsonify({
        'topic': topic,
        'history_dates': history_dates,
        'history_values': history_values,
        'forecast': prediction['forecast'],
        'lower_bound': prediction['lower_bound'],
        'upper_bound': prediction['upper_bound'],
        'trend': prediction.get('trend', '未知'),
        'change_rate': prediction.get('change_rate', 0),
    })


@app.route('/api/lda_topics')
def api_lda_topics():
    """获取 LDA 主题分析结果"""
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    topic = request.args.get('topic', 'all')

    from storage.mongo_client import get_db
    from processing.text_processor import TextProcessor
    from models.lda_model import LDAModel
    db = get_db()
    processor = TextProcessor()

    posts = db.get_all_posts(limit=10000)
    posts = filter_posts(posts, start_date, end_date, topic)

    texts = [p.get('text', '') for p in posts]
    tokenized = [processor.tokenize(text) for text in texts]
    tokenized = [t for t in tokenized if len(t) > 0]

    lda = LDAModel(num_topics=5)
    lda.fit(tokenized)
    topics = lda.get_topics(top_k=8)
    return jsonify(topics)


@app.route('/api/recent_posts')
def api_recent_posts():
    """获取最新帖子"""
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    topic = request.args.get('topic', 'all')

    from storage.mongo_client import get_db
    db = get_db()
    posts = db.get_all_posts(limit=200000)

    posts = filter_posts(posts, start_date, end_date, topic)
    posts = sorted(posts, key=lambda x: x.get('created_at', ''), reverse=True)[:20]

    result = []
    for p in posts:
        result.append({
            'post_id': p.get('post_id', ''),
            'user_name': p.get('user_name', ''),
            'text': p.get('text', '')[:100],
            'topic': p.get('topic', ''),
            'sentiment': p.get('sentiment', 0),
            'sentiment_text': config.SENTIMENT_LABELS.get(p.get('sentiment', 0), '未知'),
            'likes': p.get('likes', 0),
            'reposts': p.get('reposts', 0),
            'created_at': p.get('created_at', ''),
            'source': p.get('source', ''),
        })
    return jsonify(result)


@app.route('/api/alerts')
def api_alerts():
    """获取预警信息"""
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    from storage.mongo_client import get_db
    db = get_db()
    posts = db.get_all_posts(limit=200000)

    posts = filter_posts(posts, start_date, end_date)

    stats = {}
    for p in posts:
        t = p.get('topic', '未知')
        if t not in stats:
            stats[t] = {'_id': t, 'topic': t, 'count': 0, 'positive': 0, 'negative': 0}
        stats[t]['count'] += 1
        stats[t]['positive'] += 1 if p.get('sentiment') == 1 else 0
        stats[t]['negative'] += 1 if p.get('sentiment') == 0 else 0

    alerts = []
    for t, s in stats.items():
        negative_ratio = s['negative'] / max(s['count'], 1) * 100
        if negative_ratio > 50:
            alerts.append({
                'level': 'high',
                'topic': s.get('topic', s['_id']),
                'message': f"话题「{s.get('topic', s['_id'])}」负面情感占比 {negative_ratio:.1f}%，超过预警阈值",
                'count': s['count'],
                'negative_ratio': round(negative_ratio, 1),
            })
        elif negative_ratio > 40:
            alerts.append({
                'level': 'medium',
                'topic': s.get('topic', s['_id']),
                'message': f"话题「{s.get('topic', s['_id'])}」负面情感占比 {negative_ratio:.1f}%，接近预警阈值",
                'count': s['count'],
                'negative_ratio': round(negative_ratio, 1),
            })
    return jsonify(alerts)


@app.route('/api/model_metrics')
def api_model_metrics():
    """获取模型评估指标"""
    try:
        from storage.mongo_client import get_db
        from processing.text_processor import TextProcessor
        from models.textcnn import TextCNNModel
        from sklearn.model_selection import train_test_split
        import numpy as np

        db = get_db()
        processor = TextProcessor()

        posts = db.get_all_posts(limit=200000)
        texts = [p.get('text', '') for p in posts]
        labels = [p.get('sentiment', 0) for p in posts]

        if len(texts) < 100:
            return jsonify({
                'error': '数据量不足',
                'message': '至少需要100条数据才能评估模型'
            }), 400

        processor.build_vocab(texts)
        sequences = processor.texts_to_sequences(texts)
        X = np.array(sequences)
        y = np.array(labels)

        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
        X_train, X_val, y_train, y_val = train_test_split(X_train, y_train, test_size=0.1, random_state=42)

        model = TextCNNModel(vocab_size=len(processor.vocab))
        model.build_model()

        if model.model is None:
            return jsonify({
                'error': '模型初始化失败',
                'message': 'PyTorch可能未安装'
            }), 500

        history = model.train(X_train, y_train, X_val, y_val)
        metrics = model.evaluate(X_test, y_test)

        if not metrics:
            return jsonify({
                'error': '模型评估失败',
                'message': '模型可能未训练'
            }), 500

        from sklearn.metrics import precision_score, recall_score, f1_score
        y_pred = model.predict(X_test)
        if not y_pred:
            return jsonify({
                'error': '预测失败',
                'message': '模型预测返回空结果'
            }), 500

        y_pred_class = np.array([p['label'] for p in y_pred])

        precision = precision_score(y_test, y_pred_class)
        recall = recall_score(y_test, y_pred_class)
        f1 = f1_score(y_test, y_pred_class)

        train_loss = []
        val_loss = []
        train_acc = []
        val_acc = []
        if history:
            train_loss = [round(h['loss'], 4) for h in history]
            val_loss = [round(h['loss'], 4) for h in history]
            train_acc = [round(h.get('train_acc', 0), 4) for h in history]
            val_acc = [round(h.get('val_acc', 0), 4) for h in history]

        return jsonify({
            'accuracy': round(metrics['accuracy'], 4),
            'loss': round(metrics['loss'], 4),
            'precision': round(precision, 4),
            'recall': round(recall, 4),
            'f1_score': round(f1, 4),
            'train_loss': train_loss,
            'val_loss': val_loss,
            'train_acc': train_acc,
            'val_acc': val_acc,
            'epochs': len(history) if history else 0,
        })
    except Exception as e:
        import traceback
        print(f"❌ 模型评估失败: {e}")
        traceback.print_exc()
        return jsonify({
            'error': str(e),
            'message': '模型评估过程中发生错误'
        }), 500


@app.route('/api/topics')
def api_topics():
    """获取所有话题列表"""
    from storage.mongo_client import get_db
    db = get_db()
    posts = db.get_all_posts(limit=200000)
    topics = sorted(set(p.get('topic', '') for p in posts))
    return jsonify(topics)


def run_web():
    """启动 Web 应用"""
    print("=" * 60)
    print("  🌐 舆情监控大屏启动中...")
    print(f"  访问地址：http://localhost:{config.WEB_PORT}")
    print("=" * 60)

    app.run(
        host=config.WEB_HOST,
        port=config.WEB_PORT,
        debug=config.WEB_DEBUG,
    )


if __name__ == '__main__':
    run_web()