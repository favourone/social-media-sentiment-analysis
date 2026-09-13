# -*- coding: utf-8 -*-
"""Authenticated product pages and versioned workflow API."""

from __future__ import annotations

import hmac
import os
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import date
from functools import wraps
from pathlib import Path

from flask import (
    Blueprint, jsonify, redirect, render_template, request, send_file, session, url_for
)
from werkzeug.utils import secure_filename

import config
from services.task_queue import QueueUnavailable, dispatch, queue_status
from storage.product_store import get_product_store, utc_now


product = Blueprint('product', __name__)
_login_attempts = defaultdict(deque)
_attempt_lock = threading.Lock()


def success(data=None, status=200):
    return jsonify({'ok': True, 'data': data if data is not None else {}}), status


def error(code, message, status, **details):
    payload = {'ok': False, 'error': {'code': code, 'message': message}}
    if details:
        payload['error']['details'] = details
    return jsonify(payload), status


def _admin():
    admin_id = session.get('admin_id')
    username = session.get('username')
    return {'id': admin_id, 'username': username} if admin_id and username else None


def login_required_api(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if not _admin():
            return error('authentication_required', '请先登录', 401)
        return function(*args, **kwargs)
    return wrapped


def csrf_required(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        expected = session.get('csrf_token', '')
        supplied = request.headers.get('X-CSRF-Token', '')
        if not expected or not supplied or not hmac.compare_digest(expected, supplied):
            return error('csrf_failed', '请求校验失败，请刷新页面后重试', 403)
        return function(*args, **kwargs)
    return wrapped


def _client_key():
    return request.remote_addr or 'unknown'


def _rate_limited(key):
    now = time.monotonic()
    with _attempt_lock:
        attempts = _login_attempts[key]
        while attempts and now - attempts[0] > config.LOGIN_WINDOW_SECONDS:
            attempts.popleft()
        return len(attempts) >= config.LOGIN_MAX_ATTEMPTS


def _record_failure(key):
    with _attempt_lock:
        _login_attempts[key].append(time.monotonic())


def _clear_failures(key):
    with _attempt_lock:
        _login_attempts.pop(key, None)


def _json_body():
    return request.get_json(silent=True) if request.is_json else None


def _validate_date(value, name):
    if value in (None, ''):
        return None
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise ValueError(f'{name} 必须是 YYYY-MM-DD 格式') from exc


def _filters(payload):
    topic = str(payload.get('topic', '')).strip()[:100] or None
    start_date = _validate_date(payload.get('start_date'), 'start_date')
    end_date = _validate_date(payload.get('end_date'), 'end_date')
    if start_date and end_date and start_date > end_date:
        raise ValueError('start_date 不能晚于 end_date')
    return {'topic': topic, 'start_date': start_date, 'end_date': end_date}


def _dispatch(function_path, identifier):
    try:
        return dispatch(function_path, identifier)
    except QueueUnavailable:
        return None
    except Exception:
        # Inline tasks persist their own failed status. Do not expose stack data.
        return {'mode': 'inline', 'job_id': None, 'task_failed': True}


@product.get('/login')
def login_page():
    if _admin():
        return redirect(url_for('product.workspace'))
    return render_template(
        'login.html', setup_required=get_product_store().admin_count() == 0
    )


@product.get('/workspace')
def workspace():
    if not _admin():
        return redirect(url_for('product.login_page', next=request.path))
    return render_template('workspace.html', username=session.get('username'))


@product.post('/api/v1/auth/login')
def login():
    key = _client_key()
    if _rate_limited(key):
        return error(
            'login_rate_limited', '登录失败次数过多，请稍后再试', 429,
            retry_after_seconds=config.LOGIN_WINDOW_SECONDS
        )
    payload = _json_body()
    if not isinstance(payload, dict):
        return error('invalid_json', '请求体必须是 JSON 对象', 400)
    username = str(payload.get('username', '')).strip()
    password = str(payload.get('password', ''))
    store = get_product_store()
    if store.admin_count() == 0:
        return error(
            'setup_required', '尚未创建管理员，请设置 ADMIN_PASSWORD 后重启服务', 503
        )
    admin = store.authenticate(username, password)
    if not admin:
        _record_failure(key)
        return error('invalid_credentials', '用户名或密码错误', 401)
    _clear_failures(key)
    session.clear()
    session.permanent = True
    session['admin_id'] = admin['id']
    session['username'] = admin['username']
    session['csrf_token'] = secrets.token_urlsafe(32)
    return success({
        'user': {'id': admin['id'], 'username': admin['username']},
        'csrf_token': session['csrf_token'],
    })


@product.post('/api/v1/auth/logout')
@login_required_api
@csrf_required
def logout():
    session.clear()
    return success({'message': '已退出登录'})


@product.get('/api/v1/auth/me')
@login_required_api
def me():
    return success({'user': _admin(), 'csrf_token': session.get('csrf_token')})


@product.post('/api/v1/auth/password')
@login_required_api
@csrf_required
def change_password():
    payload = _json_body()
    if not isinstance(payload, dict):
        return error('invalid_json', '请求体必须是 JSON 对象', 400)
    current_password = str(payload.get('current_password', ''))
    new_password = str(payload.get('new_password', ''))
    if len(new_password) < 10:
        return error('weak_password', '新密码至少需要 10 个字符', 400)
    if not get_product_store().change_password(
        session['admin_id'], current_password, new_password
    ):
        return error('invalid_credentials', '当前密码错误', 401)
    session.clear()
    return success({'message': '密码已修改，请重新登录'})


@product.get('/api/v1/health/live')
def health_live():
    return success({'status': 'live'})


@product.get('/api/v1/health/ready')
def health_ready():
    try:
        store = get_product_store()
        database_ready = store.admin_count() >= 0
    except Exception:
        database_ready = False
    queue = queue_status()
    configured = bool(config.APP_SECRET_KEY and config.ADMIN_PASSWORD)
    ready = database_ready and queue['ready'] and configured
    return success({
        'status': 'ready' if ready else 'degraded',
        'database': database_ready,
        'queue': queue,
        'production_secrets_configured': configured,
    }, 200 if ready else 503)


@product.get('/api/v1/settings/status')
@login_required_api
def settings_status():
    return success({
        'queue': queue_status(),
        'collector': {
            'license_accepted': config.COLLECTOR_LICENSE_ACCEPTED,
            'installed': Path(config.MEDIACRAWLER_HOME).is_dir(),
            'command_configured': bool(config.MEDIACRAWLER_COMMAND_JSON),
            'platform': 'weibo',
            'commercial_use_allowed': False,
        },
        'database_path': config.PRODUCT_DB_PATH,
        'admin_username': config.ADMIN_USERNAME,
    })


@product.get('/api/v1/collection-jobs')
@login_required_api
def collection_jobs():
    return success(get_product_store().list_collection_jobs())


@product.post('/api/v1/collection-jobs')
@login_required_api
@csrf_required
def create_collection_job():
    payload = _json_body()
    if not isinstance(payload, dict):
        return error('invalid_json', '请求体必须是 JSON 对象', 400)
    if payload.get('platform', 'weibo') != 'weibo':
        return error('unsupported_platform', 'V1 仅支持微博', 400)
    raw_keywords = payload.get('keywords', [])
    if isinstance(raw_keywords, str):
        raw_keywords = [item.strip() for item in raw_keywords.split(',')]
    if not isinstance(raw_keywords, list):
        return error('invalid_keywords', 'keywords 必须是字符串数组', 400)
    keywords = []
    for item in raw_keywords:
        keyword = str(item).strip()
        if keyword and keyword not in keywords:
            keywords.append(keyword)
    if not keywords or len(keywords) > 5 or any(len(item) > 50 for item in keywords):
        return error('invalid_keywords', '请输入 1–5 个不超过 50 字的关键词', 400)
    try:
        max_pages = int(payload.get('max_pages', 3))
    except (TypeError, ValueError):
        return error('invalid_parameter', 'max_pages 必须是整数', 400)
    if not 1 <= max_pages <= 10:
        return error('invalid_parameter', 'max_pages 必须在 1 到 10 之间', 400)
    store = get_product_store()
    job = store.create_collection_job(
        'weibo', 'mediacrawler', keywords, {'max_pages': max_pages}
    )
    dispatch_result = _dispatch('services.tasks.run_collection_job', job['id'])
    if dispatch_result is None:
        job = store.update_collection_job(
            job['id'], status='failed', error_code='queue_unavailable',
            error_message='Redis/RQ 不可用，请启动任务服务', finished_at=utc_now()
        )
        return error('queue_unavailable', '任务队列不可用', 503, job=job)
    return success(store.get_collection_job(job['id']), 202)


@product.post('/api/v1/imports')
@login_required_api
@csrf_required
def create_import_job():
    uploaded = request.files.get('file')
    if not uploaded or not uploaded.filename:
        return error('missing_file', '请选择 JSON、JSONL 或 CSV 文件', 400)
    suffix = Path(secure_filename(uploaded.filename)).suffix.lower()
    if suffix not in {'.json', '.jsonl', '.ndjson', '.csv'}:
        return error('unsupported_file', '仅支持 JSON、JSONL 和 CSV 文件', 400)
    topic = str(request.form.get('topic', '')).strip()[:100] or '文件导入'
    Path(config.IMPORT_DIR).mkdir(parents=True, exist_ok=True)
    path = Path(config.IMPORT_DIR) / f'{uuid.uuid4().hex}{suffix}'
    uploaded.save(path)
    if path.stat().st_size > config.MAX_UPLOAD_BYTES:
        path.unlink(missing_ok=True)
        return error('file_too_large', '上传文件超过大小限制', 413)
    store = get_product_store()
    job = store.create_collection_job(
        'weibo', 'file', [topic], {'import_path': str(path)}
    )
    dispatch_result = _dispatch('services.tasks.run_collection_job', job['id'])
    if dispatch_result is None:
        job = store.update_collection_job(
            job['id'], status='failed', error_code='queue_unavailable',
            error_message='Redis/RQ 不可用，请启动任务服务', finished_at=utc_now()
        )
        return error('queue_unavailable', '任务队列不可用', 503, job=job)
    return success(store.get_collection_job(job['id']), 202)


@product.get('/api/v1/collection-jobs/<job_id>')
@login_required_api
def collection_job(job_id):
    job = get_product_store().get_collection_job(job_id)
    return success(job) if job else error('not_found', '采集任务不存在', 404)


@product.post('/api/v1/collection-jobs/<job_id>/cancel')
@login_required_api
@csrf_required
def cancel_collection_job(job_id):
    store = get_product_store()
    job = store.get_collection_job(job_id)
    if not job:
        return error('not_found', '采集任务不存在', 404)
    if job['status'] in {'succeeded', 'failed', 'cancelled'}:
        return error('invalid_state', '当前任务状态不能取消', 409)
    return success(store.update_collection_job(
        job_id, status='cancelled', finished_at=utc_now()
    ))


@product.post('/api/v1/collection-jobs/<job_id>/retry')
@login_required_api
@csrf_required
def retry_collection_job(job_id):
    store = get_product_store()
    job = store.get_collection_job(job_id)
    if not job:
        return error('not_found', '采集任务不存在', 404)
    if job['status'] not in {'paused', 'failed', 'cancelled'}:
        return error('invalid_state', '只有暂停、失败或取消的任务可以重试', 409)
    store.update_collection_job(
        job_id, status='pending', progress=0, error_code=None,
        error_message=None, started_at=None, finished_at=None
    )
    if _dispatch('services.tasks.run_collection_job', job_id) is None:
        store.update_collection_job(
            job_id, status='failed', error_code='queue_unavailable',
            error_message='Redis/RQ 不可用', finished_at=utc_now()
        )
        return error('queue_unavailable', '任务队列不可用', 503)
    return success(store.get_collection_job(job_id), 202)


@product.get('/api/v1/analysis-jobs')
@login_required_api
def analysis_jobs():
    return success(get_product_store().list_analysis_jobs())


@product.post('/api/v1/analysis-jobs')
@login_required_api
@csrf_required
def create_analysis_job():
    payload = _json_body()
    if not isinstance(payload, dict):
        return error('invalid_json', '请求体必须是 JSON 对象', 400)
    analysis_type = str(payload.get('type', 'full')).strip().lower()
    if analysis_type not in {'summary', 'lda', 'arima', 'sir', 'hybrid', 'full'}:
        return error(
            'unsupported_analysis',
            '分析类型必须是 summary、lda、arima、sir、hybrid 或 full', 400
        )
    try:
        filters = _filters(payload)
    except ValueError as exc:
        return error('invalid_filter', str(exc), 400)
    monitor_id = str(payload.get('monitor_id', '')).strip()
    if monitor_id:
        from storage.monitor_store import get_monitor_store
        if not get_monitor_store().get_monitor(monitor_id):
            return error('invalid_monitor', '请选择有效的监测项目', 400)
        filters['monitor_id'] = monitor_id
        filters['topic'] = None
    store = get_product_store()
    job = store.create_analysis_job(analysis_type, filters)
    if _dispatch('services.tasks.run_analysis_job', job['id']) is None:
        store.update_analysis_job(
            job['id'], status='failed', progress=100, error_code='queue_unavailable',
            error_message='Redis/RQ 不可用', finished_at=utc_now()
        )
        return error('queue_unavailable', '任务队列不可用', 503)
    return success(store.get_analysis_job(job['id']), 202)


@product.get('/api/v1/analysis-jobs/<job_id>')
@login_required_api
def analysis_job(job_id):
    job = get_product_store().get_analysis_job(job_id)
    return success(job) if job else error('not_found', '分析任务不存在', 404)


@product.post('/api/v1/analysis-jobs/<job_id>/cancel')
@login_required_api
@csrf_required
def cancel_analysis_job(job_id):
    store = get_product_store()
    job = store.get_analysis_job(job_id)
    if not job:
        return error('not_found', '分析任务不存在', 404)
    if job['status'] not in {'pending', 'running', 'paused'}:
        return error('invalid_state', '当前任务状态不能取消', 409)
    return success(store.update_analysis_job(
        job_id, status='cancelled', finished_at=utc_now()
    ))


@product.get('/api/v1/alerts')
@login_required_api
def alerts():
    return success(get_product_store().list_alerts())


@product.get('/api/v1/reports')
@login_required_api
def reports():
    return success(get_product_store().list_reports())


@product.post('/api/v1/reports')
@login_required_api
@csrf_required
def create_report():
    payload = _json_body()
    if not isinstance(payload, dict):
        return error('invalid_json', '请求体必须是 JSON 对象', 400)
    report_format = str(payload.get('format', 'pdf')).strip().lower()
    if report_format not in {'pdf', 'csv'}:
        return error('unsupported_format', '报告格式必须是 pdf 或 csv', 400)
    try:
        filters = _filters(payload)
    except ValueError as exc:
        return error('invalid_filter', str(exc), 400)
    store = get_product_store()
    report = store.create_report(report_format, filters)
    if _dispatch('services.tasks.run_report_job', report['id']) is None:
        store.update_report(
            report['id'], status='failed', error_code='queue_unavailable',
            error_message='Redis/RQ 不可用', finished_at=utc_now()
        )
        return error('queue_unavailable', '任务队列不可用', 503)
    return success(store.get_report(report['id']), 202)


@product.get('/api/v1/reports/<report_id>.<report_format>')
@login_required_api
def download_report(report_id, report_format):
    store = get_product_store()
    report = store.get_report(report_id)
    if not report or report['format'] != report_format:
        return error('not_found', '报告不存在', 404)
    if report['status'] != 'succeeded' or not report.get('file_path'):
        return error('report_not_ready', '报告尚未生成完成', 409)
    configured_root = Path(config.REPORT_DIR).resolve()
    path = Path(report['file_path']).resolve()
    if configured_root not in path.parents or not path.is_file():
        return error('report_file_missing', '报告文件不可用', 410)
    return send_file(
        path, as_attachment=True,
        download_name=f'舆情报告-{report_id[:8]}.{report_format}'
    )


def register_product_routes(app):
    app.register_blueprint(product)
