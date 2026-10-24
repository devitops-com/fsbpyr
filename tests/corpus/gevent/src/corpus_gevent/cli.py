"""gevent: monkey patching, a pywsgi server, a Pool of greenlets, subprocess and a threadpool."""

from gevent import monkey

monkey.patch_all()

import urllib.request

import gevent
import gevent.subprocess
from gevent.pool import Pool
from gevent.pywsgi import WSGIServer


def app(environ, start_response):
    start_response("200 OK", [("Content-Type", "text/plain")])
    gevent.sleep(0.01)
    return [f"wsgi {environ['PATH_INFO']}".encode()]


def main() -> None:
    server = WSGIServer(("127.0.0.1", 0), app, log=None)
    server.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"

        def fetch(number: int) -> str:
            with urllib.request.urlopen(f"{base}/item/{number}") as response:
                return response.read().decode()

        print("pool", sorted(Pool(4).imap_unordered(fetch, range(8))))
    finally:
        server.stop()
    print("loop", type(gevent.get_hub().loop).__name__)
    print("subprocess", gevent.subprocess.run(["echo", "hi"], capture_output=True).stdout)
    print("threadpool", gevent.get_hub().threadpool.apply(sum, ([1, 2, 3],)))
