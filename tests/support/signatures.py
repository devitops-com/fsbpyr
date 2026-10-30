"""``_fsbpyr``'s signature functions, called as signing.py calls them, inside a built app: on both
launchers, the sanitized one included. The vectors are inline: the app carries no fixtures. Their
key was made once by release-signing.sh, and is gone."""

import base64
import hashlib
import os
import tempfile
from typing import Any

PUB = base64.b64decode(
    "LS0tLS1CRUdJTiBQVUJMSUMgS0VZLS0tLS0KTUNvd0JRWURLMlZ3QXlFQTRCbFdTNkxGRzd1TTZra1ZFRTFwQ0pZWG"
    "dvNzBXVzYzYTRndEcvR2FMSWM9Ci0tLS0tRU5EIFBVQkxJQyBLRVktLS0tLQotLS0tLUJFR0lOIFBVQkxJQyBLRVkt"
    "LS0tLQpNSUlIc2pBTEJnbGdoa2dCWlFNRUF4SURnZ2VoQU10eHdQMTVYRlc5NUQ2OTRNR1I2SFJjSVY1S21HbWU0RG"
    "EzClNFU0piYXhDTGs1TUFjeXlHaTV2T3k2RzYvWU1ZYWNybUl5eHpYN3F2RDVMWTRnZ2R4UGZSaENSMWswa3pOc0wK"
    "dkNNN0RJUWlsSU9yNzEwL0RYOTZCY01wTEI0T1NwRVN0cUY0YTVObGVGWklxTWsrSWRQSXlzYmlnblRJaVdoMwpQVE"
    "ZPWkx3dlUrSzF2bUhBV3JNTEUwamRtMTZZbFQxSzBKaHF1UFBjSlFRclY0RGQvT0c0YzV5a2IwWTU4SE05ClVYNGJk"
    "Y2NaTG5HUHljalZQck5XRXBOOTAvWmJVc0pqOFRqQ2t4NVlsM3AyTHdVV2pVTlM1M2lXTkpyRU5pZHMKa1FINkdsWC"
    "tFTlF0TDBUSkFpNGZyNGU5czhUY3g3VUJKNGFaUm92TkpUNHNwc3oxbUZuMUoyK2sxZ3BST0kxLworWnpjMzE1VDdG"
    "MFhmSW5KOExQVHFaMk9NbFF4UjNPcEJJdnRjVUI5UlEwWnJuK2k5dGo0dTRuZGxZL3lNZDhnCnhXSnpDNGJsUDBPM2"
    "FtYXZpRFdhNWRndGgwSEtkU1FMTXlPYzdOVGlGOEI2cHJHRGV5RE1mVzF5U2dOT0t4QnkKVjJuUTM5SmY0QWg4TnEz"
    "Rks5aTdqdVN4Sy8ySEZqWjdNMmdMNklVLzNGN1QyZDFvWDgvakZmMlRuVklpYktBMQpjNlZZVmF5alRKWjEwaGlRbG"
    "8yVml3aEZRd2FISjVXUE4xYUR5c3BXSW94V0s3b1F0WmZ5NGFyR0tOcXdnQXpxCmJBc2FHSFRWRmluSWdpOTFTdDFy"
    "UVdkWG9JU29veHY3VkR2S2lUb3RFRWxCcUN0VTE3QnJZUlJVZG41ZEU4Wi8KbENjUU1MZ282dmZ0VjNQOEM2YXF0WV"
    "JXbkFMWGdsTGdqOWxVWEpjTW9raG02MEtnbVRXc0dlV3FpK28reDgyegp2cldHWHNFOG1aZjk4M1RTcjR4YWJvd1Mr"
    "MmM5d2dFSUdxYVFMTnJNS3RXWWdxYk95cEp5VHV2dXpTT1lmU1JUCnRtbWsrRXRxQzdJS2s1alZ2d1o0TEZRQkZZaG"
    "ZiUnBNd3o3UXBlOXhpOWYzMkErYzExY1NxVGtKUHFJRytlbHMKUEZBOEo3MzNpVi9acUVWUlo2RmIzVnQ4emNFTWtQ"
    "Um04ZkExMzRXKzJkakpGNFNlRTFaYVhBZ0dPZFhqT1N1UgpVVFplYWdrVC9RRllCREZTN05aUnJDeVNKa2szdVZneV"
    "RCTS9iNTZoQjY0Y1F1cS9oQW5mWFBOSjNHaTlzQXNNCmkzUTA1UkxXY1ZCUEEzVDFhTm5Wd1luL3lQaDFSbmlNRTk5"
    "VUNqSkF1S2Y5UkpmcG9NNkxGcWhHaE1aOEovYTEKa1NnUkNzWmNieGNRcElkVUVIaFFHaEdZVlIyVXBCSWc3dmR6RX"
    "dtUVdoTy9mZzZvU0VxMmNaT2F4NnI1VVV5NwprbFZNa3pwZHZ3Tnk1ZG5oNzJwY2syQWhTejYvMWJjRlFDNkpLamJQ"
    "R2J3ODI3VW9NeWRVRUw4UG1MbU53US9DCnlja3RMd3dWSTBqUVNhT0tFSnFnVmJSaFVuMHBPUDR6d3ZwYThVVEN2cD"
    "hsNUVEQmpDWUhCUXBQcEhEUnZleGgKZ0NLME1lQTFhcWJZK1dHcWthWjVra05sMzd3WXB6ZmlHb3lXK0QxRWVGNGJR"
    "SVYyRjhvTHBEWjdDc1BJV1piRgpwT2dlM2o1WU10ZlhwY3ZuMktZSzkwRld6YkgyQ1hEQ0h3bFBlVUQ1ME8walVvMz"
    "VieUJPRk1udndhQ2dpS3ZMCnJqUXZMdHc4REdqMUM4VjNmdVp2Z2hpMDRuNWpoQmJhaTJKaTc3L3kwMi9MUkU5WUpT"
    "eDYvZEtEOG9Sbmt5TysKUXBxNlV6UTlGOFpLZVNJemVwbWl5YWlwNmZqK0JtUFV5cEpFTlk2Q3Vab3NlaDNPcjZCRF"
    "hxZW52SlhPeGlhawpzYkthT0MvU3ZNbC94d1VlUTYrSVFabDhMbTA4WFlraXBQZSszUHVjMzhZZTVHTFFVR2RZNXFT"
    "ZDNvS3ZvazhFCnZLVTVZYlJkZ3BvVU9oMC9jZ05RWGxDWU91Y2RpcG5nOEJPZHM4Y29iVzlaTXo0b0J5QjhmZzZyZk"
    "5qbTBzenkKRDJrK1o4a1QwNUhsSXRhNzJ0eVNjSGhZWWt1eEdwMVh5QTQySTlsY3BUYldmSC9DS3JxdWExalljVVc0"
    "SVJrMwpaZmNuSmpxOGdaMWJmODdqU0lUdDhKejRHc2E3VjJyeHpubWN4Y0tEb2IybXh6bEd3Ui9tbHlpUUo3c3VzY1"
    "lrCmYrNFUwbW5tenMyQzZIK2Qyc0tseiswZEVvVlFMZGZWQ0xQQWhFK0dGcW83eXZubnFaWGZiaGwzU2Z1WndqeTYK"
    "dTN4OGwrRnFKZDBmdTdUeGJEbzR6eEcwRllUZG9NUGhVZFNoSTJpMmcvOEFSdkhibjg1TU1jZkxQWmlhcndSSwpkQX"
    "o5VkwyUU5OZTN1NnNvUUhQUjZlU1dIamNlV2F4eWduT0FiNkRVcUlUZTVVd0pxbzdFRC9vNnZjNG1nWkcxCmtnZG1s"
    "ZUZ3UmNPZjdBQ0l4ZTF4akFHQVJHMGNMdmgxanBLZ3JjVWdaQ1BzNnpXSHAySGtqQktZckZjZDFSRk4KZnJHb08vM3"
    "lPT3dyL1JhbllUODRBWHdUeHduL3RmVmRmWDhzUk9YRDVMbU51d3NnRml2Y1N0Yy9qZE13cFpQaQpmdXJsQWhkajUx"
    "V1JDSG04cEpNUlpKSnpQYWFIbHk1eGdRL21RMlNadVo0UmI5ZFlLY1ZIQXJCaVZGaWZmRHh4CmxuRE9LMEJmNWVlY3"
    "dkakgzRlJpc0t6MWV4a0FXQmsxYzBKbnpNOG0rSEFmQU5ueGIvb3RNNzVJUWZyWGc4RzQKS1RqTEF0Vk5aSURrZVY1"
    "eElTSDg3QmFhUUJYc3krYlI2KzJSWWRUdkZieGdFUTZoRytMbkM5NnMyS2pNYTduTwpaN0NiVFNtQjZ1TVlBaU1jSV"
    "dZYytBY0hSVU5ybVc3UUFBaWExNGFVZ1NOUjB0TnpjZDNBb1lHSFdVZ3Z1OTJPCnpqUGNmdzhnSjBwMGFXK29zVGZS"
    "OTJ1aTgraWJyUWVVaXdwWDdEZHZud0pxbUZDMS9GSkdoRnVIQzZaSCtjbEoKZnVJMHZDM0NCYmVUMW1wSVA0MEdtTG"
    "YyMVdvUyt1cTAzSEQ2NVZVNm90U2x0YmFiaGE1akYyb2laMzZmZ25PQgowaGM3bnZ5VUxCcmtjdjZnK2ZLci9Fa0dN"
    "dnFFWGtRclFnT3M4K3BlMjk3SWEwNzRjNnVsMHFXcUtHM3lNMHNBCnA2RzFKcFIxbHE2QjgwK3lDQVRrQ21vY3pKej"
    "lKU2lxSWl2WVNJQ2lraGlIaWR6R0xzZ2ExZWllTVpLd1NFS3kKK0o2UHBXTDEKLS0tLS1FTkQgUFVCTElDIEtFWS0t"
    "LS0tCg=="
)
STATEMENT = base64.b64decode(
    "ZnNicHlyLXNpZ25hdHVyZSAxCnN1YmplY3QgZnNicHlyCnNoYTI1NiBiMDgxM2ZlYTBlODZkZjI3NTkzM2MxMmY4Nj"
    "Q5Nzc0YzA2MDIwZTA4ZWQxOTNmNmQ0YmViNTZiNDM1NWNlNjlhCg=="
)
SIGNATURE = base64.b64decode(
    "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"
)
PASSPHRASE = b"a passphrase with spaces "


