# -*- coding: utf-8 -*-
"""Shared validation for administrator-configured outbound HTTP destinations."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeURL(ValueError):
    pass


def validate_outbound_url(url, allow_private=False):
    """Return a normalized URL after rejecting unsafe schemes and local targets."""
    value = str(url or '').strip()
    parsed = urlparse(value)
    if parsed.scheme not in {'http', 'https'}:
        raise UnsafeURL('仅允许 http 或 https 地址')
    if not parsed.hostname or parsed.username or parsed.password:
        raise UnsafeURL('地址必须包含有效主机名且不能携带登录凭据')
    if allow_private:
        return value
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(
                parsed.hostname,
                parsed.port or (443 if parsed.scheme == 'https' else 80),
                type=socket.SOCK_STREAM,
            )
        }
    except socket.gaierror as exc:
        raise UnsafeURL('无法解析目标主机名') from exc
    if not addresses:
        raise UnsafeURL('目标主机没有可用地址')
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise UnsafeURL('默认禁止访问本机、内网或保留地址')
    return value
