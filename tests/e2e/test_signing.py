"""`fsbpyr key`, `sign`, `verify` and signing in `build` on a built fsbpyr, whose crypto is the
real C (docs/signing.md). Each direction against tools/release/release-signing.sh: without these
tests `key new` and `sign` first meet the real C at users, and the script is what CI signs with.

A built fsbpyr is built from a tagged copy of the checkout that pins test keys
(tests/e2e/conftest.py); it runs contained, as every built executable here."""

import hashlib
import json
import os
import shutil
import stat
import subprocess
import termios
from pathlib import Path

import pytest

from fsbpyr.build import TARGET_PYTHON
from fsbpyr.payload.format import TRAILER_SIZE
from fsbpyr.runtime.registry import Runtime

from ..support import contain, requires
from ..support.signing import (
    KEY_SIZE,
    PASSPHRASE,
    PUB_SIZE,
    SIGNATURE_SIZE,
    fingerprint,
    new_key,
    on_a_terminal,
    script,
    sign,
    statement,
)
from .conftest import ReleaseKeys, built_release

pytestmark = pytest.mark.slow

ROOT = Path(__file__).parents[2]
HELLO = ROOT / "tests" / "fixtures" / "projects" / "hello"
TIMEOUT = 300


@pytest.fixture(scope="module")
def fsbpyr(
    tmp_path_factory: pytest.TempPathFactory, runtime: Runtime, release_keys: ReleaseKeys
) -> Path:
    return built_release(tmp_path_factory.mktemp("release"), "v1.2.0", release_keys.k.pub,
                         release_keys.r.pub)  # fmt: skip


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    """A run's environment: a config directory and a cache of its own."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith("FSBPYR_SIGNING")}
    return {**clean, "XDG_CONFIG_HOME": str(tmp_path / "config"), "FSBPYR_GC": "0",
            "FSBPYR_CACHE_DIR": str(tmp_path / "cache")}  # fmt: skip


def run(
    fsbpyr: Path, *args: str | Path, env: dict[str, str], cwd: Path | None = None,
    passphrase: str | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = dict(env)
    if passphrase is not None:
        environment["FSBPYR_SIGNING_PASSPHRASE"] = passphrase
    return contain.run([fsbpyr, *args], env=environment, cwd=cwd, stdin="", timeout=TIMEOUT)


def trust(fsbpyr: Path, project: str, pub: Path, env: dict[str, str]) -> None:
    done = run(fsbpyr, "key", "trust", project, pub, fingerprint(pub.read_bytes()), env=env)
    assert done.returncode == 0, done.stderr


def openssl_verifies(pub: Path, message: bytes, signature: bytes, directory: Path) -> bool:
    """Both halves checked by the openssl command, nothing of fsbpyr: the script checks the
    subject fsbpyr only, and an app's statement needs a checker of its own."""
    pems = pub.read_text().split("-----END PUBLIC KEY-----\n")[:2]
    (directory / "statement").write_bytes(message)
    halves = (signature[:64], signature[64:])
    for index, (pem, half) in enumerate(zip(pems, halves, strict=True)):
        (directory / f"pub.{index}").write_text(f"{pem}-----END PUBLIC KEY-----\n")
        (directory / f"sig.{index}").write_bytes(half)
        done = subprocess.run(
            [requires.tool("openssl"), "pkeyutl", "-verify", "-rawin", "-pubin", "-inkey",
             directory / f"pub.{index}", "-in", directory / "statement", "-sigfile",
             directory / f"sig.{index}"], capture_output=True, check=False)  # fmt: skip
        if done.returncode != 0:
            return False
    return True


# --- both ways against the script ---------------------------------------------------------------


def test_a_key_of_fsbpyr_signs_with_the_script_and_the_script_checks_it(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str]
) -> None:
    done = run(fsbpyr, "key", "new", tmp_path / "myapp", env=env, passphrase=PASSPHRASE)
    assert done.returncode == 0, done.stderr
    key, pub = tmp_path / "myapp.key", tmp_path / "myapp.pub"
    assert (key.stat().st_size, pub.stat().st_size) == (KEY_SIZE, PUB_SIZE)
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    assert f"fingerprint {fingerprint(pub.read_bytes())}" in done.stdout

    file = tmp_path / "SHA256SUMS"
    file.write_text("a file\n")
    sign(key, file)

    checked = script("verify", file, pub)
    assert checked.returncode == 0, checked.stderr


