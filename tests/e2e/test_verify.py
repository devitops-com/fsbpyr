"""`fsbpyr verify` with its inputs named, and against the release it downloads by itself: on a built
fsbpyr, so with the real C, with real keys and over real HTTP (docs/signing.md §3).

The fsbpyr here pins test keys (tests/e2e/conftest.py), and a release is a server on localhost.
Most of these are forgeries: each must end in NOT VERIFIED, whatever else lies around. Every test
that expects no download leaves the server's log to prove it, and a run without a server points at
a dead port, so that nothing here can reach GitHub."""

import hashlib
import os
import re
import shlex
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from fsbpyr.assemble import App, build_executable
from fsbpyr.build import TARGET_PYTHON
from fsbpyr.dispatch import DOWNLOADS_VARIABLE
from fsbpyr.payload.format import TRAILER_SIZE
from fsbpyr.runtime.registry import Runtime
from fsbpyr.signing import CHECKS_PAGE, SIGNATURE_SIZE

from ..support import contain
from ..support.releases import SUMS, FakeReleases
from ..support.signing import (
    ED25519_SIZE,
    PASSPHRASE,
    Key,
    fingerprint,
    sign,
    sign_statement,
    signed_as_release,
    statement,
)
from .conftest import ReleaseKeys, built_release

pytestmark = pytest.mark.slow

TAG, OLDER = "v1.2.0", "v1.1.0"
ASSET = "fsbpyr-3.14-x86_64-linux-gnu"
ROOT = Path(__file__).parents[2]
HELLO = ROOT / "tests" / "fixtures" / "projects" / "hello"
NOWHERE = "http://127.0.0.1:9"
TIMEOUT = 300
SETUP_ERROR = 2


@pytest.fixture(scope="module")
def fsbpyr(
    tmp_path_factory: pytest.TempPathFactory, runtime: Runtime, release_keys: ReleaseKeys
) -> Path:
    return built_release(tmp_path_factory.mktemp("release"), TAG, release_keys.k.pub,
                         release_keys.r.pub)  # fmt: skip


@pytest.fixture(scope="module")
def older(
    tmp_path_factory: pytest.TempPathFactory, runtime: Runtime, release_keys: ReleaseKeys
) -> Path:
    return built_release(tmp_path_factory.mktemp("older"), OLDER, release_keys.k.pub,
                         release_keys.r.pub)  # fmt: skip


@pytest.fixture
def server() -> Iterator[FakeReleases]:
    fake = FakeReleases()
    yield fake
    fake.close()


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    """A run's environment: its own config and cache, none of the variables under test, and
    downloads from a dead port."""
    clean = {name: value for name, value in os.environ.items()
             if not name.startswith(("FSBPYR_SIGNING", "FSBPYR_VERIFY"))}  # fmt: skip
    return {**clean, "XDG_CONFIG_HOME": str(tmp_path / "config"), "FSBPYR_GC": "0",
            "FSBPYR_CACHE_DIR": str(tmp_path / "cache"), DOWNLOADS_VARIABLE: NOWHERE}  # fmt: skip


