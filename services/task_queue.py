# -*- coding: utf-8 -*-
"""RQ dispatch with local inline and bounded heavy-analysis modes."""

from __future__ import annotations

import importlib
import logging
from concurrent.futures import CancelledError, ThreadPoolExecutor
from threading import BoundedSemaphore

import config

LOGGER = logging.getLogger(__name__)


# Local development keeps ordinary tasks inline, while expensive analysis can
# return the HTTP response immediately. This executor is process-local; use RQ
# for durable/background jobs in deployments.
_LOCAL_ANALYSIS_EXECUTOR = ThreadPoolExecutor(
    max_workers=1, thread_name_prefix='sentiment-analysis'
)
_LOCAL_ANALYSIS_SLOTS = BoundedSemaphore(4)


def _release_local_analysis_slot(future):
    try:
        error = future.exception()
        if error is not None:
            LOGGER.error(
                '本地后台分析未处理异常',
                exc_info=(type(error), error, error.__traceback__),
            )
    except CancelledError:
        LOGGER.warning('本地后台分析在执行前被取消')
    finally:
        _LOCAL_ANALYSIS_SLOTS.release()


class QueueUnavailable(RuntimeError):
    pass


def _resolve(function_path):
    module_name, function_name = function_path.rsplit('.', 1)
    return getattr(importlib.import_module(module_name), function_name)


def queue_status():
    if config.TASK_QUEUE_MODE == 'inline':
        return {
            'mode': 'inline', 'ready': True,
            'detail': '本地模式；全部/混合分析使用后台线程',
        }
    if config.TASK_QUEUE_MODE != 'rq':
        return {'mode': config.TASK_QUEUE_MODE, 'ready': False, 'detail': '未知任务队列模式'}
    try:
        from redis import Redis
        connection = Redis.from_url(config.REDIS_URL, socket_connect_timeout=1)
        connection.ping()
        return {'mode': 'rq', 'ready': True, 'detail': config.RQ_QUEUE_NAME}
    except Exception as exc:
        return {'mode': 'rq', 'ready': False, 'detail': type(exc).__name__}


def dispatch(function_path, *args, timeout=1800, background=False):
    """Dispatch a named task without silently changing queue modes."""
    function = _resolve(function_path)
    if config.TASK_QUEUE_MODE == 'inline':
        if background:
            if not _LOCAL_ANALYSIS_SLOTS.acquire(blocking=False):
                raise QueueUnavailable('本地分析任务已满，请稍后重试')
            try:
                future = _LOCAL_ANALYSIS_EXECUTOR.submit(function, *args)
            except RuntimeError as exc:
                _LOCAL_ANALYSIS_SLOTS.release()
                raise QueueUnavailable('本地分析线程不可用') from exc
            future.add_done_callback(_release_local_analysis_slot)
            return {'mode': 'local_background', 'job_id': None}
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
