# -*- coding: utf-8 -*-
"""RQ dispatch with an explicit inline development mode."""

from __future__ import annotations

import importlib

import config


class QueueUnavailable(RuntimeError):
    pass


def _resolve(function_path):
    module_name, function_name = function_path.rsplit('.', 1)
    return getattr(importlib.import_module(module_name), function_name)


def queue_status():
    if config.TASK_QUEUE_MODE == 'inline':
        return {'mode': 'inline', 'ready': True, 'detail': '本地同步开发模式'}
    if config.TASK_QUEUE_MODE != 'rq':
        return {'mode': config.TASK_QUEUE_MODE, 'ready': False, 'detail': '未知任务队列模式'}
    try:
        from redis import Redis
        connection = Redis.from_url(config.REDIS_URL, socket_connect_timeout=1)
        connection.ping()
        return {'mode': 'rq', 'ready': True, 'detail': config.RQ_QUEUE_NAME}
    except Exception as exc:
        return {'mode': 'rq', 'ready': False, 'detail': type(exc).__name__}


def dispatch(function_path, *args, timeout=1800):
    """Dispatch a named task without silently changing queue modes."""
    function = _resolve(function_path)
    if config.TASK_QUEUE_MODE == 'inline':
        function(*args)
        return {'mode': 'inline', 'job_id': None}
    if config.TASK_QUEUE_MODE != 'rq':
        raise QueueUnavailable(f'未知任务队列模式：{config.TASK_QUEUE_MODE}')
    try:
        from redis import Redis
        from rq import Queue
        connection = Redis.from_url(config.REDIS_URL, socket_connect_timeout=2)
        connection.ping()
        queued = Queue(config.RQ_QUEUE_NAME, connection=connection).enqueue(
            function, *args, job_timeout=timeout, result_ttl=3600
        )
        return {'mode': 'rq', 'job_id': queued.id}
    except Exception as exc:
        raise QueueUnavailable('Redis/RQ 不可用，请检查任务服务') from exc
