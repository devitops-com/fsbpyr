"""tools/release/release-signing.sh, previous-release.sh and check-signature.sh, which sign a
release in CI and check it as the clients will (docs/signing.md §5.3, §11.4). The script runs
with fake programs first on PATH where a failure has to be made, for any uid: a mode-555 directory
would not stop root. Passphrases are typed on a pty.

What fsbpyr reads from these files is in tests/e2e/test_signing.py."""

import base64
import json
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from ..support import requires
from ..support.signing import (
    KEY_SIZE,
    PASSPHRASE,
    PUB_SIZE,
    SCRIPT,
    SIGNATURE_SIZE,
    Key,
    new_key,
    on_a_terminal,
    run_openssl,
    script,
    sign,
    sign_statement,
    statement,
)

ROOT = Path(__file__).parents[2]
PREVIOUS = ROOT / "tools" / "release" / "previous-release.sh"
CHECK = ROOT / "tools" / "release" / "check-signature.sh"
BEGIN = "-----BEGIN ENCRYPTED PRIVATE KEY-----\n"
END = "-----END ENCRYPTED PRIVATE KEY-----\n"
FIRST_SIGNED = "0.29.0"


@pytest.fixture(scope="module")
def k(tmp_path_factory: pytest.TempPathFactory) -> Key:
    return new_key(tmp_path_factory.mktemp("k") / "k")


@pytest.fixture(scope="module")
def r(tmp_path_factory: pytest.TempPathFactory) -> Key:
    return new_key(tmp_path_factory.mktemp("r") / "r")


@pytest.fixture
def file(tmp_path: Path) -> Path:
    path = tmp_path / "SUMS"
    path.write_text("a file\n")
    return path


def fake(directory: Path, name: str, body: str) -> Path:
    directory.mkdir(exist_ok=True)
    path = directory / name
    path.write_text(f"#!/bin/sh\n{body}")
    path.chmod(0o755)
    return path


def with_path(directory: Path) -> dict[str, str]:
    return {"PATH": f"{directory}:{os.environ['PATH']}"}


def blocks(key: Path) -> tuple[str, str]:
    first, second = key.read_text().split(END)[:2]
    return first + END, second + END


def plain_block(algorithm: str) -> str:
    return run_openssl("genpkey", "-algorithm", algorithm).decode()


def mislabeled(algorithm: str) -> str:
    """A key that is not encrypted, under the label of one."""
    text = base64.b64encode(run_openssl("genpkey", "-algorithm", algorithm, "-outform", "DER"))
    lines = [text[at:at + 64].decode() for at in range(0, len(text), 64)]
    return BEGIN + "\n".join(lines) + "\n" + END


def encrypted(algorithm: str, passphrase: str = PASSPHRASE) -> str:
    plain = run_openssl("genpkey", "-algorithm", algorithm)
    return run_openssl("pkcs8", "-topk8", "-v2", "aes-256-cbc", "-v2prf", "hmacWithSHA256", "-iter",
                       "1000", "-passout", f"pass:{passphrase}", stdin=plain).decode()  # fmt: skip


# --- keys ---------------------------------------------------------------------------------------


def test_new_key_writes_both_files(tmp_path: Path) -> None:
    key, pub = new_key(tmp_path / "k")

    assert (key.stat().st_size, pub.stat().st_size) == (KEY_SIZE, PUB_SIZE)
    assert (key.stat().st_mode & 0o777, pub.stat().st_mode & 0o777) == (0o600, 0o644)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["k.key", "k.pub"]


def test_new_key_replaces_nothing(tmp_path: Path) -> None:
    key, pub = new_key(tmp_path / "k")
    before = key.read_bytes(), pub.read_bytes()

    done = script("new-key", tmp_path / "k")

    assert done.returncode == 2 and "exists; nothing replaced" in done.stderr
    assert (key.read_bytes(), pub.read_bytes()) == before