def test_a_key_of_the_script_signs_with_fsbpyr_and_openssl_checks_it(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str]
) -> None:
    key, pub = new_key(tmp_path / "myapp")
    file = tmp_path / "file"
    file.write_text("a file\n")

    done = run(fsbpyr, "sign", "--project", "myapp", "--signing-key", key, file, env=env,
               passphrase=PASSPHRASE)  # fmt: skip

    assert done.returncode == 0, done.stderr
    assert fingerprint(pub.read_bytes()) in done.stdout
    signature = (tmp_path / "file.sig").read_bytes()
    assert len(signature) == SIGNATURE_SIZE
    assert openssl_verifies(pub, statement("myapp", b"a file\n"), signature, tmp_path)


def test_a_published_file_passes_with_the_saved_key(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str]
) -> None:
    key, pub = new_key(tmp_path / "myapp")
    file = tmp_path / "file"
    file.write_text("a file\n")
    run(fsbpyr, "sign", "--project", "myapp", "--signing-key", key, file, env=env,
        passphrase=PASSPHRASE)  # fmt: skip

    trust(fsbpyr, "myapp", pub, env)
    done = run(fsbpyr, "verify", "--project", "myapp", file, env=env)

    assert done.returncode == 0, done.stderr
    assert f"VERIFIED  {file}  (project myapp, Ed25519 + ML-DSA-65, key " in done.stdout


def test_a_build_with_a_key_signs_and_a_user_checks_it(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str]
) -> None:
    """The publisher and the user of docs/signing.md §6, without fsbpyr's own project."""
    key, pub = new_key(tmp_path / "keys-hello")
    project = tmp_path / "hello"
    shutil.copytree(HELLO, project)
    out = tmp_path / "dist" / "hello"
    env = {**env, "FSBPYR_SIGNING_KEY": str(key)}

    done = run(fsbpyr, "build", project, "-o", out, "--python", TARGET_PYTHON, env=env,
               passphrase=PASSPHRASE)  # fmt: skip

    assert done.returncode == 0, done.stderr
    assert "WARNING" not in done.stderr
    signature = out.with_name("hello.sig").read_bytes()
    assert openssl_verifies(pub, statement("hello", out.read_bytes()), signature, tmp_path)
    trust(fsbpyr, "hello", pub, env)
    checked = run(fsbpyr, "verify", "--project", "hello", out, env=env)
    assert checked.returncode == 0, checked.stderr
    assert "(project hello," in checked.stdout


def test_a_changed_executable_fails_though_its_digest_is_written_again(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str]
) -> None:
    """D31 passes for whoever writes the digest; the signature is over the file's SHA-256."""
    key, pub = new_key(tmp_path / "keys-hello")
    project = tmp_path / "hello"
    shutil.copytree(HELLO, project)
    out = tmp_path / "hello-out"
    assert run(fsbpyr, "build", project, "-o", out, "--python", TARGET_PYTHON, "--signing-key", key,
               env=env, passphrase=PASSPHRASE).returncode == 0  # fmt: skip
    trust(fsbpyr, "hello", pub, env)
    data = bytearray(out.read_bytes())
    data[-(TRAILER_SIZE + 1)] ^= 1
    out.write_bytes(data)

    done = run(fsbpyr, "verify", "--project", "hello", out, env=env)

    assert done.returncode == 1 and done.stdout == ""


def test_every_truncation_and_appended_byte_fails(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str]
) -> None:
    key, pub = new_key(tmp_path / "myapp")
    file = tmp_path / "file"
    file.write_bytes(b"content" * 100)
    run(fsbpyr, "sign", "--project", "myapp", "--signing-key", key, file, env=env,
        passphrase=PASSPHRASE)  # fmt: skip
    trust(fsbpyr, "myapp", pub, env)
    original = file.read_bytes()

    for changed in (original[:-1], original + b"\0", b"\0" + original, b""):
        file.write_bytes(changed)
        assert run(fsbpyr, "verify", "--project", "myapp", file, env=env).returncode == 1


