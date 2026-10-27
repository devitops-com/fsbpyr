"""A fake GitHub release downloads server on localhost: the ``files`` it serves by path, such as
``/vX.Y.Z/ASSET`` for a release's assets and ``/el8-vX.Y.Z/ASSET`` for the EL8 stubs and their sums
file, which are assets of the companion release."""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class FakeReleases:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}  # path -> body
        self.requests: list[str] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                fake.requests.append(self.path)
                found = self.path in fake.files
                body, status = (fake.files[self.path], 200) if found else (b"", 404)
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