@pytest.mark.parametrize("name", ["k.key", "k.pub"])
def test_a_symlink_at_a_new_keys_path_is_left_alone(tmp_path: Path, name: str) -> None:
    (tmp_path / name).symlink_to(tmp_path / "dangling")

    assert script("new-key", tmp_path / "k").returncode == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == [name]


def test_a_config_cannot_shrink_a_new_key(tmp_path: Path) -> None:
    """`ml-dsa.output_formats` in openssl.cnf made a 604-byte .key that C refused."""
    config = tmp_path / "openssl.cnf"
    config.write_text("openssl_conf = init\n[init]\nproviders = list\n[list]\ndefault = on\n"
                      "[on]\nactivate = 1\nml-dsa.output_formats = seed-only\n")

    done = script("new-key", tmp_path / "k", env={"OPENSSL_CONF": str(config)})

    assert done.returncode == 0, done.stderr
    assert (tmp_path / "k.key").stat().st_size == KEY_SIZE


def test_a_pub_appearing_while_a_new_key_is_placed_leaves_no_key(tmp_path: Path) -> None:
    """The window between the check and the link, held open by a `chmod` on PATH."""
    real = shutil.which("chmod")
    fake(tmp_path / "bin", "chmod", f'''case "$1" in 644) : > "{tmp_path}/k.pub" ;; esac
exec {real} "$@"
''')

    done = script("new-key", tmp_path / "k", env=with_path(tmp_path / "bin"))

    assert done.returncode == 2
    assert not (tmp_path / "k.key").exists()
    assert (tmp_path / "k.pub").read_bytes() == b""


def test_a_directory_appearing_at_the_sig_gets_nothing_inside(
    tmp_path: Path, file: Path, k: Key
) -> None:
    real = shutil.which("chmod")
    fake(tmp_path / "bin", "chmod", f'mkdir -p "{file}.sig"\nexec {real} "$@"\n')

    done = script("sign", k.key, file, env=with_path(tmp_path / "bin"))

    assert done.returncode == 2
    assert list(Path(f"{file}.sig").iterdir()) == []


@pytest.mark.parametrize("ending", ["", "\n", "\n\n"], ids=["no last LF", "one more", "two more"])
def test_a_key_copied_through_a_secret_may_lose_or_gain_lfs(
    tmp_path: Path, file: Path, k: Key, ending: str
) -> None:
    first, second = blocks(k.key)
    copied = tmp_path / "copied.key"
    copied.write_text(first + second[:-1] + ending)

    done = script("sign", copied, file)

    assert done.returncode == 0, done.stderr
    assert script("verify", file, k.pub).returncode == 0


BAD_KEYS: dict[str, Callable[[str, str], str]] = {
    "both unencrypted": lambda _, __: plain_block("ED25519") + plain_block("ML-DSA-65"),
    "plain keys under the label": lambda _, __: mislabeled("ED25519") + mislabeled("ML-DSA-65"),
    "a plain second key under the label": lambda ed, _: ed + mislabeled("ML-DSA-65"),
    "the second unencrypted": lambda ed, _: ed + plain_block("ML-DSA-65"),
    "three encrypted": lambda ed, ml: ed + ml + ed,
    "a third block unencrypted": lambda ed, ml: ed + ml + plain_block("ML-DSA-65"),
    "a plain key between them": lambda ed, ml: ed + plain_block("ML-DSA-65") + ml,
    "text before": lambda ed, ml: "hello\n" + ed + ml,
    "text between": lambda ed, ml: ed + "hello\n" + ml,
    "text after": lambda ed, ml: ed + ml + "hello\n",
    "an empty line between": lambda ed, ml: ed + "\n" + ml,
    "CRLF": lambda ed, ml: (ed + ml).replace("\n", "\r\n"),
    "a CR at the end": lambda ed, ml: ed + ml + "\r",
    "a space in the base64": lambda ed, ml: ed[:60] + " " + ed[60:] + ml,
    "a byte in place of the LF between": lambda ed, ml: ed[:-1] + "x" + ml,
    "a byte in place of the last LF": lambda ed, ml: ed + ml[:-1] + "x",
    "one block": lambda ed, _: ed,
    "nothing": lambda _, __: "",
}