def test_the_pinned_keys_check_fsbpyrs_own_downloads(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], release_keys: ReleaseKeys
) -> None:
    """A user checks a download by hand: `verify --project fsbpyr` beside a signed SHA256SUMS,
    which the sign job's script made. Which asset it is, the line says."""
    asset, sums = tmp_path / "saved-as-this", tmp_path / "SHA256SUMS"
    asset.write_bytes(b"an asset\n")
    digest = hashlib.sha256(b"an asset\n").hexdigest()
    sums.write_text(f"{digest}  fsbpyr-3.14-x86_64-linux-gnu\n")
    sign(release_keys.k.key, sums)

    done = run(fsbpyr, "verify", "--project", "fsbpyr", asset, env=env)

    assert done.returncode == 0, done.stderr
    assert "listed as fsbpyr-3.14-x86_64-linux-gnu in SHA256SUMS" in done.stdout
    assert fingerprint(release_keys.k.pub.read_bytes()) in done.stdout


def test_a_key_that_is_not_pinned_signs_nothing_of_fsbpyrs(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], release_keys: ReleaseKeys
) -> None:
    asset = tmp_path / "file"
    asset.write_bytes(b"evil\n")
    sign(release_keys.stray.key, asset)
    # a key saved by hand for the subject, as a planted one would be
    store = tmp_path / "config" / "fsbpyr" / "trusted"
    store.mkdir(parents=True)
    shutil.copy2(release_keys.stray.pub, store / "fsbpyr.pub")

    done = run(fsbpyr, "verify", "--project", "fsbpyr", asset, env=env)

    assert done.returncode == 1 and done.stdout == ""


def test_the_recovery_key_passes_too(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], release_keys: ReleaseKeys
) -> None:
    file = tmp_path / "file"
    file.write_bytes(b"signed by hand in a recovery\n")
    sign(release_keys.r.key, file)

    assert run(fsbpyr, "verify", "--project", "fsbpyr", file, env=env).returncode == 0


# --- what cannot reach into a built fsbpyr ------------------------------------------------------


def test_files_in_the_current_directory_replace_nothing(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], release_keys: ReleaseKeys
) -> None:
    """A download unpacked here brings modules of these names: a built fsbpyr's _fsbpyr is
    built in, and its sys.path holds no directory of the file system. It also ignores PYTHON*."""
    planted = tmp_path / "planted"
    (planted / "fsbpyr").mkdir(parents=True)
    bomb = "raise SystemExit('a planted module ran')\n"
    for name in ("_fsbpyr.py", "signing.py", "hashlib.py", "json.py", "fsbpyr/__init__.py",
                 "fsbpyr/signing.py", "click.py"):  # fmt: skip
        (planted / name).write_text(bomb)
    file = planted / "file"
    file.write_bytes(b"evil\n")
    sign(release_keys.stray.key, file)
    env = {**env, "PYTHONPATH": str(planted), "PYTHONHOME": str(planted), "PYTHONSTARTUP": bomb,
           "PYTHONINSPECT": "1"}  # fmt: skip

    done = run(fsbpyr, "verify", "--project", "fsbpyr", file, env=env, cwd=planted)

    assert done.returncode == 1, done.stderr
    assert "a planted module ran" not in done.stderr and done.stdout == ""


def test_verify_passes_under_a_fips_config(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], release_keys: ReleaseKeys
) -> None:
    """Under `fips=yes` the default context finds no algorithm: a call that fell back to it, or
    read this file into its context, would fail here."""
    config = tmp_path / "openssl.cnf"
    config.write_text("openssl_conf = openssl_init\n[openssl_init]\nalg_section = props\n"
                      "[props]\ndefault_properties = fips=yes\n")
    file = tmp_path / "file"
    file.write_bytes(b"content\n")
    sign(release_keys.k.key, file)

    done = run(fsbpyr, "verify", "--project", "fsbpyr", file,
               env={**env, "OPENSSL_CONF": str(config)})  # fmt: skip

    assert done.returncode == 0, done.stderr


