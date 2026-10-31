# Signed fsbpyr releases and signed apps: concept

Status: implemented on branch `signing-openssl`, as §14 says, with the release keys K and R pinned.
On 2026-10-31 the owner moved signing off GitHub, after a stolen account was played against the
first design (§14): K never reaches GitHub, and §5 describes that. Before the first signed release,
§5.2 steps 3 and 5 are still the admin's. The concept was written
on 2026-10-29 at fadc3d9, revised the same day after an independent review, and on 2026-10-30 after
a test round of every open gap (§11.1). It replaces the earlier concept on branch `signing-concept`
(SSHSIG signatures embedded in the executable), which is dropped.

Facts without a mark were run or read while this concept was made. "Measured" for fsbpyr's part
means a scratch program or a stand-in, on the bundled libcrypto or on fadc3d9's code, since none of
it is implemented. **[A]** marks an assumption that nobody has checked; §11.2 lists those still
open, and the facts that were only read where a run would settle them. **[Pn]** marks a choice
made in the test round of 2026-10-30, which the owner accepted on 2026-10-30; §13 lists each with
the alternatives it was weighed against.

## 1. Summary

- **Two signatures, always both.** Ed25519 and ML-DSA-65 sign one short statement that holds the
  file's SHA-256. A check passes only if both are good:
  - ML-DSA-65 guards against a future quantum computer;
  - Ed25519 guards against a flaw in the young ML-DSA code.

  The pair costs the user nothing: no extra step, 3.3 KB per signature.
- **Detached.** The signature is `FILE.sig`, next to FILE. Whoever wants to check keeps the two
  together.
- **fsbpyr does the crypto itself.** It runs in launcher C (`_fsbpyr`), through the OpenSSL that
  every fsbpyr already carries: 3.5.9 in all eight PBS pins at fadc3d9, x86-64 and aarch64
  (runtime/pins.py:30-95). Checks need nothing installed and give the same results on every
  distribution fsbpyr runs on. The system's openssl.cnf is still read, but one that loads no code
  changes no check's result (§4). Python reads files, asks for passphrases and prints.
- **fsbpyr's releases.** Each release signs one `SHA256SUMS`, and the EL8 companion release signs
  `SHA256SUMS-glibc2.28`.
  - GitHub builds and attests everything, and stops at a draft. The admin checks the draft
    against their own commit, signs on their own machine with the release key K, using an openssl
    script, and uploads only the signature.
  - fsbpyr pins the public keys of K and of the recovery key R. Neither key ever reaches GitHub.
  - An audit from the admin's machine finds a tampered account or repository, so that the admin
    is the first to know, and no release goes out until it passes.
- **Apps.** `fsbpyr build` signs when a key is set. If the key cannot be used, it prints a warning
  and builds unsigned. Users check with `fsbpyr verify --project`. How publishers hand out their
  keys, and how users keep them, is out of scope.
- **Trust.** The first fsbpyr a user installs is trusted as GitHub serves it. Each later fsbpyr is
  checked by the one before it (`self update`).

## 2. Formats

The script and fsbpyr write and read exactly these files. The script's `.pub` and `.sig` were read
and checked with the bundled libcrypto through EVP: `PEM_read_bio_PUBKEY_ex` twice on one file and
the canonical check of §2.3, then `EVP_DigestVerify` on each half (measured; §4).

### 2.1 Statement

```
fsbpyr-signature 1
subject <project>
sha256 <the file's SHA-256 in lowercase hex>
```
- ASCII with LF line ends, ending in an LF.
- `subject` is the project name in PEP 503 normal form: `[a-z0-9]+(-[a-z0-9]+)*`, matched against
  the whole name (`re.match` with `$` takes `myapp\n`, measured), 1–64 characters.
  - `fsbpyr` is reserved: only K and R sign it, through the script, and `key trust` refuses it.
    `sign` refuses the subject `fsbpyr`, from build info or from `--project` (exit 2). An app of
    project fsbpyr (a directory or script of that name gives one, loose.py:366-369) cannot be
    signed: `build` with a key warns and builds unsigned (§3.1) [P8].
  - A name longer than 64 characters cannot be signed either: `sign` exits 2, and `build` with a
    key warns and builds unsigned [P7]. All 926,724 PyPI names have the form above once
    normalized, and 143 are longer than 64 characters (measured, 2026-10-30). clean_name sets no
    limit.
- **A name the user types** (`--project`, `key trust PROJECT`) must be ASCII, is then PEP 503
  normalized (`My_App` is `my-app`), and must then have the form above; otherwise exit 2 [P6].
  ASCII comes first: `canonicalize_name` turns the Kelvin sign (U+212A) into `k` (measured).
- **A name read from build info** is used as it is: never normalized, and never replaced by the
  app name, as `fsbpyr inspect` does for a missing one (inspection.py:204-209). Without the form
  above, `verify` gives `NOT VERIFIED: FILE: its build info names no valid project` (exit 1), with
  `--project` too, and does not print the name; `sign` exits 2 [P6].
  - Only a name checked this way is joined into a path or printed.
  - Why: a forged executable can name anything, and D31 passes, because the forger writes the
    digest. Joined into the trust-store path unchecked, `../../../Downloads/x` reads
    `~/Downloads/x.pub`, which the same download can bring, and `/tmp/x` reads `/tmp/x.pub`, which
    any local user can make; `canonicalize_name` keeps `/tmp/x` as it is (measured with fsbpyr's
    writer and reader and a model of the path).
  - Honest builds record the normal form: `canonicalize_name` of the installed Name
    (closure.py:81), `clean_name` for scripts and directories (loose.py:366-369) [A: that uv
    refuses a Name outside PEP 508].
- The statement is never stored. The signer and the checker each build it from the project name
  and the file. `release-signing.sh` signs and checks as `fsbpyr` only (release-signing.sh:29);
  statements of other projects come from fsbpyr alone.
- It binds the project: a signature for project A fails for project B, even with the same key.
- It holds no version. Where fsbpyr needs a version, it reads it from the checked file's build
  info (§5.5).

### 2.2 Signature, `FILE.sig`

- Exactly 3373 bytes:
  - the Ed25519 signature, 64 bytes;
  - then the ML-DSA-65 signature, 3309 bytes.

  Both sign the statement, in pure mode, with no context string.
- Any other size is not a signature of this format. A later format must differ in size.
- The Ed25519 half is deterministic: the same each time, from C and from the script. ML-DSA-65
  signing is randomized: the same file signed twice gives different ML-DSA bytes, and both verify
  (measured).
- OpenSSL 3.5 can also sign ML-DSA deterministically (the signature parameter `deterministic` set
  to 1, `openssl pkeyutl -pkeyopt deterministic:1`): two such signatures were equal, and C on the
  bundled 3.5.9 and the 3.5.7 command gave the same bytes (measured). fsbpyr and the script sign
  randomized, and `_sign` is tested by checking what it returns (§11.3) [P16].

### 2.3 Keys

- `NAME.key`: two encrypted PKCS#8 PEM blocks (`ENCRYPTED PRIVATE KEY`) under one passphrase,
  Ed25519 then ML-DSA-65 (6088 bytes).
  - Each block is PBES2: PBKDF2 with hmacWithSHA256, 600,000 iterations and a 16-byte salt, then
    AES-256-CBC with a 16-byte IV. The ML-DSA-65 key holds its seed and its expanded key, OpenSSL
    3.5's default.
  - The script writes a block with `openssl pkcs8 -topk8 -v2 aes-256-cbc -v2prf hmacWithSHA256
    -iter 600000` (release-signing.sh:131-133). C writes it with `EVP_PKEY2PKCS8`, then
    `PKCS8_encrypt_ex(-1, AES-256-CBC, …, 600000, …)`, which takes hmacWithSHA256 for AES, then
    `PEM_write_bio_PKCS8`; not with `PEM_write_bio_PKCS8PrivateKey`, which writes 2048 iterations.
    Measured: both have the same structure and size, outside and inside the encryption (`openssl
    asn1parse`), and each signs with the other's key.
  - Readers take exactly two `ENCRYPTED PRIVATE KEY` blocks, Ed25519 then ML-DSA-65, each ending
    in an LF, with nothing before or between them and only LFs after them. The last LF may be
    missing, as a key copied through a secret may lose it. A CR anywhere refuses the file. Other
    key types are refused with a message that says so, not "wrong passphrase?" [P17]. OpenSSL's
    reader takes an unencrypted block and then ignores the passphrase (measured). The script does
    not follow this rule yet (§11.4).
  - The `openssl` command takes ML-DSA key formats from openssl.cnf (`ml-dsa.output_formats`,
    `ml-dsa.input_formats`): set to `seed-only`, the script wrote a 604-byte `.key`, and refused
    C's key (measured). So the script is to run openssl with `OPENSSL_CONF=/dev/null`, which gave
    6088 bytes; it does not yet (§11.4) [P17]. C's private context takes no provider or
    key-format setting from the file (§4): under the same config it wrote 6088 bytes. Readers do
    not check a `.key`'s size: C read the 604-byte one (measured).
- `NAME.pub`: the two public keys as PEM, in the same order, and nothing else (2839 bytes).
  - It is canonical: byte for byte what OpenSSL writes for its two keys. Readers refuse any other
    `.pub`: with anything else in it, bytes after a key inside a block, the keys in the other
    order, or other algorithms. In C: read two keys with `PEM_read_bio_PUBKEY_ex`, check them with
    `EVP_PKEY_is_a` (`ED25519`, then `ML-DSA-65`), write both again with `PEM_write_bio_PUBKEY_ex`
    and compare with the file's bytes.
  - Reading alone is not enough: of 19 bad files, OpenSSL's reader took 10, and the signature
    passed with them (an extra line, leading text, a third key, CRLF, a header line and base64 at
    76 columns among them). It also reads a block whose DER has a byte after the key. C's check and
    the script refused all 19 with exit 2; the script also refused both trailing-byte files
    (measured).
- Fingerprint: `SHA256:` followed by the SHA-256 of the `.pub` file, in lowercase hex.
- Passphrase [P4]:
  - `FSBPYR_SIGNING_PASSPHRASE`, otherwise asked for on the terminal, `/dev/tty`. It is never read
    from standard input. Python's getpass would read it there when there is no terminal, with a
    warning (measured).
  - An empty variable counts as unset, and an empty passphrase is refused, as in the script.
  - 1 to 1024 bytes: OpenSSL's PEM reader takes no longer one, in the 3.5.7 command and in the
    bundled 3.5.9 (measured). `key new` refuses a longer one before it makes keys.
  - It is bytes. From the variable, fsbpyr takes `os.environb`'s: a key made by the script under a
    passphrase that is not UTF-8 opened in C with the same bytes, and `os.environ`'s text of it
    does not encode as UTF-8 (measured). A typed one is encoded with `os.fsencode` [A].
  - Without a terminal and without the variable, `key new` and `sign` exit 2, and `build` warns
    and builds unsigned (§3.1).

## 3. Commands

| Command | What it does |
|---|---|
| `fsbpyr key new NAME` | Makes both keys, writes `NAME.key` (mode 600) and `NAME.pub`, and prints the fingerprint. Asks for the passphrase twice unless `FSBPYR_SIGNING_PASSPHRASE` is set. Replaces nothing. |
| `fsbpyr key trust PROJECT PUBKEY FINGERPRINT [--replace]` | Checks PUBKEY and FINGERPRINT and saves PUBKEY as PROJECT's key (rules below). A different saved key is replaced only with `--replace`. |
| `fsbpyr sign FILE [--project P] [--signing-key KEY]` | Writes `FILE.sig`, after checking the new signature. Replaces nothing. |
| `fsbpyr verify FILE [--project P]` | Checks FILE and prints `VERIFIED` or `NOT VERIFIED`, with the project it checked FILE as. With `--project`, FILE must be P's. |

Rules shared by the commands:
- **Project.**
  - For an executable that fsbpyr built, the name comes from its build info.
  - For other files, `--project` is required: "FILE has no build info: name its project with
    --project".
  - A typed `--project` and a name read from build info are checked as §2.1 says.
  - `sign`: a `--project` that differs from the build info is an error (exit 2).
  - `verify`: a `--project` that differs from the build info is NOT VERIFIED (exit 1): the file
    is another project's. Without `--project`, `verify` checks FILE as the project its build info
    names, so a genuine file of any project the user trusts passes. Whoever expects a certain app,
    a script above all, passes `--project`.
- **Signing key.** `--signing-key KEY`, otherwise `FSBPYR_SIGNING_KEY` (a path).
  - It has no `[tool.fsbpyr]` key, like `--offline` and the other options that exist on the
    command line only (User-Settings.md:47-48): the key path belongs to the machine that builds
    [P24].
  - A `signing-key` key in the table is an unknown key and stops the build (config.py:157-163).
- **Checking key.**
  - For `fsbpyr`: the keys this fsbpyr pins (K and R). A `trusted/fsbpyr.pub` is never read.
  - For any other project: the key that `key trust` saved, in
    `${XDG_CONFIG_HOME:-~/.config}/fsbpyr/trusted/<project>.pub`, with `<project>` checked as
    §2.1 says.
  - A relative `XDG_CONFIG_HOME` is ignored, and so is a relative `HOME`, as for the cache
    (runtime/registry.py:90-111). Otherwise a download unpacked in the current directory could
    bring its own `fsbpyr/trusted/` (measured with a model of the path and
    `XDG_CONFIG_HOME=cfg`) [P12].
