"""The launcher's signature functions (sig.c, docs/signing.md §4), built with ASan and UBSan and
with LeakSanitizer on (tests/probes/sig.c), against the ones that matter most: signatures nobody
holding the key made, and files that only look like keys.

Everything fsbpyr accepts here goes through the same check as a real one. The independent vectors
come from outside OpenSSL's own tests and outside the release script, which uses the same OpenSSL,
so that a mistake both share (a context string, the internal interface, a pre-hash) cannot pass:
RFC 8032, NIST's ACVP, and Wycheproof (C2SP/wycheproof, Apache-2.0, tests/fixtures/signing:
testvectors_v1/ed25519_test.json at a3d5d1423f25, mldsa_65_verify_test.json at 20b256949ea8), whose
cases exist to catch a verifier that accepts too much. A pin without Ed25519 or ML-DSA-65 fails
here: full-suite.yml runs this on every Python and target."""

import base64
import contextlib
import hashlib
import json
import os
import pty
import random
import select
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from fsbpyr.assemble import build_executable
from fsbpyr.runtime.registry import Runtime

from ..support import requires, signatures
from ..support.apps import demo_app
from ..support.signing import (
    ED25519_SIZE,
    KEY_SIZE,
    PASSPHRASE,
    PUB_SIZE,
    SIGNATURE_SIZE,
    new_key,
    run_openssl,
    sign,
    sign_statement,
    statement,
)
from .conftest import LauncherBuild

pytestmark = pytest.mark.slow

ROOT = Path(__file__).parents[2]
PROBE = ROOT / "tests" / "probes" / "sig.c"
FIXTURES = ROOT / "tests" / "fixtures" / "signing"
OK, BAD_KEY_FILE, BAD_PASSPHRASE, BAD_KEY_TYPE, FAILED = 0, 1, 2, 3, 4
BEGIN, END = "-----BEGIN ENCRYPTED PRIVATE KEY-----\n", "-----END ENCRYPTED PRIVATE KEY-----\n"
PBKDF2_ITERATIONS = 600000
ED25519_ORDER = 2**252 + 27742317777372353535851937790883648493
FLIPS = 400  # bits of the ML-DSA half: the sweep over all of them takes a minute under ASan