def run(fsbpyr: Path, *args: str | Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return contain.run([fsbpyr, *args], env=env, stdin="", timeout=TIMEOUT)


def verified(done: subprocess.CompletedProcess[str]) -> None:
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith("VERIFIED  ")


def not_verified(done: subprocess.CompletedProcess[str]) -> None:
    assert done.returncode == 1, (done.stdout, done.stderr)
    assert done.stdout == ""
    assert "NOT VERIFIED: " in done.stderr
    assert "Traceback" not in done.stderr


def no_verdict(done: subprocess.CompletedProcess[str]) -> None:
    assert done.returncode == SETUP_ERROR, (done.stdout, done.stderr)
    assert done.stdout == ""
    assert "VERIFIED" not in done.stderr
    assert "Traceback" not in done.stderr


def warned(done: subprocess.CompletedProcess[str], tag: str = TAG) -> None:
    """The warning, whole, on standard error: what it can show, what not, and where to read on."""
    lines = [line.removeprefix("fsbpyr: WARNING ").strip() for line in done.stderr.splitlines()
             if line.startswith("fsbpyr: WARNING ")]  # fmt: skip
    text = " ".join(lines)
    assert f"It CAN show that the file is the one the release keys signed for {tag}" in text
    assert "It CANNOT show that this fsbpyr is genuine" in text
    assert CHECKS_PAGE in text
    assert "WARNING" not in done.stdout


def not_warned(done: subprocess.CompletedProcess[str]) -> None:
    assert "WARNING" not in done.stderr


def line_of(file: Path, name: str = ASSET) -> bytes:
    """The line of a sums file for ``file``, as sha256sum writes it."""
    return f"{hashlib.sha256(file.read_bytes()).hexdigest()}  {name}\n".encode()


def publish(
    server: FakeReleases, directory: Path, key: Key, sums: bytes, tag: str = TAG
) -> None:
    """A release ``tag`` on the server, whose sums file ``sums`` ``key`` signed as fsbpyr. The files
    are made in a directory of their own: beside a checked file they would be what is checked."""
    server.files[f"/{tag}/{SUMS}"] = sums
    server.files[f"/{tag}/{SUMS}.sig"] = signed_as_release(key.key, SUMS, sums,
                                                           directory / "published")  # fmt: skip


def downloads(env: dict[str, str], server: FakeReleases) -> dict[str, str]:
    return {**env, DOWNLOADS_VARIABLE: server.base}


def asked_for(tag: str = TAG) -> list[str]:
    return [f"/{tag}/{SUMS}", f"/{tag}/{SUMS}.sig"]


def changed_copy(fsbpyr: Path, directory: Path) -> Path:
    """``fsbpyr`` with one byte of its payload changed."""
    file = directory / "fsbpyr"
    data = bytearray(fsbpyr.read_bytes())
    data[-(TRAILER_SIZE + 1)] ^= 1
    file.write_bytes(data)
    return file


def forged_tag(fsbpyr: Path, directory: Path, tag: bytes) -> Path:
    """A copy of ``fsbpyr`` whose build info names the git tag ``tag``, of the same length."""
    file = directory / "fsbpyr"
    data = fsbpyr.read_bytes()
    recorded = f'"tag": "{TAG}"'.encode()
    assert data.count(recorded) == 1 and len(tag) == len(TAG)
    file.write_bytes(data.replace(recorded, b'"tag": "' + tag + b'"'))
    return file


class Publisher:
    """A project's key, made and trusted by the built fsbpyr, as a publisher and a user do."""

    def __init__(self, fsbpyr: Path, directory: Path, env: dict[str, str], project: str) -> None:
        self.fsbpyr, self.env, self.project = fsbpyr, env, project
        self.signing = {**env, "FSBPYR_SIGNING_PASSPHRASE": PASSPHRASE}
        self.key = directory / f"{project}.key"
        pub = directory / f"{project}.pub"
        made = run(fsbpyr, "key", "new", directory / project, env=self.signing)
        assert made.returncode == 0, made.stderr
        done = run(fsbpyr, "key", "trust", project, pub, fingerprint(pub.read_bytes()), env=env)
        assert done.returncode == 0, done.stderr

    def sign(self, file: Path, project: str | None = None) -> Path:
        """``file``.sig by this key, as ``project`` (by default its own)."""
        done = run(self.fsbpyr, "sign", "--project", project or self.project, "--signing-key",
                   self.key, file, env=self.signing)  # fmt: skip
        assert done.returncode == 0, done.stderr
        return file.with_name(f"{file.name}.sig")


@pytest.fixture
def publisher(fsbpyr: Path, tmp_path: Path, env: dict[str, str]) -> Publisher:
    directory = tmp_path / "keys"
    directory.mkdir()
    return Publisher(fsbpyr, directory, env, "myapp")


def app_file(directory: Path, name: str = "myapp") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    file = directory / name
    file.write_text(f"the bytes of {name}\n")
    return file


def move(file: Path, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    return file.rename(directory / file.name)


# --- files named by option or variable, for any file --------------------------------------------


def test_a_signature_kept_elsewhere_is_named_with_an_option(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], publisher: Publisher
) -> None:
    file = app_file(tmp_path / "files")
    signature = move(publisher.sign(file), tmp_path / "elsewhere")

    done = run(fsbpyr, "verify", "--project", "myapp", "--signature", signature, file, env=env)

    verified(done)
    assert "listed as" not in done.stdout


def test_a_signature_kept_elsewhere_is_named_with_a_variable(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], publisher: Publisher
) -> None:
    file = app_file(tmp_path / "files")
    signature = move(publisher.sign(file), tmp_path / "elsewhere")
    given = {**env, "FSBPYR_VERIFY_SIGNATURE": str(signature)}

    verified(run(fsbpyr, "verify", "--project", "myapp", file, env=given))


