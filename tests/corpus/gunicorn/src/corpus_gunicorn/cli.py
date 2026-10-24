"""Serves a WSGI app with gunicorn's arbiter (forked) and two worker classes."""

import os
import signal
import socket
import tempfile
import time
import urllib.request

from gunicorn.app.base import BaseApplication


def wsgi_app(environ, start_response):
    body = f"hello from {environ['PATH_INFO']}".encode()
    start_response("200 OK", [("Content-Type", "text/plain"), ("Content-Length", str(len(body)))])
    return [body]


class Application(BaseApplication):
    def __init__(self, app, options):
        self.app = app
        self.options = options
        super().__init__()

    def load_config(self):
        for key, value in self.options.items():
            self.cfg.set(key, value)

    def load(self):
        return self.app


def fetch(port: int, path: str) -> str:
    deadline = time.monotonic() + 30
    while True:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
                return f"{response.status} {response.read().decode()}"
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.1)


def serve(worker_class: str, tmp: str) -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(16)
    port = listener.getsockname()[1]
    options = {"bind": f"fd://{listener.fileno()}", "workers": 2, "worker_class": worker_class,
               "loglevel": "warning", "errorlog": os.path.join(tmp, f"{worker_class}.log"),
               "accesslog": None, "pidfile": os.path.join(tmp, f"{worker_class}.pid"),
               "worker_tmp_dir": tmp}
    pid = os.fork()
    if pid == 0:
        status = 0
        try:
            Application(wsgi_app, options).run()
        except SystemExit as exit_:
            status = exit_.code or 0
        os._exit(status)
    listener.close()
    try:
        for path in ("/one", "/two", "/three"):
            print(worker_class, path, fetch(port, path))
    finally:
        os.kill(pid, signal.SIGTERM)
        _, wait_status = os.waitpid(pid, 0)
    print(worker_class, "arbiter exit status", os.waitstatus_to_exitcode(wait_status))


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        for worker_class in ("sync", "gthread"):
            serve(worker_class, tmp)