@pytest.mark.parametrize("name", BAD_KEYS)
def test_a_key_file_of_any_other_form_writes_nothing(
    tmp_path: Path, file: Path, k: Key, name: str
) -> None:
    """Before these were refused a plain ML-DSA key between the two blocks was the key it signed
    with, and it exited 0."""
    bad = tmp_path / "bad.key"
    bad.write_text(BAD_KEYS[name](*blocks(k.key)))

    done = script("sign", bad, file)

    assert done.returncode == 2, done.stdout
    assert not Path(f"{file}.sig").exists()
    assert "two encrypted keys and nothing else" in done.stderr


def test_keys_of_other_kinds_write_nothing(tmp_path: Path, file: Path, k: Key) -> None:
    ed25519, ml_dsa = blocks(k.key)
    for text in (ml_dsa + ed25519, ed25519 + ed25519, encrypted("ED448") + ml_dsa,
                 ed25519 + encrypted("ML-DSA-87"),
                 ed25519 + encrypted("ML-DSA-65", "another passphrase")):  # fmt: skip
        bad = tmp_path / "bad.key"
        bad.write_text(text)

        assert script("sign", bad, file).returncode == 2
        assert not Path(f"{file}.sig").exists()


def test_a_key_file_of_junk_is_exit_2(tmp_path: Path, file: Path) -> None:
    bad = tmp_path / "junk.key"
    bad.write_text(f"{BEGIN}AAAA\n{END}{BEGIN}AAAA\n{END}")

    assert script("sign", bad, file).returncode == 2


# --- signing ------------------------------------------------------------------------------------


def test_the_signature_is_both_halves_over_the_statement(
    file: Path, k: Key, tmp_path: Path
) -> None:
    signature = sign(k.key, file).read_bytes()

    assert len(signature) == SIGNATURE_SIZE
    done = script("verify", file, k.pub)
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith(f"VERIFIED  {file}  (project fsbpyr, Ed25519 + ML-DSA-65, key ")
    message = statement("fsbpyr", file.read_bytes())
    assert signature[:64] == sign_statement(k.key, message, tmp_path)[:64]  # the statement's bytes


def test_sign_replaces_nothing(file: Path, k: Key) -> None:
    sign(k.key, file)
    before = Path(f"{file}.sig").read_bytes()

    assert script("sign", k.key, file).returncode == 2
    assert Path(f"{file}.sig").read_bytes() == before


@pytest.mark.parametrize("target", ["dangling", "a file", "a directory"])
def test_a_symlink_at_the_sig_is_left_alone(
    tmp_path: Path, file: Path, k: Key, target: str
) -> None:
    pointed = tmp_path / "pointed"
    if target == "a file":
        pointed.write_bytes(b"precious")
    elif target == "a directory":
        pointed.mkdir()
    Path(f"{file}.sig").symlink_to(pointed)

    assert script("sign", k.key, file).returncode == 2
    assert Path(f"{file}.sig").is_symlink()
    if target == "a file":
        assert pointed.read_bytes() == b"precious"
    if target == "a directory":
        assert list(pointed.iterdir()) == []


def test_a_signature_by_another_key_passes_sign_and_fails_verify(
    file: Path, k: Key, r: Key
) -> None:
    """Why the sign job checks against the pins: `sign` checks only against the key it signed
    with, and a secret may hold another one."""
    sign(r.key, file)

    assert script("verify", file, k.pub).returncode == 1
    assert script("verify", file, k.pub, r.pub).returncode == 0


def test_any_given_key_passes(file: Path, k: Key, r: Key) -> None:
    sign(k.key, file)

    assert script("verify", file, r.pub, k.pub).returncode == 0
    assert script("verify", file, r.pub).returncode == 1