def test_checksums_kept_elsewhere_come_with_their_own_sig(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], publisher: Publisher
) -> None:
    file = app_file(tmp_path / "files")
    sums = tmp_path / "elsewhere" / "my-sums"
    sums.parent.mkdir()
    sums.write_bytes(line_of(file, "myapp-1.0"))
    publisher.sign(sums)

    done = run(fsbpyr, "verify", "--project", "myapp", "--checksums", sums, file, env=env)

    verified(done)
    assert "listed as myapp-1.0 in my-sums" in done.stdout


def test_checksums_and_their_signature_are_named_with_options(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], publisher: Publisher
) -> None:
    file = app_file(tmp_path / "files")
    sums = tmp_path / "sums" / "SHA256SUMS"
    sums.parent.mkdir()
    sums.write_bytes(line_of(file))
    signature = move(publisher.sign(sums), tmp_path / "signatures")

    verified(run(fsbpyr, "verify", "--project", "myapp", "--checksums", sums, "--signature",
                 signature, file, env=env))  # fmt: skip


def test_both_variables_name_them(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], publisher: Publisher
) -> None:
    file = app_file(tmp_path / "files")
    sums = tmp_path / "sums" / "SHA256SUMS"
    sums.parent.mkdir()
    sums.write_bytes(line_of(file))
    signature = move(publisher.sign(sums), tmp_path / "signatures")
    given = {**env, "FSBPYR_VERIFY_CHECKSUMS": str(sums),
             "FSBPYR_VERIFY_SIGNATURE": str(signature)}  # fmt: skip

    verified(run(fsbpyr, "verify", "--project", "myapp", file, env=given))


def test_an_option_goes_before_its_variable(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], publisher: Publisher
) -> None:
    file = app_file(tmp_path / "files")
    signature = move(publisher.sign(file), tmp_path / "elsewhere")
    given = {**env, "FSBPYR_VERIFY_SIGNATURE": str(tmp_path / "not-there")}

    verified(run(fsbpyr, "verify", "--project", "myapp", "--signature", signature, file,
                 env=given))  # fmt: skip


def test_an_empty_variable_counts_as_unset(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], publisher: Publisher
) -> None:
    file = app_file(tmp_path / "files")
    publisher.sign(file)
    given = {**env, "FSBPYR_VERIFY_SIGNATURE": "", "FSBPYR_VERIFY_CHECKSUMS": ""}

    verified(run(fsbpyr, "verify", "--project", "myapp", file, env=given))


def test_a_named_signature_that_fails_is_not_saved_by_a_good_one_beside_the_file(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], publisher: Publisher
) -> None:
    """What was named is the only way tried. Otherwise whoever points a check at a file learns
    nothing about that file, and a pass says nothing about it."""
    file = app_file(tmp_path / "files")
    publisher.sign(file)
    other = app_file(tmp_path / "other", "other-file")
    wrong = publisher.sign(other)

    done = run(fsbpyr, "verify", "--project", "myapp", "--signature", wrong, file, env=env)

    not_verified(done)
    assert "the signature does not match the trusted key of myapp" in done.stderr


def test_named_checksums_that_fail_are_not_saved_by_a_good_signature_beside_the_file(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], publisher: Publisher
) -> None:
    file = app_file(tmp_path / "files")
    publisher.sign(file)
    sums = tmp_path / "sums" / "SHA256SUMS"
    sums.parent.mkdir()
    sums.write_bytes(line_of(file))
    move(publisher.sign(sums), tmp_path / "gone")
    (tmp_path / "sums" / "SHA256SUMS.sig").write_bytes(b"\0" * SIGNATURE_SIZE)

    not_verified(run(fsbpyr, "verify", "--project", "myapp", "--checksums", sums, file, env=env))


