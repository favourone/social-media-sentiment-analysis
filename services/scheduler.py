# -*- coding: utf-8 -*-
"""Small scheduler process for recurring V2 monitoring projects."""

from __future__ import annotations

import logging
import signal
import time

import config
from services.task_queue import QueueUnavailable, dispatch
from storage.monitor_store import get_monitor_store
from storage.product_store import utc_now


LOGGER = logging.getLogger(__name__)
_running = True


def enqueue_due_monitors():
    store = get_monitor_store()
    created = []
    for monitor in store.list_due_monitors():
        if store.active_run(monitor['id']):
            continue
        run = store.create_run(monitor['id'])
        try:
            dispatch('services.intelligence.run_monitor', run['id'])
            created.append(run['id'])
        except QueueUnavailable:
            store.update_run(
                run['id'],
                status='failed',
                progress=100,
                error_code='queue_unavailable',
                error_message='Redis/RQ 不可用，调度任务未进入队列',
                finished_at=utc_now(),
            )
    return created


def _stop(*_):
    global _running
    _running = False


def main():
    logging.basicConfig(level=logging.INFO)
    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, _stop)
    LOGGER.info(
        'Monitoring scheduler started; interval=%ss',
        config.MONITOR_SCHEDULER_SECONDS,
    )
    while _running:
        try:
            enqueue_due_monitors()
        except Exception:
            LOGGER.exception('Monitoring scheduler tick failed')
        time.sleep(max(config.MONITOR_SCHEDULER_SECONDS, 5))


if __name__ == '__main__':
    main()
