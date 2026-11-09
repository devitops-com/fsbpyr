"""A minimal wheel that a test writes, for a lock that names a local file (a Pipfile.lock
``file:`` entry, a Poetry ``type = "file"`` source): one module, METADATA, WHEEL and RECORD,
the same bytes every time."""

import base64
import hashlib
import zipfile
from pathlib import Path

_DATE = (2020, 1, 1, 0, 0, 0)


def _record_hash(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def write(directory: Path, name: str, version: str) -> tuple[Path, str]:
    """``{name}-{version}-py3-none-any.whl`` in ``directory``, holding the module ``name``, and
    the wheel's SHA-256."""
    info = f"{name}-{version}.dist-info"
    files = {
        f"{name}/__init__.py": f'VERSION = "{version}"\n'.encode(),
        f"{info}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n".encode(),
        f"{info}/WHEEL": b"Wheel-Version: 1.0\nGenerator: tests\nRoot-Is-Purelib: true\n"
                         b"Tag: py3-none-any\n",
    }  # fmt: skip
    record = "".join(f"{member},{_record_hash(data)},{len(data)}\n"
                     for member, data in files.items()) + f"{info}/RECORD,,\n"  # fmt: skip
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for member, data in {**files, f"{info}/RECORD": record.encode()}.items():
            archive.writestr(zipfile.ZipInfo(member, _DATE), data)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()
