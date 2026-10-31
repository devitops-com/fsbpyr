"""Fake GitHub servers on localhost, so that nothing reaches the net.

``FakeReleases`` is the release downloads: the ``files`` it serves by path, such as
``/vX.Y.Z/ASSET`` for a release's assets and ``/el8-vX.Y.Z/ASSET`` for the EL8 stubs and their sums
file, which are assets of the companion release. ``FakeGitHub`` is the release document at /api,
with the assets below /files.

With a ``signer`` (bytes of a sums file -> its ``.sig``), each serves a sums file that a test did
not give, of the files it holds, and the ``.sig`` of any sums file: the tests that are about
something else get a signed release. Tests give their own files, or no signer, for one that is
not."""

import hashlib
import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer

SUMS = "SHA256SUMS"

type Signer = Callable[[bytes], bytes]


def sums_of(files: dict[str, bytes]) -> bytes:
    """A checksums file of ``files`` (name -> content), as `sha256sum` writes one."""
    lines = (f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in files.items())
    return "".join(lines).encode()


class _Server:
    """The HTTP server: ``answer`` gives a GET's status and body."""

    def __init__(self) -> None:
        self.requests: list[str] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                fake.requests.append(self.path)
                status, body = fake.answer(self.path)
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def answer(self, path: str) -> tuple[int, bytes]:
        raise NotImplementedError

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class FakeReleases(_Server):
    def __init__(self, signer: Signer | None = None) -> None:
        self.files: dict[str, bytes] = {}  # path -> body
        self.signer = signer
        super().__init__()

    def _body(self, path: str) -> bytes | None:
        if path in self.files:
            return self.files[path]
        if self.signer is None:
            return None
        if path.endswith(".sig"):
            sums = self._body(path.removesuffix(".sig"))
            return None if sums is None else self.signer(sums)
        directory, _, name = path.rpartition("/")
        listed = {key.rpartition("/")[2]: data for key, data in self.files.items()
                  if key.rpartition("/")[0] == directory}  # fmt: skip
        return sums_of(listed) if name == SUMS and listed else None

    def answer(self, path: str) -> tuple[int, bytes]:
        body = self._body(path)
        return (404, b"") if body is None else (200, body)


class FakeGitHub(_Server):
    def __init__(self, signer: Signer | None = None) -> None:
        self.tag = "v1.2.0"
        self.assets: dict[str, bytes] = {}
        self.api_status = 200
        self.signer = signer
        super().__init__()

    def served(self) -> dict[str, bytes]:
        """The release's assets: with a signer, its sums file and the signature too."""
        files = dict(self.assets)
        if self.signer is not None:
            files.setdefault(SUMS, sums_of(self.assets))
            files.setdefault(f"{SUMS}.sig", self.signer(files[SUMS]))
        return files

    def answer(self, path: str) -> tuple[int, bytes]:
        files = self.served()
        if path == "/api":
            return self.api_status, json.dumps({
                "tag_name": self.tag,
                "assets": [{"name": name, "browser_download_url": f"{self.base}/files/{name}"}
                           for name in files],
            }).encode()  # fmt: skip
        if path.startswith("/files/") and path[7:] in files:
            return 200, files[path[7:]]
        return 404, b""