def test_a_named_signature_is_not_used_for_a_file_beside_the_checksums(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], publisher: Publisher
) -> None:
    """With --checksums, --signature is the signature of that file, never of the checked one."""
    file = app_file(tmp_path / "files")
    signature = publisher.sign(file)
    sums = tmp_path / "sums" / "SHA256SUMS"
    sums.parent.mkdir()
    sums.write_bytes(line_of(file))

    done = run(fsbpyr, "verify", "--project", "myapp", "--checksums", sums, "--signature",
               signature, file, env=env)  # fmt: skip

    not_verified(done)
    assert "SHA256SUMS is not signed by the trusted key of myapp" in done.stderr


def test_signed_checksums_that_do_not_list_the_file_fail(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], publisher: Publisher
) -> None:
    file = app_file(tmp_path / "files")
    other = app_file(tmp_path / "other", "other-file")
    sums = tmp_path / "sums" / "SHA256SUMS"
    sums.parent.mkdir()
    sums.write_bytes(line_of(other))
    publisher.sign(sums)

    done = run(fsbpyr, "verify", "--project", "myapp", "--checksums", sums, file, env=env)

    not_verified(done)
    assert "the signed SHA256SUMS does not list it" in done.stderr


def test_a_line_added_to_signed_checksums_fails(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], publisher: Publisher
) -> None:
    file = app_file(tmp_path / "files")
    other = app_file(tmp_path / "other", "other-file")
    sums = tmp_path / "sums" / "SHA256SUMS"
    sums.parent.mkdir()
    sums.write_bytes(line_of(other))
    publisher.sign(sums)
    sums.write_bytes(line_of(other) + line_of(file))

    not_verified(run(fsbpyr, "verify", "--project", "myapp", "--checksums", sums, file, env=env))


def test_checksums_signed_for_another_project_fail(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], publisher: Publisher
) -> None:
    """Their signature is good, by a trusted key, and its statement names another subject: a key
    is trusted for one project alone."""
    file = app_file(tmp_path / "files")
    sums = tmp_path / "sums" / "SHA256SUMS"
    sums.parent.mkdir()
    sums.write_bytes(line_of(file))
    publisher.sign(sums, project="otherapp")

    not_verified(run(fsbpyr, "verify", "--project", "myapp", "--checksums", sums, file, env=env))


def test_a_key_of_another_project_signs_nothing_of_this_one(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], publisher: Publisher
) -> None:
    keys = tmp_path / "other-keys"
    keys.mkdir()
    stranger = Publisher(fsbpyr, keys, env, "otherapp")
    file = app_file(tmp_path / "files")
    signature = stranger.sign(file, project="myapp")

    not_verified(run(fsbpyr, "verify", "--project", "myapp", "--signature", signature, file,
                     env=env))  # fmt: skip


@pytest.mark.parametrize("size", [0, 64, SIGNATURE_SIZE - 1, SIGNATURE_SIZE + 1, 1 << 20],
                         ids=lambda size: f"{size}-bytes")  # fmt: skip