def test_a_changed_file_is_not_verified(file: Path, k: Key) -> None:
    sign(k.key, file)
    file.write_text("another file\n")

    assert script("verify", file, k.pub).returncode == 1


@pytest.mark.parametrize("position", [10, 2000])
def test_a_flipped_byte_is_not_verified(file: Path, k: Key, position: int) -> None:
    signature = sign(k.key, file)
    data = bytearray(signature.read_bytes())
    data[position] ^= 1
    signature.write_bytes(data)

    assert script("verify", file, k.pub).returncode == 1


@pytest.mark.parametrize("size", [0, 64, 3372, 3374, 6746])
def test_a_sig_of_another_size_is_not_verified(file: Path, k: Key, size: int) -> None:
    Path(f"{file}.sig").write_bytes(bytes(size))

    done = script("verify", file, k.pub)

    assert done.returncode == 1 and f"has {size} bytes" in done.stderr


def test_a_missing_sig_is_not_verified(file: Path, k: Key) -> None:
    assert script("verify", file, k.pub).returncode == 1


def bad_pubs(pub: Path) -> dict[str, bytes]:
    text = pub.read_bytes()
    ed25519, ml_dsa = text.split(b"-----END PUBLIC KEY-----\n")[:2]
    end = b"-----END PUBLIC KEY-----\n"
    return {"an extra line": text + b"extra\n", "CRLF": text.replace(b"\n", b"\r\n"),
            "swapped": ml_dsa + end + ed25519 + end, "one key": ed25519 + end,
            "three keys": text + ed25519 + end, "Ed25519 twice": (ed25519 + end) * 2}  # fmt: skip


@pytest.mark.parametrize("name", ["an extra line", "CRLF", "swapped", "one key", "three keys",
                                  "Ed25519 twice"])  # fmt: skip
def test_a_bad_pub_is_no_key_and_a_good_one_after_it_is_still_tried(
    tmp_path: Path, file: Path, k: Key, r: Key, name: str
) -> None:
    sign(k.key, file)
    bad = tmp_path / "bad.pub"
    bad.write_bytes(bad_pubs(k.pub)[name])

    assert script("verify", file, bad).returncode == 2
    assert script("verify", file, bad, k.pub).returncode == 0
    assert script("verify", file, bad, r.pub).returncode == 1  # NOT VERIFIED, not a setup error


def test_a_pub_that_is_not_there_is_no_key(tmp_path: Path, file: Path, k: Key) -> None:
    sign(k.key, file)

    assert script("verify", file, tmp_path / "none.pub", k.pub).returncode == 0


# --- passphrases --------------------------------------------------------------------------------


def test_a_wrong_passphrase_writes_nothing(file: Path, k: Key) -> None:
    done = script("sign", k.key, file, passphrase="wrong")  # noqa: S106

    assert done.returncode == 2 and "wrong passphrase?" in done.stderr
    assert not Path(f"{file}.sig").exists()


@pytest.mark.parametrize("passphrase", [None, ""], ids=["unset", "empty"])
def test_no_passphrase_without_a_terminal_is_exit_2(
    file: Path, k: Key, passphrase: str | None
) -> None:
    done = script("sign", k.key, file, passphrase=passphrase)

    assert done.returncode == 2 and "no passphrase" in done.stderr
    assert not Path(f"{file}.sig").exists()


def test_a_passphrase_over_1024_bytes_says_so(tmp_path: Path) -> None:
    done = script("new-key", tmp_path / "k", passphrase="x" * 1025)

    assert done.returncode == 2
    assert "longer than 1024 bytes" in done.stderr
    assert sorted(tmp_path.iterdir()) == []


def test_a_passphrase_of_1024_bytes_works(tmp_path: Path, file: Path) -> None:
    passphrase = "x" * 1024
    key, pub = new_key(tmp_path / "k", passphrase)

    sign(key, file, passphrase)

    assert script("verify", file, pub).returncode == 0