def _refuses(call: Any, *args: Any) -> str:
    try:
        call(*args)
    except ValueError as exc:
        return str(exc)
    return "accepted"


def _flipped(data: bytes, position: int) -> bytes:
    return data[:position] + bytes([data[position] ^ 1]) + data[position + 1:]


def run(fsb: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "fixed": fsb._verify(PUB, STATEMENT, SIGNATURE),
        "a flipped Ed25519 half": fsb._verify(PUB, STATEMENT, _flipped(SIGNATURE, 3)),
        "a flipped ML-DSA half": fsb._verify(PUB, STATEMENT, _flipped(SIGNATURE, 2000)),
        "another statement": fsb._verify(PUB, STATEMENT + b"\n", SIGNATURE),
        "too short": fsb._verify(PUB, STATEMENT, SIGNATURE[:-1]),
        "empty": fsb._verify(PUB, b"", b""),
        "no pub": _refuses(fsb._verify, b"", STATEMENT, SIGNATURE),
        "a pub with a line more": _refuses(fsb._verify, PUB + b"\n", STATEMENT, SIGNATURE),
        "sha256 of bytes": fsb._sha256(b"abc").hex() == hashlib.sha256(b"abc").hexdigest(),
    }
    with tempfile.TemporaryFile() as file:
        file.write(b"abc" * 100000)
        file.flush()
        out["sha256 of a file"] = (
            fsb._sha256(file.fileno()).hex() == hashlib.sha256(b"abc" * 100000).hexdigest())
    read, write = os.pipe()
    try:
        fsb._sha256(read)
    except OSError as exc:
        out["sha256 of a pipe"] = exc.errno
    os.close(read)
    os.close(write)
    key, pub = fsb._key_new(PASSPHRASE)
    out["sizes"] = [len(key), len(pub)]
    signature, same = fsb._sign(key, PASSPHRASE, STATEMENT)
    out["signed"] = [len(signature), same == pub, fsb._verify(pub, STATEMENT, signature),
                     fsb._verify(PUB, STATEMENT, signature)]
    out["wrong passphrase"] = _refuses(fsb._sign, key, b"wrong", STATEMENT)
    out["no passphrase"] = _refuses(fsb._sign, key, b"", STATEMENT)
    out["not a key"] = _refuses(fsb._sign, PUB, PASSPHRASE, STATEMENT)
    out["a passphrase too long"] = _refuses(fsb._key_new, b"x" * 1025)
    return out


EXPECTED = {
    "fixed": True, "a flipped Ed25519 half": False, "a flipped ML-DSA half": False,
    "another statement": False, "too short": False, "empty": False,
    "no pub": "not a public key file", "a pub with a line more": "not a public key file",
    "sha256 of bytes": True, "sha256 of a file": True, "sha256 of a pipe": 29,  # ESPIPE
    "sizes": [6088, 2839], "signed": [3373, True, True, False],
    "wrong passphrase": "wrong passphrase?", "no passphrase": "wrong passphrase?",
    "not a key": "not a key file", "a passphrase too long": "wrong passphrase?",
}  # fmt: skip