- **`key trust`** [P9].
  - PROJECT is a typed name (§2.1). `fsbpyr` is refused, also when typed as `FSBPYR`: "the keys
    of fsbpyr are pinned in fsbpyr itself".
  - FINGERPRINT is `SHA256:` and all 64 hex digits; upper-case digits count as lower-case.
    Anything else, a shortened fingerprint too, is a usage error: "not a fingerprint: give
    SHA256: and all 64 hex digits".
  - PUBKEY is read once, at most 2840 bytes, into a new temporary file in the trust store. The
    checks run on that copy: 2839 bytes, the two keys as §2.3 says (`_verify`, §4), and its
    fingerprint by `_sha256` of the copy. Then the copy is renamed into place, so the file saved
    is the file checked. Every `.pub` the script made has 2839 bytes (measured, five keys).
    Hashing PUBKEY by its path and then copying it saves a file swapped in between (measured in
    a Python model).
  - The same key already saved: nothing changes, exit 0.
  - Another key saved: without `--replace`, nothing changes: "myapp has another trusted key,
    SHA256:…; give --replace to replace it" (exit 2). With `--replace`, the rename replaces it,
    and both fingerprints are printed. With no key saved, `--replace` saves as it does without.
  - Exit 0 when saved or already saved, 2 otherwise, a wrong fingerprint too. 1 means NOT
    VERIFIED only, as in the script.
- **Which signature.**
  - FILE's symlinks are followed first. `verify` then works on the file FILE leads to: its
    `.sig`, its sums file, and its name in the line. A command link is a symlink, both the
    build's (links.py:76) and `FSBPYR_INSTALL_LINKS`'s (boot/_fsbpyr_rt/links.py:51), so it is
    checked as its executable [P11]. The script does not follow links: for a symlink whose
    target has its `.sig`, it says `NOT VERIFIED: link: no link.sig` (measured).
  - `FILE.sig`. A hard link or a copy needs a `.sig` under its own name. The `.sig` names no
    file, so a copy of it serves (measured with the script).
  - Without one: a signed sums file beside FILE (`SHA256SUMS` or `SHA256SUMS-glibc2.28`, with its
    `.sig`) that lists FILE's SHA-256. The sums file is checked as a file of the project, then FILE
    is looked up by its SHA-256, so its local name does not matter. Its lines are read as
    runtime/stubs.py:164-167 reads them.
  - So every file the sums file lists passes, under any name. In v0.28.1's `SHA256SUMS`,
    `install.sh` saved as `fsbpyr-3.14-x86_64-linux-gnu` is found as `install.sh`, and a licence
    file is listed under four names (measured with a model of the lookup on the real file). An
    older release's file passes with that release's sums file. So the VERIFIED line names what
    FILE is listed as, every name if there are several, and whoever expects a certain asset
    compares that name [P10].
  - This is how a user checks fsbpyr's downloads by hand (§7). fsbpyr's own download paths take
    their asset's own line, and `self update` and dispatch also check `release_asset` and the
    version (§5.5).
- **Exit status.**
  - 0: VERIFIED.
  - 1: NOT VERIFIED.
  - 2: usage or setup errors: no trusted key, an unreadable key file, a wrong passphrase, a typed
    name that §2.1 refuses.

| `fsbpyr verify` prints | Exit |
|---|---|
| `VERIFIED  myapp  (project myapp, Ed25519 + ML-DSA-65, key SHA256:…)` | 0 |
| `VERIFIED  myapp  (project myapp, listed as myapp-1.2-x86_64 in SHA256SUMS, Ed25519 + ML-DSA-65, key SHA256:…)` [P10] | 0 |
| `NOT VERIFIED: myapp: the signature does not match the trusted key of myapp` | 1 |
| `NOT VERIFIED: myapp: it is an executable of project otherapp, not of myapp` | 1 |
| `NOT VERIFIED: myapp: its build info names no valid project` [P6] | 1 |
| `NOT VERIFIED: myapp: no myapp.sig, and no signed SHA256SUMS beside it lists it` | 1 |
| `NOT VERIFIED: myapp: myapp.sig is not a signature (N bytes)` | 1 |
| `no trusted key for myapp: get the publisher's key and its fingerprint, then: fsbpyr key trust myapp KEY.pub SHA256:…` | 2 |

Names in these lines come from files. The project is printed only after §2.1's check. FILE and
the names a sums file lists are printed as `fsbpyr inspect` prints values: repr() when not
printable (inspection.py:80-82), so a terminal escape in a name cannot rewrite a line [P12].

A pass means that the holder of the trusted key signed exactly this file, as the project it
prints. It does not mean that the app is safe, that it is the newest version, or that the key
belongs to whom the user thinks.

### 3.1 `fsbpyr build`

- **A signing key is set** (`--signing-key` or `FSBPYR_SIGNING_KEY`): after the smoke test, the
  build signs the output as `fsbpyr sign` does and writes `<output>.sig` beside it:
  `dist/<name>.sig`, or `PATH.sig` with `-o PATH`, unless that path is the user's symlink (below).
  - `<output>` is the file the build writes. Where `dist/<name>` or `-o PATH` is a symlink of the
    user's, that is the file the link leads to (build.py:797, :122-129; User-Building.md:299),
    and the `.sig` goes beside that file. Measured with fadc3d9's `_output_path`:
    `-o bin/myapp`, with `bin/myapp -> ../opt/myapp-1.2`, writes `opt/myapp-1.2`. Command links
    get no `.sig`: `verify` follows them (§3) [P11].
  - A command named `<output>.sig` fails the build before anything is written, with a key or
    without: its link would take the signature's path (links.py:15 allows the name; measured:
    `dist/multi.sig -> multi`) [P11].
- **The passphrase and the build's processes.** The build starts processes that run third-party
  code as the user: uv (staging.py:303), the build backends of dependencies, and the new app in
  the smoke test (build.py:634 passes it the whole environment).
  - fsbpyr reads `FSBPYR_SIGNING_KEY` and `FSBPYR_SIGNING_PASSPHRASE` once, before it reads the
    project (cli.py:204), and removes both from its environment before it starts any process.
    Its processes then lack them in their own environments, so a process that prints or logs
    its environment shows neither (measured with stand-ins).
  - That keeps the passphrase from code that does not look for it, not from code that does.
    Code the build starts runs as the user and can (measured with stand-ins on a Debian 13 host
    with `kernel.yama.ptrace_scope` 0, not with fsbpyr):
    - read the variable in /proc/PID/environ of fsbpyr, which shows the environment fsbpyr
      started with, and of the process that started fsbpyr. A CI step's shell has the step's
      `env:` there (bash started as GitHub starts a run step). No Yama scope guards
      /proc/PID/environ (Linux 6.12: fs/proc/base.c:996, security/yama/yama_lsm.c:362);
    - read fsbpyr's memory, which holds the passphrase until it signs, where
      `kernel.yama.ptrace_scope` is 0: Debian 13's default; almalinux:9 ships 0; ubuntu:24.04
      sets 1;
    - read the terminal when the passphrase is typed: a process left running got the typed line
      in 5 of 5 runs through /dev/tty, and in 5 of 5 by the terminal's /dev/pts name from a
      session of its own, and fsbpyr's prompt got nothing;
    - read the variable at a later fsbpyr start, from a process left running (10 of 10 starts);
    - read the key file, as the user can.
  - Not done: making fsbpyr non-dumpable (`prctl(PR_SET_DUMPABLE, 0)`) closes only its own
    environ and memory, and its multiprocessing children would then fail the boot code's parent
    check, which stats /proc/PPID/exe (mp.py:150-163; the stat is refused, measured). An
    in-place wipe of the environment, or a re-exec without the variables, stops none of the
    other ways [P1].
  - The launcher's collection child (main.c:143, due.c:175-191) starts before Python, with the
    whole environment, and may outlive the build. It drops both variables before its exec [P2].
  - So a publisher who builds code they do not trust signs in a separate job, on a machine or
    account the build never ran on (§6). On the same machine and account, signing later does not
    help against a process the build left running (above). `FSBPYR_SIGNING_PASSPHRASE` is meant
    for jobs without a terminal: set on a shell's command line, it stays in the shell's memory
    and in its history file (measured with bash).
  - Without the variable, the passphrase is asked for after the smoke test. That keeps it from
    processes that have ended, not from one left running (above).
- **Another Python: dispatch.** When the project builds with another Python, the fsbpyr that was
  started builds nothing. It runs this release's variant for that Python with the same
  arguments, in the same directory, and returns its exit status (cli.py:228-231,
  dispatch.py:94-107). The variant builds, signs and warns.
  - The fsbpyr that dispatches removes both variables at start, as any build does, before it
    starts any process. It does start some: for a directory of Python files it runs
    `git ls-files` (vcs.py:92, :95), which runs the project's `core.fsmonitor` command
    (measured).
  - It puts both values into the variant's environment, and into no other (dispatch.py:103). The
    variant is this release's own fsbpyr (dispatch.py:35-47, :81), checked as §5.5 says when it is
    fetched. The dispatcher does not open the key, so a key that cannot be used gives one warning,
    the variant's [P3].
  - `--signing-key` passes unchanged among the arguments; a relative path names the same file.
    The variant asks for a typed passphrase after its own smoke test, and it has the terminal
    (measured on a pty: a passphrase with spaces was read, and echo was on again after Ctrl-C).
  - Both fsbpyr hold the values with the limits above: a process the variant started read the
    passphrase in /proc/PID/environ of both (measured with stand-ins). Without the hand-over,
    the variant builds unsigned with no warning (measured with fadc3d9's `cli.main` and
    `dispatch.run`).
  - Only `build` dispatches. `key`, `sign` and `verify` give the same result in every variant.
    `self update` never moves to another Python (cli.py:413-419).
- **The key cannot be used** (missing file, not a key file, wrong or missing passphrase), or the
  project cannot be signed (§2.1): the build prints a warning, finishes unsigned, and exits 0.
  Signing is optional for apps:
  ```
  fsbpyr: WARNING ****************************************************************
  fsbpyr: WARNING  dist/myapp is NOT SIGNED: cannot open /home/me/keys/myapp.key:
  fsbpyr: WARNING  wrong passphrase? Unset FSBPYR_SIGNING_KEY to build unsigned
  fsbpyr: WARNING  without this warning.
  fsbpyr: WARNING ****************************************************************
  ```
  An empty `FSBPYR_SIGNING_PASSPHRASE` counts as unset, and without a terminal the passphrase is
  missing (§2.3). Ctrl-C at the passphrase prompt stops the build with exit 130; the output
  stays, with no `.sig`. End of input at the prompt counts as a missing passphrase [P5].
- **No key is set:** nothing is signed and nothing is printed about it.
- **Stale signatures:** the build removes an old `<output>.sig` before the new executable
  replaces the output (build.py:696), and puts the new `.sig` in place after it. An interrupted
  or killed build never leaves a `.sig` beside a file it was not made for. That matters today: a
  dispatching fsbpyr kills its variant soon after Ctrl-C, a bug outside signing (§11.4).
- **fsbpyr's own release** is never signed by `fsbpyr build` (§5.3). For fsbpyr, a signature that
  fails stops the release.

## 4. Inside fsbpyr: C and Python

- `_fsbpyr` gains four private, undocumented functions, on the bundled OpenSSL 3.5.9:
  - `_sha256(fd)`: the file's SHA-256, in C, so no Python hashing sits on these paths. It uses the
    low-level `SHA256_Init`, `SHA256_Update` and `SHA256_Final` and the context type of the
    start-up check (integrity.c:22-28, integrity.h:16-20), which read no openssl.cnf, and is as
    fast as EVP: 7.3 ms per 30 MB (measured). It also takes bytes, for a `.pub` held in memory:
    the pinned keys, whose fingerprints `verify` prints [P13].
  - `_key_new(passphrase)`: both keys, returned as the `.key` and `.pub` bytes, written as §2.3
    says.
  - `_sign(key_pem, passphrase, statement)`: the 3373-byte signature and the key's `.pub` bytes,
    for the fingerprint that `sign` prints. It refuses a `.key` that §2.3 refuses, and checks the
    new signature against that `.pub` before it returns [P13].
  - `_verify(pub_pem, statement, signature)`: first checks `pub_pem` as §2.3 says (the types in
    order, re-encoded and compared) and raises ValueError for one it refuses. Then False for any
    size but 3373, and True only if both halves are good. `key trust` checks a PUBKEY with
    `_verify(pub, b"", b"")` [P13].