def _typed_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k != "FSBPYR_SIGNING_PASSPHRASE"}


def test_a_typed_passphrase_keeps_its_spaces(tmp_path: Path, file: Path) -> None:
    typed = b"  with spaces  "

    code, printed, _ = on_a_terminal(["bash", SCRIPT, "new-key", tmp_path / "t"], _typed_env(),
                                     typed, typed)  # fmt: skip

    assert code == 0, printed
    assert typed not in printed
    assert script("sign", tmp_path / "t.key", file, passphrase=typed.decode()).returncode == 0
    Path(f"{file}.sig").unlink()
    assert script("sign", tmp_path / "t.key", file, passphrase=typed.decode().strip()
                  ).returncode == 2  # fmt: skip


def test_two_different_typed_passphrases_write_nothing(tmp_path: Path) -> None:
    code, printed, _ = on_a_terminal(["bash", SCRIPT, "new-key", tmp_path / "d"], _typed_env(),
                                     b"one", b"two")  # fmt: skip

    assert code == 2 and b"the passphrases differ" in printed
    assert sorted(tmp_path.iterdir()) == []


def test_an_empty_typed_passphrase_is_refused(tmp_path: Path) -> None:
    code, printed, _ = on_a_terminal(["bash", SCRIPT, "new-key", tmp_path / "e"], _typed_env(),
                                     b"", b"")  # fmt: skip

    assert code == 2 and b"the passphrase is empty" in printed


def test_a_typed_passphrase_signs(file: Path, k: Key) -> None:
    code, printed, _ = on_a_terminal(["bash", SCRIPT, "sign", k.key, file], _typed_env(),
                                     PASSPHRASE.encode())  # fmt: skip

    assert code == 0, printed


# --- what it needs ------------------------------------------------------------------------------


def test_an_openssl_without_ml_dsa_is_refused_up_front(tmp_path: Path) -> None:
    fake(tmp_path / "bin", "openssl", '''case "$1" in
list) echo ED25519 ;;
version) echo "OpenSSL 3.0.13" ;;
esac
''')

    done = script("new-key", tmp_path / "k", env=with_path(tmp_path / "bin"))

    assert done.returncode == 2 and "OpenSSL 3.5 or newer is needed" in done.stderr
    assert not (tmp_path / "k.key").exists()


def test_no_openssl_is_refused_up_front(tmp_path: Path) -> None:
    empty = tmp_path / "bin"
    for tool in ("sha256sum", "dirname", "mktemp", "rm"):
        found = shutil.which(tool)
        assert found
        fake(empty, tool, f'exec {found} "$@"\n')

    done = subprocess.run(
        [requires.tool("bash"), SCRIPT, "new-key", tmp_path / "k"],
        env={"PATH": str(empty)}, capture_output=True, text=True, check=False)  # fmt: skip

    assert done.returncode == 2 and "openssl is missing" in done.stderr


@pytest.mark.parametrize("command", ["sign", "new-key", "verify"])
def test_a_failed_step_is_never_exit_1(
    tmp_path: Path, file: Path, k: Key, command: str
) -> None:
    """1 says NOT VERIFIED. A failing `mktemp` fails for any uid, as a mode-555 directory would
    not for root."""
    Path(f"{file}.sig").write_bytes(bytes(SIGNATURE_SIZE))
    if command == "sign":
        Path(f"{file}.sig").unlink()
    fake(tmp_path / "bin", "mktemp", "exit 1\n")
    arguments = {"sign": ("sign", k.key, file), "new-key": ("new-key", tmp_path / "x"),
                 "verify": ("verify", file, k.pub)}[command]  # fmt: skip

    assert script(*arguments, env=with_path(tmp_path / "bin")).returncode == 2


def test_new_key_in_a_missing_directory_is_exit_2(tmp_path: Path) -> None:
    assert script("new-key", tmp_path / "nothing" / "k").returncode == 2