def test_a_named_signature_of_another_size_fails(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], publisher: Publisher, size: int
) -> None:
    file = app_file(tmp_path / "files")
    good = publisher.sign(file).read_bytes()
    signature = tmp_path / "wrong-size"
    signature.write_bytes((good * (size // len(good) + 1))[:size])

    not_verified(run(fsbpyr, "verify", "--project", "myapp", "--signature", signature, file,
                     env=env))  # fmt: skip


def test_named_checksums_larger_than_any_sums_file_fail(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], publisher: Publisher
) -> None:
    file = app_file(tmp_path / "files")
    sums = tmp_path / "SHA256SUMS"
    sums.write_bytes(line_of(file) * ((16 << 20) // len(line_of(file)) + 1))

    done = run(fsbpyr, "verify", "--project", "myapp", "--checksums", sums, file, env=env)

    not_verified(done)
    assert "is larger than a checksums file may be" in done.stderr


@pytest.mark.parametrize("named", ["signature", "checksums", "its-sig"])
def test_a_named_file_that_is_missing_says_nothing_about_the_file(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], publisher: Publisher, named: str
) -> None:
    """Exit 2, and a good signature beside the file does not stand in for it."""
    file = app_file(tmp_path / "files")
    publisher.sign(file)
    sums = tmp_path / "SHA256SUMS"
    sums.write_bytes(line_of(file))
    options = {"signature": ("--signature", tmp_path / "gone"),
               "checksums": ("--checksums", tmp_path / "gone"),
               "its-sig": ("--checksums", sums)}  # fmt: skip

    done = run(fsbpyr, "verify", "--project", "myapp", *options[named], file, env=env)

    no_verdict(done)
    assert "cannot read" in done.stderr


@pytest.mark.parametrize("option", ["--signature", "--checksums"])
def test_a_fifo_or_a_directory_as_a_named_file_stops_nothing(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], publisher: Publisher, option: str
) -> None:
    file = app_file(tmp_path / "files")
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)

    for named in (fifo, tmp_path):
        no_verdict(run(fsbpyr, "verify", "--project", "myapp", option, named, file, env=env))


def test_fsbpyr_takes_its_files_by_option_too_and_asks_no_server(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], release_keys: ReleaseKeys,
    server: FakeReleases,
) -> None:
    sums = tmp_path / "SHA256SUMS"
    sums.write_bytes(line_of(fsbpyr))
    signature = move(sign(release_keys.k.key, sums), tmp_path / "elsewhere")

    done = run(fsbpyr, "verify", "--checksums", sums, "--signature", signature, fsbpyr,
               env=downloads(env, server))  # fmt: skip

    verified(done)
    not_warned(done)
    assert server.requests == []


def test_a_key_that_is_not_pinned_signs_nothing_of_fsbpyrs_by_option_either(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], release_keys: ReleaseKeys
) -> None:
    sums = tmp_path / "SHA256SUMS"
    sums.write_bytes(line_of(fsbpyr))
    sign(release_keys.stray.key, sums)
    store = tmp_path / "config" / "fsbpyr" / "trusted"
    store.mkdir(parents=True)
    shutil.copy2(release_keys.stray.pub, store / "fsbpyr.pub")  # planted, as `key trust` refuses

    not_verified(run(fsbpyr, "verify", "--checksums", sums, fsbpyr, env=env))


# --- against the release it downloads ------------------------------------------------------------


def test_fsbpyr_checks_itself_against_the_release_it_records(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], release_keys: ReleaseKeys,
    server: FakeReleases,
) -> None:
    publish(server, tmp_path, release_keys.k, line_of(fsbpyr))

    done = run(fsbpyr, "verify", fsbpyr, env=downloads(env, server))

    verified(done)
    assert f"listed as {ASSET} in {SUMS} of {TAG}" in done.stdout
    assert fingerprint(release_keys.k.pub.read_bytes()) in done.stdout
    assert server.requests == asked_for()
    assert f"fsbpyr: downloading {SUMS} and {SUMS}.sig of {TAG}" in done.stderr
    warned(done)


def test_the_recovery_key_passes_too(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], release_keys: ReleaseKeys,
    server: FakeReleases,
) -> None:
    publish(server, tmp_path, release_keys.r, line_of(fsbpyr))

    done = run(fsbpyr, "verify", fsbpyr, env=downloads(env, server))

    verified(done)
    assert fingerprint(release_keys.r.pub.read_bytes()) in done.stdout


def test_a_link_to_fsbpyr_is_checked_as_the_file_it_leads_to(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], release_keys: ReleaseKeys,
    server: FakeReleases,
) -> None:
    publish(server, tmp_path, release_keys.k, line_of(fsbpyr))
    link = tmp_path / "bin" / "fsbpyr"
    link.parent.mkdir()
    link.symlink_to(fsbpyr)

    verified(run(link, "verify", link, env=downloads(env, server)))


def test_the_release_asked_is_the_one_the_file_records(
    fsbpyr: Path, older: Path, tmp_path: Path, env: dict[str, str], release_keys: ReleaseKeys,
    server: FakeReleases,
) -> None:
    publish(server, tmp_path, release_keys.k, line_of(older), tag=OLDER)

    done = run(fsbpyr, "verify", older, env=downloads(env, server))

    verified(done)
    assert server.requests == asked_for(OLDER)
    warned(done, OLDER)


