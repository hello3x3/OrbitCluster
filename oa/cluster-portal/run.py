# -*- coding: utf-8 -*-
"""集群门户生产入口（waitress 多线程 WSGI 服务）。"""
import logging
import ipaddress
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from portalapp.app import create_app  # noqa: E402
from portalapp import siteconf  # noqa: E402


def proxy_settings():
    """Only an explicitly configured reverse proxy may supply the TLS scheme."""
    address = siteconf.get("TRUSTED_PROXY", "").strip()
    if not address:
        return {}
    ipaddress.ip_address(address)  # Never accept wildcard trust on a LAN-facing port.
    return {"trusted_proxy": address, "trusted_proxy_count": 1,
            "trusted_proxy_headers": {"x-forwarded-proto"}}


def main():
    from waitress import serve
    app = create_app()
    host = os.environ.get("PORTAL_HOST", "0.0.0.0")
    port = int(os.environ.get("PORTAL_PORT", "80"))
    threads = int(os.environ.get("PORTAL_THREADS", "32"))
    print("cluster-portal listening on http://%s:%s" % (host, port), flush=True)
    serve(app, host=host, port=port, threads=threads, channel_timeout=120, **proxy_settings())


if __name__ == "__main__":
    main()