@pytest.mark.parametrize("arguments", [(), ("verify",), ("verify", "x"), ("sign", "k"), ("what",)])
def test_a_usage_error_is_exit_2(arguments: tuple[str, ...]) -> None:
    assert script(*arguments).returncode == 2


def test_a_name_like_an_option_is_a_name(tmp_path: Path, k: Key) -> None:
    """A verdict on `-f` that came from `head`'s usage message would be no verdict."""
    file = tmp_path / "-f"
    file.write_text("a file\n")

    signed = script("sign", k.key, "-f", cwd=tmp_path)
    checked = script("verify", "-f", k.pub, cwd=tmp_path)

    assert signed.returncode == 0, signed.stderr
    assert checked.returncode == 0, checked.stderr


def test_the_pinned_keys_are_public_key_files(tmp_path: Path) -> None:
    """A file that is no key passes the guard of `make release`, and stops the sign job after the
    tag has burned the version."""
    file = tmp_path / "file"
    file.write_text("x\n")
    Path(f"{file}.sig").write_bytes(bytes(SIGNATURE_SIZE))

    for pin in sorted((ROOT / "src" / "fsbpyr" / "release-keys").glob("*.pub")):
        assert script("verify", file, pin).returncode == 1, pin  # not 2: no usable key


def test_no_temporary_directory_is_left(file: Path, k: Key) -> None:
    sign(k.key, file)
    script("verify", file, k.pub)

    assert not list(file.parent.glob(".release-signing.*"))


# --- the previous release -----------------------------------------------------------------------


def _release(tag: str, *, draft: bool = False, prerelease: bool = False) -> dict[str, object]:
    return {"tag_name": tag, "draft": draft, "prerelease": prerelease}


def _previous(
    tmp_path: Path, tag: str, *pages: list[dict[str, object]], fails: bool = False,
) -> subprocess.CompletedProcess[str]:
    """previous-release.sh against a fake curl that serves ``pages`` by their page number."""
    served = tmp_path / "pages"
    served.mkdir(exist_ok=True)
    for number, page in enumerate(pages, 1):
        (served / str(number)).write_text(json.dumps(page))
    body = "exit 22\n" if fails else f'''for word in "$@"; do last=$word; done
page=${{last##*page=}}
exec cat "{served}/$page"
'''
    fake(tmp_path / "bin", "curl", body)
    requires.tool("jq")
    env = {**os.environ, **with_path(tmp_path / "bin")}
    return subprocess.run([requires.tool("bash"), PREVIOUS, tag], capture_output=True, text=True,
                          env=env, check=False)  # fmt: skip


def test_the_previous_release_is_the_highest_published_version_below(tmp_path: Path) -> None:
    done = _previous(tmp_path, "v0.3.0", [_release("v0.2.0"), _release("v0.1.0")])

    assert (done.returncode, done.stdout) == (0, "v0.2.0\n")


def test_a_burned_tag_is_not_listed_and_does_not_count(tmp_path: Path) -> None:
    """v0.30.0 is a tag with no release: GitHub's list decides, not git."""
    done = _previous(tmp_path, "v0.30.1", [_release("v0.29.0")])

    assert done.stdout == "v0.29.0\n"


def test_a_companion_is_not_a_release(tmp_path: Path) -> None:
    done = _previous(tmp_path, "v0.3.0", [_release("el8-v0.2.0"), _release("v0.2.0"),
                                          _release("el8-v0.3.0")])  # fmt: skip

    assert done.stdout == "v0.2.0\n"


def test_a_companion_alone_is_no_previous_release(tmp_path: Path) -> None:
    """Its name sorts below every vX.Y.Z, so without the filter it would be taken for one."""
    done = _previous(tmp_path, "v0.1.0", [_release("el8-v0.1.0"), _release("nightly")])

    assert (done.returncode, done.stdout) == (0, "")


def test_the_tag_itself_is_not_its_previous_release(tmp_path: Path) -> None:
    done = _previous(tmp_path, "v0.3.0", [_release("v0.3.0"), _release("v0.2.0")])

    assert done.stdout == "v0.2.0\n"


