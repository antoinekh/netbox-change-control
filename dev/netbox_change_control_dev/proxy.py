"""
The proxy router of the development stack, so a webhook reaches a receiver on the local network.

Docker passes the host's HTTP proxy into the containers, and NetBox sends every webhook through
the proxy that its routers return, whatever no_proxy says. A proxy cannot reach a compose
service or a private address.
"""

import ipaddress
from urllib.parse import urlparse

from utilities.proxy import DefaultProxyRouter

__all__ = ('LocalBypassProxyRouter',)


def is_local(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # A compose service name, like localhost, has no domain.
        return '.' not in host or host == 'host.docker.internal'
    return address.is_private or address.is_loopback


class LocalBypassProxyRouter(DefaultProxyRouter):
    def route(self, url: str | None = None, protocol: str | None = None, context: dict | None = None) -> dict | None:
        if url and is_local(urlparse(url).hostname or ''):
            return None
        return super().route(url=url, protocol=protocol, context=context)