def test_a_forged_build_info_is_checked_by_the_real_reader(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str]
) -> None:
    """Only a checked name is joined into a path or printed; written with the real writer by
    tests/unit/test_signing.py on the stand-in, and here its last line of defence is the same
    code in a built fsbpyr, on an executable it did not build."""
    file = tmp_path / "forged"
    shutil.copy2(fsbpyr, file)
    data = bytearray(file.read_bytes())
    start = data.find(b'"project"')
    assert start > 0
    data[start:start + 9] = b'"xroject"'  # the build info no longer names a project
    file.write_bytes(data)

    done = run(fsbpyr, "verify", file, env=env)

    assert done.returncode == 1
    assert "its build info names no valid project" in done.stderr
    assert "Traceback" not in done.stderr


@pytest.mark.parametrize("cut", [1, 40, TRAILER_SIZE, 4096])
def test_a_damaged_executable_never_passes(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], cut: int, release_keys: ReleaseKeys
) -> None:
    """Cut or trailing garbage: as no executable it needs --project, and no signature matches."""
    file = tmp_path / "damaged"
    file.write_bytes(fsbpyr.read_bytes()[:-cut])
    sign(release_keys.stray.key, file)

    done = run(fsbpyr, "verify", "--project", "fsbpyr", file, env=env)

    assert done.returncode == 1 and done.stdout == ""
    assert "Traceback" not in done.stderr


# --- the passphrase -----------------------------------------------------------------------------


def test_a_new_key_asks_twice_on_the_terminal_and_keeps_spaces(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str]
) -> None:
    typed = b"  a passphrase with spaces  "

    code, printed, _ = on_a_terminal([fsbpyr, "key", "new", tmp_path / "k"], env, typed, typed)

    assert code == 0, printed
    assert typed not in printed  # echo was off
    file = tmp_path / "SHA256SUMS"
    file.write_text("x\n")
    sign(tmp_path / "k.key", file, passphrase=typed.decode())
    assert script("verify", file, tmp_path / "k.pub").returncode == 0


def test_two_different_passphrases_write_nothing(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str]
) -> None:
    code, printed, _ = on_a_terminal([fsbpyr, "key", "new", tmp_path / "k"], env, b"one", b"two")

    assert code == 2 and b"the passphrases differ" in printed
    assert not (tmp_path / "k.key").exists()


def test_a_build_asks_after_the_smoke_test_and_ctrl_c_keeps_the_output_unsigned(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str]
) -> None:
    key, _ = new_key(tmp_path / "k")
    project = tmp_path / "hello"
    shutil.copytree(HELLO, project)
    out = tmp_path / "hello-out"

    code, printed, flags = on_a_terminal(
        [fsbpyr, "build", project, "-o", out, "--python", TARGET_PYTHON, "--signing-key", key],
        env, interrupt=True)  # fmt: skip

    assert code == 130, printed
    assert printed.index(b"smoke-testing") < printed.index(b"Passphrase")
    assert out.exists() and not out.with_name("hello-out.sig").exists()
    assert flags[0] & termios.ECHO  # the terminal is as it was


def test_a_build_typed_on_the_terminal_signs(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str]
) -> None:
    key, _ = new_key(tmp_path / "k")
    project = tmp_path / "hello"
    shutil.copytree(HELLO, project)
    out = tmp_path / "hello-out"

    code, printed, _ = on_a_terminal(
        [fsbpyr, "build", project, "-o", out, "--python", TARGET_PYTHON, "--signing-key", key],
        env, PASSPHRASE.encode())  # fmt: skip

    assert code == 0, printed
    assert out.with_name("hello-out.sig").stat().st_size == SIGNATURE_SIZE


def test_without_a_terminal_and_the_variable_it_is_a_setup_error(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str]
) -> None:
    """Text on standard input is no passphrase, as getpass would take it."""
    key, _ = new_key(tmp_path / "k")
    file = tmp_path / "file"
    file.write_text("x\n")
    for arguments in (("key", "new", tmp_path / "new"),
                      ("sign", "--project", "myapp", "--signing-key", key, file)):  # fmt: skip
        done = subprocess.run([fsbpyr, *map(str, arguments)], input=f"{PASSPHRASE}\n", text=True,
                              capture_output=True, env=env, start_new_session=True, check=False,
                              timeout=TIMEOUT)  # fmt: skip

        assert done.returncode == 2 and "no passphrase" in done.stderr
    assert not (tmp_path / "new.key").exists() and not (tmp_path / "file.sig").exists()


