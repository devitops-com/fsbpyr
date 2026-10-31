"""docs/signing.md from source: the statement, the names, the trust store, how a file is looked up
and what each command says and exits with, on the stand-in for sig.c (tests/support/signing.py).
tests/e2e/test_signatures.py tests the C, and tests/e2e/test_signing.py the commands on it.

The tests that matter most here are the ones where a file lies: a forged executable can name
anything in its build info, and its D31 digest passes, since the forger writes it."""

import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from fsbpyr import signing
from fsbpyr.assemble import App, build_executable
from fsbpyr.cli import main

from ..support import signing as support
from ..support.signing import PUB_SIZE, SIGNATURE_SIZE, fake_pub, fake_signature, use_stand_in
from .boot.conftest import FAKE_RUNTIME

PASSPHRASE = b"a passphrase"
MYAPP_PUB = fake_pub("myapp's key")
EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


@pytest.fixture(autouse=True)
def stand_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_stand_in(monkeypatch)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv(signing.KEY_VARIABLE, raising=False)
    monkeypatch.delenv(signing.PASSPHRASE_VARIABLE, raising=False)


def trust(project: str = "myapp", pub: bytes = MYAPP_PUB) -> None:
    store = signing.trust_store()
    (store / f"{project}.pub").write_bytes(pub)


def signature(data: bytes, project: str = "myapp", pub: bytes = MYAPP_PUB) -> bytes:
    return fake_signature(pub, support.statement(project, data))


def signed_file(path: Path, data: bytes = b"content", project: str = "myapp") -> Path:
    path.write_bytes(data)
    path.with_name(f"{path.name}.sig").write_bytes(signature(data, project))
    return path


def executable(path: Path, project: object, *, app: str = "app") -> Path:
    """An executable with a valid D31 digest whose build info names ``project``, as a forger
    writes one; with ``...`` it has no project at all."""
    info = () if project is ... else (("project", {"name": project, "version": "1"}),)
    build_executable(FAKE_RUNTIME, App(app, "app:main", dists=(), build_info=info), path)
    return path