def test_versions_compare_as_numbers(tmp_path: Path) -> None:
    """As text v0.9.0 sorts above v0.30.0."""
    done = _previous(tmp_path, "v0.30.0", [_release("v0.9.0"), _release("v0.29.0")])
    assert done.stdout == "v0.29.0\n"

    done = _previous(tmp_path, "v0.10.0", [_release("v0.9.0"), _release("v0.30.0")])
    assert done.stdout == "v0.9.0\n"


def test_drafts_and_prereleases_have_no_users(tmp_path: Path) -> None:
    done = _previous(tmp_path, "v0.3.0", [_release("v0.2.2", draft=True),
                                          _release("v0.2.1", prerelease=True),
                                          _release("v0.2.0")])  # fmt: skip

    assert done.stdout == "v0.2.0\n"


def test_every_page_is_read(tmp_path: Path) -> None:
    first = [_release(f"v9.{n}.0") for n in range(100)]

    done = _previous(tmp_path, "v1.0.0", first, [_release("v0.9.9")])

    assert done.stdout == "v0.9.9\n"


def test_no_earlier_release_prints_nothing(tmp_path: Path) -> None:
    done = _previous(tmp_path, "v0.1.0", [_release("v0.1.0")])

    assert (done.returncode, done.stdout) == (0, "")


def test_a_failed_call_stops_it(tmp_path: Path) -> None:
    done = _previous(tmp_path, "v0.3.0", fails=True)

    assert done.returncode == 2 and done.stdout == ""


@pytest.mark.parametrize("body", [
    '{"message": "API rate limit exceeded"}', "", "null", "7", '"text"', "not json",
    "[" + ",".join(['{"tag_name": "v9.0.0", "draft": false, "prerelease": false}'] * 100) + "]\n[]",
], ids=["an object", "empty", "null", "a number", "a string", "no JSON", "two documents"])
def test_a_page_that_is_no_list_stops_it(tmp_path: Path, body: str) -> None:
    """A list cut short must not read as the last page: an older release's pins would be used."""
    served = tmp_path / "pages"
    served.mkdir()
    (served / "1").write_text(body)
    fake(tmp_path / "bin", "curl", f'exec cat "{served}/1"\n')

    done = subprocess.run([requires.tool("bash"), PREVIOUS, "v0.3.0"], capture_output=True,
                          text=True, env={**os.environ, **with_path(tmp_path / "bin")},
                          check=False)  # fmt: skip

    assert done.returncode == 2 and done.stdout == ""


@pytest.mark.parametrize("tag", ["0.3.0", "el8-v0.3.0", "v1.2", "v01.2.3", ""])
def test_only_a_version_tag_is_asked_about(tmp_path: Path, tag: str) -> None:
    assert _previous(tmp_path, tag, []).returncode == 2


# --- the signature against each tag's pins ------------------------------------------------------


def _repository(tmp_path: Path, *tags: tuple[str, tuple[Path, ...]]) -> Path:
    """A git repository with a commit and a tag for each (tag, pinned .pub files). Every commit
    names the first signed release, as the release's checkout does."""
    requires.tool("git")
    repo = tmp_path / "repo"
    keys = repo / "src" / "fsbpyr" / "release-keys"
    keys.mkdir(parents=True)
    (repo / "tools" / "release").mkdir(parents=True)
    (repo / "tools" / "release" / "first-signed-release").write_text(f"{FIRST_SIGNED}\n")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.org", "-C", str(repo)]
    subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
    for tag, pins in tags:
        for old in keys.iterdir():
            old.unlink()
        for pin in pins:
            shutil.copy2(pin, keys / pin.name)
        subprocess.run([*git, "add", "-A"], check=True)
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", tag], check=True)
        subprocess.run([*git, "tag", tag], check=True)
    return repo


