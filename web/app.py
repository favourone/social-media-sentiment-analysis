# -*- coding: utf-8 -*-
"""
Flask Web 应用
==============
舆情监控大屏后端，提供 RESTful API 和页面渲染
支持时间范围筛选和话题筛选
"""

import os
import secrets
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

from flask import Flask, render_template, jsonify, request, redirect, session, url_for
from flask_cors import CORS

app = Flask(__name__)
app.secret_key = config.APP_SECRET_KEY or secrets.token_hex(32)
app.permanent_session_lifetime = timedelta(hours=config.SESSION_HOURS)
app.config.update(
    MAX_CONTENT_LENGTH=config.MAX_UPLOAD_BYTES,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.environ.get('SESSION_COOKIE_SECURE', '').lower()
    in {'1', 'true', 'yes', 'on'},
)
if hasattr(app, 'json'):
    app.json.ensure_ascii = False
else:
    app.config['JSON_AS_ASCII'] = False
if config.CORS_ORIGINS:
    CORS(app, resources={r"/api/*": {"origins": config.CORS_ORIGINS}})

from web.product_api import register_product_routes
register_product_routes(app)


@app.after_request
def add_security_headers(response):
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    response.headers.setdefault(
        'Content-Security-Policy',
        "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'"
    )
    return response


def api_error(code, message, status, **details):
    """Return a consistent, machine-readable API error."""
    payload = {'error': code, 'message': message}
    if details:
        payload['details'] = details
    return jsonify(payload), status


def parse_iso_date(name):
    """Validate an optional ISO date query argument."""
    raw = request.args.get(name)
    if not raw:
        return None, None
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        return None, api_error(
            'invalid_date', f'{name} 必须是 YYYY-MM-DD 格式', 400, parameter=name
        )
    return parsed.isoformat(), None


def parse_filters():
    """Parse and validate the shared date/topic filters."""
    start_date, error = parse_iso_date('start_date')
    if error:
        return None, error
    end_date, error = parse_iso_date('end_date')
    if error:
        return None, error
    if start_date and end_date and start_date > end_date:
        return None, api_error(
            'invalid_date_range', 'start_date 不能晚于 end_date', 400
        )
    return {
        'start_date': start_date,
        'end_date': end_date,
        'topic': request.args.get('topic', 'all').strip() or 'all',
    }, None


def parse_bounded_int(name, default, minimum, maximum):
    """Parse an integer query argument with explicit resource bounds."""
    raw = request.args.get(name)
    if raw is None:
        return default, None
    try:
        value = int(raw)
    except ValueError:
        return None, api_error(
            'invalid_parameter', f'{name} 必须是整数', 400, parameter=name
        )
    if value < minimum or value > maximum:
        return None, api_error(
            'parameter_out_of_range',
            f'{name} 必须在 {minimum} 到 {maximum} 之间',
            400,
            parameter=name,
            minimum=minimum,
            maximum=maximum,
        )
    return value, None


def filter_posts(posts, start_date=None, end_date=None, topic=None):
    """过滤帖子数据"""
    filtered = []
    for p in posts:
        date = p.get('created_at', '')[:10]
        p_topic = p.get('topic', '')

        if (start_date or end_date) and not date:
            continue
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

        if (start_date or end_date) and not date:
            continue
        if start_date and date and date < start_date:
            continue
        if end_date and date and date > end_date:
            continue
        if topic and topic != 'all' and s_topic != topic:
            continue

        filtered.append(s)
    return filtered


def filter_comments(comments, allowed_post_ids, start_date=None, end_date=None):
    """Filter comments by their parent posts and the comment timestamp."""
    filtered = []
    for comment in comments:
        if comment.get('post_id') not in allowed_post_ids:
            continue
        comment_date = comment.get('created_at', '')[:10]
        if (start_date or end_date) and not comment_date:
            continue
        if start_date and comment_date and comment_date < start_date:
            continue
        if end_date and comment_date and comment_date > end_date:
            continue
        filtered.append(comment)
    return filtered


@app.route('/')
def index():
    """舆情监控大屏首页"""
    if not session.get('admin_id'):
        return redirect(url_for('product.login_page'))
    return render_template('index.html')


@app.route('/dashboard')
def dashboard():
    """Authenticated legacy visualization dashboard."""
    if not session.get('admin_id'):
        return redirect(url_for('product.login_page'))
    return render_template('index.html')