def test_a_changed_fsbpyr_fails_against_its_genuine_release(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], release_keys: ReleaseKeys,
    server: FakeReleases,
) -> None:
    publish(server, tmp_path, release_keys.k, line_of(fsbpyr))
    changed = changed_copy(fsbpyr, tmp_path)

    done = run(fsbpyr, "verify", changed, env=downloads(env, server))

    not_verified(done)
    assert f"the signed {SUMS} of {TAG} does not list it" in done.stderr
    warned(done)


def test_a_release_signed_by_a_key_that_is_not_pinned_fails(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], release_keys: ReleaseKeys,
    server: FakeReleases,
) -> None:
    """A hacked release: its sums list the file, and the only key the thief has is their own."""
    publish(server, tmp_path, release_keys.stray, line_of(fsbpyr))

    done = run(fsbpyr, "verify", fsbpyr, env=downloads(env, server))

    not_verified(done)
    assert f"{SUMS} of {TAG} is not signed by the trusted key of fsbpyr" in done.stderr
    warned(done)


def test_a_release_signed_by_the_release_key_as_another_project_fails(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], release_keys: ReleaseKeys,
    server: FakeReleases,
) -> None:
    sums = line_of(fsbpyr)
    server.files[f"/{TAG}/{SUMS}"] = sums
    server.files[f"/{TAG}/{SUMS}.sig"] = sign_statement(
        release_keys.k.key, statement("otherapp", sums), tmp_path)

    not_verified(run(fsbpyr, "verify", fsbpyr, env=downloads(env, server)))


def test_a_real_signature_of_other_checksums_fails(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], release_keys: ReleaseKeys,
    server: FakeReleases,
) -> None:
    """A replay: the signature of a genuine release, over sums that now also list a forgery."""
    publish(server, tmp_path, release_keys.k, b"0" * 64 + b"  another-file\n")
    server.files[f"/{TAG}/{SUMS}"] += line_of(fsbpyr)

    not_verified(run(fsbpyr, "verify", fsbpyr, env=downloads(env, server)))


@pytest.mark.parametrize("position", [0, ED25519_SIZE - 1, ED25519_SIZE, SIGNATURE_SIZE - 1],
                         ids=["ed25519-first", "ed25519-last", "ml-dsa-first",
                              "ml-dsa-last"])  # fmt: skip
def test_one_flipped_bit_in_either_half_fails(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], release_keys: ReleaseKeys,
    server: FakeReleases, position: int,
) -> None:
    publish(server, tmp_path, release_keys.k, line_of(fsbpyr))
    signature = bytearray(server.files[f"/{TAG}/{SUMS}.sig"])
    signature[position] ^= 1
    server.files[f"/{TAG}/{SUMS}.sig"] = bytes(signature)

    not_verified(run(fsbpyr, "verify", fsbpyr, env=downloads(env, server)))


@pytest.mark.parametrize("name", [SUMS, f"{SUMS}.sig"])
def test_a_release_without_one_of_the_files_fails(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], release_keys: ReleaseKeys,
    server: FakeReleases, name: str,
) -> None:
    publish(server, tmp_path, release_keys.k, line_of(fsbpyr))
    del server.files[f"/{TAG}/{name}"]

    done = run(fsbpyr, "verify", fsbpyr, env=downloads(env, server))

    not_verified(done)
    assert f"release {TAG} has no valid release signature" in done.stderr


def test_no_such_release_fails(
    fsbpyr: Path, env: dict[str, str], server: FakeReleases
) -> None:
    not_verified(run(fsbpyr, "verify", fsbpyr, env=downloads(env, server)))


@pytest.mark.parametrize(("name", "size"), [(SUMS, (1 << 20) + 1),
                                            (f"{SUMS}.sig", SIGNATURE_SIZE + 1),
                                            (f"{SUMS}.sig", SIGNATURE_SIZE - 1),
                                            (f"{SUMS}.sig", 0)],
                         ids=["sums-too-long", "sig-too-long", "sig-too-short",
                              "sig-empty"])  # fmt: skip
