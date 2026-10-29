#!/usr/bin/env bash
#
# release-signing.sh — fsbpyr's release keys and the signature of a release's SHA256SUMS, made
# and checked with the openssl command alone (docs/signing.md). release.yml's and el8.yml's sign
# jobs run it, so the job that holds the release key runs no fsbpyr; `make release-check` checks
# with it; the admin makes the release keys with it.
#
#     bash tools/release/release-signing.sh new-key NAME         NAME.key and NAME.pub
#     bash tools/release/release-signing.sh sign KEY FILE        FILE.sig, signed as fsbpyr
#     bash tools/release/release-signing.sh verify FILE PUB...   FILE.sig against any PUB
#
# The files, as fsbpyr's `key new`, `sign` and `verify` write and read them (docs/signing.md §2):
#   NAME.key  two encrypted PKCS#8 PEM blocks under one passphrase, Ed25519 then ML-DSA-65;
#   NAME.pub  the two public keys as PEM, in the same order, and nothing else; its fingerprint
#             is SHA256: and the SHA-256 of the file;
#   FILE.sig  the Ed25519 signature (64 bytes), then the ML-DSA-65 one (3309 bytes), both over
#             the statement "fsbpyr-signature 1\nsubject fsbpyr\nsha256 <FILE's SHA-256>\n".
#
# The passphrase comes from FSBPYR_SIGNING_PASSPHRASE, or is asked for on the terminal. Nothing
# that exists is replaced. Needs OpenSSL 3.5 or newer and sha256sum. Exit status: 0 done or
# VERIFIED, 1 NOT VERIFIED, 2 usage or setup error.
set -euo pipefail
umask 077
export LC_ALL=C

SUBJECT=fsbpyr
ALGORITHMS=(ED25519 ML-DSA-65)
SIG_SIZES=(64 3309)

die() { printf '%s\n' "$2" >&2; exit "$1"; }

usage() {
    cat >&2 <<'EOF'
usage: release-signing.sh new-key NAME
       release-signing.sh sign KEY FILE
       release-signing.sh verify FILE PUB...
EOF
    exit 2
}

need_tools() {
    command -v sha256sum >/dev/null || die 2 'sha256sum is missing: install coreutils'
    command -v openssl >/dev/null || die 2 'openssl is missing: OpenSSL 3.5 or newer is needed'
    local listed algorithm
    listed=$(openssl list -signature-algorithms 2>/dev/null) || die 2 'openssl cannot list its signature algorithms'
    for algorithm in "${ALGORITHMS[@]}"; do
        grep -qw -- "$algorithm" <<<"$listed" ||
            die 2 "$(openssl version) has no $algorithm: OpenSSL 3.5 or newer is needed"
    done
}

# passphrase new|old: FSBPYR_SIGNING_PASSPHRASE, or asked for on the terminal (twice for a new key).
passphrase() {
    [[ -z "${FSBPYR_SIGNING_PASSPHRASE-}" ]] || return 0
    { : </dev/tty; } 2>/dev/null || die 2 'no passphrase: set FSBPYR_SIGNING_PASSPHRASE or run this on a terminal'
    local again
    read -rsp 'Passphrase: ' FSBPYR_SIGNING_PASSPHRASE </dev/tty; echo >&2
    if [[ "$1" == new ]]; then
        read -rsp 'Passphrase again: ' again </dev/tty; echo >&2
        [[ "$again" == "$FSBPYR_SIGNING_PASSPHRASE" ]] || die 2 'the passphrases differ'
    fi
    [[ -n "$FSBPYR_SIGNING_PASSPHRASE" ]] || die 2 'the passphrase is empty'
    export FSBPYR_SIGNING_PASSPHRASE
}

fingerprint() { local digest; digest=$(sha256sum <"$1"); printf 'SHA256:%s\n' "${digest%% *}"; }

# split_pem FILE FIRST REST: FILE's first PEM block, and what follows it.
split_pem() {
    sed -n '1,/^-----END /p' "$1" >"$2"
    sed '1,/^-----END /d' "$1" >"$3"
}

# check_public PUB DIR: PUB holds exactly the two public keys, Ed25519 then ML-DSA-65, as openssl
# writes them. DIR is scratch space.
check_public() {
    local pub=$1 dir=$2 i
    [[ -f "$pub" && -r "$pub" ]] || die 2 "cannot read the public key $pub"
    split_pem "$pub" "$dir/p.0" "$dir/p.1"
    : >"$dir/canonical"
    for i in 0 1; do
        [[ "$(openssl pkey -pubin -in "$dir/p.$i" -text_pub -noout 2>/dev/null | sed -n 1p)" == "${ALGORITHMS[i]} Public-Key:" ]] ||
            die 2 "$pub: key $((i + 1)) is not an ${ALGORITHMS[i]} public key"
        openssl pkey -pubin -in "$dir/p.$i" -pubout >>"$dir/canonical"
    done
    [[ "$(sha256sum <"$dir/canonical")" == "$(sha256sum <"$pub")" ]] ||
        die 2 "$pub holds more than the two public keys"
}

# statement FILE OUT: the statement that both signatures sign.
statement() {
    local digest
    digest=$(sha256sum <"$1") || die 2 "cannot read $1"
    printf 'fsbpyr-signature 1\nsubject %s\nsha256 %s\n' "$SUBJECT" "${digest%% *}" >"$2"
}