def test_without_a_terminal_a_build_warns_and_stays_unsigned(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str]
) -> None:
    key, _ = new_key(tmp_path / "k")
    project = tmp_path / "hello"
    shutil.copytree(HELLO, project)
    out = tmp_path / "hello-out"

    done = subprocess.run(
        [fsbpyr, "build", str(project), "-o", str(out), "--python", TARGET_PYTHON, "--signing-key",
         str(key)], input="", text=True, capture_output=True, env=env, start_new_session=True,
        check=False, timeout=TIMEOUT)  # fmt: skip

    assert done.returncode == 0, done.stderr
    assert "fsbpyr: WARNING  " in done.stderr and "is NOT SIGNED" in done.stderr
    assert out.exists() and not out.with_name("hello-out.sig").exists()


@pytest.mark.parametrize("problem", ["a missing file", "not a key", "a wrong passphrase"])
def test_a_key_that_cannot_be_used_warns_and_the_build_stands(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], problem: str
) -> None:
    key, pub = new_key(tmp_path / "k")
    if problem == "a missing file":
        key.unlink()
    elif problem == "not a key":
        shutil.copy2(pub, key)
    project = tmp_path / "hello"
    shutil.copytree(HELLO, project)
    out = tmp_path / "hello-out"
    env = {**env, "FSBPYR_SIGNING_KEY": str(key)}

    done = run(fsbpyr, "build", project, "-o", out, "--python", TARGET_PYTHON, env=env,
               passphrase="wrong" if problem == "a wrong passphrase" else PASSPHRASE)  # fmt: skip

    assert done.returncode == 0, done.stderr
    assert "is NOT SIGNED" in done.stderr
    assert f"cannot open {key}" in done.stderr.replace("\nfsbpyr: WARNING  ", " ")
    assert out.exists() and not out.with_name("hello-out.sig").exists()


# --- what the build's processes get -------------------------------------------------------------

_BACKEND = """\
import base64, hashlib, json, os, pathlib, tarfile, zipfile

def _record():
    with open(os.environ["SEEN_FILE"], "a") as out:
        out.write(json.dumps(dict(os.environ)) + "\\n")

def get_requires_for_build_wheel(config_settings=None):
    _record()
    return []

def get_requires_for_build_sdist(config_settings=None):
    return []

def build_sdist(directory, config_settings=None):
    _record()
    name = "seen-1.0.tar.gz"
    pathlib.Path("PKG-INFO").write_text("Metadata-Version: 2.1\\nName: seen\\nVersion: 1.0\\n")
    with tarfile.open(pathlib.Path(directory) / name, "w:gz") as sdist:
        for path in ("PKG-INFO", "pyproject.toml", "seenbackend.py", "seen.py"):
            sdist.add(path, arcname=f"seen-1.0/{path}")
    return name

def prepare_metadata_for_build_wheel(directory, config_settings=None):
    return _write_dist_info(pathlib.Path(directory))

def _write_dist_info(directory):
    info = directory / "seen-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text("Metadata-Version: 2.1\\nName: seen\\nVersion: 1.0\\n")
    (info / "WHEEL").write_text(
        "Wheel-Version: 1.0\\nGenerator: t\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n")
    (info / "entry_points.txt").write_text("[console_scripts]\\nseen = seen:main\\n")
    return info.name

def build_wheel(directory, config_settings=None, metadata_directory=None):
    _record()
    name = "seen-1.0-py3-none-any.whl"
    source = pathlib.Path("seen.py").read_text()
    work = pathlib.Path(directory) / "work"
    work.mkdir()
    info = work / _write_dist_info(work)
    rows = []
    with zipfile.ZipFile(pathlib.Path(directory) / name, "w") as wheel:
        files = {"seen.py": source.encode()}
        for path in info.iterdir():
            files[f"{info.name}/{path.name}"] = path.read_bytes()
        for path, data in files.items():
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            rows.append(f"{path},sha256={digest},{len(data)}\\n")
            wheel.writestr(path, data)
        wheel.writestr(f"{info.name}/RECORD", "".join(rows))
    return name
"""
_SEEN_MODULE = """\
import json, os
with open(os.environ["SEEN_FILE"], "a") as out:
    out.write(json.dumps(dict(os.environ)) + "\\n")

def main():
    print("seen")
"""