def command(*args: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = main(list(args))
    out, err = capsys.readouterr()
    return code, out, err


# --- the statement ------------------------------------------------------------------------------


def test_the_statement_is_these_bytes() -> None:
    message = signing.statement("myapp", bytes.fromhex(EMPTY_SHA256))

    assert message == b"fsbpyr-signature 1\nsubject myapp\nsha256 %b\n" % EMPTY_SHA256.encode()
    assert len(message) == 105
    assert message == support.statement("myapp", b"")


def test_the_statement_of_fsbpyr_is_the_scripts() -> None:
    assert signing.statement("fsbpyr", bytes.fromhex(EMPTY_SHA256)).startswith(
        b"fsbpyr-signature 1\nsubject fsbpyr\nsha256 e3b0c442")


# --- names --------------------------------------------------------------------------------------


@pytest.mark.parametrize(("typed", "name"), [
    ("myapp", "myapp"), ("My_App", "my-app"), ("MY.APP", "my-app"), ("a--b", "a-b"), ("a1", "a1"),
    ("x" * 64, "x" * 64),
])
def test_a_typed_name_is_normalized(typed: str, name: str) -> None:
    assert signing.typed_name(typed) == name


@pytest.mark.parametrize("typed", [
    "", "-a", "a-", "a/b", "../x", "/tmp/x", "a b", "a\n", "\nmyapp", "myapp\n", "x" * 65,  # noqa: S108
    "\u212aelvin", "\uff4d\uff59\uff41\uff50\uff50", "a\x1b[2Jb", "a\0b",
])
def test_a_typed_name_of_another_form_is_refused(typed: str) -> None:
    with pytest.raises(signing.SigningError, match="not a project name"):
        signing.typed_name(typed)


@pytest.mark.parametrize("name", [
    "myapp\n", "My_App", "MyApp", "a_b", "a.b", "../../../Downloads/x", "/tmp/x", "a/b",  # noqa: S108
    "x" * 65, "a\x1b]0;x\x07", 7, None, ["myapp"], {"a": 1}, True,
])
def test_a_name_from_a_file_is_not_normalized_into_a_valid_one(name: object) -> None:
    assert signing.valid_name(name) is False


# --- what a forged executable can name ----------------------------------------------------------

HOSTILE = ("../../../Downloads/x", "/tmp/x", "a/b", "myapp\n", "\x1b[2Jmyapp", "My_App", "x" * 65,  # noqa: S108
           7, None, ..., ["myapp"], {"name": "myapp"})  # fmt: skip


@pytest.mark.parametrize("project", HOSTILE, ids=repr)
@pytest.mark.parametrize("typed", [(), ("--project", "myapp")], ids=["bare", "with --project"])
def test_a_build_info_without_a_valid_name_is_not_verified(
    tmp_path: Path, project: object, typed: tuple[str, ...], capsys: pytest.CaptureFixture[str]
) -> None:
    """Signed by a key planted where the unchecked path leads, so that only the check of the name
    stops it. The name is not printed."""
    trust()
    planted = tmp_path / "Downloads" / "x.pub"  # where trusted/../../../Downloads/x.pub leads
    planted.parent.mkdir()
    planted.write_bytes(MYAPP_PUB)
    file = executable(tmp_path / "forged", project)
    data = file.read_bytes()
    file.with_name("forged.sig").write_bytes(signature(data, str(project)))

    code, out, err = command("verify", *typed, str(file), capsys=capsys)

    assert (code, out) == (1, "")
    assert err == f"NOT VERIFIED: {file}: its build info names no valid project\n"


@pytest.mark.parametrize("project", ["../x", "myapp\n", "x" * 65, 7, None, ...], ids=repr)
def test_signing_an_executable_without_a_valid_name_is_an_error(
    tmp_path: Path, project: object, capsys: pytest.CaptureFixture[str]
) -> None:
    key = tmp_path / "k.key"
    key.write_bytes(b"key:" + PASSPHRASE)
    os.environ[signing.PASSPHRASE_VARIABLE] = PASSPHRASE.decode()
    try:
        code, _, err = command("sign", "--signing-key", str(key), str(executable(
            tmp_path / "forged", project)), capsys=capsys)  # fmt: skip
    finally:
        del os.environ[signing.PASSPHRASE_VARIABLE]

    assert code == 2 and "names no valid project" in err
    assert not (tmp_path / "forged.sig").exists()


def test_a_forged_build_info_naming_a_trusted_project_needs_its_signature(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    trust()
    file = executable(tmp_path / "forged", "myapp")
    forged = support.statement("myapp", b"x")
    file.with_name("forged.sig").write_bytes(fake_signature(fake_pub("the forger's key"), forged))

    code, _, err = command("verify", str(file), capsys=capsys)

    assert code == 1 and "does not match the trusted key of myapp" in err


def test_a_file_named_after_a_project_is_not_that_project(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    trust()
    trust("otherapp", fake_pub("other"))
    file = executable(tmp_path / "myapp", "otherapp")
    file.with_name("myapp.sig").write_bytes(fake_signature(fake_pub("other"), support.statement(
        "otherapp", file.read_bytes())))  # fmt: skip

    code, _, err = command("verify", "--project", "myapp", str(file), capsys=capsys)

    assert code == 1
    assert err == f"NOT VERIFIED: {file}: it is an executable of project otherapp, not of myapp\n"


def test_a_genuine_file_of_another_trusted_project_passes_only_as_that_project(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    trust("otherapp", fake_pub("other"))
    file = executable(tmp_path / "myapp", "otherapp")
    file.with_name("myapp.sig").write_bytes(fake_signature(fake_pub("other"), support.statement(
        "otherapp", file.read_bytes())))  # fmt: skip

    code, out, _ = command("verify", str(file), capsys=capsys)

    assert code == 0 and "(project otherapp," in out


def test_a_garbage_file_that_looks_like_an_executable_cannot_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    file = executable(tmp_path / "file", "myapp")
    data = bytearray(file.read_bytes())
    data[-100:] = bytes(100)  # its trailer goes: then it is no executable, and needs --project
    file.write_bytes(data)

    code, _, err = command("verify", str(file), capsys=capsys)

    assert code == 2 and "has no build info: name its project with --project" in err


# --- the signature ------------------------------------------------------------------------------


def test_a_signed_file_passes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    trust()
    file = signed_file(tmp_path / "file")

    code, out, _ = command("verify", "--project", "myapp", str(file), capsys=capsys)

    assert code == 0
    assert out == (f"VERIFIED  {file}  (project myapp, Ed25519 + ML-DSA-65, key "
                   f"{support.fingerprint(MYAPP_PUB)})\n")


@pytest.mark.parametrize("change", [
    lambda data, sig: (data + b"x", sig),
    lambda data, sig: (b"", sig),
    lambda data, sig: (data, bytes(SIGNATURE_SIZE)),
    lambda data, sig: (data, sig[:-1] + bytes([sig[-1] ^ 1])),
    lambda data, sig: (data, signature(data, "otherapp")),
    lambda data, sig: (data, fake_signature(fake_pub("another key"),
                                            support.statement("myapp", data))),
], ids=["a byte more", "emptied", "zeros", "a flipped bit", "another project's", "another key's"])
def test_a_changed_file_or_signature_is_not_verified(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], change: object
) -> None:
    trust()
    file = signed_file(tmp_path / "file")
    data, sig = change(file.read_bytes(), file.with_name("file.sig").read_bytes())  # type: ignore[operator]
    file.write_bytes(data)
    file.with_name("file.sig").write_bytes(sig)

    code, out, err = command("verify", "--project", "myapp", str(file), capsys=capsys)

    assert (code, out) == (1, "")
    assert err == (f"NOT VERIFIED: {file}: the signature does not match the trusted key of "
                   "myapp\n")


@pytest.mark.parametrize("size", [0, 64, 3372, 3374, 6746])
def test_a_sig_of_another_size_is_not_a_signature(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], size: int
) -> None:
    trust()
    file = signed_file(tmp_path / "file")
    file.with_name("file.sig").write_bytes(bytes(size))

    code, _, err = command("verify", "--project", "myapp", str(file), capsys=capsys)

    assert code == 1
    assert err == f"NOT VERIFIED: {file}: file.sig is not a signature ({size} bytes)\n"


@pytest.mark.parametrize("make", [os.mkfifo, os.mkdir], ids=["a FIFO", "a directory"])
def test_a_sig_that_is_no_file_does_not_hang_a_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], make: object
) -> None:
    trust()
    file = signed_file(tmp_path / "file")
    file.with_name("file.sig").unlink()
    make(file.with_name("file.sig"))  # type: ignore[operator]

    code, _, err = command("verify", "--project", "myapp", str(file), capsys=capsys)

    assert code == 1 and "is not a signature" in err