@pytest.fixture(scope="module")
def probe(launcher_build: LauncherBuild, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("probe") / "sig"
    ssl = launcher_build.libs[-1].with_name("libssl.a")
    flags = ("-DFSB_SIG_NO_PYTHON", "-Wl,--wrap=EVP_DigestVerify", "-fsanitize=address,undefined",
             "-fno-sanitize-recover=undefined")  # fmt: skip
    return launcher_build.compile(out, [PROBE, "sig.c", "integrity.c", "parallel.c", ssl], *flags)


def run(probe: Path, *args: str | Path, env: dict[str, str] | None = None) -> list[str]:
    done = subprocess.run([probe, *map(str, args)], capture_output=True, text=True, check=False,
                          env=env)  # fmt: skip
    assert done.returncode == 0, done.stderr[-4000:]
    return done.stdout.split()


@dataclass(frozen=True)
class Pair:
    """A key of the script, a message, and the script's signature of it as subject fsbpyr."""

    key: Path
    pub: Path
    file: Path
    signature: Path
    message: Path


@pytest.fixture(scope="module")
def pair(tmp_path_factory: pytest.TempPathFactory) -> Pair:
    directory = tmp_path_factory.mktemp("pair")
    key, pub = new_key(directory / "k")
    file = directory / "SHA256SUMS"
    file.write_bytes(b"0123456789abcdef  some-file\n")
    signature = sign(key, file)
    message = directory / "statement"
    message.write_bytes(statement("fsbpyr", file.read_bytes()))
    return Pair(key, pub, file, signature, message)


@pytest.fixture(scope="module")
def other(tmp_path_factory: pytest.TempPathFactory) -> Pair:
    """A second key, signing the same file."""
    directory = tmp_path_factory.mktemp("other")
    key, pub = new_key(directory / "r")
    file = directory / "SHA256SUMS"
    file.write_bytes(b"0123456789abcdef  some-file\n")
    signature = sign(key, file)
    message = directory / "statement"
    message.write_bytes(statement("fsbpyr", file.read_bytes()))
    return Pair(key, pub, file, signature, message)


def verify(probe: Path, pub: Path, message: Path, signature: bytes, tmp_path: Path) -> int:
    path = tmp_path / "candidate.sig"
    path.write_bytes(signature)
    return int(run(probe, "verify", pub, message, path)[0])


def test_the_files_sha256_is_hashlibs(probe: Path, tmp_path: Path) -> None:
    for size in (0, 1, 55, 56, 64, (64 << 10) - 1, 64 << 10, (64 << 10) + 1, 3 << 20):
        path = tmp_path / str(size)
        data = random.Random(size).randbytes(size)  # noqa: S311  # test data
        path.write_bytes(data)

        assert run(probe, "sha256", path) == [hashlib.sha256(data).hexdigest()]


@pytest.mark.parametrize("result", ["0", "-1", "-2", "2"])
def test_only_a_result_of_1_from_openssl_is_a_good_signature(
    probe: Path, pair: Pair, result: str
) -> None:
    """Its verify returns a negative value for an error, which reads as true in C, and no input
    makes it do so: this is the one way to test it, and a mistake here passes every vector."""
    env = {**os.environ, "PROBE_VERIFY_RESULT": result}

    assert run(probe, "verify", pair.pub, pair.message, pair.signature, env=env) == ["0"]


def test_a_new_signature_that_does_not_verify_is_not_returned(
    probe: Path, pair: Pair, tmp_path: Path
) -> None:
    """The self-check: a key whose halves do not sign what they should gives no signature."""
    env = {**os.environ, "PROBE_VERIFY_RESULT": "0"}
    new, pub = tmp_path / "new.sig", tmp_path / "new.pub"

    done = run(probe, "sign", pair.key, PASSPHRASE, pair.message, new, pub, env=env)

    assert done == [str(FAILED)]
    assert not new.exists()


def test_a_signature_of_the_script_verifies(probe: Path, pair: Pair, tmp_path: Path) -> None:
    assert verify(probe, pair.pub, pair.message, pair.signature.read_bytes(), tmp_path) == 1


def test_a_signature_made_earlier_still_verifies(probe: Path, tmp_path: Path) -> None:
    """A committed .pub and .sig, of the script (subject fsbpyr) and of `openssl pkeyutl` (an app):
    a later change of the format or of the calls fails here, not at a user's."""
    data = (FIXTURES / "fixed-file").read_bytes()
    pub = FIXTURES / "fixed.pub"
    for project, signature in (("fsbpyr", "fixed-file.sig"), ("myapp", "fixed-file.myapp.sig")):
        message = tmp_path / project
        message.write_bytes(statement(project, data))

        assert verify(probe, pub, message, (FIXTURES / signature).read_bytes(), tmp_path) == 1


def test_a_signature_of_any_subject_is_the_same_statement(
    probe: Path, pair: Pair, tmp_path: Path
) -> None:
    """The one check of an app's statement that fsbpyr does not make itself: the script signs as
    fsbpyr only, so `openssl pkeyutl` signs this one."""
    message = tmp_path / "statement"
    message.write_bytes(statement("myapp", pair.file.read_bytes()))
    signature = sign_statement(pair.key, message.read_bytes(), tmp_path)

    assert verify(probe, pair.pub, message, signature, tmp_path) == 1


def flipped(data: bytes, position: int, bit: int = 0) -> bytes:
    return data[:position] + bytes([data[position] ^ (1 << bit)]) + data[position + 1:]


@pytest.mark.parametrize("position", [0, 31, 32, 63, 64, 65, 1000, 2000, 3000, SIGNATURE_SIZE - 1])
def test_a_flipped_byte_anywhere_refuses(
    probe: Path, pair: Pair, tmp_path: Path, position: int
) -> None:
    signature = pair.signature.read_bytes()

    assert verify(probe, pair.pub, pair.message, flipped(signature, position), tmp_path) == 0


@pytest.mark.parametrize("size", [0, 1, 63, 64, 65, 3309, 3372, 3374, 6746])
def test_any_other_size_is_no_signature(
    probe: Path, pair: Pair, tmp_path: Path, size: int
) -> None:
    signature = pair.signature.read_bytes()
    forged = (signature * 2)[:size]

    assert verify(probe, pair.pub, pair.message, forged, tmp_path) == 0


def test_one_good_half_is_not_enough(
    probe: Path, pair: Pair, other: Pair, tmp_path: Path
) -> None:
    """No half can stand in for the other: Ed25519 must pass with ML-DSA-65, whichever breaks."""
    ours, theirs = pair.signature.read_bytes(), other.signature.read_bytes()
    ed25519, ml_dsa = ours[:ED25519_SIZE], ours[ED25519_SIZE:]
    their_ed25519, their_ml_dsa = theirs[:ED25519_SIZE], theirs[ED25519_SIZE:]

    for forged in (ed25519 + their_ml_dsa, their_ed25519 + ml_dsa, ed25519 + bytes(len(ml_dsa)),
                   bytes(ED25519_SIZE) + ml_dsa, ml_dsa + ed25519, bytes(SIGNATURE_SIZE)):
        assert verify(probe, pair.pub, pair.message, forged, tmp_path) == 0
        assert verify(probe, other.pub, pair.message, forged, tmp_path) == 0


def test_a_signature_by_another_key_refuses(
    probe: Path, pair: Pair, other: Pair, tmp_path: Path
) -> None:
    assert verify(probe, pair.pub, other.message, other.signature.read_bytes(), tmp_path) == 0


@pytest.mark.parametrize("change", [
    lambda m: m.replace(b"subject fsbpyr", b"subject fsbpyS"),
    lambda m: m.replace(b"fsbpyr-signature 1", b"fsbpyr-signature 2"),
    lambda m: m.replace(b"sha256 0", b"sha256 1").replace(b"sha256 ", b"sha256  "),
    lambda m: m[:-1],
    lambda m: m + b"\n",
    lambda m: m + b"sha256 " + b"0" * 64 + b"\n",
    lambda m: m.upper(),
], ids=["subject", "version", "digest", "no last LF", "another LF", "a line more", "upper case"])
def test_a_signature_binds_its_whole_statement(
    probe: Path, pair: Pair, tmp_path: Path, change: Callable[[bytes], bytes]
) -> None:
    changed = tmp_path / "changed"
    changed.write_bytes(change(pair.message.read_bytes()))

    assert verify(probe, pair.pub, changed, pair.signature.read_bytes(), tmp_path) == 0


def _vectors(probe: Path, rows: list[tuple[int, bytes, bytes, bytes]], tmp_path: Path) -> list[int]:
    """fsb_sig_half for each (half, key file, message, signature)."""
    path = tmp_path / "vectors"
    with path.open("w") as out:
        for half, pem, message, signature in rows:
            print(half, *(part.hex() or "-" for part in (pem, message, signature)), file=out)
    return [int(line) for line in run(probe, "half", path)]


def _pem(der: bytes, width: int = 64) -> bytes:
    text = base64.b64encode(der).decode()
    lines = [text[at:at + width] for at in range(0, len(text), width)]
    return "\n".join(("-----BEGIN PUBLIC KEY-----", *lines, "-----END PUBLIC KEY-----", "")
                     ).encode()  # fmt: skip


ED25519_SPKI = bytes.fromhex("302a300506032b6570032100")
ML_DSA_65_SPKI = bytes.fromhex("308207b2300b0609608648016503040312038207a100")


def test_the_independent_vectors(probe: Path, tmp_path: Path) -> None:
    """RFC 8032 and ACVP, each with its own key and message: `_verify`, which takes one key file
    and one statement, cannot run them, so the probe calls the check of one half."""
    rows, expected, names = [], [], []
    for line in (FIXTURES / "independent.txt").read_text().splitlines():
        if line.startswith("#"):
            continue
        name, algorithm, expect, pub, message, signature = line.split()
        half, prefix = ((0, ED25519_SPKI) if algorithm == "ED25519" else (1, ML_DSA_65_SPKI))
        rows.append((half, _pem(prefix + bytes.fromhex(pub)),
                     b"" if message == "-" else bytes.fromhex(message), bytes.fromhex(signature)))
        expected.append(int(expect))
        names.append(name)

    assert dict(zip(names, _vectors(probe, rows, tmp_path), strict=True)) == dict(
        zip(names, expected, strict=True))
    assert len(rows) == 8


def _wycheproof(name: str) -> Iterator[tuple[dict[str, object], dict[str, str | int | list[str]]]]:
    document = json.loads((FIXTURES / name).read_text())
    for group in document["testGroups"]:
        for test in group["tests"]:
            yield group, test


def test_wycheproofs_ed25519_cases(probe: Path, tmp_path: Path) -> None:
    cases = list(_wycheproof("ed25519_test.json"))
    rows = [(0, _pem(bytes.fromhex(str(group["publicKeyDer"]))), bytes.fromhex(str(test["msg"])),
             bytes.fromhex(str(test["sig"]))) for group, test in cases]

    results = _vectors(probe, rows, tmp_path)

    # "acceptable" cases (a key of small order, an unusual encoding) may go either way by the
    # standard; every result here is pinned, so that a change of behaviour is noticed.
    wrong = [test["tcId"] for (_, test), got in zip(cases, results, strict=True)
             if (got == 1) != (test["result"] == "valid") and test["result"] != "acceptable"]
    assert wrong == []
    assert len(cases) == 210


def test_wycheproofs_ml_dsa_65_cases(probe: Path, tmp_path: Path) -> None:
    """Cases with a context pass here only if fsbpyr passed one: it signs with none, so the
    signature of such a case must fail, whatever its result under that context."""
    cases = list(_wycheproof("mldsa_65_verify_test.json"))
    rows = [(1, _pem(bytes.fromhex(str(group["publicKeyDer"]))), bytes.fromhex(str(test["msg"])),
             bytes.fromhex(str(test["sig"]))) for group, test in cases]

    results = _vectors(probe, rows, tmp_path)

    wrong = [test["tcId"] for (_, test), got in zip(cases, results, strict=True)
             if (got == 1) != (test["result"] == "valid" and not test.get("ctx"))]
    assert wrong == []
    assert len(cases) == 210


def test_ed25519_refuses_a_malleated_signature(probe: Path, pair: Pair, tmp_path: Path) -> None:
    """(R, S + L) passes a verifier that does not check S < L: a second signature of the same
    message, that nobody signed."""
    signature = pair.signature.read_bytes()[:ED25519_SIZE]
    der = base64.b64decode("".join(pair.pub.read_text().splitlines()[1:2]))
    pem = _pem(der)
    message = pair.message.read_bytes()
    r, s = signature[:32], int.from_bytes(signature[32:], "little")

    rows = [(0, pem, message, signature)]
    rows += [(0, pem, message, r + (s + multiple).to_bytes(32, "little"))
             for multiple in (ED25519_ORDER, 2 * ED25519_ORDER, 8 * ED25519_ORDER)]
    rows += [(0, pem, message, r + ED25519_ORDER.to_bytes(32, "little")),
             (0, pem, message, r + bytes(32)), (0, pem, message, bytes(32) + signature[32:])]

    assert _vectors(probe, rows, tmp_path) == [1, *[0] * (len(rows) - 1)]


def test_no_flipped_bit_of_a_signature_verifies(probe: Path, pair: Pair, tmp_path: Path) -> None:
    signature = pair.signature.read_bytes()
    pems = pair.pub.read_text().split("-----END PUBLIC KEY-----\n")[:2]
    ed25519_pem, ml_dsa_pem = (f"{pem}-----END PUBLIC KEY-----\n".encode() for pem in pems)
    message = pair.message.read_bytes()
    ed25519, ml_dsa = signature[:ED25519_SIZE], signature[ED25519_SIZE:]
    noise = random.Random(65)  # noqa: S311  # test data

    rows = [(0, ed25519_pem, message, flipped(ed25519, position // 8, position % 8))
            for position in range(8 * ED25519_SIZE)]
    rows += [(1, ml_dsa_pem, message, flipped(ml_dsa, position // 8, position % 8))
             for position in noise.sample(range(8 * len(ml_dsa)), FLIPS)]

    assert set(_vectors(probe, rows, tmp_path)) == {0}


def _keys(*algorithms: str) -> list[bytes]:
    """A public key file's text for each algorithm, as openssl writes it."""
    pems = []
    for algorithm in algorithms:
        private = run_openssl("genpkey", "-algorithm", algorithm)
        pems.append(run_openssl("pkey", "-pubout", stdin=private))
    return pems


def _with_trailing_byte(pem: bytes) -> bytes:
    lines = pem.decode().splitlines()
    der = base64.b64decode("".join(lines[1:-1]))
    return _pem(der + b"\0")


BAD_PUBS: dict[str, Callable[[bytes, bytes], bytes]] = {
    "empty": lambda _, __: b"",
    "one key": lambda ed, _: ed,
    "the other key alone": lambda _, ml: ml,
    "swapped": lambda ed, ml: ml + ed,
    "Ed25519 twice": lambda ed, _: ed + ed,
    "ML-DSA-65 twice": lambda _, ml: ml + ml,
    "a third key": lambda ed, ml: ed + ml + ed,
    "an extra line": lambda ed, ml: ed + ml + b"\n",
    "a line before": lambda ed, ml: b"hello\n" + ed + ml,
    "a line between": lambda ed, ml: ed + b"hello\n" + ml,
    "a line after": lambda ed, ml: ed + ml + b"hello\n",
    "no last LF": lambda ed, ml: (ed + ml)[:-1],
    "CRLF": lambda ed, ml: (ed + ml).replace(b"\n", b"\r\n"),
    "a header line": lambda ed, ml: (ed + ml).replace(
        b"-----BEGIN PUBLIC KEY-----\n", b"-----BEGIN PUBLIC KEY-----\nProc-Type: 4,X\n\n", 1),
    "base64 at 76 columns": lambda ed, ml: ed + _pem_wrapped(ml, 76),
    "a byte after the Ed25519 key": lambda ed, ml: _with_trailing_byte(ed) + ml,
    "a byte after the ML-DSA key": lambda ed, ml: ed + _with_trailing_byte(ml),
    "an RSA PUBLIC KEY label": lambda ed, ml: ed.replace(b"PUBLIC KEY", b"RSA PUBLIC KEY") + ml,
}


def _pem_wrapped(pem: bytes, width: int) -> bytes:
    return _pem(base64.b64decode("".join(pem.decode().splitlines()[1:-1])), width)


@pytest.mark.parametrize("name", BAD_PUBS)
def test_only_the_canonical_pub_is_read(
    probe: Path, pair: Pair, tmp_path: Path, name: str
) -> None:
    text = pair.pub.read_text()
    ed25519, ml_dsa = (f"{pem}-----END PUBLIC KEY-----\n".encode()
                       for pem in text.split("-----END PUBLIC KEY-----\n")[:2])
    bad = tmp_path / "bad.pub"
    bad.write_bytes(BAD_PUBS[name](ed25519, ml_dsa))

    # the signature is good for these keys: only the file's form refuses it
    assert verify(probe, bad, pair.message, pair.signature.read_bytes(), tmp_path) == -1


@pytest.mark.parametrize("algorithms", [
    ("ED448", "ML-DSA-65"), ("RSA", "ML-DSA-65"), ("EC", "ML-DSA-65"),
    ("ED25519", "ML-DSA-44"), ("ED25519", "ML-DSA-87"), ("ED25519", "ED448"),
], ids="+".join)
def test_a_pub_of_other_algorithms_is_refused(
    probe: Path, pair: Pair, tmp_path: Path, algorithms: tuple[str, str]
) -> None:
    first, second = algorithms
    arguments = {"EC": ("-pkeyopt", "ec_paramgen_curve:P-256")}
    pems = []
    for algorithm in (first, second):
        private = run_openssl("genpkey", "-algorithm", algorithm, *arguments.get(algorithm, ()))
        pems.append(run_openssl("pkey", "-pubout", stdin=private))
    bad = tmp_path / "bad.pub"
    bad.write_bytes(b"".join(pems))

    assert verify(probe, bad, pair.message, pair.signature.read_bytes(), tmp_path) == -1


def _signing(probe: Path, key: Path, message: Path, tmp_path: Path, passphrase: str = PASSPHRASE
             ) -> int:  # fmt: skip
    signature, pub = tmp_path / "new.sig", tmp_path / "new.pub"
    signature.unlink(missing_ok=True)
    status = int(run(probe, "sign", key, passphrase, message, signature, pub)[0])
    assert signature.exists() == (status == OK)  # no failure leaves a signature
    return status


def _blocks(key: Path) -> tuple[str, str]:
    first, second = key.read_text().split(END)[:2]
    return first + END, second + END


def _plain_block(algorithm: str) -> str:
    return run_openssl("genpkey", "-algorithm", algorithm).decode()


def _encrypted_block(algorithm: str, passphrase: str = PASSPHRASE) -> str:
    plain = run_openssl("genpkey", "-algorithm", algorithm)
    return run_openssl("pkcs8", "-topk8", "-v2", "aes-256-cbc", "-v2prf", "hmacWithSHA256", "-iter",
                       "1000", "-passout", f"pass:{passphrase}", stdin=plain).decode()  # fmt: skip


def _in_the_body(block: str, text: str, at: int = 20) -> str:
    """``block`` with ``text`` ``at`` characters into its base64."""
    position = len(BEGIN) + at
    return block[:position] + text + block[position:]


BAD_KEY_FILES: dict[str, Callable[[str, str], str]] = {
    "empty": lambda _, __: "",
    "one block": lambda ed, _: ed,
    "three blocks": lambda ed, ml: ed + ml + ed,
    "text before": lambda ed, ml: "hello\n" + ed + ml,
    "a line between": lambda ed, ml: ed + "hello\n" + ml,
    "an LF between": lambda ed, ml: ed + "\n" + ml,
    "text after": lambda ed, ml: ed + ml + "hello\n",
    "CRLF": lambda ed, ml: (ed + ml).replace("\n", "\r\n"),
    "a CR at the end": lambda ed, ml: ed + ml + "\r\n",
    "no LF between": lambda ed, ml: ed[:-1] + ml,
    "the first block unencrypted": lambda _, ml: _plain_block("ED25519") + ml,
    "the second block unencrypted": lambda ed, _: ed + _plain_block("ML-DSA-65"),
    "both blocks unencrypted": lambda _, __: _plain_block("ED25519") + _plain_block("ML-DSA-65"),
    "a plain key between": lambda ed, ml: ed + _plain_block("ML-DSA-65") + ml,
    "a plain third key": lambda ed, ml: ed + ml + _plain_block("ML-DSA-65"),
    "a space in the base64": lambda ed, ml: _in_the_body(ed, " ") + ml,
    "a NUL in the base64": lambda ed, ml: _in_the_body(ed, "\0") + ml,
    "a character outside base64": lambda ed, ml: ed + _in_the_body(ml, "*"),
    "header lines in a block": lambda ed, ml: _in_the_body(
        ed, "Proc-Type: 4,ENCRYPTED\nDEK-Info: AES-256-CBC,00\n\n", at=0) + ml,
    "a byte in place of the LF between": lambda ed, ml: ed[:-1] + "x" + ml,
    "a byte in place of the last LF": lambda ed, ml: ed + ml[:-1] + "x",
    "text on the last line": lambda ed, ml: ed + ml[:-1] + "x\n",
    "a NUL": lambda ed, ml: ed + ml + "\0",
    "a public key file's label": lambda ed, ml: (ed + ml).replace("ENCRYPTED PRIVATE KEY",
                                                                  "PUBLIC KEY"),
}


@pytest.mark.parametrize("name", BAD_KEY_FILES)
def test_a_key_file_of_any_other_form_is_refused(
    probe: Path, pair: Pair, tmp_path: Path, name: str
) -> None:
    bad = tmp_path / "bad.key"
    bad.write_text(BAD_KEY_FILES[name](*_blocks(pair.key)))

    assert _signing(probe, bad, pair.message, tmp_path) == BAD_KEY_FILE


@pytest.mark.parametrize("ending", ["", "\n", "\n\n\n"], ids=["no LF", "one more", "three more"])
def test_a_key_may_lose_its_last_lf_or_gain_some(
    probe: Path, pair: Pair, tmp_path: Path, ending: str
) -> None:
    ed25519, ml_dsa = _blocks(pair.key)
    copied = tmp_path / "copied.key"
    copied.write_text(ed25519 + ml_dsa[:-1] + ending)

    assert _signing(probe, copied, pair.message, tmp_path) == OK


def test_keys_of_other_types_say_so(probe: Path, pair: Pair, tmp_path: Path) -> None:
    ed25519, ml_dsa = _blocks(pair.key)
    for name, text in (("swapped", ml_dsa + ed25519), ("Ed25519 twice", ed25519 + ed25519),
                       ("RSA first", _encrypted_block("RSA") + ml_dsa),
                       ("Ed448 first", _encrypted_block("ED448") + ml_dsa),
                       ("ML-DSA-44 second", ed25519 + _encrypted_block("ML-DSA-44"))):
        bad = tmp_path / "other.key"
        bad.write_text(text)

        assert _signing(probe, bad, pair.message, tmp_path) == BAD_KEY_TYPE, name


def test_a_wrong_passphrase_makes_no_signature(probe: Path, pair: Pair, tmp_path: Path) -> None:
    for passphrase in ("wrong", PASSPHRASE[:-1], PASSPHRASE + " ", "x" * 1024):
        assert _signing(probe, pair.key, pair.message, tmp_path, passphrase) == BAD_PASSPHRASE


def test_the_second_key_under_another_passphrase_makes_no_signature(
    probe: Path, pair: Pair, tmp_path: Path
) -> None:
    ed25519, _ = _blocks(pair.key)
    bad = tmp_path / "bad.key"
    bad.write_text(ed25519 + _encrypted_block("ML-DSA-65", "another passphrase"))

    assert _signing(probe, bad, pair.message, tmp_path) == BAD_PASSPHRASE


def test_a_passphrase_is_one_to_1024_bytes(probe: Path, tmp_path: Path) -> None:
    key, pub = tmp_path / "k.key", tmp_path / "k.pub"

    assert run(probe, "keynew", "", key, pub) == [str(BAD_PASSPHRASE)]
    assert run(probe, "keynew", "x" * 1025, key, pub) == [str(BAD_PASSPHRASE)]
    assert not key.exists()
    assert run(probe, "keynew", "x" * 1024, key, pub) == [str(OK)]
    message = tmp_path / "statement"
    message.write_bytes(b"a statement\n")
    assert _signing(probe, key, message, tmp_path, "x" * 1024) == OK
    assert _signing(probe, key, message, tmp_path, "x" * 1025) == BAD_PASSPHRASE


def test_an_empty_passphrase_is_refused_without_a_prompt(
    probe: Path, pair: Pair, tmp_path: Path
) -> None:
    """Given NULL or an empty one, OpenSSL asks on the terminal itself."""
    primary, secondary = pty.openpty()
    args = [str(part) for part in (probe, "sign", pair.key, "", pair.message, tmp_path / "s",
                                   tmp_path / "p")]  # fmt: skip
    done = subprocess.Popen(args, stdin=secondary, stdout=subprocess.PIPE, stderr=secondary,
                            start_new_session=True)  # fmt: skip
    os.close(secondary)
    out, _ = done.communicate(timeout=30)
    readable, _, _ = select.select([primary], [], [], 0.2)
    printed = b""
    with contextlib.suppress(OSError):  # EIO: the child closed its side, having written nothing
        printed = os.read(primary, 4096) if readable else b""
    os.close(primary)

    assert out.split() == [str(BAD_PASSPHRASE).encode()]
    assert printed == b""


def test_keys_of_the_c_code_and_the_script_work_both_ways(
    probe: Path, pair: Pair, tmp_path: Path
) -> None:
    key, pub = tmp_path / "c.key", tmp_path / "c.pub"
    assert run(probe, "keynew", PASSPHRASE, key, pub) == [str(OK)]
    assert (key.stat().st_size, pub.stat().st_size) == (KEY_SIZE, PUB_SIZE)

    file = tmp_path / "file"
    file.write_bytes(b"signed by the script with a key of the C code\n")
    by_script = sign(key, file).read_bytes()
    message = tmp_path / "statement"
    message.write_bytes(statement("fsbpyr", file.read_bytes()))
    assert verify(probe, pub, message, by_script, tmp_path) == 1

    assert _signing(probe, pair.key, message, tmp_path) == OK
    by_c = (tmp_path / "new.sig").read_bytes()
    assert (tmp_path / "new.pub").read_bytes() == pair.pub.read_bytes()
    assert verify(probe, pair.pub, message, by_c, tmp_path) == 1
    file.rename(tmp_path / "SHA256SUMS")
    (tmp_path / "SHA256SUMS.sig").write_bytes(by_c)
    from ..support.signing import script  # noqa: PLC0415

    checked = script("verify", tmp_path / "SHA256SUMS", pair.pub)
    assert checked.returncode == 0, checked.stderr


def test_the_ed25519_half_is_deterministic_and_ml_dsa_is_not(
    probe: Path, pair: Pair, tmp_path: Path
) -> None:
    """The one byte comparison a signature allows: ML-DSA signs randomized."""
    assert _signing(probe, pair.key, pair.message, tmp_path) == OK
    first = (tmp_path / "new.sig").read_bytes()
    assert _signing(probe, pair.key, pair.message, tmp_path) == OK
    second = (tmp_path / "new.sig").read_bytes()
    script_signature = pair.signature.read_bytes()

    assert first[:ED25519_SIZE] == second[:ED25519_SIZE] == script_signature[:ED25519_SIZE]
    assert first[ED25519_SIZE:] != second[ED25519_SIZE:]
    assert verify(probe, pair.pub, pair.message, first, tmp_path) == 1
    assert verify(probe, pair.pub, pair.message, second, tmp_path) == 1


def test_a_new_key_is_encrypted_as_the_script_encrypts_it(probe: Path, tmp_path: Path) -> None:
    key, pub = tmp_path / "c.key", tmp_path / "c.pub"
    run(probe, "keynew", PASSPHRASE, key, pub)

    for block in _blocks(key):
        parsed = run_openssl("asn1parse", stdin=block.encode()).decode()

        assert "PBES2" in parsed and "PBKDF2" in parsed
        assert "hmacWithSHA256" in parsed and "aes-256-cbc" in parsed
        assert f":{PBKDF2_ITERATIONS:06X}" in parsed.replace(" ", "")
        assert parsed.count("OCTET STRING") >= 2


def test_the_error_queue_stays_as_it_was(probe: Path, pair: Pair) -> None:
    """Shared with CPython's _ssl, which does not empty it before a read: a failed check between
    urlopen() and read() made read() raise SSLError."""
    run(probe, "queue", pair.key, PASSPHRASE, pair.pub, pair.message, pair.signature)


CONFIGS = {
    "none": "",
    "fips": "[openssl_init]\nalg_section = props\n[props]\ndefault_properties = fips=yes\n",
    "base only": "[openssl_init]\nproviders = list\n[list]\nbase = base\n[base]\nactivate = 1\n",
    "a missing provider": "[openssl_init]\nproviders = list\n[list]\nfips = fips\n"
                          "[fips]\nactivate = 1\n",
    "a syntax error": "[openssl_init\nproviders = x\n",
    "legacy properties": "[openssl_init]\nproviders = list\nalg_section = props\n"
                         "[list]\ndefault = on\nlegacy = on\n[on]\nactivate = 1\n"
                         "[props]\ndefault_properties = provider=legacy\n",
    "a missing include": ".include /nonexistent/openssl.cnf\n[openssl_init]\n",
    "the rdrand engine": "[openssl_init]\nengines = list\n[list]\nrdrand = rd\n"
                         "[rd]\ndefault_algorithms = ALL\n",
    "an engine that cannot be loaded": "[openssl_init]\nengines = list\n[list]\nbad = bad\n"
                                       "[bad]\ndynamic_path = /nonexistent/engine.so\n"
                                       "default_algorithms = ALL\n",
}


def _config_cases() -> list[tuple[str, str, bool]]:
    cases = [(name, text, False) for name, text in CONFIGS.items()]
    cases += [(name, text, True) for name, text in CONFIGS.items()
              if name in ("a missing provider", "a syntax error")]  # fmt: skip
    return cases


@pytest.mark.parametrize("init", ["none", "crypto", "ssl", "digests"])
@pytest.mark.parametrize(("name", "text", "diagnostics"), _config_cases(),
                         ids=lambda value: value if isinstance(value, str) and len(value) < 32
                         else "diagnostics" if value is True else "quiet" if value is False
                         else "text")  # fmt: skip
def test_no_config_changes_a_result(
    probe: Path, pair: Pair, tmp_path: Path, init: str, name: str, text: str, diagnostics: bool
) -> None:
    """Under `fips=yes` the default context finds no algorithm, so this also shows that no call
    falls back to it. Each case is the process's first OpenSSL use, or comes after what CPython's
    modules do first."""
    config = tmp_path / "openssl.cnf"
    start = "config_diagnostics = 1\n" if diagnostics else ""
    config.write_text(f"{start}openssl_conf = openssl_init\n{text}")
    env = {**os.environ, "OPENSSL_CONF": str(config)}
    if name == "none":
        env["OPENSSL_MODULES"] = str(tmp_path)  # an empty directory
    bad = tmp_path / "bad.sig"
    bad.write_bytes(flipped(pair.signature.read_bytes(), 100))
    new, new_pub = tmp_path / "new.sig", tmp_path / "new.pub"
    prefix = ("-i", init)

    assert run(probe, *prefix, "verify", pair.pub, pair.message, pair.signature, env=env) == ["1"]
    assert run(probe, *prefix, "verify", pair.pub, pair.message, bad, env=env) == ["0"]
    assert run(probe, *prefix, "sign", pair.key, "wrong", pair.message, new, new_pub, env=env) == [
        str(BAD_PASSPHRASE)]
    assert run(probe, *prefix, "sign", pair.key, PASSPHRASE, pair.message, new, new_pub, env=env
               ) == [str(OK)]  # fmt: skip
    assert run(probe, *prefix, "verify", pair.pub, pair.message, new, env=env) == ["1"]


def test_the_declarations_match_openssls_headers(
    launcher_build: LauncherBuild, tmp_path: Path
) -> None:
    """The distribution ships no headers, so sig.c writes out what it uses. This compiles it with
    Debian's 3.5 headers included too: a declaration that differs is a conflict, and an error."""
    requires.openssl_headers()
    source = tmp_path / "with-headers.c"
    source.write_text(
        "#include <openssl/bio.h>\n#include <openssl/crypto.h>\n#include <openssl/err.h>\n"
        "#include <openssl/evp.h>\n#include <openssl/pem.h>\n#include <openssl/pkcs12.h>\n"
        "#include <openssl/x509.h>\n#undef BIO_CTRL_INFO\n"
        f'#include "{launcher_build.sources / "sig.c"}"\n')
    command = [str(launcher_build.clang), "-std=c11", "-D_GNU_SOURCE", "-DFSB_SIG_NO_PYTHON",
               "-Wall", "-Wextra", "-Werror", f"-I{launcher_build.sources}", "-fsyntax-only",
               str(source)]  # fmt: skip

    done = subprocess.run(command, capture_output=True, text=True, check=False)

    assert done.returncode == 0, done.stderr


@pytest.mark.sanitized
def test_the_functions_of_fsbpyr_on_both_launchers(runtime: Runtime, tmp_path: Path) -> None:
    """As signing.py calls them, inside an app: the wrappers, on the release launcher and with
    ASan and UBSan, where any memory error ends the run with status 99."""
    demo = tmp_path / "demo"
    build_executable(runtime, demo_app(), demo)

    env = {**os.environ, "FSBPYR_CACHE_DIR": str(tmp_path)}
    result = subprocess.run([demo, "signatures"], capture_output=True, text=True, check=False,
                            timeout=120, env=env)  # fmt: skip

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == signatures.EXPECTED