def test_no_process_of_the_build_has_a_signing_variable_and_the_output_is_signed(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str]
) -> None:
    """The build backend and the app's entry module, which the smoke test imports, write their
    environment to a file. A process that prints or logs its environment shows neither variable.
    That keeps the passphrase from code that does not look for it, not from code that does
    (docs/signing.md §3.1)."""
    project = tmp_path / "seen"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "seen"\nversion = "1.0"\nrequires-python = ">=3.12"\n'
        '[project.scripts]\nseen = "seen:main"\n'
        '[build-system]\nrequires = []\nbuild-backend = "seenbackend"\nbackend-path = ["."]\n')
    (project / "seenbackend.py").write_text(_BACKEND)
    (project / "seen.py").write_text(_SEEN_MODULE)
    (project / "uv.lock").unlink(missing_ok=True)
    seen = tmp_path / "environments"
    key, _ = new_key(tmp_path / "k")
    out = tmp_path / "seen-out"
    env = {**env, "SEEN_FILE": str(seen), "FSBPYR_SIGNING_KEY": str(key)}

    done = run(fsbpyr, "build", project, "-o", out, "--python", TARGET_PYTHON, env=env,
               passphrase=PASSPHRASE)  # fmt: skip

    assert done.returncode == 0, done.stderr
    assert "WARNING" not in done.stderr
    assert out.with_name("seen-out.sig").stat().st_size == SIGNATURE_SIZE
    recorded = [json.loads(line) for line in seen.read_text().splitlines()]
    assert len(recorded) >= 2  # the backend, and the smoke test
    for environment in recorded:
        assert not [name for name in environment if name.startswith("FSBPYR_SIGNING")]
    assert PASSPHRASE not in seen.read_text()


# --- where the signature goes -------------------------------------------------------------------


def test_a_build_through_a_users_link_signs_the_file_it_writes(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str]
) -> None:
    key, pub = new_key(tmp_path / "k")
    project = tmp_path / "hello"
    shutil.copytree(HELLO, project)
    (tmp_path / "opt").mkdir()
    (tmp_path / "bin").mkdir()
    (tmp_path / "opt" / "hello-1.2").write_bytes(b"")
    link = tmp_path / "bin" / "hello"
    link.symlink_to("../opt/hello-1.2")

    done = run(fsbpyr, "build", project, "-o", link, "--python", TARGET_PYTHON, "--signing-key",
               key, env=env, passphrase=PASSPHRASE)  # fmt: skip

    assert done.returncode == 0, done.stderr
    assert (tmp_path / "opt" / "hello-1.2.sig").is_file()
    assert not (tmp_path / "bin" / "hello.sig").exists()
    trust(fsbpyr, "hello", pub, env)
    assert run(fsbpyr, "verify", "--project", "hello", link, env=env).returncode == 0


def test_a_build_removes_a_stale_signature_with_or_without_a_key(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str]
) -> None:
    project = tmp_path / "hello"
    shutil.copytree(HELLO, project)
    out = tmp_path / "hello-out"
    out.with_name("hello-out.sig").write_bytes(b"of the file before")

    done = run(fsbpyr, "build", project, "-o", out, "--python", TARGET_PYTHON, env=env)

    assert done.returncode == 0, done.stderr
    assert not out.with_name("hello-out.sig").exists()