- The passphrase reaches OpenSSL through a callback of sig.c that returns the given bytes and
  their length, never as NULL [P13]. Given neither, OpenSSL asks on the terminal itself ("Enter
  PEM pass phrase:"): on a pty it read the typed passphrase and opened the key, and without a
  terminal it still printed the prompt (measured). An empty passphrase given through the callback
  is refused, with no prompt (measured). Python passes a non-empty passphrase or does not call.
- Python handles the arguments, the files, the statement, the passphrase (§2.3), the trust store
  and the messages.
- **Context.** Each call of `_key_new`, `_sign` and `_verify` makes its own context with
  `OSSL_LIB_CTX_new()` and frees it before it returns. It loads no config into it, calls no
  `OPENSSL_init_*`, and never passes the default context (NULL). That costs about 0.4 ms per
  check: 0.53 ms, against 0.16 ms in a kept context (measured) [P13].
- **openssl.cnf.** The private context keeps the config from changing a check's result, not from
  being read:
  - The first EVP call of a process, in a private context too, reads `$OPENSSL_CONF`, else
    /etc/ssl/openssl.cnf (PBS's OPENSSLDIR), into the default context: the engine lookup loads it
    (eng_table.c:207-210 in 3.5.9; measured with strace). CPython reads it at `import hashlib` and
    `import urllib.request`, which the build and the download paths import (assemble.py:4,
    selfupdate.py:26).
  - 12 configs, each with 4 pre-inits (none, `OPENSSL_init_crypto`, `SSL_CTX_new`,
    `EVP_MD_do_all_provided`): none, `fips=yes`, only the base provider, a missing provider and a
    syntax error (each with and without `config_diagnostics = 1`), `provider=legacy`, a missing
    `.include`, the rdrand engine, an engine that cannot be loaded, OPENSSL_MODULES at an empty
    directory. In a private context, fresh or kept, no result changed. The default context failed
    closed under 6: it refused good signatures and the right passphrase, and passed no bad
    signature (measured).
  - `OPENSSL_INIT_NO_LOAD_CONFIG` is no substitute: it holds for the whole process, and after a
    hashlib-like init it changes nothing (measured).
  - A config can load code: an engine's `dynamic_path` ran from a private context's first call.
    Without loading code, the built-in rdrand engine becomes the RAND method of the whole process,
    which `_key_new` and `_sign` then draw from; checks are not affected (measured). Both are the
    user's environment (§8). An ENGINE set as the default for digests or signatures would take
    over a private context's operations [A: read in OpenSSL's source, not tried].
- **Error queue.** OpenSSL keeps one error queue per thread, shared with CPython's _ssl and
  _hashlib. A failed call leaves 1–4 errors in it, and a pass by one pinned key after another
  failed still leaves one (measured).
  - CPython does not empty it before `SSL_read_ex` or `SSL_write_ex` (_ssl.c:2686, :2529 in
    3.14.10). In a CPython 3.14.10 linked as a stub, a failed check between `urlopen()` and
    `read()` made `read()` raise `SSLError` (measured). The paths of §5.5 check after the response
    is read and closed, so they would not hit this even without the mark below [A].
  - So each function calls `ERR_set_mark()` before its first OpenSSL call and `ERR_pop_to_mark()`
    on every return, which leaves the queue as it was found (measured) [P13]. On an empty queue
    `ERR_set_mark()` returns 0 and sets no mark; that is no error (err_mark.c:15-25 in 3.5.9).
  - Results come from return values only. No OpenSSL text reaches Python.
- **Declarations.** PBS ships no OpenSSL headers (integrity.h:16), nor do the sysroots
  (runtime/pins.py) or the suite's image; the prototypes used the host's 3.5.7 headers. sig.c
  declares what it uses by hand, as integrity.c:22-28 does: in a scratch sig.c, 36 functions, 11
  opaque types, the callback type and one constant, `BIO_CTRL_INFO` [P14]. Its test-only
  deterministic option, which P16 leaves out, adds the `OSSL_PARAM` layout and two constants.
  - `BIO_get_mem_data` is a macro, written as `BIO_ctrl(bio, 3, 0, &data)`. `EVP_PKEY_Q_keygen`
    is variadic, so keys come from `EVP_PKEY_CTX_new_from_name`, `EVP_PKEY_keygen_init` and
    `EVP_PKEY_generate`. No NID is needed (§2.3).
  - They agree with the 3.5.7 headers: sig.c compiled with them included, under `-Werror`, and a
    `const` dropped from one declaration failed as conflicting types (measured). The suite
    repeats that compile with Debian's libssl-dev 3.5 (§11.3).
  - Not vendored headers: `evp.h`, `pem.h`, `pkcs12.h` and `err.h` pull in 53 headers, 25,977
    lines; 28 of OpenSSL's headers are made from templates, `configuration.h` from the build's
    Configure options.
- **Measured** with that scratch sig.c, built with `-Wall -Wextra -Werror -Wvla
  -Wframe-larger-than=8192` by gcc 14 and clang 23 against the PBS `libcrypto.a`
  (cpython-3.14.10+20261026): keys, signatures and checks with the script in both directions, the
  same under ASan and UBSan; 152 lines of functions and helpers, 66 of declarations (that option's
  `OSSL_PARAM` layout included) and 81 of Python wrappers, about 300. A C test of keygen, sign
  and verify, and the independent vectors of §11.3, passed against the `libcrypto.a` of all eight
  pins: natively for x86-64, and for aarch64 under qemu-user in the suite's debian:trixie image,
  arm64.
- `_fsbpyr` is in every app fsbpyr builds, so these functions are too. They stay private.
  - The stub grows by 4,416 bytes (20,998,008 to 21,002,424), linked with stub.py's own command
    for PBS 3.14.10 x86-64, and not run (measured). Their names are in the cached 0.25.0 and
    0.27.0 stubs, and sig.c adds only those 4,416 bytes, so the ML-DSA, PBES2 and PEM code is
    taken to be there already [A: that _ssl and _hashlib pull it in].
  - sig.c joins LAUNCHER_SOURCES and LAUNCHER_FILES (stub.py:16-41), so every runtime id changes
    (stub.py:126-135), as with any launcher change.
  - The PEM bytes go on the heap: a stack frame over 8192 bytes fails the stub's link
    (stub.py:220-223), as two 8 KiB buffers on the wrappers' stack did (16,600 and 11,912 bytes,
    measured).
  - The wrappers stay in sig.c with their own method table; module.c (837 lines) adds that table
    to its own [P15].
- **From source**, without `_fsbpyr`, the commands fail with "signing needs a built fsbpyr".
  signing.py reaches the four functions through one function of its own, and the pins through
  another, so that tests can replace them (§11.3) [P25].
  - From source, dispatch runs a variant already in the cache but never fetches one: where it
    would download, it stops with the message of a developer build (dispatch.py:41-46) [P25]. A
    checkout at a tag is a release to it (vcs.py:115): from source in a checkout tagged v1.2.0,
    `own_version()` is 1.2.0-7f9499e5 and dispatch takes the tag v1.2.0 (measured), so today it
    fetches the variant (dispatch.py:50-70).
  - The EL8 stub download runs only in an executable (registry.py:322-331), and `self update`
    refuses a checkout already (selfupdate.py:82-92).
- An app does not check its own signature: whoever can change the file can remove the check.

## 5. fsbpyr's releases

### 5.1 Keys

The account on GitHub is what may be lost, and whoever holds it can run any workflow with whatever
it can reach. The play of §14 showed it: with K in a GitHub environment, a backdoored commit on a
pushed tag went through the unchanged pipeline and K signed it. So K is not on GitHub at all, not
even for a moment: a copy taken there stays valid until K is replaced, and nothing the admin can
read says it was taken.

- **K**, the release key, signs on the admin's machine, for every release. Only the `.sig` goes to
  GitHub.
- **R**, the recovery key, stays offline. It signs like K, through the same command (§5.4).
- **Pins.** Each `.pub` file in src/fsbpyr/release-keys/ is packaged into fsbpyr, and a signature by
  any of them passes. Normally these are k.pub and r.pub. For one release during a change of K
  there are three (§5.4).
- **The pins are what main holds when the admin tags it**, like every other file, and K signs
  that. Nothing checks who added a pin (measured with the script, keys k, r and x):
  - release N pins k, r and an added x, and K signs N. Both checks of `make release-sign` pass,
    since k is in both sets of pins (§5.3);
  - sums signed by x then pass N's pins and fail those of the release before N. Every client that
    installs N accepts x's signature on later sums.

  What the admin pulls into main and tags is the admin's to know, and is out of this project's
  scope (owner, 2026-10-31). Against mistakes, the suite compares the pins with the fingerprints
  the wiki lists (§11.3).
- **Key files.** K is an encrypted file on a medium the admin plugs in to sign; R is apart from it,
  offline. `make release-sign` refuses a key inside the checkout, so a key is never kept in the
  repository, not even for a moment. fsbpyr stores no private key.

### 5.2 One-time setup

1. **Make the keys** with OpenSSL 3.5 or newer (Debian 13 has 3.5.7), R preferably on a machine
   that is offline. Each command asks for its own passphrase:
   ```
   bash tools/release/release-signing.sh new-key ~/release-k
   bash tools/release/release-signing.sh new-key ~/release-r
   ```
2. **Pin the keys.** Copy `release-k.pub` and `release-r.pub` to src/fsbpyr/release-keys/k.pub
   and r.pub, and commit them with the release that brings signing. Write that release's version
   into tools/release/first-signed-release (one line, `X.Y.Z`) [P20].
3. **Remove what can write.** On 2026-10-31 the admin is the only collaborator, and the GitHub App
   `chatgpt-codex-connector` holds write access to contents and actions in all of the
   organization's repositories (read with `gh api`). The admin removes its access to fsbpyr, or
   trusts it as they trust themselves: a write to main is what they would pull and tag.
4. **Keep the rulesets of 2026-10-29:**
   - `release tag creation`: only RepositoryRole 5, the admin role [A: the API shows only the id],
     creates `v*` and `el8-v*` tags;
   - `release tags`: nobody updates or deletes them.

   They stop nobody who holds the admin's account, and nothing here depends on them: such a tag
   gets a draft that nobody signs.
5. **Start the audit's record** on the admin's machine: read GitHub's state, then
   `bash tools/release/audit.sh --accept` (§5.7).
6. **Release.** The first signed release goes through §5.3 like every other. Its signature is
   checked against its own keys only. The previous release pins none, which only the version in
   tools/release/first-signed-release may (§5.3).

### 5.3 Release pipeline

Everything is built on GitHub, and attested there. The admin signs locally and pushes the `.sig`,
nothing else. This rests on one assumption: GitHub itself is sound and an account on it may be lost.
An account cannot get GitHub to attest a file for a commit that did not build it. If GitHub itself
is compromised, far more than fsbpyr is, and that is out of scope.

| Job | What it does |
|---|---|
| `build`, `sbom` | as before |
| `prepare` | `make-install.sh`, where the built fsbpyr reads the executables, then `SHA256SUMS`. Its token is read-only (`contents: read`) and it gets no secret. |
| `draft` | Runs no built code. It checks its files (below), attests them with `SHA256SUMS`, and makes the release as a draft, without a signature. |
| el8.yml's `prepare`, `draft` | The same for `SHA256SUMS-glibc2.28` and the stubs' `.xz`, in a draft of the companion release, with `--latest=false`. It waits for nothing: the release of the tag stays a draft until the admin signs it. A draft found there may be the one waiting for the signature, so a re-run leaves it alone and fails, and a stopped run's is deleted by hand. |

No workflow holds a key, reads a repository variable, or publishes: `gh release edit --draft=false`
is on the admin's machine only, and a test pins that for every workflow.

`draft` writes the sums again [P22]. `prepare` runs the built fsbpyr on a runner with passwordless
`sudo` (GitHub docs, read), so its files and artifacts are that code's to choose. So the build
job's files must be exactly the assets of every (Python, target), and `sha256sum` over them and
`prepare`'s `install.sh` must equal `prepare`'s `SHA256SUMS` (`cmp`). Every line but `install.sh`'s
then names a file of the build job, and a file too many stops the job. It does not stop a poisoned
build (§8): the build job ran the same code. el8.yml has no such step: its `prepare` runs no built
code.

`make release` tags main's HEAD, after the audit has passed, and CI makes the drafts. Then, on the
admin's machine, `make release-sign KEY=PATH`:

1. **Your commit.** It refuses unless HEAD, the local tag, GitHub's `main` and GitHub's tag are one
   commit, on a clean tree, and the key file is outside the checkout. A commit pushed to GitHub, or
   a moved tag, stops it here.
2. **The audit** (§5.7) must pass.
3. **The attestation.** It downloads the draft into an empty directory. Before anything reads the
   sums file, `gh attestation verify` must find GitHub's attestation of it, with `--source-digest`
   your commit, `--source-ref refs/tags/vX.Y.Z`, `--signer-workflow` this repository's release.yml
   (el8.yml for the companion) and `--deny-self-hosted-runners`. The commit is the point: comparing
   `main` alone says nothing about the draft's files, which an account can replace, and which a
   run of a commit you never saw can have built. Measured on GitHub's real attestations in the test
   repository: the genuine sums pass, and the sums of a backdoored commit, the wrong commit and the
   wrong workflow are each refused.
   **Where it was built.** `--deny-self-hosted-runners` speaks only for the job that attested, and
   that job only hashes what the jobs before it left. A runner the attacker registers with the
   label `ubuntu-24.04` might take a build job, if GitHub routes a hosted label to it [A], and the
   attestation would then be a true one, for your commit, of the attacker's bytes. So the command
   follows the attestation's `runInvocationURI` to the run and attempt, and reads the record of
   every job: each must have `runner_group_id` 0, or be skipped. 0 is what GitHub's own machines
   have (read on both repositories), and an organization's groups start at 1 (read:
   `Default` is 1). Every attestation of the sums file counts. Attesting in the build jobs instead
   would hand an OIDC token to the job that runs built code.
