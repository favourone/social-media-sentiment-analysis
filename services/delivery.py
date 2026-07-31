# -*- coding: utf-8 -*-
"""Audited outbound notification delivery for V2 alerts."""

from __future__ import annotations

from urllib.parse import urlparse

import requests

import config
from services.network_safety import UnsafeURL, validate_outbound_url


def _safe_destination(url):
    parsed = urlparse(url)
    port = f':{parsed.port}' if parsed.port else ''
    return f'{parsed.scheme}://{parsed.hostname}{port}'


def deliver_webhook(store, monitor, alert):
    """Deliver one newly-created alert and persist a redacted attempt record."""
    raw_url = monitor.get('webhook_url')
    if not raw_url:
        return None
    payload = {
        'type': 'public_opinion_alert',
        'monitor': {'id': monitor['id'], 'name': monitor['name']},
        'alert': {
            'id': alert['id'],
            'kind': alert['kind'],
            'severity': alert['severity'],
            'status': alert['status'],
            'title': alert['title'],
            'message': alert['message'],
            'evidence': alert['evidence'],
            'first_seen': alert['first_seen'],
        },
    }
    destination = _safe_destination(raw_url)
    delivery_id = store.create_delivery(
        monitor['id'], alert['id'], 'webhook', destination, payload
    )
    try:
        url = validate_outbound_url(
            raw_url, allow_private=config.ALLOW_PRIVATE_NETWORK_URLS
        )
        response = requests.post(
            url,
            json=payload,
            timeout=config.WEBHOOK_TIMEOUT_SECONDS,
            headers={'User-Agent': 'SentimentRadar/2.0'},
        )
        if not 200 <= response.status_code < 300:
            store.finish_delivery(
                delivery_id,
                'failed',
                response_code=response.status_code,
                error_message='Webhook 返回非成功状态',
            )
            return delivery_id
        store.finish_delivery(
            delivery_id, 'succeeded', response_code=response.status_code
        )
    except (UnsafeURL, requests.RequestException) as exc:
        store.finish_delivery(
            delivery_id,
            'failed',
            error_message=f'{type(exc).__name__}: {str(exc)[:300]}',
        )
    return delivery_id