# verify_with STATEMENT SIG PUB DIR: both signatures in SIG are good for the two keys in PUB.
verify_with() {
    local i
    split_pem "$3" "$4/v.0" "$4/v.1"
    head -c "${SIG_SIZES[0]}" "$2" >"$4/vs.0"
    tail -c "${SIG_SIZES[1]}" "$2" >"$4/vs.1"
    for i in 0 1; do
        openssl pkeyutl -verify -rawin -pubin -inkey "$4/v.$i" -in "$1" -sigfile "$4/vs.$i" >/dev/null 2>&1 ||
            return 1
    done
}

# place TMP FINAL MODE: TMP gets the name FINAL, which must not exist.
place() {
    [[ ! -e "$2" && ! -L "$2" ]] || die 2 "$2 exists; nothing replaced"
    chmod "$3" "$1"
    ln -- "$1" "$2" || die 2 "cannot write $2"
}

new_key() {
    local name=$1 file i
    for file in "$name.key" "$name.pub"; do
        [[ ! -e "$file" && ! -L "$file" ]] || die 2 "$file exists; nothing replaced"
    done
    passphrase new
    tmp=$(mktemp -d "$(dirname -- "$name")/.release-signing.XXXXXXXX")
    : >"$tmp/key"
    : >"$tmp/pub"
    for i in 0 1; do
        # Generated into a pipe, so the unencrypted key is never written to disk.
        openssl genpkey -algorithm "${ALGORITHMS[i]}" |
            openssl pkcs8 -topk8 -v2 aes-256-cbc -v2prf hmacWithSHA256 -iter 600000 \
                -passout env:FSBPYR_SIGNING_PASSPHRASE -out "$tmp/k.$i"
        openssl pkey -in "$tmp/k.$i" -passin env:FSBPYR_SIGNING_PASSPHRASE -pubout >>"$tmp/pub"
        cat "$tmp/k.$i" >>"$tmp/key"
    done
    check_public "$tmp/pub" "$tmp"
    place "$tmp/key" "$name.key" 600
    place "$tmp/pub" "$name.pub" 644
    printf 'wrote %s (keep it secret) and %s\nfingerprint %s\n' "$name.key" "$name.pub" "$(fingerprint "$name.pub")"
}

sign() {
    local key=$1 file=$2 sig=$2.sig i
    [[ -f "$key" && -r "$key" ]] || die 2 "cannot read the key $key"
    [[ -f "$file" && -r "$file" ]] || die 2 "cannot read $file"
    [[ ! -e "$sig" && ! -L "$sig" ]] || die 2 "$sig exists; nothing replaced"
    [[ "$(grep -c '^-----BEGIN ENCRYPTED PRIVATE KEY-----$' "$key")" == 2 ]] ||
        die 2 "$key does not hold two encrypted keys"
    passphrase old
    tmp=$(mktemp -d "$(dirname -- "$sig")/.release-signing.XXXXXXXX")
    statement "$file" "$tmp/statement"
    split_pem "$key" "$tmp/k.0" "$tmp/k.1"
    : >"$tmp/pub"
    for i in 0 1; do
        openssl pkey -in "$tmp/k.$i" -passin env:FSBPYR_SIGNING_PASSPHRASE -pubout >>"$tmp/pub" 2>/dev/null ||
            die 2 "cannot open $key: wrong passphrase?"
        openssl pkeyutl -sign -rawin -inkey "$tmp/k.$i" -passin env:FSBPYR_SIGNING_PASSPHRASE \
            -in "$tmp/statement" -out "$tmp/s.$i"
    done
    check_public "$tmp/pub" "$tmp"
    cat "$tmp/s.0" "$tmp/s.1" >"$tmp/sig"
    verify_with "$tmp/statement" "$tmp/sig" "$tmp/pub" "$tmp" ||
        die 2 'the new signature does not verify; nothing written'
    place "$tmp/sig" "$sig" 644
    printf 'signed %s as %s with key %s\nwrote %s\n' "$file" "$SUBJECT" "$(fingerprint "$tmp/pub")" "$sig"
}

verify() {
    local file=$1 sig=$1.sig pub size
    shift
    [[ -f "$file" && -r "$file" ]] || die 2 "cannot read $file"
    [[ -f "$sig" && -r "$sig" ]] || die 1 "NOT VERIFIED: $file: no $sig"
    size=$(wc -c <"$sig")
    ((size == SIG_SIZES[0] + SIG_SIZES[1])) ||
        die 1 "NOT VERIFIED: $file: $sig has $((size)) bytes, not the $((SIG_SIZES[0] + SIG_SIZES[1])) of a signature"
    tmp=$(mktemp -d)
    statement "$file" "$tmp/statement"
    for pub in "$@"; do
        check_public "$pub" "$tmp"
        if verify_with "$tmp/statement" "$sig" "$pub" "$tmp"; then
            printf 'VERIFIED  %s  (%s, Ed25519 + ML-DSA-65, %s %s)\n' "$file" "$SUBJECT" "$pub" "$(fingerprint "$pub")"
            return 0
        fi
    done
    die 1 "NOT VERIFIED: $file: $sig does not match $*"
}

tmp=
trap '[[ -z "$tmp" ]] || rm -rf -- "$tmp"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

case "${1-}:$#" in
    new-key:2) need_tools; new_key "$2" ;;
    sign:3) need_tools; sign "$2" "$3" ;;
    verify:*) (($# >= 3)) || usage; need_tools; shift; verify "$@" ;;
    *) usage ;;
esac