@app.route('/api/overview')
def api_overview():
    """
    获取总览数据
    参数：start_date, end_date, topic
    返回：帖子总数、评论总数、话题数、情感分布
    """
    filters, error = parse_filters()
    if error:
        return error
    start_date = filters['start_date']
    end_date = filters['end_date']
    topic = filters['topic']

    from storage.mongo_client import get_db
    db = get_db()
    posts = db.get_all_posts(limit=200000)
    comments = db.get_all_comments()

    posts = filter_posts(posts, start_date, end_date, topic)
    allowed_post_ids = {post.get('post_id') for post in posts}
    comments = filter_comments(comments, allowed_post_ids, start_date, end_date)

    total_posts = len(posts)
    total_comments = len(comments)
    positive = sum(1 for p in posts if p.get('sentiment') == 1)
    negative = total_posts - positive

    topics = {p.get('topic') for p in posts if p.get('topic')}

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
    filters, error = parse_filters()
    if error:
        return error
    start_date = filters['start_date']
    end_date = filters['end_date']

    from storage.mongo_client import get_db
    db = get_db()
    posts = db.get_all_posts(limit=200000)

    posts = filter_posts(posts, start_date, end_date, filters['topic'])

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
    filters, error = parse_filters()
    if error:
        return error
    start_date = filters['start_date']
    end_date = filters['end_date']
    topic = filters['topic']

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
    filters, error = parse_filters()
    if error:
        return error
    start_date = filters['start_date']
    end_date = filters['end_date']
    topic = filters['topic']

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
    filters, error = parse_filters()
    if error:
        return error
    topic = filters['topic'] if filters['topic'] != 'all' else 'AI人工智能'
    days, error = parse_bounded_int('days', 30, 1, 90)
    if error:
        return error
    start_date = filters['start_date']
    end_date = filters['end_date']

    from storage.mongo_client import get_db
    from models.sir_model import SIRModel
    db = get_db()

    series_data = db.get_time_series(topic=topic)
    series_data = filter_time_series(series_data, start_date, end_date, topic)

    values = [d['value'] for d in series_data if d.get('topic') == topic]
    if len(values) < 6:
        return api_error(
            'insufficient_data',
            '当前话题和时间范围至少需要 6 个观测点才能运行 SIR 情景模拟',
            422,
            topic=topic,
            available_points=len(values),
            required_points=6,
        )

    sir = SIRModel()
    try:
        sir.fit(values[:30])
        forecast = sir.predict(days=days)
        status = sir.get_status(len(values), values)
    except Exception:
        app.logger.exception('SIR fit failed for topic %s', topic)
        return api_error('model_fit_failed', 'SIR 模型无法在当前数据上稳定拟合', 422, topic=topic)

    return jsonify({
        'topic': topic,
        'interpretation': 'scenario_simulation',
        'claim_scope': '基于本地讨论量形状的情景模拟，不代表真实用户规模或因果预测',
        'observed_points': len(values),
        'data_provenance': db.get_data_metadata(),
        'time': forecast['time'],
        'S': forecast['S'],
        'I': forecast['I'],
        'R': forecast['R'],
        'peak_day': forecast['peak_day'],
        'peak_value': forecast['peak_value'],
        'R0': forecast['R0'],
        'phase': status['phase'],
        'risk_level': status['risk_level'],
        'fit_nrmse': getattr(sir, 'fit_nrmse', None),
    })