def test_no_stale_signature_stays_if_the_build_is_stopped_at_the_prompt(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str]
) -> None:
    key, _ = new_key(tmp_path / "k")
    project = tmp_path / "hello"
    shutil.copytree(HELLO, project)
    out = tmp_path / "hello-out"
    out.with_name("hello-out.sig").write_bytes(b"of the file before")

    code, printed, _ = on_a_terminal(
        [fsbpyr, "build", project, "-o", out, "--python", TARGET_PYTHON, "--signing-key", key],
        env, interrupt=True)  # fmt: skip

    assert code == 130, printed
    assert not out.with_name("hello-out.sig").exists()


def test_a_command_named_as_the_signature_fails_the_build(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str]
) -> None:
    project = tmp_path / "many"
    (project / "src" / "many").mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        '[project]\nname = "many"\nversion = "1.0"\nrequires-python = ">=3.12"\n'
        '[project.scripts]\nmany = "many:a"\n"many.sig" = "many:b"\n'
        '[build-system]\nrequires = ["uv_build>=0.12.17,<0.14.0"]\nbuild-backend = "uv_build"\n')
    (project / "src" / "many" / "__init__.py").write_text(
        "def a():\n    pass\ndef b():\n    pass\n")
    out = tmp_path / "many-out" / "many"

    done = run(fsbpyr, "build", project, "-o", out, "--python", TARGET_PYTHON, env=env)

    assert done.returncode == 1
    assert "a command named many.sig would take the path of the signature" in done.stderr
    assert not out.exists()


def test_a_command_link_is_checked_as_its_executable(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str]
) -> None:
    key, pub = new_key(tmp_path / "k")
    project = tmp_path / "many"
    (project / "src" / "many").mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        '[project]\nname = "many"\nversion = "1.0"\nrequires-python = ">=3.12"\n'
        '[project.scripts]\none = "many:a"\ntwo = "many:b"\n'
        '[build-system]\nrequires = ["uv_build>=0.12.17,<0.14.0"]\nbuild-backend = "uv_build"\n')
    (project / "src" / "many" / "__init__.py").write_text(
        "def a():\n    pass\ndef b():\n    pass\n")
    out = tmp_path / "out" / "many"

    done = run(fsbpyr, "build", project, "-o", out, "--python", TARGET_PYTHON, "--signing-key", key,
               env=env, passphrase=PASSPHRASE)  # fmt: skip

    assert done.returncode == 0, done.stderr
    assert sorted(p.name for p in out.parent.iterdir()) == ["many", "many.sig", "one", "two"]
    trust(fsbpyr, "many", pub, env)
    assert run(fsbpyr, "verify", "--project", "many", out.parent / "one", env=env).returncode == 0


def test_a_project_of_the_name_fsbpyr_builds_unsigned_with_a_warning(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str]
) -> None:
    key, _ = new_key(tmp_path / "k")
    project = tmp_path / "fsbpyr"
    project.mkdir()
    (project / "tool.py").write_text("if __name__ == '__main__':\n    print('hi')\n")
    out = tmp_path / "out"

    done = run(fsbpyr, "build", project, "-o", out, "--python", TARGET_PYTHON, "--signing-key", key,
               env=env, passphrase=PASSPHRASE)  # fmt: skip

    assert done.returncode == 0, done.stderr
    assert "is NOT SIGNED" in done.stderr
    assert not out.with_name("out.sig").exists()


def test_the_signing_commands_leave_no_terminal_escape_in_a_report(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str], release_keys: ReleaseKeys
) -> None:
    (tmp_path / "dir").mkdir()
    file = tmp_path / "dir" / "a\x1b[2Jb"
    file.write_bytes(b"content\n")
    sign(release_keys.k.key, file)

    done = run(fsbpyr, "verify", "--project", "fsbpyr", file, env=env)

    assert done.returncode == 0, done.stderr
    assert "\x1b" not in done.stdout


def test_a_hanging_fifo_as_the_key_file_stops_nothing(
    fsbpyr: Path, tmp_path: Path, env: dict[str, str]
) -> None:
    key, file = tmp_path / "fifo", tmp_path / "file"
    os.mkfifo(key)
    file.write_text("x\n")

    done = run(fsbpyr, "sign", "--project", "myapp", "--signing-key", key, file, env=env,
               passphrase=PASSPHRASE)  # fmt: skip

    assert done.returncode == 2 and "cannot open" in done.stderr