def test_a_file_of_another_size_fails(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], release_keys: ReleaseKeys,
    server: FakeReleases, name: str, size: int,
) -> None:
    publish(server, tmp_path, release_keys.k, line_of(fsbpyr))
    body = server.files[f"/{TAG}/{name}"]
    server.files[f"/{TAG}/{name}"] = (body * (size // len(body) + 1))[:size]

    not_verified(run(fsbpyr, "verify", fsbpyr, env=downloads(env, server)))


def test_no_network_says_nothing_about_the_file_and_names_the_way_on(
    fsbpyr: Path, env: dict[str, str]
) -> None:
    done = run(fsbpyr, "verify", fsbpyr, env=env)

    no_verdict(done)
    assert "--checksums and --signature" in done.stderr
    not_warned(done)


def test_a_tag_that_is_no_release_goes_into_no_url(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], server: FakeReleases
) -> None:
    """A name read from a file: it could leave the release's directory."""
    forged = forged_tag(fsbpyr, tmp_path, b"../a/b")

    done = run(fsbpyr, "verify", forged, env=downloads(env, server))

    not_verified(done)
    assert "it records no release" in done.stderr
    assert server.requests == []
    assert "../a/b" not in done.stderr


def test_a_forged_tag_of_a_real_release_gets_a_release_that_does_not_list_the_file(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], release_keys: ReleaseKeys,
    server: FakeReleases,
) -> None:
    publish(server, tmp_path, release_keys.k, line_of(fsbpyr), tag=OLDER)
    forged = forged_tag(fsbpyr, tmp_path, OLDER.encode())

    done = run(fsbpyr, "verify", forged, env=downloads(env, server))

    not_verified(done)
    assert server.requests == asked_for(OLDER)


@pytest.mark.parametrize("tag", [f"v{'1' * 5000}.0.0", "\x1b[31mv1.2.0", "v1.2.0/../../x", 12, None,
                                 ["v1.2.0"], "v\u0661.\u0662.\u0660"],
                         ids=["5000-digits", "terminal-escape", "path", "number", "null", "list",
                              "arabic-digits"])  # fmt: skip
def test_a_forged_tag_of_any_form_asks_no_server(
    fsbpyr: Path, runtime: Runtime, tmp_path: Path, env: dict[str, str], server: FakeReleases,
    tag: object,
) -> None:
    """Written with the real writer, so with any length and a valid digest, as a forger would."""
    forged = tmp_path / "forged"
    info = (("project", {"name": "fsbpyr", "version": "1"}), ("git", {"tag": tag}))
    build_executable(runtime, App("app", "app:main", dists=(), build_info=info), forged)

    done = run(fsbpyr, "verify", forged, env=downloads(env, server))

    not_verified(done)
    assert "it records no release" in done.stderr
    assert server.requests == []
    assert "\x1b" not in done.stderr


def test_a_build_that_records_no_tag_asks_no_server(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], server: FakeReleases
) -> None:
    file = tmp_path / "fsbpyr"
    data = fsbpyr.read_bytes()
    assert data.count(b'"tag": "') == 1
    file.write_bytes(data.replace(b'"tag": "', b'"tog": "'))

    done = run(fsbpyr, "verify", file, env=downloads(env, server))

    not_verified(done)
    assert server.requests == []


def test_an_app_is_never_looked_up_on_github(
    fsbpyr: Path, runtime: Runtime, tmp_path: Path, env: dict[str, str], publisher: Publisher,
    server: FakeReleases,
) -> None:
    """An executable of a project, which records a tag like any other: a release of fsbpyr's has
    nothing to say about it, even one that its own key signed and that lists it."""
    file = tmp_path / "files" / "myapp"
    file.parent.mkdir()
    info = (("project", {"name": "myapp", "version": "1"}), ("git", {"tag": TAG}))
    build_executable(runtime, App("app", "app:main", dists=(), build_info=info), file)
    sums = tmp_path / "published" / SUMS
    sums.parent.mkdir()
    sums.write_bytes(line_of(file))
    server.files[f"/{TAG}/{SUMS}"] = sums.read_bytes()
    server.files[f"/{TAG}/{SUMS}.sig"] = publisher.sign(sums).read_bytes()

    done = run(fsbpyr, "verify", "--project", "myapp", file, env=downloads(env, server))

    not_verified(done)
    assert "no myapp.sig, and no signed SHA256SUMS beside it lists it" in done.stderr
    assert server.requests == []
    not_warned(done)


def test_a_plain_file_named_as_fsbpyr_records_no_release(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], server: FakeReleases
) -> None:
    file = app_file(tmp_path / "files", "not-an-executable")

    done = run(fsbpyr, "verify", "--project", "fsbpyr", file, env=downloads(env, server))

    not_verified(done)
    assert server.requests == []