def test_a_missing_sig_is_not_verified(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    trust()
    file = tmp_path / "file"
    file.write_bytes(b"content")

    code, _, err = command("verify", "--project", "myapp", str(file), capsys=capsys)

    assert code == 1
    assert err == (f"NOT VERIFIED: {file}: no file.sig, and no signed SHA256SUMS beside it "
                   "lists it\n")


def test_no_trusted_key_is_exit_2_and_says_what_to_do(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    file = signed_file(tmp_path / "file")

    code, _, err = command("verify", "--project", "myapp", str(file), capsys=capsys)

    assert code == 2
    assert ("no trusted key for myapp: get the publisher's key and its fingerprint, then: "
            "fsbpyr key trust myapp KEY.pub SHA256:…") in err


@pytest.mark.parametrize(("arguments", "exit_code"), [
    ((), 2), (("--project", "../x"), 2),
])
def test_a_file_without_build_info_needs_a_project(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], arguments: tuple[str, ...], exit_code: int
) -> None:
    file = signed_file(tmp_path / "file")

    assert command("verify", *arguments, str(file), capsys=capsys)[0] == exit_code


def test_a_missing_file_is_exit_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = command("verify", "--project", "myapp", str(tmp_path / "nothing"), capsys=capsys)

    assert code == 2 and "cannot read" in err


def test_a_directory_is_no_file_to_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert command("verify", "--project", "myapp", str(tmp_path), capsys=capsys)[0] == 2


def test_a_file_name_with_a_terminal_escape_prints_as_repr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    trust()
    file = signed_file(tmp_path / "a\x1b[2Jb")

    code, out, _ = command("verify", "--project", "myapp", str(file), capsys=capsys)

    assert code == 0 and "\x1b" not in out and "\\x1b[2J" in out


# --- fsbpyr's own files: the pins, and nothing else ---------------------------------------------


def test_a_trusted_key_for_fsbpyr_is_never_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Saved by hand: `key trust` refuses it. A signature by it is still not fsbpyr's."""
    evil = fake_pub("evil")
    (signing.trust_store() / "fsbpyr.pub").write_bytes(evil)
    file = tmp_path / "file"
    file.write_bytes(b"content")
    file.with_name("file.sig").write_bytes(signature(b"content", "fsbpyr", evil))

    code, _, err = command("verify", "--project", "FSBPYR", str(file), capsys=capsys)

    assert code == 1 and "does not match" in err


def test_any_pinned_key_passes_for_fsbpyr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    pins = fake_pub("k"), fake_pub("r"), fake_pub("new k")
    use_stand_in(monkeypatch, *pins)
    file = tmp_path / "file"
    file.write_bytes(b"content")

    for pin in pins:
        file.with_name("file.sig").write_bytes(signature(b"content", "fsbpyr", pin))
        code, out, _ = command("verify", "--project", "fsbpyr", str(file), capsys=capsys)

        assert code == 0 and support.fingerprint(pin) in out


def test_without_pins_nothing_is_fsbpyrs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(signing, "pinned_keys", tuple)
    file = tmp_path / "file"
    file.write_bytes(b"content")
    file.with_name("file.sig").write_bytes(signature(b"content", "fsbpyr"))

    code, _, err = command("verify", "--project", "fsbpyr", str(file), capsys=capsys)

    assert code == 2 and "pins no release keys" in err
    assert signing.check_release_file(b"content", bytes(SIGNATURE_SIZE)) is False


# --- through a signed sums file -----------------------------------------------------------------


def _sums(directory: Path, lines: str, *, pub: bytes = MYAPP_PUB, name: str = "SHA256SUMS") -> None:
    content = lines.encode()
    (directory / name).write_bytes(content)
    (directory / f"{name}.sig").write_bytes(signature(content, "myapp", pub))


def _line(data: bytes, name: str) -> str:
    return f"{hashlib.sha256(data).hexdigest()}  {name}\n"


def test_a_file_listed_in_a_signed_sums_file_passes_and_says_as_what(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    trust()
    file = tmp_path / "saved-under-another-name"
    file.write_bytes(b"content")
    _sums(tmp_path, _line(b"content", "myapp-1.2-x86_64"))

    code, out, _ = command("verify", "--project", "myapp", str(file), capsys=capsys)

    assert code == 0
    assert out == (f"VERIFIED  {file}  (project myapp, listed as myapp-1.2-x86_64 in SHA256SUMS, "
                   f"Ed25519 + ML-DSA-65, key {support.fingerprint(MYAPP_PUB)})\n")


def test_a_digest_listed_twice_prints_both_names(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    trust()
    file = tmp_path / "LICENSE"
    file.write_bytes(b"content")
    _sums(tmp_path, _line(b"content", "LICENSE-a") + _line(b"other", "x")
          + _line(b"content", "LICENSE-b"))  # fmt: skip

    _, out, _ = command("verify", "--project", "myapp", str(file), capsys=capsys)

    assert "listed as LICENSE-a, LICENSE-b in SHA256SUMS" in out


def test_a_listed_name_with_a_terminal_escape_prints_as_repr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    trust()
    file = tmp_path / "file"
    file.write_bytes(b"content")
    _sums(tmp_path, _line(b"content", "a\x1b[2Jb"))

    code, out, _ = command("verify", "--project", "myapp", str(file), capsys=capsys)

    assert code == 0 and "\x1b" not in out and "\\x1b[2J" in out


def test_the_glibc_sums_file_counts_too(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    trust()
    file = tmp_path / "stub"
    file.write_bytes(b"content")
    _sums(tmp_path, _line(b"content", "stub"), name="SHA256SUMS-glibc2.28")

    assert command("verify", "--project", "myapp", str(file), capsys=capsys)[0] == 0


@pytest.mark.parametrize("damage", ["sums", "signature", "no signature", "other key", "not listed"])
def test_a_sums_file_that_fails_lists_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], damage: str
) -> None:
    trust()
    file = tmp_path / "file"
    file.write_bytes(b"content")
    _sums(tmp_path, _line(b"content", "file"), pub=fake_pub("another key") if damage == "other key"
          else MYAPP_PUB)  # fmt: skip
    sums, sig = tmp_path / "SHA256SUMS", tmp_path / "SHA256SUMS.sig"
    if damage == "sums":
        sums.write_bytes(sums.read_bytes() + _line(b"other", "x").encode())
    elif damage == "signature":
        sig.write_bytes(bytes(SIGNATURE_SIZE))
    elif damage == "no signature":
        sig.unlink()
    elif damage == "not listed":
        _sums(tmp_path, _line(b"other", "file"))

    code, out, err = command("verify", "--project", "myapp", str(file), capsys=capsys)

    assert (code, out) == (1, "")
    assert "and no signed SHA256SUMS beside it lists it" in err


def test_a_bad_sig_does_not_fall_back_to_a_sums_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    trust()
    file = tmp_path / "file"
    file.write_bytes(b"content")
    _sums(tmp_path, _line(b"content", "file"))
    file.with_name("file.sig").write_bytes(bytes(SIGNATURE_SIZE))

    assert command("verify", "--project", "myapp", str(file), capsys=capsys)[0] == 1


def test_a_sums_file_is_read_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """What is checked is what is looked up in: a sums file swapped after its check adds nothing."""
    trust()
    file = tmp_path / "file"
    file.write_bytes(b"evil")
    _sums(tmp_path, _line(b"content", "file"))
    original = signing._read_limited
    swapped: list[str] = []

    def read_then_swap(path: Path, limit: int) -> bytes | None:
        data = original(path, limit)
        if path.name == "SHA256SUMS.sig":
            (tmp_path / "SHA256SUMS").write_text(_line(b"evil", "file"))
            swapped.append(path.name)
        return data

    monkeypatch.setattr(signing, "_read_limited", read_then_swap)

    with pytest.raises(signing.NotVerified):
        signing.verify(file, "myapp")
    assert swapped


# --- links --------------------------------------------------------------------------------------


def test_a_link_is_checked_as_the_file_it_leads_to(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    trust()
    (tmp_path / "opt").mkdir()
    (tmp_path / "bin").mkdir()
    file = signed_file(tmp_path / "opt" / "myapp-1.2")
    link = tmp_path / "bin" / "myapp"
    link.symlink_to("../opt/myapp-1.2")

    code, out, _ = command("verify", "--project", "myapp", str(link), capsys=capsys)

    assert code == 0 and f"VERIFIED  {file}  " in out


def test_a_sig_beside_a_link_does_not_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    trust()
    (tmp_path / "opt").mkdir()
    (tmp_path / "opt" / "app").write_bytes(b"content")
    link = tmp_path / "app"
    link.symlink_to("opt/app")
    (tmp_path / "app.sig").write_bytes(signature(b"content"))

    assert command("verify", "--project", "myapp", str(link), capsys=capsys)[0] == 1


# --- the trust store ----------------------------------------------------------------------------


@pytest.fixture
def pub_file(tmp_path: Path) -> Path:
    path = tmp_path / "myapp.pub"
    path.write_bytes(MYAPP_PUB)
    return path


FINGERPRINT = support.fingerprint(MYAPP_PUB)


@pytest.mark.parametrize("fingerprint", [
    "SHA256:" + "0" * 64, FINGERPRINT[:23], FINGERPRINT + "0", FINGERPRINT.removeprefix("SHA256:"),
    "sha256:" + FINGERPRINT[7:], FINGERPRINT + "\n", " " + FINGERPRINT, "SHA1:" + FINGERPRINT[7:],
])
def test_a_wrong_fingerprint_saves_nothing(
    pub_file: Path, capsys: pytest.CaptureFixture[str], fingerprint: str
) -> None:
    code, _, _ = command("key", "trust", "myapp", str(pub_file), fingerprint, capsys=capsys)

    assert code == 2
    assert list(signing.trust_store().iterdir()) == []


def test_a_shortened_fingerprint_says_what_a_fingerprint_is(
    pub_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, _, err = command("key", "trust", "myapp", str(pub_file), FINGERPRINT[:23], capsys=capsys)

    assert "not a fingerprint: give SHA256: and all 64 hex digits" in err


@pytest.mark.parametrize("project", [
    "fsbpyr", "FSBPYR", "Fsbpyr", "../x", "/tmp/x", "a/b", "a\nb", "x" * 65,  # noqa: S108
    "\uff4d\uff59\uff41\uff50\uff50"])
def test_a_project_that_cannot_be_trusted_saves_nothing(
    pub_file: Path, capsys: pytest.CaptureFixture[str], project: str
) -> None:
    assert command("key", "trust", project, str(pub_file), FINGERPRINT, capsys=capsys)[0] == 2
    assert list(signing.trust_store().iterdir()) == []


@pytest.mark.parametrize("content", [
    MYAPP_PUB[:-1], MYAPP_PUB + b"\n", b"", b"x" * PUB_SIZE, fake_pub("x")[::-1],
], ids=["a byte short", "a byte more", "empty", "junk", "not a key"])
def test_a_pubkey_that_is_no_pub_saves_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], content: bytes
) -> None:
    pub = tmp_path / "bad.pub"
    pub.write_bytes(content)
    print_key = support.fingerprint(content)

    assert command("key", "trust", "myapp", str(pub), print_key, capsys=capsys)[0] == 2
    assert list(signing.trust_store().iterdir()) == []


def test_an_upper_case_fingerprint_counts_and_the_name_is_normalized(
    pub_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    upper = "SHA256:" + FINGERPRINT[7:].upper()

    code, out, _ = command("key", "trust", "My_App", str(pub_file), upper, capsys=capsys)

    assert code == 0 and FINGERPRINT in out
    saved = signing.trust_store() / "my-app.pub"
    assert hashlib.sha256(saved.read_bytes()).hexdigest() == FINGERPRINT[7:]
    assert not list(signing.trust_store().glob(".trust-*"))


def test_the_same_key_again_changes_nothing(
    pub_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert command("key", "trust", "myapp", str(pub_file), FINGERPRINT, capsys=capsys)[0] == 0
    saved = signing.trust_store() / "myapp.pub"
    before = saved.stat().st_mtime_ns

    assert command("key", "trust", "myapp", str(pub_file), FINGERPRINT, capsys=capsys)[0] == 0
    assert command("key", "trust", "myapp", str(pub_file), FINGERPRINT, "--replace",
                   capsys=capsys)[0] == 0  # fmt: skip
    assert saved.stat().st_mtime_ns == before


def test_another_key_needs_replace(
    pub_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    command("key", "trust", "myapp", str(pub_file), FINGERPRINT, capsys=capsys)
    other = fake_pub("the new key")
    new = tmp_path / "new.pub"
    new.write_bytes(other)
    saved = signing.trust_store() / "myapp.pub"

    code, _, err = command("key", "trust", "myapp", str(new), support.fingerprint(other),
                           capsys=capsys)  # fmt: skip

    assert code == 2 and saved.read_bytes() == MYAPP_PUB
    assert f"myapp has another trusted key, {FINGERPRINT}; give --replace" in err

    code, out, _ = command("key", "trust", "myapp", str(new), support.fingerprint(other),
                           "--replace", capsys=capsys)  # fmt: skip

    assert code == 0 and saved.read_bytes() == other
    assert FINGERPRINT in out and support.fingerprint(other) in out


def test_replace_with_no_key_saved_saves_it(
    pub_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, _ = command("key", "trust", "myapp", str(pub_file), FINGERPRINT, "--replace",
                         capsys=capsys)  # fmt: skip

    assert code == 0 and (signing.trust_store() / "myapp.pub").read_bytes() == MYAPP_PUB


def test_the_key_saved_is_the_key_checked(
    pub_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read once: a file swapped after its first read is never what is saved."""
    original = signing._read_limited

    def read_then_swap(path: Path, limit: int) -> bytes | None:
        data = original(path, limit)
        if path == pub_file:
            path.write_bytes(fake_pub("swapped in"))
        return data

    monkeypatch.setattr(signing, "_read_limited", read_then_swap)

    signing.key_trust("myapp", pub_file, FINGERPRINT)

    assert (signing.trust_store() / "myapp.pub").read_bytes() == MYAPP_PUB


@pytest.mark.parametrize("variable", ["XDG_CONFIG_HOME", "HOME"])
def test_a_relative_path_brings_no_trusted_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variable: str,
    capsys: pytest.CaptureFixture[str]
) -> None:
    """A download unpacked in the current directory must not bring its own trusted keys."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", "cfg" if variable == "XDG_CONFIG_HOME" else "")
    monkeypatch.setenv("HOME", str(tmp_path / "home") if variable != "HOME" else "cfg")
    for root in ("cfg/fsbpyr/trusted", "cfg/.config/fsbpyr/trusted"):
        (tmp_path / root).mkdir(parents=True)
        (tmp_path / root / "myapp.pub").write_bytes(MYAPP_PUB)
    file = signed_file(tmp_path / "file")

    code, _, _ = command("verify", "--project", "myapp", str(file), capsys=capsys)

    assert code == 2  # no trusted key: the planted ones are not looked at


def test_a_trust_store_someone_else_can_write_is_not_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    trust()
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o777)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(shared))
    file = signed_file(tmp_path / "file")

    code, _, err = command("verify", "--project", "myapp", str(file), capsys=capsys)

    assert code == 2 and "the trust store cannot be used" in err


# --- signing ------------------------------------------------------------------------------------


@pytest.fixture
def key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "k.key"
    path.write_bytes(b"key:" + PASSPHRASE)
    monkeypatch.setenv(signing.PASSPHRASE_VARIABLE, PASSPHRASE.decode())
    return path


def test_sign_writes_a_sig_that_verifies(
    tmp_path: Path, key: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    file = tmp_path / "file"
    file.write_bytes(b"content")

    code, out, _ = command("sign", "--project", "myapp", "--signing-key", str(key), str(file),
                           capsys=capsys)  # fmt: skip

    assert code == 0 and "as myapp" in out
    sig = tmp_path / "file.sig"
    assert len(sig.read_bytes()) == SIGNATURE_SIZE
    assert stat.S_IMODE(sig.stat().st_mode) == 0o644
    trust(pub=support.StandIn._key_new(PASSPHRASE)[1])
    assert signing.verify(file, "myapp").project == "myapp"


def test_sign_takes_the_key_from_the_variable(
    tmp_path: Path, key: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(signing.KEY_VARIABLE, str(key))
    file = tmp_path / "file"
    file.write_bytes(b"content")

    assert command("sign", "--project", "myapp", str(file), capsys=capsys)[0] == 0


def test_sign_needs_a_key(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    file = tmp_path / "file"
    file.write_bytes(b"content")

    code, _, err = command("sign", "--project", "myapp", str(file), capsys=capsys)

    assert code == 2 and "no signing key" in err


@pytest.mark.parametrize("project", ["fsbpyr", "FSBPYR", "../x", "x" * 65])
def test_sign_refuses_these_projects(
    tmp_path: Path, key: Path, capsys: pytest.CaptureFixture[str], project: str
) -> None:
    file = tmp_path / "file"
    file.write_bytes(b"content")

    code, _, _ = command("sign", "--project", project, "--signing-key", str(key), str(file),
                         capsys=capsys)  # fmt: skip

    assert code == 2 and not (tmp_path / "file.sig").exists()


def test_sign_refuses_an_app_of_project_fsbpyr(
    tmp_path: Path, key: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    file = executable(tmp_path / "app", "fsbpyr")

    assert command("sign", "--signing-key", str(key), str(file), capsys=capsys)[0] == 2


def test_sign_refuses_a_project_other_than_the_build_infos(
    tmp_path: Path, key: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    file = executable(tmp_path / "app", "otherapp")

    code, _, err = command("sign", "--project", "myapp", "--signing-key", str(key), str(file),
                           capsys=capsys)  # fmt: skip

    assert code == 2 and "project otherapp, not of myapp" in err


def test_sign_takes_the_project_from_the_build_info(
    tmp_path: Path, key: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    file = executable(tmp_path / "app", "myapp")

    code, out, _ = command("sign", "--signing-key", str(key), str(file), capsys=capsys)

    assert code == 0 and "as myapp" in out


def test_a_file_without_build_info_needs_a_project_to_sign(
    tmp_path: Path, key: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    file = tmp_path / "file"
    file.write_bytes(b"content")

    code, _, err = command("sign", "--signing-key", str(key), str(file), capsys=capsys)

    assert code == 2 and "has no build info: name its project with --project" in err


@pytest.mark.parametrize("what", ["file", "dangling link", "link to a file", "link to a directory",
                                  "directory"])
def test_sign_replaces_nothing_and_follows_no_link(
    tmp_path: Path, key: Path, capsys: pytest.CaptureFixture[str], what: str
) -> None:
    file = tmp_path / "file"
    file.write_bytes(b"content")
    target, sig = tmp_path / "target", tmp_path / "file.sig"
    if what == "file":
        sig.write_bytes(b"old")
    elif what == "directory":
        sig.mkdir()
    elif what == "link to a file":
        target.write_bytes(b"precious")
        sig.symlink_to(target)
    elif what == "link to a directory":
        target.mkdir()
        sig.symlink_to(target)
    else:
        sig.symlink_to(target)

    code, _, err = command("sign", "--project", "myapp", "--signing-key", str(key), str(file),
                           capsys=capsys)  # fmt: skip

    assert code == 2 and "exists; nothing replaced" in err
    if what == "file":
        assert sig.read_bytes() == b"old"
    if what == "link to a file":
        assert target.read_bytes() == b"precious"
    if what == "link to a directory":
        assert list(target.iterdir()) == []
    if what == "dangling link":
        assert not target.exists()


def test_a_sig_appearing_while_signing_is_not_replaced(
    tmp_path: Path, key: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    file = tmp_path / "file"
    file.write_bytes(b"content")
    sig = tmp_path / "file.sig"
    original = support.StandIn._sign

    def and_a_sig_appears(*args: bytes) -> tuple[bytes, bytes]:
        sig.write_bytes(b"theirs")
        return original(*args)

    monkeypatch.setattr(support.StandIn, "_sign", staticmethod(and_a_sig_appears))

    with pytest.raises(signing.SigningError, match="exists; nothing replaced"):
        signing.sign(file, project="myapp", key=key)
    assert sig.read_bytes() == b"theirs"
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith(".")) == []


def test_a_wrong_passphrase_writes_nothing(
    tmp_path: Path, key: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(signing.PASSPHRASE_VARIABLE, "wrong")
    file = tmp_path / "file"
    file.write_bytes(b"content")

    code, _, err = command("sign", "--project", "myapp", "--signing-key", str(key), str(file),
                           capsys=capsys)  # fmt: skip

    assert code == 2 and f"cannot open {key}: wrong passphrase?" in err
    assert not (tmp_path / "file.sig").exists()


def test_a_missing_key_says_so(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    file = tmp_path / "file"
    file.write_bytes(b"content")

    code, _, err = command("sign", "--project", "myapp", "--signing-key", str(tmp_path / "none"),
                           str(file), capsys=capsys)  # fmt: skip

    assert code == 2 and "cannot open" in err


# --- new keys -----------------------------------------------------------------------------------


def test_key_new_writes_both_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(signing.PASSPHRASE_VARIABLE, PASSPHRASE.decode())
    name = tmp_path / "myapp"

    code, out, _ = command("key", "new", str(name), capsys=capsys)

    assert code == 0
    pub = tmp_path / "myapp.pub"
    assert stat.S_IMODE((tmp_path / "myapp.key").stat().st_mode) == 0o600
    assert stat.S_IMODE(pub.stat().st_mode) == 0o644
    assert f"fingerprint {support.fingerprint(pub.read_bytes())}" in out
    assert sorted(p.name for p in tmp_path.iterdir()) == ["myapp.key", "myapp.pub"]


@pytest.mark.parametrize("taken", ["myapp.key", "myapp.pub"])
def test_key_new_replaces_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, taken: str
) -> None:
    monkeypatch.setenv(signing.PASSPHRASE_VARIABLE, PASSPHRASE.decode())
    (tmp_path / taken).symlink_to(tmp_path / "dangling")

    code, _, err = command("key", "new", str(tmp_path / "myapp"), capsys=capsys)

    assert code == 2 and "exists; nothing replaced" in err
    assert sorted(p.name for p in tmp_path.iterdir()) == [taken]


def test_a_pub_appearing_while_keys_are_made_leaves_no_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(signing.PASSPHRASE_VARIABLE, PASSPHRASE.decode())
    original = support.StandIn._key_new

    def and_a_pub_appears(passphrase: bytes) -> tuple[bytes, bytes]:
        (tmp_path / "myapp.pub").write_bytes(b"theirs")
        return original(passphrase)

    monkeypatch.setattr(support.StandIn, "_key_new", staticmethod(and_a_pub_appears))

    with pytest.raises(signing.SigningError, match="exists; nothing replaced"):
        signing.key_new(tmp_path / "myapp")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["myapp.pub"]


@pytest.mark.parametrize(("value", "message"), [
    (b"x" * 1025, "longer than 1024 bytes"),
])
def test_a_passphrase_too_long_is_refused_before_keys_are_made(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: bytes, message: str
) -> None:
    monkeypatch.setenv(signing.PASSPHRASE_VARIABLE, value.decode())

    with pytest.raises(signing.SigningError, match=message):
        signing.key_new(tmp_path / "myapp")


def test_a_passphrase_is_the_variables_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    value = b"\xff\xfe not UTF-8"
    monkeypatch.setitem(os.environb, signing.PASSPHRASE_VARIABLE.encode(), value)

    assert signing.passphrase() == value


def test_an_empty_variable_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(signing.PASSPHRASE_VARIABLE, "")
    monkeypatch.setattr(signing, "_tty_line", lambda prompt: b"typed")

    assert signing.passphrase() == b"typed"


def test_no_terminal_and_no_variable_is_a_setup_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_terminal(*_: object) -> int:
        raise OSError(6, "No such device or address")

    real_open = os.open
    monkeypatch.setattr(os, "open", lambda path, *rest: no_terminal() if path == "/dev/tty"
                        else real_open(path, *rest))  # fmt: skip

    with pytest.raises(signing.SigningError, match="no passphrase"):
        signing.passphrase()


# --- from source --------------------------------------------------------------------------------


@pytest.mark.parametrize("arguments", [
    ("key", "new", "x"), ("key", "trust", "myapp", "x.pub", FINGERPRINT),
    ("sign", "--project", "myapp", "--signing-key", "k", "x"),
    ("verify", "--project", "myapp", "x"),
])
def test_from_source_the_commands_say_they_need_a_built_fsbpyr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    arguments: tuple[str, ...]
) -> None:
    monkeypatch.undo()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv(signing.PASSPHRASE_VARIABLE, "x")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "x").write_bytes(b"content")
    (tmp_path / "k").write_bytes(b"key")

    code, _, err = command(*arguments, capsys=capsys)

    assert code == 2 and "signing needs a built fsbpyr" in err


def test_this_checkout_pins_no_test_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real seam, in the tree as it is: pins are .pub files of 2839 bytes, or none at all."""
    monkeypatch.undo()

    assert all(len(pub) == PUB_SIZE for pub in signing.pinned_keys())


def test_nothing_in_the_environment_changes_the_pins(monkeypatch: pytest.MonkeyPatch) -> None:
    """No variable or option names them: the code reads only the packaged directory."""
    monkeypatch.undo()
    before = signing.pinned_keys()
    for name in ("FSBPYR_RELEASE_KEYS", "FSBPYR_PINS", "FSBPYR_PINNED_KEYS", "FSBPYR_TEST_PINS"):
        monkeypatch.setenv(name, "/nonexistent")

    assert signing.pinned_keys() == before


# --- the build's warning ------------------------------------------------------------------------


def test_the_warning_is_boxed_and_ends_with_how_to_silence_it() -> None:
    lines = signing.warning_lines(Path("dist/myapp"), "cannot open /k.key: wrong passphrase?")

    assert lines[0] == lines[-1] == "*" * 64
    text = " ".join(line.strip() for line in lines[1:-1])
    assert text == ("dist/myapp is NOT SIGNED: cannot open /k.key: wrong passphrase? Unset "
                    "FSBPYR_SIGNING_KEY to build unsigned without this warning.")


def test_a_build_takes_both_variables_out_of_its_environment(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(signing.KEY_VARIABLE, "/keys/k.key")
    monkeypatch.setenv(signing.PASSPHRASE_VARIABLE, "secret")

    taken = signing.take_build_variables(None)

    assert signing.KEY_VARIABLE not in os.environ
    assert signing.PASSPHRASE_VARIABLE not in os.environ
    assert b"FSBPYR_SIGNING_PASSPHRASE" not in os.environb
    assert taken.wanted
    assert taken.environment() == {signing.KEY_VARIABLE: "/keys/k.key",
                                   signing.PASSPHRASE_VARIABLE: "secret"}  # fmt: skip


def test_a_build_without_a_key_wants_no_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    assert not signing.take_build_variables(None).wanted
    assert signing.take_build_variables(Path("k.key")).wanted
    assert json.dumps(signing.take_build_variables(None).environment()) == "{}"


# --- a name in a sums file cannot add a line ------------------------------------------------------

EVIL = hashlib.sha256(b"evil").hexdigest()
SEPARATORS = ("\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029")


@pytest.mark.parametrize("separator", SEPARATORS, ids=repr)
def test_a_file_name_cannot_add_a_line_to_a_sums_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], separator: str
) -> None:
    """A publisher runs `sha256sum *` in a directory where someone else chose one name, and signs
    the result. GNU's sha256sum does not escape these characters, and str.splitlines cuts at them,
    so this name would add a line with the digest of an unsigned file."""
    trust()
    file = tmp_path / "evil"
    file.write_bytes(b"evil")
    _sums(tmp_path, f"{hashlib.sha256(b'x').hexdigest()}  a{separator}{EVIL}  note\n")

    code, out, _ = command("verify", "--project", "myapp", str(file), capsys=capsys)

    assert (code, out) == (1, "")


def test_a_file_name_cannot_add_a_line_through_the_real_sha256sum(tmp_path: Path) -> None:
    (tmp_path / "myapp").write_bytes(b"genuine")
    (tmp_path / f"a\u2028{EVIL}  note").write_bytes(b"anything")
    listed = subprocess.run(["sha256sum", *sorted(p.name for p in tmp_path.iterdir())],  # noqa: S607
                            cwd=tmp_path, capture_output=True, text=True, check=True).stdout

    names = [name for _, name in signing.sums_lines(listed)]

    assert signing.listed_names(listed, EVIL) == ()
    assert len(names) == 2


@pytest.mark.parametrize("separator", SEPARATORS, ids=repr)
def test_a_name_cannot_put_a_digest_in_front_of_an_asset(separator: str) -> None:
    """In fsbpyr's own paths the first line of an asset's name wins."""
    real = "a" * 64
    sums = f"{'b' * 64}  a{separator}{EVIL}  asset.xz\n{real}  asset.xz\n"

    assert signing.asset_digest(sums.encode(), "asset.xz") == real


@pytest.mark.parametrize(("line", "listed"), [
    (f"{EVIL}  name", "name"),
    (f"{EVIL} *name", "name"),  # the binary marker
    (f"{EVIL.upper()}  name", "name"),
    (f"{EVIL}  *name", "*name"),  # a file of that name: only one marker counts
    (f"{EVIL}   name", " name"),
    (f"{EVIL} **name", "*name"),
])
def test_a_line_is_a_digest_a_marker_and_a_name(line: str, listed: str) -> None:
    assert list(signing.sums_lines(f"{line}\n")) == [(EVIL, listed)]


@pytest.mark.parametrize("line", [
    "", "name", f"{EVIL} name", f"{EVIL}\tname", f"{EVIL}  ", f"\\{EVIL}  a\\nb",
    f"g{EVIL[1:]}  name", f"{EVIL}0  name", f"{EVIL[:-1]}  name",
])
def test_anything_else_is_no_line(line: str) -> None:
    assert list(signing.sums_lines(f"{line}\n")) == []


def test_a_crlf_line_lists_no_such_name() -> None:
    assert signing.asset_digest(f"{EVIL}  name\r\n".encode(), "name") is None


def test_a_name_that_names_no_file_is_a_usage_error(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(signing.PASSPHRASE_VARIABLE, "x")

    assert command("key", "new", "/", capsys=capsys)[0] == 2


def test_the_passphrase_is_not_in_a_repr() -> None:
    assert "hunter2" not in repr(signing.BuildSigning(None, b"/k.key", b"hunter2"))