4. **The files.** The attested sums vouch for the rest. Every file of the draft must be listed
   there with the digest it has, every listed file must be there (for a companion, which publishes
   each stub's `.xz` only, the stubs themselves are not), and no signature may be on the draft yet.
5. **You.** It prints the release, the commit, the number of files and the digest of the sums, and
   asks.
6. **The signature.** `release-signing.sh sign`, which asks for K's passphrase on the terminal, then
   a check as the clients will, with `check-signature.sh`:
   - against the keys that this tag pins: this release's own fsbpyr, whose dispatch and EL8 stubs
     read this tag's sums;
   - against the keys that the previous release pins: the fsbpyr that updates to this release, and
     that reads this tag's EL8 sums during the update.
7. **Publishing.** It uploads the `.sig`, publishes the draft (`--latest=false` for a companion: a
   companion as the latest release breaks self update and install.sh), records the release (§5.7)
   and runs the audit again. A companion that comes later is signed by a second run.

   Whoever holds the account has as long as the prompts take. So the draft is read again before
   the upload and once more before publishing: the refs must still be your commit, and the tag
   must have exactly one draft, no prerelease, whose files are exactly what was checked, by name
   and digest. This is what stands for `install.sh`, which `curl | sh` runs and no client checks.
   It publishes by the draft's id: a tag can have two drafts, and `gh release edit` takes one by
   name. A change between the last read and the publication remains possible, for a moment; the
   audit that follows reports it, and clients refuse any executable that does not match.

   If a run stops after the upload, the `.sig` stays on the draft and the next run refuses it,
   saying how to remove it: it cannot know that the signature is its own.

**The previous release** is the published release `vX.Y.Z` with the highest version below this
tag's [P20]:
- published: listed by `GET /repos/devitops-com/fsbpyr/releases`, every page (`per_page=100`);
  neither a draft nor a prerelease. `self update` takes the latest release from the same API;
- only names `vX.Y.Z` count, so never an `el8-v…` tag. Versions compare as numbers (`sort -V`):
  as text, v0.9.0 sorts above v0.30.0 (measured);
- not taken from git, which does not say which tags were published. A release that failed keeps
  its tag, and the version is burned; v0.15.1, v0.18.2 and v0.20.0 are such tags (gh api: no
  release);
- why it matters (measured with the script, scratch repository): v0.29.0 is published and pins old
  K and R; v0.30.0 is burned and pins old K, new K and R; v0.30.1 pins new K and R and is signed by
  new K. The check against v0.30.0's pins passes, the one against v0.29.0's fails, and only v0.29.0
  has users.

A tag's pins are the `.pub` files that `git ls-tree --name-only TAG -- src/fsbpyr/release-keys/`
lists, each read with `git show`. For a tag missing from the checkout this fails (exit 128), and so
does the command. For a tag from before signing it lists nothing (exit 0). `release-signing.sh
verify` given no `.pub` exits 2 (all measured).

**The first signed release** is the only release whose previous release may pin nothing; then only
the first check runs. Its version is named in tools/release/first-signed-release, not inferred. For
any other version, a previous release that pins nothing stops the command, so a lookup that goes
wrong (a text sort, a failed API call, a page not read) stops the release instead of skipping the
check. If the named version is burned, the release that replaces it changes the file.

A failure at any step publishes nothing, and the draft stays a draft. A release someone publishes
by hand without a pinned key's signature is refused by signed fsbpyr (§5.5), and the audit finds it
(§5.7).

### 5.4 Changing keys

- **A new pair** comes from `fsbpyr key new` or from the script; both write the same format (§2.3).
- **Every reader must find a pinned key.** The sums of release N are read by the fsbpyr of the
  previous release (a burned tag does not count), in self update and for the EL8 sums, and by N's
  own fsbpyr (dispatch, the EL8 stubs). So N's signature must pass both the previous release's pins
  and N's. `make release-sign` checks both (§5.3).
- **A planned change of K** takes two releases:
  1. Release N pins the old K, the new K and R. The old K signs N and its EL8 companion.
  2. Release N+1 drops the old K and is signed by the new one.
- **A new R** replaces the old one in the next release's pins. K signs that release as usual.
- **K lost or stolen:** release N pins a new K and R, and drops the old K at once. Only R is pinned
  by both the previous release and N, so R signs both of N's sums files, with the same command and
  the same checks: `make release-sign KEY=~/release-r.key`. Release N+1 is signed by the new K.
- **A client that skipped the release that changed K** refuses every later release, with the two
  lines of §5.5. It cannot tell a key change from a forgery, and the release that changed the keys
  is signed by no key it trusts. It links nowhere, so the wiki's "Release keys" section says what
  happened, for whoever looks: it lists each key change with its release and the new fingerprints.
  The commit that changes the pins changes that wiki section too (§11.3).
- **R lost:** the next release pins a new R and is signed by K.
- **Both lost:** users reinstall.
- **Limit:** a stolen K works against every client that still pins it, until that client updates
  past the release that dropped it.

### 5.5 fsbpyr's own checks, mandatory from the first signed release

All of them run in C, against the pinned keys, with subject `fsbpyr`.
- **`self update`:**
  1. Fetch the latest release's `SHA256SUMS` and `SHA256SUMS.sig`, and check them.
  2. Download the `.xz` to a temporary file, with a size cap. Its SHA-256 must equal its line
     before lzma sees it. Today the `.xz` streams into lzma (selfupdate.py:152), so this is a
     restructure.
  3. Unpack it. The build info's `release_asset` must equal its own, and the version must be newer
     than its own and the tag's.
  4. Continue as today: `inspect --verify`, then the rename. `inspect --verify` stays the D31
     check and does not change: the signature covers `SHA256SUMS`, not the executable.

  A release with no valid signature prints these two lines and nothing else, with no fallback
  (owner, 2026-10-31):
  ```
  fsbpyr: error: new v0.30.0 has not been installed because it has no valid release signature.
  fsbpyr: currently installed fsbpyr version (0.29.0) has been kept unmodified.
  ```
  An `.xz` that does not match its line, and sums that do not list it, print the same: no valid
  signature covers that file. A replayed or mislabelled release has a valid signature, so its
  first line says what is wrong instead, and the second is the same. They link nowhere: a hacked
  release comes with a hacked wiki, and people hear of an incident through CVEs, advisories and
  mailing lists. Nothing is shown to a user who does not run `self update`, whom nothing touched.
- **dispatch**, the other Python variants (dispatch.py:54): the same steps at its own tag, with a
  version equal to its own.
- **EL8 stubs** (runtime/stubs.py:160–171):
  - `SHA256SUMS-glibc2.28` and its `.sig` are checked before any line of it is used.
  - Today the file lists only the unpacked stubs (vcs.py:138), and the stub's `.xz` streams into
    lzma (stubs.py:178). el8.yml's `prepare` (§5.3) adds a line for each stub's `.xz`, as
    `sha256sum` writes the stubs' lines. fsbpyr downloads the `.xz` to a temporary file, with a
    size cap, and checks it against its line before lzma sees it, as `self update` does.
  - Older fsbpyr are not disturbed by the extra lines: `_published` looks a stub up by its name,
    and `refused_after_update` only collects digests.
  - `refused_after_update` (stubs.py:135), which the old fsbpyr runs during self update, checks
    the new tag's EL8 sums against the old fsbpyr's pins. It only warns, and a failed check counts
    as "unknown", as an unreadable file does today.
- **Cached files** are not checked again. D31 only detects damage, so it is no defence. The
  assumption is that whoever can write the user's fsbpyr cache can also replace fsbpyr itself.
  Stubs that the user places beside fsbpyr are the user's choice and are checked by runtime id
  only, as today.
- **Releases before the first signed one** stay unsigned. The first update from one of them runs
  old code and is not checked.

### 5.6 `make release-check`

`tools/release/check-release.sh` gains these checks [P23]. A failure is a FAIL like any other.
- **Signed or not.** It checks any X.Y.Z (check-release.sh:59-65), releases from before signing
  too. A release is signed if its tag pins keys: the clone of the tag (check-release.sh:122) has a
  `.pub` in src/fsbpyr/release-keys/. The new checks below, but openssl's, run for signed
  releases only. An older release keeps today's checks, and the script prints `info  vX.Y.Z pins
  no release keys: released before signing`. A tag that pins keys must have a signed release:
  nothing else gets published (§5.3). So a
  missing `.sig` there is a FAIL, and no version list is needed, unlike 0.26.0's exception
  (check-release.sh:441).
- **Assets.** `SHA256SUMS.sig` joins the release's assets (check-release.sh:146) and its download
  (:161-163), and `SHA256SUMS-glibc2.28.sig` joins the companion's assets (:248). The checks that
  exist change with them:
  - "SHA256SUMS lists every other asset" and the check of GitHub's digests exempt only
    `SHA256SUMS` today (check-release.sh:170, :179). They exempt `SHA256SUMS.sig` too.
  - The companion's sums list exactly the 8 stubs and their 8 `.xz` (check-release.sh:264-267).
    Measured: today's check FAILs on a sums file with the `.xz` lines.
  - Each `.xz` matches its own line: `sha256sum --quiet --ignore-missing -c
    SHA256SUMS-glibc2.28`, run in the directory of the downloaded assets, which holds the `.xz`
    but not the unpacked stubs. Each `.xz` also unpacks to its stub's line, as today (:274-279).
    Measured: this passes on good files, FAILs on one flipped byte, and FAILs ("no file was
    verified") when no `.xz` is there or the sums have no `.xz` lines; hence signed releases
    only.
- **Signatures.** `release-signing.sh verify` of both sums files against the keys this tag pins
  and against those of the previous release, found as in §5.3. A `verify` that exits 1 or 2 is a
  FAIL.
  - This replaces check-release.sh:362-365, which needs TAG itself in a list of the newest 50
    releases, ordered by `createdAt`. That order does not follow the version (gh api:
    el8-v0.27.0's is earlier than v0.27.0's). The self-update step uses the new value too.
  - The previous release's tag is fetched into the clone as the companion's is
    (check-release.sh:241). Measured: after a depth-1 fetch, `git ls-tree` and `git show` read
    its pins. It may pin nothing only for the version in tools/release/first-signed-release.
- `fsbpyr verify` of each executable with the released fsbpyr. This checks fsbpyr's reader against
  the script's files.
- **`SHA256SUMS`'s attestation:** its provenance attestation by release.yml from the tag, as
  check-release.sh:285 checks `SHA256SUMS-glibc2.28`'s by el8.yml. This also FAILs a release that
  someone with write access published by hand.
- **openssl.** It joins the tool list (check-release.sh:60) for every release, signed or not, so
  there is one tool list. That list checks only `command -v`, which cannot see the version. So
  before any download the script also checks that `openssl list -signature-algorithms` names
  ED25519 and ML-DSA-65, as the script's own tool check does, and stops with exit 2 otherwise.
  Without this, an OpenSSL 3.0 passes the start and fails each signature check later: the script
  exits 2 and names the version (measured with 3.0.13 and 3.0.22), a FAIL.

### 5.7 `make release-audit`

The first to see a tampered account or repository should be the admin, not a user. `audit.sh`
compares what GitHub holds with what the admin's machine recorded. `make release` and `make
release-sign` run it and stop if it does not pass, and it can run from cron on any machine with a
clone: it needs no key, only a `gh` login that can read the settings. It never runs in GitHub
Actions, which whoever holds the account could turn off. With K offline, existing users stay
protected meanwhile, so what the delay costs is new installs, which take what GitHub serves (§8).

The reference is on that machine, outside GitHub, in `FSBPYR_RELEASE_STATE` (default
`$XDG_STATE_HOME/fsbpyr-release`): `releases`, a line per release (tag, commit, the SHA-256 of its
files' names and digests), and `settings`. `make release-sign` adds each release it publishes.
`audit.sh --accept` takes what it finds as the reference, so it comes after reading what differs.

| Check | Finds |
|---|---|
| Every tag here and on GitHub, by commit | a moved, added or removed tag |
| GitHub's `main` is held by this clone's main | a commit pushed that you do not have |
| Every published release is on record with the same id, commit, title, notes and files, and every recorded one is there | an added, changed, or deleted and remade release |
| Each signed release passes the keys its tag and the previous release pin, and its files are what its sums list | a replaced sums file or file, a removed signature |
| A valid signature on a release that is not on record | **a stolen key**: the one thing a signature cannot show |
| Every release is immutable; no draft for a tag this clone lacks | a weakened release, a draft that is not yours |
| The latest release is the newest on record | updates held back |
| Settings against the record: repository, collaborators, owners, members, apps with their permissions, webhooks, deploy keys, rulesets in full, secrets, variables, environments, runners, Actions policy, the token's default, immutable releases, the wiki's head | what someone changes to stay, and fingerprints changed in the wiki |

Releases before the first signed one have no signature, so the record alone stands for them.

A lookup that fails is exit 2 and never a pass: nothing to compare is not a match, since a blinded
check would read as "no secrets", which is what the record says. Exit 1 lists each finding, and a
finding means GitHub is treated as tampered with until it is explained.

The organization's audit log is not available through the API on the Team plan (HTTP 404, read
2026-10-31), so this compares state, and does not read who did what: a change undone before it
runs is not seen. `gh release verify` is no
substitute for the record: it passed the backdoored release of §14, since it shows that a release
is unchanged since it was published, not that it is yours.

## 6. Apps

**Publisher:**
1. Run `fsbpyr key new myapp` once. Keep `myapp.key`, and publish `myapp.pub` and its fingerprint
   wherever they choose.
2. Build with `FSBPYR_SIGNING_KEY=~/keys/myapp.key fsbpyr build`. It asks for the passphrase
   after the smoke test and writes `dist/myapp` and `dist/myapp.sig`. Publish both. On a
   terminal the passphrase is typed: `FSBPYR_SIGNING_PASSPHRASE` is meant for jobs without one
   (§3.1).
3. **In CI**, keep the key out of the job that builds:
   - The build job runs `fsbpyr build` with no signing variable set and hands `dist/myapp` on.
     It runs third-party code (§3.1), so it never gets the key or the passphrase.
   - A separate job, with a released fsbpyr, runs `fsbpyr sign --project myapp dist/myapp`.
     With `--project`, a build info that names another project is an error (§3).
   - `FSBPYR_SIGNING_KEY` is a path: the job writes the key from its secret to a file that only
     the job's user can read, and removes it after signing. The passphrase comes from
     `FSBPYR_SIGNING_PASSPHRASE`, since the job has no terminal (§2.3).
   - A later step, without the key, checks as users will: `fsbpyr key trust myapp myapp.pub
     SHA256:…`, with the `.pub` kept in the repository, then `fsbpyr verify --project myapp
     dist/myapp`. `sign` checks the new signature only against the key it signed with, so only
     this step catches a secret that holds another key, as §5.3's checks do for fsbpyr.
   - Which CI, and where it keeps the secrets, is the publisher's choice (§10).

**User:**
1. Get `myapp.pub` and its fingerprint from the publisher, then run
   `fsbpyr key trust myapp myapp.pub SHA256:…` once.
2. Download `myapp` and `myapp.sig`, then run `fsbpyr verify --project myapp myapp`.
   `--project` makes sure that the file is myapp's, and not a genuine file of another project
   whose key the user also trusts.

**Out of scope:** how publishers hand out and change their keys, how users keep them, and whether
users check at all.

**Where fsbpyr does not run,** users check on a machine where it does, then copy the checked
file:
- EL8 (`--glibc 2.28` apps): fsbpyr itself needs glibc 2.34.
- musl, such as Alpine: fsbpyr and its apps are built for glibc only (musl put aside by the owner
  on 2026-10-21, IMPLEMENTATION_PLAN.md:1520-1521). A released fsbpyr is "not found" there, as
  User-Running.md:197-198 says for apps (measured with fsbpyr 0.28.0 on Alpine 3.24.3). Apps do
  not run there either, so this concerns only other files, signed with `sign --project`.

This needs nothing new: there is no standalone check script (§10).

## 7. Wiki

- **User-Signing** (new):
  - §6 for publishers and users, with the messages table, always with `verify --project`;
  - the CI pattern of §6 step 3, with one GitHub Actions example. On GitLab, the key fits a File
    variable, whose value in the job is the path of a file that holds it. A passphrase can be
    masked only if it is one line with no spaces and 8 or more characters (GitLab's CI/CD
    variables page, read 2026-10-30);
  - checking fsbpyr's own downloads: `fsbpyr verify --project fsbpyr
    fsbpyr-3.14-x86_64-linux-gnu` with `SHA256SUMS` and `SHA256SUMS.sig` beside it. The user
    checks that the VERIFIED line names the asset they downloaded [P10];
  - EL8 and musl (Alpine): check on another machine and copy the file (§6).
- **User-Building:** `--signing-key` in the options table, with `—` for its key
  (User-Building.md:324); `FSBPYR_SIGNING_KEY` and `FSBPYR_SIGNING_PASSPHRASE`; the warning; and
  what the build keeps from its processes and what it does not (§3.1).
- **User-Settings:** `--signing-key` joins the options that exist on the command line only
  (User-Settings.md:47-48) [P24].
- **User-Installing, User-Other-Commands:** updates are checked, and the new commands.
  User-Installing gains the section "release keys": the fingerprints that fsbpyr pins, one per
  line in a code block that the suite reads (§11.3), and each key change with its release
  (§5.4).
- **Dev-Repository:** §5.2–5.4, `FSBPYR_RECOVERY`, and that everyone with write access is
  trusted as the admin is (§5.2).
- **Dev-Security:** what a pass means, and §8.

## 8. Threats

| Threat | Result |
|---|---|
| File changed after signing | **Stopped**: its SHA-256 is signed |
| A signature for project A presented as project B's | **Stopped**: the project is in the statement |
| A genuine file of another trusted project, served under this app's name | **Stopped** with `verify --project` (exit 1). Without it, `verify` passes and prints the other project's name |
| Another file of the same release, or an older release's file with its own sums file, served under an asset's name | **Not stopped** by `verify` through a sums file: the VERIFIED line names what FILE is listed as (§3) [P10]. `self update` checks the asset and the version (§5.5) |
| A forged executable whose build info names a path (`../../../Downloads/x`), a terminal escape or another non-name | **Stopped**: a name from build info is checked before it is used or printed, and fails as NOT VERIFIED (§2.1). FILE and listed names print as repr() when not printable (§3) [P6][P12] |
| A relative `XDG_CONFIG_HOME` that brings its own trusted keys in a download | **Stopped**: it is ignored, as a relative `HOME` is (§3) [P12] |
| `.sig` removed | fsbpyr's own paths refuse. For apps, `NOT VERIFIED: no myapp.sig`; users must expect a signature |
| Ed25519 broken, by a quantum computer for instance | **Stopped**: ML-DSA-65 must pass too |
| A flaw in the ML-DSA-65 code | **Stopped**: Ed25519 must pass too |
| The first fsbpyr download tampered with | **Not stopped**: it is trusted as GitHub serves it (owner decision). That includes a release published by whoever holds the account. GitHub stays the only host (owner, 2026-10-31): a second site would only move the problem. The audit limits how long it lasts (§5.7) |
| Malicious mirror or TLS interception, for updates | **Stopped**, given the poisoned-build row |
| Updates withheld | **Not stopped**, but found: a writer can mark an older release as the latest, which the version check refuses (§5.5). The audit reports it (§5.7) |
| Rollback of fsbpyr through an update | **Stopped**: the version must be newer |
| Rollback of apps | **Not stopped** |
| A stolen GitHub account: a backdoored commit or workflow on a pushed tag, built and attested by GitHub | **Stopped**: CI leaves an unsigned draft, K is not there to reach, and `make release-sign` refuses an attestation that does not name your commit. Published by hand, every signed fsbpyr refuses it, and the audit reports it (§5.3, §5.5, §5.7). Played on GitHub (§14) |
| A key, or any other change, pushed to main by someone with write access | **Not stopped by a check**: `make release-sign` refuses while GitHub's main is not your commit, and the audit finds a commit you do not hold, but what the admin pulls and tags is the admin's to know: out of scope (owner, 2026-10-31). Whatever they tag, K signs, and clients accept any key it adds (§5.1) |
| A release made by hand by whoever holds the account, on any tag | **Stopped** for updates: it has no signature by a pinned key (§5.5). The audit reports it. First installs trust it (above) |
| Poisoned build | Gets a signed release. K is never near built code. The `draft` job writes the sums again from the build job's files, so the built code in `prepare` cannot get digests of unpublished files listed. Everything the commit builds is what the admin signs: a compromised dependency in that commit is signed too |
| A key the tag does not pin | **Stopped**: `make release-sign` checks against the pins of this release and of the previous published one before it uploads anything; a burned tag does not count (§5.3) |
| Stolen K | **Partly**: it now takes the admin's key medium and passphrase, not the GitHub account. R signs the key change; clients that do not update stay exposed. The audit reports a release that is not on record (§5.7) |
| A draft's files replaced before they are signed | **Stopped**: the attested sums must name your commit, and every file must match them (§5.3) |
| A draft's files, tag or flags changed while the admin is at a prompt | **Stopped**: the draft and the refs are read again before the upload and before publishing. In the moment left after the last read, a file that changes no longer matches the signed sums: clients refuse it, and the audit that follows reports it, with the version burned |
| A build job taken by a self-hosted runner of the attacker's, on a hosted label | **Stopped**: every job of the attested run must have run on GitHub's machines (§5.3) [A: whether GitHub routes so] |
| The passphrase printed or logged by code that runs during `build`, in a dump of its environment | **Stopped**: the environments of the build's processes lack both variables (§3.1) |
| The passphrase and the key file taken by code that runs during `build` and looks for them: in /proc, in memory, on the terminal, at a later start | **Not stopped**: build without a key and sign in a separate job, on a machine or account the build never ran on (§3.1, §6) [P1] |
| fsbpyr's cache, or stubs placed beside fsbpyr, changed by someone who can write them | **Not stopped**: they can replace fsbpyr itself (§5.5). A reinstall does not clean them (§14), nor the trust store: the wiki's restore steps remove both |
| GitHub itself compromised, so that it attests what it did not build | **Not stopped**: out of scope, and far more than fsbpyr would be at stake |
| The admin's own machine compromised | **Not stopped**: K's passphrase can be taken as it is typed. A separate offline machine for step 6 of §5.3 stops that: only `SHA256SUMS` and the `.sig` cross |
| openssl.cnf, `OPENSSL_CONF`, `OPENSSL_MODULES` or `LD_PRELOAD` set by an attacker | **Not stopped**: they load code into fsbpyr. A config that loads no code changes no check's result, but it can choose the randomness of `key new` and `sign` (§4) |
| An app key handed out by an attacker | Out of scope (publishers and users) |
| A change undone before the audit runs: a ruleset toggled, an ephemeral runner | **Not seen**: the audit compares state, and the audit log is not available to it (§5.7). What such a change could reach is checked at signing |

## 9. Performance and size

| Item | Value |
|---|---|
| Checking a 30 MB file | One SHA-256 pass plus two signature checks over a statement of 101–164 bytes. The script took 0.04 s in all; `sha256sum` alone 0.01 s. In C, measured with a scratch program on the PBS libcrypto.a (Core Ultra 9 285, file in the page cache, one thread): 11–14 ms as the first OpenSSL use of a process; SHA-256 alone 7.3 ms, low-level and EVP alike; both checks 0.5 ms with a fresh context, 1.3–3.9 ms as the first call of a process |
| Signing a 30 MB file | The script 0.30 s, mostly opening the key: four key decryptions at 600,000 PBKDF2 iterations. In C 0.11 s: two key decryptions and the self-check |
| `key new` | The script 0.25 s, C 0.10 s |
| Sign job | Downloads the release's files again to write the sums: 337 MiB for v0.28.1 (34 assets; gh api) [P22] |
| `.sig` | 3373 bytes |
| `.pub`, `.key` | 2839 and 6088 bytes |
| Per release | one more asset (`SHA256SUMS.sig`), and one more for the EL8 companion |
| Per stub | +4,416 bytes (§4) |
| App start | unchanged |

## 10. Decided, 2026-10-29

- Signatures are detached. Users who want to check keep them themselves.
- Ed25519 and ML-DSA-65, both always: "it can't hurt us and it does not cost us".
- The crypto is in `_fsbpyr` C, with the bundled OpenSSL. There is no standalone check script for
  users.
- The first fsbpyr install trusts the GitHub release. App users who want to check download a
  released fsbpyr. Checking is optional.
- EL8 users check on another machine: too few users to justify more.
- The wiki explains checking with fsbpyr, not with external tools.
- Each release signs one `SHA256SUMS`.
- One K and one R. A new pair comes from `fsbpyr key new` or from the script.
- Signatures are mandatory in fsbpyr's own download paths.
- Commands are `key new`, `key trust`, `sign` and `verify`. `build` signs as soon as a key is set.
  The project comes from build info, otherwise from `--project`.
- Private keys are kept by the admin or the publisher, outside the project's scope. App key
  management is out of scope too.
- The statement carries SHA-256, not D31. `sign` takes any file, and `SHA256SUMS` has no D31.
- CI signs every release with the openssl script. The job holding K runs no fsbpyr.
- For fsbpyr, a signing failure stops the release, and `make release-check` checks the signature.
  For apps, a key that cannot be used gives a warning and an unsigned build.
- The names in §2 and §3.

After the review, the same day, the owner approved its fixes, among them three changes to the
design:
- a planned change of K pins three keys for one release (§5.4);
- a `verify --project` that differs from the file's project is NOT VERIFIED (§3);
- `SHA256SUMS-glibc2.28` lists each stub's `.xz` too (§5.5).

Decided on 2026-10-31, after a stolen account was played against the first design (§14). Where these
differ from the list above, they win:
- K never reaches GitHub, not even for a moment. The admin downloads the draft, signs on their own
  machine and pushes the `.sig`. Everything is still built on GitHub, and attestations stay.
- The admin signs only if their commit is GitHub's `main`, GitHub's tag and what the sums are
  attested for. GitHub itself being compromised is out of scope.
- What the admin pulls into main is theirs to know, and out of scope.
- Checking prior signed releases and the repository is part of the release process (§5.7): the
  first to detect tampering should be the admin, not users.
- A user checks a suspect local fsbpyr with a shell script, by signature or attestation. This
  replaces "there is no standalone check script for users", for that purpose.
- GitHub is the only host: no second site for install.sh or a status page. New installs during a
  hack are an accepted risk, which fast detection limits.
- A refusal prints its two lines and nothing else, with no link (§5.5). Incidents go through the
  usual channels: CVEs, advisories, mailing lists.

The test round of 2026-10-30 decided nothing. Its choices are marked [Pn] and listed in §13. The
owner accepted all of them as written on 2026-10-30, and asked for Wycheproof's cases as well.

## 11. Tests

### 11.1 The test round of 2026-10-30

The comparison with the dropped concept left 35 open gaps, in 10 groups. On 2026-10-30 each was
tested, with real runs where a run could settle it: scratch C on the PBS `libcrypto.a` of the
pins, stand-ins for fsbpyr's Python on fadc3d9's code, the script, podman images (the pinned
Debian 13 image, Debian 12, Ubuntu 24.04, AlmaLinux 9, Alpine 3.24) and aarch64 under qemu-user.
The rest was read: the code, read-only `gh api`, and GitHub's and GitLab's docs. Some gaps were
read only: the plan lines that promise a D31 signature, where `--signing-key` is documented, and
who may change what on GitHub. A second tester re-ran each test and tried to refute it; where the
two differed, the sections follow the re-run. Gaps found on the way were tested the same way.
The results are in the sections they changed. The suite runs as a normal user
(CONTRIBUTING.md:270), and CI runs it as `tester` (full-suite.yml:114-116, :145, :275-280).

### 11.2 Still open: not run

None of these was run. Those marked [A] where the text states them rest on inference. The others
were read in docs or source, or are steps no run has tried yet; a run could still contradict them.
- **The write role** (§5.3, §8), from GitHub's docs, with no second account to try it: it
  publishes a draft, and may release on an existing `v*` tag. A
- **RepositoryRole 5** is the admin role (§5.2): the API shows only the id.
- **Run artifacts**: whether `prepare`'s code can use the artifact token after `prepare` ended
  (§5.3, §8), and that anyone can download a public repository's run artifacts.
- **Yama scope 1** does not guard /proc/PID/environ (§3.1): read in the kernel source.
- **A typed passphrase is encoded with `os.fsencode`** (§2.3).
- **A CPU with a broken RDRAND**, under the rdrand engine (§4): keys and ML-DSA signatures would
  draw on it alone.
- **An ENGINE set as the default** for digests or signatures takes over a private context's
  operations (§4): read in OpenSSL's source.
- **Today's stubs hold the ML-DSA, PBES2 and PEM code** because `_ssl` and `_hashlib` pull it in.
- **§5.5's paths check after the response is read and closed**, clear of §4's error-queue case.
- **uv refuses a Name outside PEP 508**, so honest builds record only names of §2.1's form.

The rehearsal of §14 settled what this list held about the environment's tag rule, `gh secret set`,
K's bytes, a job step's terminal, a `.sig` after publication, the workflows on GitHub, a planned
change of K and a recovery; and the full suite settled aarch64 hardware.

### 11.3 Tests the implementation adds

**The C** (§12 step 1):
- The script's fixed vectors and the negatives of §12 step 1. `_sign` is checked by what it
  returns: the signature passes `_verify` and the script, and its Ed25519 half equals the
  script's. No test compares ML-DSA bytes (§2.2).
- Independent vectors, since the script uses the same OpenSSL, and a mistake both share (a
  context string, the internal or mu interface, pre-hash) would pass both. Each has its own key
  and message, so the probe calls sig.c's check of one half, with the public keys as PEM
  fixtures [P16]. All gave the expected result on all eight pins, with no context string
  (measured):
  - Ed25519, RFC 8032 §7.1 TEST 1 (empty message) and TEST 2 pass, and fail with one byte of the
    signature flipped;
  - ML-DSA-65, usnistgov/ACVP-Server at a7f283cdc87d: sigGen tcId 44 (deterministic) and 216
    (randomized), external, pure, empty context, pass. sigVer tcId 39 (empty context, modified
    message) fails, and so does sigVer tcId 33, valid under a 142-byte context, checked without
    it. ACVP's sigVer file has no passing ML-DSA-65 case with an empty context.
- A vector for a subject other than `fsbpyr`, signed by the test with `openssl pkeyutl`. The
  script signs as `fsbpyr` only (§2.1), so this is the one check of an app's statement that
  fsbpyr does not make itself.
- openssl.cnf: every vector gives the same result under each config of §4, as the process's
  first OpenSSL use and after `SSL_CTX_new` and `EVP_MD_do_all_provided(NULL, …)`. Under
  `fips=yes` the default context finds no algorithm (measured), so this also shows that no call
  falls back to it. The test writes the configs.
- The error queue, for each call, failing ones included, and for a failed key followed by a good
  one: a queue empty before the call is empty after it, and an error queued before it is still
  the only one.
- An empty passphrase given on a pty: refused, with no prompt (§4).
- The declarations: sig.c compiled with Debian's libssl-dev 3.5 headers included, under
  `-Werror`, so a declaration that does not match fails the suite (§4) [P14].
- The same vectors through `_fsbpyr`'s functions, in the demo app on both launchers
  (tests/support/apps.py:30-34), the sanitized one (stub.py:69-81) included.

**Python, from source** (§12 step 2):
- The stand-in [P25]. Tests that run from source replace signing.py's two functions with
  monkeypatch: the 74 unit tests that take a fake release server (tests/unit/test_selfupdate.py,
  test_dispatch.py, runtime/test_stubs.py), the commands' tests, tests/e2e/test_el8.py:281-302.
  tests/support/signing.py does no signing crypto: `_sha256` is hashlib's; `_verify` is true
  only for `fake_signature(pub, statement)`, 3373 bytes of shake_256 over both; `_key_new` and
  `_sign` are fakes bound to the passphrase; the pins are the test's `.pub` bytes. It is not
  `sys.modules["_fsbpyr"]`, as at test_selfupdate.py:91: vcs.py:113 and build.py:288 would take
  it for a built fsbpyr, and `own_version` and `builder_uv` raise AttributeError (measured).
- The statement against literals, for `fsbpyr` and an app. For the empty file and `myapp`:
  `fsbpyr-signature 1\nsubject myapp\nsha256 e3b0c442…b855\n`, 105 bytes with the whole digest.
- Build info naming `../../../Downloads/x`, `/tmp/x`, `a/b`, `myapp` with a trailing newline, a
  terminal escape, `My_App`, 65 characters, a number, null or no project, written with
  payload/writer.py and signed by a key planted where the unchecked path leads: NOT VERIFIED
  (exit 1), with and without `--project`, and no part of the name printed (§2.1).
- `key trust` saves nothing for a FINGERPRINT that is wrong, cut to 16 digits, a digit longer,
  without `SHA256:`, as `sha256:`, with a newline or a leading space; for PROJECT `fsbpyr`,
  `FSBPYR`, `../x`, `/tmp/x`, `a/b`, with a newline, of 65 characters or `ｍｙａｐｐ`; for a PUBKEY
  of 2838 or 2840 bytes, swapped, with two Ed25519 keys or text around them. It saves an
  upper-case FINGERPRINT, and `My_App` as `my-app.pub`; `--replace` in its four cases (§3). The
  saved file's SHA-256 is FINGERPRINT.
- The sums lookup: a file saved under another listed name prints the name it is listed as; a
  digest listed twice prints both; FILE and listed names with a terminal escape print as repr().
- `.sig` placement: `build` through a user's symlink at `dist/<name>` and at `-o PATH` writes
  `<target>.sig`; `verify` of a build's command link and of an `FSBPYR_INSTALL_LINKS` link finds
  the executable's `.sig`; a command named `<output>.sig` fails the build before it writes.
- A relative `XDG_CONFIG_HOME` or `HOME` with a key under it in the current directory: not used.
- Dispatch: a stand-in variant gets both variables, and the `core.fsmonitor` command that the
  dispatcher's `git ls-files` runs has neither in its environment. From source, in a checkout
  tagged v1.2.0 with no cached variant, dispatch stops before any download, with the
  developer-build message (§4).
- A dispatched build signs: the dispatcher runs in-process from source with `own_version`
  patched, and the variant, a built fsbpyr, is put into the test's cache first (dispatch.py:81),
  where nothing checks it again (§5.5). With both variables set, the `.sig` passes
  `verify --project` after `key trust`; a wrong passphrase gives exit 0, no `.sig` and one
  warning.

**A built fsbpyr**, run with tests/support/contain.py (§12 steps 2 and 3):
- Test pins [P25]: a tagged copy of the checkout with test keys in src/fsbpyr/release-keys/, in
  place of K and R. `_tagged_copy` and `release_fsbpyr` (tests/e2e/test_el8.py:305-331) move to
  tests/e2e/conftest.py, with the tag as a parameter. The keys go in before the copy's commit, so
  the real tree is never written. Measured: the build holds them as the payload files
  fsbpyr/release-keys/k.pub and r.pub, in 1.2 s with the runtime cached, and a small app built the
  same way read such a directory with `importlib.resources`. tests/e2e/test_dispatch.py:55-56
  and test_build.py:284 build from the checkout itself. There is never an environment variable
  or option that changes the pins.
- tests/support/signing.py makes the test keys once per session with `release-signing.sh
  new-key` (0.25 s each), as install.sh's tests make their TLS key (test_install.py:75-77), so
  none is committed. It signs the fakes' sums files with `release-signing.sh sign`.
- The download paths [P25]. Without them, release N's checks first run on real files in N+1's
  `make release-check` (check-release.sh:360-387), when both releases are immutable.
  - `self update`, tests/e2e/test_selfupdate.py (new): the v1.2.0 build, and a fake GitHub with
    v1.3.0's `.xz` (a second build of the copy), `SHA256SUMS` and its `.sig`. It updates, and it
    refuses, leaving the file as it was, with no `.sig`, a `.sig` by a key it does not pin
    (§5.4's message), an `.xz` that does not match its line, or the v1.2.0 build as v1.3.0. With
    three pins (§5.4) a `.sig` by the new K passes. A placed EL8 stub whose new sums fail their
    check only warns.
  - Dispatch, tests/e2e/test_dispatch.py as a subprocess, to a variant built from the same copy
    on the other Python, with the version and asset §5.5 checks (measured on 3.13: tag v1.2.0,
    the same commit, `release_asset` fsbpyr-3.13-x86_64-linux-gnu; fetched and run in 0.8 s).
  - EL8 stubs, tests/e2e/test_el8.py:344-389, with the test keys; the fake companion (:270-278)
    serves `SHA256SUMS-glibc2.28` with the `.xz` lines and its `.sig`.
  - The fakes serve a subprocess over HTTP on 127.0.0.1: FakeGitHub
    (tests/unit/test_selfupdate.py:32-66, moved to tests/support/releases.py) through
    `FSBPYR_RELEASE_API`, FakeReleases through `FSBPYR_RELEASE_DOWNLOADS`; a self update with a
    placed stub needs both (runtime/stubs.py:144-151). Measured with today's code: the v1.2.0
    build updated itself to v1.3.0 in 0.3 s. Each `.xz` is packed once per session at preset 0
    (1.1 s for the 29.5 MB build, against 4.8 s at the default; measured).
  - Refusals in Python only (another `release_asset`, the size cap) are unit tests.
  - full-suite.yml runs them on every merge, on x86-64 and aarch64 (:24-28), and release.sh tags
    only a commit whose run passed (full-suite.yml:15-17). Their limit: N updates to a build of
    the same code; that N reads N+1's real files is first checked in N+1's `make release-check`.
- The commands, tests/e2e/test_signing.py (new), against the script both ways: `key new`, the
  script's `sign`, `verify --project`; the script's `new-key`, `sign` and `build` with
  `FSBPYR_SIGNING_KEY`, the script's `verify`; `verify --project fsbpyr` beside a signed
  `SHA256SUMS`. Without them `key new` and `sign` first meet the real C at users.
- The build's processes: with a key and `FSBPYR_SIGNING_PASSPHRASE`, a project whose dependency
  a fake build backend builds. The backend and the app's entry module, which the smoke test
  imports (build.py:630-640), write their environment to a file: neither holds a signing
  variable, and the output is signed. The launcher's collection child, started while they are
  set, has neither in its /proc/PID/environ (§3.1).
- The prompt: typed on a pty after the smoke test; Ctrl-C there gives exit 130 and an output
  without a `.sig` (§3.1). Without a terminal and the variable, `key new` and `sign` exit 2 with
  text on standard input, and `build` warns and builds unsigned (§2.3).
- `verify --project` of a signed file passes with `OPENSSL_CONF` at the `fips=yes` config (§4).

**The release** (§12 step 4). Steps' shell runs with fake programs, as
tests/integration/test_el8_workflow.py runs one today (`step_script`, :34):
- tests/unit/test_wiki.py: the fingerprints of src/fsbpyr/release-keys/*.pub equal the code
  block of the wiki's "release keys" section (§7). A test key, or a key change without its wiki
  entry, fails the suite.
- el8.yml: the sums are made in `prepare`; a published companion without its `.sig` fails "Is it
  published"; in recovery a draft is kept, and `publish` stops at it.
- `publish`'s condition: the shape tests (§12 step 4) check that `prepare` needs every job that
  `publish` needs today, so a failed `sbom` job or test job of el8.yml publishes nothing.
- The previous release: a burned tag, an `el8-v…` tag on the same commit, the tag itself, v0.9.0
  against v0.30.0, a shallow checkout, a failed API call, a list longer than one page. Any
  release but the one named in tools/release/first-signed-release fails when its previous
  release pins nothing.
- The sign job's compare: a `prepare` whose `SHA256SUMS` lists another digest or file fails it;
  a `publish` whose files differ from the sums it uploads publishes nothing.
- check-release.sh against a release from before signing (no signature checks, no FAIL), and
  against a signed one with its `.xz` lines.

**The suite's setup** (§12 steps 2 and 5):
- The script's test [P26]: cases.sh's cases, with the mode-555 `sign` case replaced by the
  failing `mktemp` of §11.4 so that no case depends on the uid, and the other cases of §11.4, as
  a pytest file beside tests/integration/test_release.py, with fake programs first on PATH as
  there (`write_program`, :37), passphrases typed on a pty from Python, and without the 30 MB
  timing.
- It needs an OpenSSL 3.5 command, and the suite has no skips. CI's pinned image gets it from the
  job's first `apt-get`, as a dependency of `ca-certificates` (full-suite.yml:91-93; simulated
  with `apt-get -s` in the pinned image), and tools/test-setup.sh:22 names it. It is 3.5.7
  (measured in a `test` image, and on arm64 in the pinned image under QEMU). The runner host runs
  only test_portability.py (full-suite.yml:494), so its 3.0.13 is never used. requires.py checks
  only presence (tests/support/requires.py:64), so the test first calls an environment
  condition, as `uv_python` is one (:181): `openssl list -signature-algorithms` must name
  ML-DSA-65, else "unmet environment: … install OpenSSL 3.5 or newer (Debian 13 has 3.5.7), or
  run make test-container". `_unmet_conditions` (:209) lists it for `make test-prereqs`, and
  CONTRIBUTING.md's tool list (:250, :261) says 3.5 or newer.
- The declaration check needs libssl-dev 3.5 there too (Debian 13: 3.5.7, measured).
- A session fixture in tests/conftest.py unsets `FSBPYR_SIGNING_KEY` and
  `FSBPYR_SIGNING_PASSPHRASE` and points `XDG_CONFIG_HOME` at a temporary directory, as
  `FSBPYR_GC` (tests/conftest.py:49-53) and `FSBPYR_CACHE_DIR` (tests/e2e/conftest.py:35-41) are
  set. Otherwise a developer's exported key signs the suite's builds, and `key trust` tests
  write into their ~/.config.

### 11.4 The script

So far the script was run for these cases, on the Debian 13 host (OpenSSL 3.5.7) as the user and
under `unshare -r`, and as root on arm64 in the pinned Debian 13 image under QEMU, with the same
results unless a case says otherwise:
- keys made, with modes 600 and 644;
- nothing replaced: key, `.pub` and `.sig`;
- signed, and checked against K; refused against R; with K and R, passed by R;
- signed with R: `sign` passes, `verify` against K alone refuses (the sign job's check);
- a changed file, a flipped byte in either half, a 64-byte `.sig` and a missing `.sig`: all
  refused; a `.sig` of 0, 3372, 3374 or 6746 bytes: NOT VERIFIED, exit 1, with its size;
- a `.pub` with an extra line, with CRLF line ends, or with its keys swapped: refused. Given
  alone, exit 2. Followed by a good `.pub`, that one is still tried: exit 0 when it matches,
  exit 1 when it does not;
- a `.pub` given alone with P-256, Ed448 or RSA first; P-256, ML-DSA-44 or ML-DSA-87 second; two
  Ed25519 keys, or one key twice; one key, three keys, or none: exit 2, and a good `.pub` after
  it is still tried. One byte after the DER of either key: exit 2 (on the host);
- key files refused with exit 2, nothing written: both keys unencrypted, three encrypted blocks,
  the blocks swapped, two Ed25519 keys, the second key under another passphrase;
- a symlink at FILE.sig (dangling, to a file, to a directory), at NAME.key or at NAME.pub:
  exit 2, nothing written, the symlink and its target unchanged;
- a wrong passphrase, and a missing passphrase without a terminal: refused, nothing written. An
  empty `FSBPYR_SIGNING_PASSPHRASE` counts as unset: without a terminal exit 2, on a terminal it
  is asked for. An empty passphrase typed: exit 2;
- the passphrase typed on a terminal (a pty): signed; two different passphrases for a new key:
  refused, nothing written. Leading and trailing spaces are kept, as typed and as in the
  variable;
- OpenSSL 3.0, a fake one first on PATH and the real 3.0.13 of Ubuntu 24.04 and 3.0.22 of Debian
  12 in their images (x86-64 only): exit 2, "… has no ML-DSA-65: OpenSSL 3.5 or newer is
  needed", nothing written. No `openssl` on PATH: exit 2;
- a temporary directory that cannot be made, and a key file of junk: exit 2, not 1, and nothing
  written. A failing `mktemp` first on PATH makes `sign`, `new-key` and `verify` fail for any
  uid, and so does `new-key` in a missing directory; `verify` had no such case before. These also
  passed as root and as `tester` in a `test` image of the Containerfile (built 2026-10-22). The
  first `sign` case used a mode-555 directory instead. Root writes into it, so as root `sign`
  exits 0 and writes the `.sig` (measured); this is the only case that differs.

The cases that came with the review fail with the script before it: the VERIFIED line without the
project, a bad `.pub` that stopped `verify`, exit 1 when `mktemp` fails, and typed passphrases
that lost their spaces.

Script bugs these cases found, fixed with the script's test (§11.3):
1. `sign` counts only the lines that begin an encrypted block (release-signing.sh:148), and
   `split_pem` takes everything after the first block as the second key (:73-76). So a key file
   with a third block that is not encrypted, or text before, between or after the two, is used;
   a plaintext ML-DSA-65 key between the two encrypted ones is the key it signs with (exit 0),
   and `verify` against K then refuses the signature. The script must take only the `.key` that
   §2.3 allows [P17].
2. `place` checks that the path is free (:115), then runs `chmod` and `ln` without `-T`
   (:116-117). A directory, or a symlink to one, made there in between gets the file inside it,
   and `sign` says "wrote FILE.sig" (exit 0); if NAME.pub appears, `new-key` exits 2 but leaves
   NAME.key. Measured with the window held open by a `chmod` on PATH; `ln -T` refuses a
   directory, a symlink to one and a dangling symlink.
3. A passphrase over 1024 bytes: `new-key` fails reading its own key back ("Could not find
   private key … UI routines", exit 2, nothing written), a message that misses the cause (§2.3).
4. The `openssl` commands take ML-DSA key formats from openssl.cnf, so the fix runs them with
   `OPENSSL_CONF=/dev/null` (§2.3). The script's test sets `ml-dsa.output_formats` in a config.

shellcheck: Debian 13's 0.10.0 reports nothing on the script (measured, in a throwaway container
of the pinned image). The repository lints no shell: `make check` is ruff and mypy
(Makefile:242-244), and shellcheck appears only in three directive comments (tools/bootstrap.sh:82,
tools/release/prepare.sh:25, tools/release/release.sh:31). So the script gets no lint step [P26].

Found outside signing, for the owner:
- a dispatching fsbpyr SIGKILLs its variant 0.25 s after Ctrl-C (`subprocess.run`,
  dispatch.py:105), and SIGTERM reaches only the dispatcher (cli.py:554-556; measured);
- on Alpine with gcompat a released fsbpyr says its file was modified or damaged (exit 70),
  though it starts on Debian 13 (measured once, not re-run);
- tools/test-setup.sh's header (:7-15) does not list openssl, which :22 installs;
- shellcheck 0.10.0 warns in three shell files: spikes/common.sh, tools/release/version-files.sh
  and tools/release/check-release.sh.

## 12. Implementation outline

1. **C.** `sig.c` with the four functions, their Python wrappers and their own method table
   [P15], its OpenSSL declarations written by hand [P14], and the context and error-queue rules
   of §4 [P13]. The launcher's collection child drops both signing variables before its exec
   (due.c:175-191) [P2].
   - A probe, tests/probes/sig.c, with fixed vectors made by the script, the independent vectors
     of §11.3, and negatives: each half flipped; sizes 0, 64, 3372 and 3374; keys swapped; other
     algorithms; extra PEM content in a `.pub` and in a `.key`; text before, between and after
     the keys; two Ed25519 keys; a block whose DER has one byte after the key; other key types in
     a `.key`. Also a subject other than fsbpyr, the PBKDF2 iteration count of a key that
     `_key_new` made (600,000), and the config matrix and the error queue of §11.3.
   - LauncherBuild builds it with ASan and UBSan, as tests/e2e/test_digests.py:29 does, against
     the PBS libcrypto.a of the run's Python and target (tests/e2e/conftest.py:139-148), and
     full-suite.yml runs every Python on x86-64 and aarch64 (:58-67): a pin without Ed25519 or
     ML-DSA-65 fails the suite, with no check of its own.
   - Nothing sets ASAN_OPTIONS, so LeakSanitizer runs, and sig.c frees on every path, failures
     included. Measured: t.c leaked 364,603 bytes in 4,387 allocations without
     `OSSL_LIB_CTX_free`, and interop.c, which leaks, exited 1 for a good and a bad signature
     alike. The sanitized launcher turns leak detection off (main.c:48-50), so the probe is the
     only leak check. The demo-app run of §11.3 comes in the same step.
2. **Python.** `signing.py` for the statement, the trust store, the names (§2.1), the messages,
   and the seam through which it reaches the four functions and the pins [P25]; the `key`,
   `sign` and `verify` commands; signing in `build`, with the signing variables removed from the
   environment at start and handed only to a dispatched variant [P3]; dispatch from source
   never fetching a variant (§4). Their tests (§11.3): from source, with the stand-in, the
   statement test and a dispatched build; with a built fsbpyr, tests/e2e/test_signing.py, the
   build's processes, the prompt and `fips=yes`; and the session fixture.
3. **Download paths.** `self update`, dispatch and the EL8 stubs download the `.xz` and check it
   before unpacking. Their tests (§11.3) come in the same change: unit tests with the stand-in;
   FakeGitHub moved to tests/support/releases.py; the tagged copy and its build moved from
   test_el8.py to tests/e2e/conftest.py, with the test keys; tests/e2e/test_selfupdate.py (new);
   test_dispatch.py as a subprocess; test_el8.py's real-release tests with signed sums.
4. **Release.** The pinned keys; tools/release/first-signed-release [P20]; the
   `prepare`/`sign`/`publish` split with its permissions and its condition; the sign job's
   compare [P22]; el8.yml's `prepare`, whose sums list each stub's `.xz` too (§5.5), and its
   `publish` changes [P21]; `FSBPYR_RECOVERY` in both workflows; check-release.sh [P23] and its
   tests.
   - The workflow shape tests change: tests/integration/test_release.py:1490 and :1766
     (publish's `needs: [build, sbom]`), tests/integration/test_el8_workflow.py:55 (seven
     checkouts) and :133-146 (el8.yml's jobs, `permissions:` in publish only). They gain what
     §5.3 states: `environment: release-signing` in `sign` only; the secrets in one step's `env`;
     in `sign`, no fsbpyr and no action but checkout, download-artifact and upload-artifact;
     `sign` with `contents: read` and `needs: prepare`; `prepare` with `contents: read` and no
     secret; publish's `if:` starting with `!cancelled()`
     and naming `FSBPYR_RECOVERY`; no built code in `publish`. How GitHub applies them: §11.2.
5. **The rest.**
   - The script's fixes (§11.4), and its pytest test with the requires.py condition [P26].
   - tools/test-setup.sh installs libssl-dev for the declaration check [P14], and its header
     names openssl.
   - The wiki (§7), and a CHANGELOG "Added" entry, so the first signed release is the next minor.
   - The man page: the new commands and `--signing-key`; under ENVIRONMENT
     `FSBPYR_SIGNING_KEY`, `FSBPYR_SIGNING_PASSPHRASE` and `XDG_CONFIG_HOME`, used only when it is
     an absolute path, as `XDG_CACHE_HOME` is; under FILES the trust store. The test that keeps
     ENVIRONMENT complete finds a variable only in a `getenv` or `environ.get` call
     (tests/unit/test_cli_docs.py:224), not one removed with `os.environ.pop`, so it names the
     two signing variables.
   - The security docs, and, in the same merge, the lines that still promise a D31 signature
     checked with an outside key:
     - docs/IMPLEMENTATION_PLAN.md:1522 becomes "signed releases (a detached signature of
       `SHA256SUMS`, checked with keys fsbpyr pins, docs/signing.md;";
     - :1574-1575: signing adds checks before `inspect --verify`, of `SHA256SUMS` and its `.sig`
       against the pinned keys, then of the `.xz` against its line before lzma reads it, and
       `inspect --verify` stays the D31 check;
     - :2390 keeps its third cell and gains, as other rows do (:2386), "(2026-10-29:
       docs/signing.md signs `SHA256SUMS` and apps' files instead; the statement carries the
       file's SHA-256, not D31)";
     - docs/security.md:70-71, docs/format.md:66, wiki/Dev-Executable-Format.md:109 and
       wiki/Dev-Security.md:660.

## 13. Proposed in the test round, accepted by the owner

The test round made these choices where §10 left the matter open. The owner accepted all of them
as written on 2026-10-30. P19 and what P18 and P21 say of K in CI went with the decisions of
2026-10-31 (§10): §5 is what holds.

- **P1 The build's processes.** The variables leave fsbpyr's environment, and nothing more is
  done in the process; §3.1 states the limits, and untrusted builds sign in a separate job.
  Instead: `prctl(PR_SET_DUMPABLE, 0)`, which closes only fsbpyr's own /proc files to same-uid
  processes without CAP_SYS_PTRACE and breaks mp.py's parent check; an in-place wipe of the
  environment; a re-exec; a passphrase file.
- **P2 The collection child** drops both signing variables before its exec (§3.1). Instead: no
  collection while either variable is set.
- **P3 Dispatch.** The dispatcher removes the variables at start, hands them to the variant
  only, and opens no key (§3.1). Instead: exec the variant (the tests call `main()`
  in-process); the dispatcher signs the variant's output (two places know the output); a pipe.
- **P4 The passphrase** comes from the variable or /dev/tty, never from standard input; an empty
  variable is unset; 1 to 1024 bytes; raw bytes (§2.3). Instead: getpass's fallback to standard
  input; no length check, so that OpenSSL's reader fails later.
- **P5 Ctrl-C at the prompt** exits 130 and keeps the output without a `.sig`; end of input is a
  missing passphrase (§3.1). Instead: remove the output on Ctrl-C.
- **P6 Names.** The grammar matches the whole name; a typed name must be ASCII and is then
  normalized; a name from build info is used as it is, and an invalid one is NOT VERIFIED,
  unprinted (§2.1, §3). Instead: exit 2 for an invalid build-info name; refuse typed names that
  are not already normal ("give my-app").
- **P7 Names over 64 characters** cannot be signed (§2.1). Instead: a limit of 200, with
  statements of 101–300 bytes.
- **P8 The subject fsbpyr**: `sign` refuses it, from build info or `--project`, and an app named
  fsbpyr builds unsigned (§2.1). Instead: sign it, and say that such a signature never verifies.
- **P9 `key trust`**: PUBKEY read once into the trust store and checked there, the whole
  fingerprint in either case, exit 2 for every refusal, `_verify` for the `.pub` (§3). Instead:
  exact-case fingerprints only; exit 1 for a wrong fingerprint; a fifth C function for a `.pub`.
- **P10 VERIFIED through a sums file** names what FILE is listed as (§3, §7). Instead: also the
  version from build info; the listed name must equal the local name; `release_asset` must match.
- **P11 Links.** The `.sig` goes beside the link's target, `verify` follows symlinks, and a
  command named `<output>.sig` fails the build (§3, §3.1). Instead: `verify` refuses a symlink
  and names its target; a `.sig` per command link; LINK.sig first, then the target's.
- **P12 A relative `XDG_CONFIG_HOME` or `HOME`** is ignored, and names from files print as
  repr() when not printable (§3). Instead: none was weighed; both follow the existing rules for
  the cache and for `inspect`.
- **P13 The C interface**: a low-level `_sha256` of a file or bytes; a fresh private context in
  each call of `_key_new`, `_sign` and `_verify`; the error queue's mark and pop; `_sign` returns
  the `.pub`; `_verify` raises for a refused `.pub`; the passphrase through a callback (§4).
  Instead: `_sha256` through EVP (six more declarations, and it reads the config); one kept
  context (0.4 ms less, shared state); `ERR_clear_error` (drops the caller's errors).
- **P14 Declarations by hand**, compiled in the suite against Debian's libssl-dev 3.5 headers
  (§4). Instead: vendored 3.5.9 headers (53 files, about 26,000 lines, `configuration.h` per
  build); hand declarations with no check, as integrity.c has them.
- **P15 The wrappers** stay in sig.c with their own method table (§4). Instead: module.c, which
  has 837 lines.
- **P16 Randomized ML-DSA only**, and `_sign` tested by checking what it returns (§2.2);
  independent vectors (RFC 8032, ACVP) through sig.c's check of one half, with PEM fixtures
  (§11.3). Instead: a private, test-only deterministic argument with vectors compared byte for
  byte (the OSSL_PARAM layout then joins the declarations); only the script's vectors through
  `_verify`; a stand-alone EVP test (it checks OpenSSL, not sig.c); all of Wycheproof's Ed25519
  and ML-DSA-65 cases (391).
- **P17 A strict `.key` reader**, in the script too, whose openssl commands run with
  `OPENSSL_CONF=/dev/null` (§2.3, §11.4). Instead: a lenient one that relies on the check of the
  derived public keys and on the sign job's pin check;
  `-provparam ml-dsa.output_formats=seed-priv` on `openssl pkcs8` (also 6088 bytes, measured).
- **P18 Write access is trusted as the admin is**; the GitHub App is removed or trusted before
  the first signed release; the suite compares the pins with the wiki's list (§5.1, §5.2).
  Instead: a ruleset on main with code-owner review (every change, the admin's too, then needs a
  pull request); a fingerprint list in the annotated tag that the sign job compares; a review of
  the diff before each release; a pin guard in release.sh (a writer puts a pin elsewhere); the
  fingerprint list in each key-changing CHANGELOG entry rather than the wiki, for the suite to
  compare.
- **P19 `FSBPYR_RECOVERY` stays a repository variable** (§5.4). Instead: a variable of the
  environment `release-signing` that `sign` reads and passes on; a switch in the annotated tag's
  message; recovery as "K's secret is empty".
- **P20 The previous release** comes from GitHub's release list, and the first signed release is
  named in tools/release/first-signed-release (§5.2, §5.3). Instead: git tags with a rule for
  burned tags; `/releases/latest`; no file, the first one inferred from the list; a small job
  with gh that outputs the tag.
- **P21 el8.yml gets a `prepare` job**; its `publish` needs the `.sig`, keeps drafts in recovery
  and stops at a draft (§5.3). Instead: `publish` writes the sums, `sign` writes them again,
  `cmp` compares; drafts deleted in recovery too, with a warning not to re-run.
- **P22 release.yml's `sign` writes `SHA256SUMS` again and compares**, and `publish` checks its
  files against the sums (§5.3, §9). Instead: `prepare` makes only install.sh and `sign` writes
  the sums; the build job records each executable's glibc, so `prepare` goes; no compare, and §8
  says that `prepare`'s code chooses every line K signs.
- **P23 check-release.sh**: signed if the tag pins keys; the OpenSSL ML-DSA check up front; the
  new previous-release rule; `SHA256SUMS`'s attestation (§5.6). Instead: a version constant, as
  0.26.0's exception; gating on the `.sig` being there (misses a missing `.sig`); openssl only
  for signed releases.
- **P24 `--signing-key` has no `[tool.fsbpyr]` key** (§3, §7). Instead: a `signing-key` key
  (input the project controls, a path of one machine); a per-user config file, a new layer.
- **P25 Test seams**: the tagged copy in tests/e2e/conftest.py; test keys made per session, none
  committed; the stand-in through signing.py, not `sys.modules`; the session fixture; dispatch
  from source never fetching; the download paths tested in a built fsbpyr, dispatch as a
  subprocess to a real variant built on the other Python (§4, §11.3). Instead: committed test
  keys; a stand-in backed by the openssl command; a `sys.modules` fake (it breaks `own_version`
  and `builder_uv`, measured); dispatch from source refusing with "signing needs a built fsbpyr";
  a crafted variant with a forged build info; a job between `sign` and `publish` that runs the
  built fsbpyr's `verify` on the release's files; `make release-check` alone.
- **P26 The script's pytest test** with a requires.py condition, and no shellcheck step (§11.3,
  §11.4). Instead: rely on the script's exit 2; a minimum version in `Tool`; shellcheck in `make
  check`, which then needs it everywhere.

## 14. What was built

All of §12, on branch `signing-openssl`, as first built, with K in CI. The last section says what
replaced that, and §5 is what holds; this list and the rehearsal stay as the record. What differs
from the text above, or adds to it:

- **`_sha256`'s hashing is in integrity.c** (`fsb_sha256_file`, `fsb_sha256_bytes`), beside the
  declarations of the low-level SHA-256 it uses; sig.c holds the wrapper. So sig.c declares only
  what compiles against the real headers: SHA256_Init's context is opaque here on purpose.
- **Python-less builds of sig.c.** `FSB_SIG_NO_PYTHON` leaves the wrappers out, for the probe.
  `fsb_sig_half` is the check of one half that §11.3 asks for.
- **The trust store goes through `secure_directory`**, as the cache does: another user who can write
  on the way to it could plant a key, which §3 does not mention and which an absolute
  `XDG_CONFIG_HOME` under a shared directory allows.
- **Two scripts more.** `tools/release/previous-release.sh` is §5.3's lookup, and
  `tools/release/check-signature.sh` is its check against both sets of pins, both used by the sign
  jobs and by check-release.sh, so that there is one of each.
- **`make release` refuses a tree that pins no keys**: the sign job would refuse it after the tag
  burned the version. That is not P18's pin guard, which is about a writer who means harm.
- **The sign jobs install their packages before the checkout**, not after it as §5.3's order has
  it: the image has no git, and without git the checkout takes a tarball, with no tags in it.
  Nothing else differs, and K is still in no step but `Sign`.
- **Both the `prepare` and `sign` sums are written with `LC_ALL=C`**, so the globs sort alike on
  the runner and in the image.
- **`publish` writes the sums again and compares**, rather than `sha256sum -c`, so that a file too
  many stops it as well: the steps after it take what the globs find. el8.yml's `publish` checks
  its stubs too, and the sign job checks that the build's files are exactly the expected assets,
  since `pattern: built-*` takes any artifact of the run of such a name.
- **A label is not trusted either.** `_sign` refuses a block that never asked for the passphrase,
  which a plain key under the label of an encrypted one does not, and the script checks for PBES2.
- **`self update` also requires the version of the tag it asked for**, or the message would name a
  release that was not installed. It gives no freshness: the release document is not signed.
- **Sums files are split at LF alone**, with one marker before the name, since `str.splitlines`
  also cuts at characters that `sha256sum` leaves as they are in a name, which would let a file's
  name add a line to a sums file someone signs. §3 says they are read as stubs.py read them.
- **Download URLs are http or https**, with limits on the release document, whose text reaches the
  terminal through `shown`.
- **`verify` and `sign` follow links alike**: `sign` writes beside the file a link leads to, where
  `verify` looks.
- **A dispatched build is tested as a subprocess**, the fsbpyr that dispatches built from the same
  tagged copy as the variant, not in-process from source as §11.3 has it: with §4's rule, from
  source never fetches, so the in-process form would test a stand-in.
- **`tests/fixtures/signing/`**: Wycheproof's two files (Apache-2.0, with their licence), the
  independent vectors, and a fixed `.pub` with two signatures, whose key was thrown away, so that a
  later change of the format fails the suite.
- **Fault injection.** `EVP_DigestVerify` is wrapped in the probe to return what no input can make
  it return, which is the only way to test that a negative result is no pass.

### The rehearsal, 2026-10-30

The release path ran on GitHub in a test repository, devitops-com/fsbpyr-signing-test, with
immutable releases on, throwaway keys and fake assets: `prepare`, `sign` and `publish` of both
workflows as they are here, with only what builds and tests replaced, and `FSBPYR_RELEASES_API` set
in the check of the clients. What it showed:

- **The tag rule.** A job of the environment on `main`, and on a branch named `v1`, stopped with
  "Branch … is not allowed to deploy to release-signing"; on the tag it ran. So a rule `v*` of type
  tag does not match a branch of that name.
- **Secrets.** `gh secret set` took K from standard input and the passphrase from a pipe. The secret
  returned K without its last LF (6087 bytes, the digest of the file without it), which §2.3 allows.
- **The first signed release** passed against its own pins alone, in the pinned image: 35 assets
  with `SHA256SUMS.sig`, the attestation of `SHA256SUMS` from the tag, and a companion with its
  `.sig` and `--latest=false`.
- **A planned change of K** took v0.3.0 signed by old K with three pins, then v0.4.0 signed by new K.
- **A wrong key in the secret** signed, and the check of the clients stopped the job; **a failed
  build and an unset secret** also published nothing, and the tag was burned.
- **A recovery**: `FSBPYR_RECOVERY=1` skipped `sign` and left drafts, the draft's sums had the tag
  run's attestation, R's signature passed both sets of pins, and both were published by hand. The
  next release was signed by the new K.
- **A `.sig` after publication** is refused: HTTP 422, "Cannot upload assets to an immutable release".
- **A built fsbpyr** with the throwaway keys pinned took the signature of GitHub's sign job, and
  refused a changed file and sums signed by a key it does not pin.
- **A re-run after a change of K** (`gh workflow run el8.yml --ref v0.2.0`, with a K that tag does
  not pin) failed in `sign`, NOT VERIFIED, and published nothing, as §5.3 says.
- **`attest-build-provenance` failed once**, in `publish` after `sign`: "error fetching tlog entry
  - (404) Not Found", during a degradation of GitHub's service. Nothing was published. In release.yml
  the same failure would burn the version, as it can today.

Not rehearsed: `make release-check`, whose repository is fixed; anything of the write role (§11.2).

### A stolen account, played, 2026-10-31

The owner asked what happens when fsbpyr on a user's machine is compromised, and when the GitHub
repository is hacked. Both ran in the test repository, with real executables of this branch, real
builds of an attacker's code (which only proves whose code runs), the throwaway keys and isolated
homes. What the first design did:

- **Without K**, eight forged releases were refused, nothing changed and no file stayed behind:
  two published on GitHub, no signature and another key's; the rest from a local server, reusing
  genuine signed files with a hacked `.xz`, edited or missing sums, a truncated `.sig`, a release
  called newer than it is, and a replay. Being told the latest is old gave "up to date": updates
  can be held back.
- **With a tag push, K signed the backdoor.** A commit not on main went through the unchanged
  pipeline. `self update` installed it, a genuine `verify` said VERIFIED, the attestation passed,
  and a new user with `FSBPYR_VERIFY=1` got it. That is why K left GitHub.
- **A rebuilt evil fsbpyr of the same version** passed its own `--version`, `verify`, `inspect
  --verify` and `self update`, and a genuine fsbpyr's `inspect --verify`. `sha256sum` against the
  sums, the attestation and a fresh fsbpyr's `verify` each caught it; a `sha256sum` earlier on PATH
  lied. One flipped byte stopped it at start, exit 70.
- **The installer restored it**: it did not run the old file, and replaced a tampered completion.
  A poisoned cached variant and a planted trusted key survived it, and ran and passed under the
  genuine fsbpyr, until they were removed.

The same repository then ran what §5 describes, with no key on GitHub:

- **CI** left two unsigned drafts, which the public cannot see (HTTP 404).
- **`make release-sign` refused** each of these on GitHub's real API, and the draft stayed as it
  was: a commit pushed to main, a file swapped under the attested sums, the sums rewritten to match,
  a key the tag does not pin, and an `install.sh` swapped on GitHub after the checks had passed.
- **The genuine release** was signed here and published, by id, with the job records of its real
  run read, and a real fsbpyr updated to its exact bytes. The audit passed on 18 releases, key
  changes and the recovery among them. A run takes 5 to 6 minutes, most of it the audit's.
- **The attack that won above** now ends at a draft nobody can sign. `make release-sign` has no such
  tag. The audit reported it before anything was published. Published by hand and marked the latest,
  it got the two lines of §5.5 and the executable stayed as it was.

**The tests found bugs that the design did not.** With one lookup failing, the audit passed, since
bash does not wait for `<(...)`, and "no secrets" matched the record: now each result is read
before it is compared, and every endpoint has its own test. An independent review found that the
attestation says nothing of where the build ran, the window between the checks and the publication,
and regex dots in a file name: §5.3 has the fixes. Running the wiki's own commands showed that an
attestation check without `--source-ref` passes the attacker's build. Mutation testing weakened
each check of the two scripts in turn: 91 mutants, all killed for a valid reason once the survivors
it found had tests, a redundant line had gone and five tests that a second finding had hidden were
made exact.

Not run: whether GitHub routes a hosted label to a self-hosted runner, whose job records would
differ, so that check is proven with the fake only (§5.3); the tag rulesets, as the test repository
has none and there is no second account; and the real K and R, which have signed nothing yet.