@pytest.mark.parametrize("beside", ["fsbpyr.sig", SUMS, "SHA256SUMS-glibc2.28"])
def test_a_bad_file_beside_fsbpyr_is_not_gone_around_by_downloading(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], release_keys: ReleaseKeys,
    server: FakeReleases, beside: str,
) -> None:
    """Whoever lays a file there means it to be what is checked. A good release must not hide
    that it fails, and a check must not depend on who answers first."""
    publish(server, tmp_path, release_keys.k, line_of(fsbpyr))
    copy = tmp_path / "bin" / "fsbpyr"
    copy.parent.mkdir()
    shutil.copy2(fsbpyr, copy)
    (copy.parent / beside).write_bytes(b"\0" * SIGNATURE_SIZE)

    done = run(fsbpyr, "verify", copy, env=downloads(env, server))

    not_verified(done)
    assert server.requests == []
    not_warned(done)


def test_a_dangling_link_as_the_signature_is_not_gone_around_by_downloading(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], release_keys: ReleaseKeys,
    server: FakeReleases,
) -> None:
    publish(server, tmp_path, release_keys.k, line_of(fsbpyr))
    copy = tmp_path / "bin" / "fsbpyr"
    copy.parent.mkdir()
    shutil.copy2(fsbpyr, copy)
    (copy.parent / "fsbpyr.sig").symlink_to(tmp_path / "gone")

    done = run(fsbpyr, "verify", copy, env=downloads(env, server))

    not_verified(done)
    assert server.requests == []


def test_a_good_signature_beside_fsbpyr_needs_no_download_and_no_warning(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], release_keys: ReleaseKeys,
    server: FakeReleases,
) -> None:
    copy = tmp_path / "fsbpyr"
    shutil.copy2(fsbpyr, copy)
    sums = tmp_path / SUMS
    sums.write_bytes(line_of(copy))
    sign(release_keys.k.key, sums)

    done = run(fsbpyr, "verify", copy, env=downloads(env, server))

    verified(done)
    not_warned(done)
    assert server.requests == []


def test_the_release_cannot_name_another_project(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], server: FakeReleases
) -> None:
    """--project goes first: an fsbpyr is not checked as an app, and asks nothing."""
    done = run(fsbpyr, "verify", "--project", "myapp", fsbpyr, env=downloads(env, server))

    not_verified(done)
    assert "it is an executable of project fsbpyr, not of myapp" in done.stderr
    assert server.requests == []


# --- what the Security chapter tells a user to type ----------------------------------------------


def test_the_four_commands_of_the_security_chapter_do_what_it_says(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str]
) -> None:
    """Run as they are printed, from the chapter's own text: this is the whole of what someone who
    only wants a signed app reads. Only the fingerprint is theirs."""
    chapter = (ROOT / "wiki" / "User-Security.md").read_text(encoding="utf-8")
    block = re.search(r"Four commands, and nothing to install:\n\n```sh\n(.*?)```", chapter, re.S)
    assert block is not None
    project = tmp_path / "myapp"
    shutil.copytree(HELLO, project)
    (project / "src" / "hello").rename(project / "src" / "myapp")
    for file in (project / "pyproject.toml", project / "uv.lock"):
        file.write_text(file.read_text().replace("hello", "myapp"))
    (project / ".python-version").write_text(f"{TARGET_PYTHON}\n")
    given = {**env, "FSBPYR_SIGNING_PASSPHRASE": PASSPHRASE}

    outputs: list[str] = []
    for line in block[1].splitlines():
        command = shlex.split(line.partition("  #")[0])
        assert command[0] == "fsbpyr"
        arguments = [outputs[0].split("fingerprint ")[1].strip() if word.startswith("SHA256:")
                     else word for word in command[1:]]  # fmt: skip
        done = contain.run([fsbpyr, *arguments], env=given, cwd=project, stdin="", timeout=TIMEOUT)
        assert done.returncode == 0, done.stderr
        outputs.append(done.stdout)

    assert outputs[-1].startswith("VERIFIED  dist/myapp  (project myapp,")
    assert (project / "dist" / "myapp.sig").stat().st_size == SIGNATURE_SIZE