def _check(repo: Path, file: Path, tag: str, previous: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([requires.tool("bash"), CHECK, repo, file, tag, previous],
                          capture_output=True, text=True, check=False)  # fmt: skip


def test_a_release_passes_its_own_pins_and_the_previous_ones(
    tmp_path: Path, file: Path, k: Key, r: Key
) -> None:
    repo = _repository(tmp_path, ("v0.29.0", (k.pub, r.pub)), ("v0.30.0", (k.pub, r.pub)))
    sign(k.key, file)

    done = _check(repo, file, "v0.30.0", "v0.29.0")

    assert done.returncode == 0, done.stderr
    assert done.stdout.count("VERIFIED") == 2


def test_a_key_this_tag_does_not_pin_fails(
    tmp_path: Path, file: Path, k: Key, r: Key
) -> None:
    repo = _repository(tmp_path, ("v0.29.0", (r.pub,)), ("v0.30.0", (r.pub,)))
    sign(k.key, file)

    assert _check(repo, file, "v0.30.0", "v0.29.0").returncode == 1


def test_a_key_the_previous_release_does_not_pin_fails(
    tmp_path: Path, file: Path, k: Key, r: Key
) -> None:
    """Clients of the previous release could not update: what stops a secret holding a new K."""
    repo = _repository(tmp_path, ("v0.29.0", (r.pub,)), ("v0.30.0", (k.pub, r.pub)))
    sign(k.key, file)

    assert _check(repo, file, "v0.30.0", "v0.29.0").returncode == 1


def test_a_burned_tags_pins_do_not_count(tmp_path: Path, file: Path, k: Key, r: Key) -> None:
    """v0.29.0 is published, v0.30.0 is burned and pins the new K too, v0.30.1 is signed by the
    new K. Only v0.29.0 has users, and it does not pin that key."""
    repo = _repository(tmp_path, ("v0.29.0", (r.pub,)), ("v0.30.0", (r.pub, k.pub)),
                       ("v0.30.1", (k.pub,)))  # fmt: skip
    sign(k.key, file)

    assert _check(repo, file, "v0.30.1", "v0.30.0").returncode == 0  # the wrong one passes
    assert _check(repo, file, "v0.30.1", "v0.29.0").returncode == 1  # the right one does not


def test_a_tag_that_pins_nothing_stops_the_release(tmp_path: Path, file: Path, k: Key) -> None:
    repo = _repository(tmp_path, ("v0.30.0", ()))
    sign(k.key, file)

    done = _check(repo, file, "v0.30.0", "")

    assert done.returncode == 2 and "pins no keys" in done.stderr


def test_a_previous_tag_missing_from_the_checkout_stops_the_release(
    tmp_path: Path, file: Path, k: Key
) -> None:
    """As in a shallow checkout, which has no other tag."""
    repo = _repository(tmp_path, ("v0.30.0", (k.pub,)))
    sign(k.key, file)

    assert _check(repo, file, "v0.30.0", "v0.29.0").returncode == 2


def test_only_the_first_signed_release_may_have_no_pins_before_it(
    tmp_path: Path, file: Path, k: Key
) -> None:
    repo = _repository(tmp_path, ("v0.28.1", ()), (f"v{FIRST_SIGNED}", (k.pub,)),
                       ("v0.30.0", (k.pub,)))  # fmt: skip
    sign(k.key, file)

    first = _check(repo, file, f"v{FIRST_SIGNED}", "v0.28.1")
    later = _check(repo, file, "v0.30.0", "v0.28.1")
    none_found = _check(repo, file, "v0.30.0", "")

    assert first.returncode == 0, first.stderr
    assert later.returncode == 2 and "may have none" in later.stderr
    assert none_found.returncode == 2  # a lookup that went wrong stops the release


def test_the_first_signed_release_is_named_as_x_y_z() -> None:
    text = (ROOT / "tools" / "release" / "first-signed-release").read_text()

    assert text.count("\n") == 1 and text.strip().count(".") == 2
