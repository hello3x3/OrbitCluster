# -*- coding: utf-8 -*-
"""集群门户生产入口（waitress 多线程 WSGI 服务）。"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from portalapp.app import create_app  # noqa: E402


def main():
    from waitress import serve
    app = create_app()
    host = os.environ.get("PORTAL_HOST", "0.0.0.0")
    port = int(os.environ.get("PORTAL_PORT", "8000"))
    threads = int(os.environ.get("PORTAL_THREADS", "32"))
    print("cluster-portal listening on http://%s:%s" % (host, port), flush=True)
    serve(app, host=host, port=port, threads=threads, channel_timeout=120)


if __name__ == "__main__":
    main()