@app.route('/api/arima_prediction')
def api_arima_prediction():
    """
    ARIMA 时间序列预测
    参数：topic（可选）、steps（预测步数）、start_date、end_date
    """
    filters, error = parse_filters()
    if error:
        return error
    topic = filters['topic'] if filters['topic'] != 'all' else 'AI人工智能'
    steps, error = parse_bounded_int('steps', 7, 1, 30)
    if error:
        return error
    start_date = filters['start_date']
    end_date = filters['end_date']

    from storage.mongo_client import get_db
    from models.arima_model import ARIMAPredictor
    db = get_db()

    series_data = db.get_time_series(topic=topic)
    series_data = filter_time_series(series_data, start_date, end_date, topic)

    values = [d['value'] for d in series_data if d.get('topic') == topic]
    if len(values) < 10:
        return api_error(
            'insufficient_data',
            '当前话题和时间范围至少需要 10 个观测点才能运行 ARIMA 预测',
            422,
            topic=topic,
            available_points=len(values),
            required_points=10,
        )

    arima = ARIMAPredictor()
    try:
        arima.fit(values)
        prediction = arima.predict(steps=steps)
    except Exception:
        app.logger.exception('ARIMA fit failed for topic %s', topic)
        return api_error('model_fit_failed', 'ARIMA 模型无法在当前数据上稳定拟合', 422, topic=topic)

    dates = [d['date'] for d in series_data if d.get('topic') == topic]
    history_dates = dates[-30:] if len(dates) >= 30 else dates
    history_values = values[-30:] if len(values) >= 30 else values

    return jsonify({
        'topic': topic,
        'interpretation': 'statistical_forecast',
        'claim_scope': '仅适用于所示本地时间序列及置信区间，不证明外部场景有效性',
        'observed_points': len(values),
        'data_provenance': db.get_data_metadata(),
        'history_dates': history_dates,
        'history_values': history_values,
        'forecast': prediction['forecast'],
        'lower_bound': prediction['lower_bound'],
        'upper_bound': prediction['upper_bound'],
        'trend': prediction.get('trend', '未知'),
        'change_rate': prediction.get('change_rate', 0),
        'converged': prediction.get('converged'),
    })


@app.route('/api/lda_topics')
def api_lda_topics():
    """获取 LDA 主题分析结果"""
    filters, error = parse_filters()
    if error:
        return error
    start_date = filters['start_date']
    end_date = filters['end_date']
    topic = filters['topic']

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

    if len(tokenized) < 10:
        return api_error(
            'insufficient_data', '当前筛选范围至少需要 10 篇有效文本才能运行 LDA', 422,
            available_documents=len(tokenized), required_documents=10
        )

    lda = LDAModel(num_topics=5)
    try:
        lda.fit(tokenized)
    except Exception:
        app.logger.exception('LDA fit failed')
        return api_error('model_fit_failed', 'LDA 模型无法在当前数据上稳定拟合', 422)
    topics = lda.get_topics(top_k=8)
    return jsonify(topics)


@app.route('/api/recent_posts')
def api_recent_posts():
    """获取最新帖子"""
    filters, error = parse_filters()
    if error:
        return error
    start_date = filters['start_date']
    end_date = filters['end_date']
    topic = filters['topic']

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
    filters, error = parse_filters()
    if error:
        return error
    start_date = filters['start_date']
    end_date = filters['end_date']

    from storage.mongo_client import get_db
    db = get_db()
    posts = db.get_all_posts(limit=200000)

    posts = filter_posts(posts, start_date, end_date, filters['topic'])

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
    """读取流水线生成的离线模型评估指标，不在 GET 请求中训练。"""
    from storage.mongo_client import get_db

    report = get_db().get_model_metrics()
    if report is None:
        return api_error(
            'model_metrics_unavailable',
            '尚无离线评估报告，请先运行 python scripts/run_pipeline.py',
            503,
        )
    return jsonify(report)


@app.route('/api/data_status')
def api_data_status():
    """返回可审计的数据规模、时间范围和来源状态。"""
    from storage.mongo_client import get_db

    db = get_db()
    posts = db.get_all_posts(limit=200000)
    comments = db.get_all_comments()
    series = db.get_time_series()
    post_dates = sorted(p.get('created_at', '')[:10] for p in posts if p.get('created_at'))
    sources = {}
    for post in posts:
        source = post.get('source') or '未知'
        sources[source] = sources.get(source, 0) + 1
    return jsonify({
        'counts': {
            'posts': len(posts),
            'comments': len(comments),
            'time_series_points': len(series),
        },
        'date_range': {
            'start': post_dates[0] if post_dates else None,
            'end': post_dates[-1] if post_dates else None,
        },
        'record_sources': sources,
        'provenance': db.get_data_metadata(posts=posts),
        'model_metrics_available': db.get_model_metrics() is not None,
    })


@app.route('/api/topics')
def api_topics():
    """获取所有话题列表"""
    from storage.mongo_client import get_db
    db = get_db()
    posts = db.get_all_posts(limit=200000)
    topics = sorted({p.get('topic') for p in posts if p.get('topic')})
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
