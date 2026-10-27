# fsbpyr executable format — version 7

The single source of truth for this format is `src/fsbpyr/payload/format.py`.
`src/fsbpyr/launcher/format.h` mirrors it, and a test checks that the two agree.
All integers are little-endian.

## Executable layout

```
0                     payload_offset                         file_size - 64      file_size
| stub ELF | zero pad |  payload (header, tables, blob data)  |  trailer (64 B)  |
```

- The stub is an unmodified ELF. The loader maps only its `PT_LOAD` segments, so the appended
  bytes are inert.
- `payload_offset` is a multiple of 65536 (4096 up to version 6), so that the payload can be
  mapped with `mmap` directly on kernels with 4K, 16K and 64K pages (version 7). Tested on
  4K-page kernels; 16K- and 64K-page aarch64 kernels are smoke-tested in QEMU (`make test-vm`),
  not on hardware.
- The payload ends exactly where the trailer begins:
  `payload_offset + payload_size + 64 == file_size`.

## Trailer (64 bytes, at EOF)

| Offset | Size | Field |
|--------|------|-------|
| 0 | 8 | magic `FSBPYR\0T` |
| 8 | 4 | `format_version` (= 7) |
| 12 | 4 | `flags` (reserved, 0) |
| 16 | 8 | `payload_offset` (absolute file offset) |
| 24 | 8 | `payload_size` |
| 32 | 32 | `digest`: integrity digest of everything before it (versions 1 and 2: SHA-256 of the payload) |

## Integrity (D31: version 3; D32: version 4)

The integrity digest of some bytes is the SHA-256 over the concatenated SHA-256 digests of each
1 MiB piece (`DIGEST_LEAF`) of them; the last piece may be shorter, and no bytes have no pieces.
It is the one digest construction of the format, computed by one routine in each language
(`fsb_tree_digest` in the launcher, `tree_digest` in `format.py`; a contract test holds them
equal), for two things:

- the executable: its last 32 bytes are the digest of everything before them (stub, padding,
  payload and the first 32 bytes of the trailer);
- every file the runtime extracts: DIGESTS holds its digest (format versions 1 to 3: plain
  SHA-256).

- The launcher checks its file before it reads any other byte of it: before the trailer, the
  payload or CPython. It hashes the pieces on min(8, usable cores) threads, one per 4 pieces,
  with the SHA-256 of the static OpenSSL (SHA-NI where the CPU has it). On a mismatch it prints
  `fsbpyr: cannot start <path>: integrity check failed: the file was modified or damaged` and
  exits with status 70. The time
  it took is `integrity_ns` in `FSBPYR_TRACE`.
- `fsbpyr inspect` shows the digest; `fsbpyr inspect --verify` recomputes it. Standard tools
  give the same digest, so a published one can be checked without trusting fsbpyr:

  ```sh
  head -c -32 app | split -b 1M --filter='openssl dgst -sha256 -binary' | sha256sum
  tail -c 32 app | od -An -tx1 | tr -d ' \n'   # the stored digest
  split -b 1M --filter='openssl dgst -sha256 -binary' < cached.so | sha256sum   # a cached file
  ```

- What it detects: any change to the file that leaves the stored digest alone, such as
  damage, truncation or a patch. What it cannot detect: a change that also rewrites the digest
  or removes the check, since the checking code is in the same file. Provenance against
  deliberate modification needs a signature checked with a key from outside the file (planned
  with the release system). Damage to the launcher's own code can also crash it before the check
  runs, without the message. The check reads the file a leaf at a time on its threads and never
  maps it (D36), so a file that shrinks or fails to read while it is hashed (a failing disk, a
  network file system) fails the check like a changed one, and its size never shows in the
  process's resident memory. A cached file that shrinks or fails to read while it is hashed
  counts as changed and is restored.
- Files extracted to the cache (natives, FS_REQUIRED packages, resources asked for as paths)
  run outside the file, so each process hashes them against DIGESTS once, before it first uses
  them, with the same routine: the launcher's `_fsbpyr.sync` (D35) takes up to 1024 files of the
  expected size per parallel pass (those up to 64 KiB read into a buffer, at most 64 larger ones
  held open and read a leaf at a time by the hashing threads, D36), and checks data about to be
  written the same way; a changed file, or one that cannot be read, is reported on stderr and
  restored. When the files a pass holds leave no descriptor or memory to open the next file or
  its directories, the pass is checked first; if that is not enough, the run fails. A file
  changed while a process runs is noticed by the next one. Files are written under a temporary
  name a window at a time, hashed as they are written, in pieces of at most 1 MiB (a piece of
  zeros is left a hole), then renamed if their digest is right, without fsync: one a crash left
  incomplete fails its check and is restored. Content is decoded a window of at most 64 MiB at
  a time, and the digest keeps a bounded window of leaf digests, so neither a file's size nor
  the number of files (synced 4096 at a time) sets the memory a sync needs (D38).
- `fsbpyr inspect` shows the stored digest, which proves nothing by itself; `--verify` and the
  shell recipe compute it from the bytes.

## Payload header (208 bytes, at payload offset 0)

| Offset | Size | Field |
|--------|------|-------|
| 0 | 8 | magic `FSBPYR\0H` |
| 8 | 4 | `format_version` (= 7) |
| 12 | 4 | `header_size` (= 208) |
| 16 | 4 | `python_hexversion` of the target CPython (`PY_VERSION_HEX`) |
| 20 | 4 | bytecode magic (`importlib.util.MAGIC_NUMBER`) of the target CPython (3.12 to 3.15) |
| 24 | 32 | `generation_id` (native cache generation, §7 of the plan) |
| 56 | 8 | `app_name` — Str |
| 64 | 8 | `entrypoint` — Str (`module:attr` or `module`; for several commands, newline-separated `name=target` lines, see below) |
| 72 | 8 | `build_info` — Str (JSON for `inspect`; the runtime reads its `resources` key, D27) |
| 80 | 8 × 16 | section table: `(u64 offset, u64 count)` for each section below, in this order |

**Str** is `(u32 offset, u32 length)` into the string table. Every string is stored as UTF-8
followed by one NUL byte, which the length does not count, so C code can use `strtab + offset`
directly as a C string.

**Several commands.** A build of a project with several console scripts stores them in
`entrypoint` as lines `name=target`, a target being `module:attr` or `module`, sorted by name;
a single command stays the plain target. The launcher treats the entrypoint as an opaque string
(`format_version` stays 7: the boot code stored in the same file reads the table). A name matches
`[A-Za-z0-9][A-Za-z0-9_.+-]*`, so no name contains `=` or a newline; the writer rejects only NUL.
At start the boot code picks the command from `argv[0]`'s basename (also without `.py`), else
from `argv[1]` as an exact name, which it removes from `sys.argv`. A start that names no command lists them on stderr (`usage: NAME COMMAND [ARGUMENT...]`) and exits with status 2; an argument that is a path is never a command (it is the restart idiom or a file argument). `argv[0]` is the name a link was started under.

**Scripts.** A target below the module `_fsbpyr_scripts` (a project without a build system, a directory of scripts or a single script,
[Scripts and tools](https://github.com/devitops-com/fsbpyr/wiki/User-Scripts) in the wiki) is started after the boot code puts the target's module
directory (`<exe>/` and the dotted name's path without its last part) first on `sys.path`; a
target without `:attr` is run as `__main__` and the selftest only finds it, never imports it. A
directory below `_fsbpyr_scripts` whose name is no Python name (`sub-dir`) is recorded under its
mangled name (`sub_dir`; likewise a file `check-disk` is `check_disk.py`: each character no
identifier holds becomes `_`, a leading `_` for a digit); the path hook maps an entry named by the on-disk name to the
recorded one, so modules are still looked up by exact dotted names. A module of this root has
`__file__` and `spec.origin` `<dir of the executable>/<path below the root>` (a file next to the
executable, D24), and the path hook claims those directories too (entries with `..` or `.` components are normalized first), so the code is the payload's
while the files a script reads next to `__file__` are the host's; its code objects keep the
payload's `co_filename`. No format change.

All offsets inside the payload are relative to the payload start. The value `0xFFFFFFFF`
(`NONE`) means "no reference" in every u32 index field.

## Sections

| # | Section | Count means | Record size | Alignment |
|---|---------|-------------|-------------|-----------|
| 0 | STRTAB | bytes | 1 | 8 |
| 1 | BLOBS | records | 32 | 8 |
| 2 | MODULES | records | 40 | 8 |
| 3 | FILES | records | 24 | 8 |
| 4 | DISTS | records | 24 | 8 |
| 5 | CLOSURES | u32 words | 4 | 8 |
| 6 | DIGESTS | records | 32 | 8 |
| 7 | STARTUP | u32 words | 4 | 8 |

The MODULES, FILES and DISTS tables are sorted strictly ascending by their key's UTF-8 bytes
(`memcmp` order, shorter prefix first). Lookups are binary searches. Keys are unique.

### BLOBS record (32 B)

| Offset | Size | Field |
|--------|------|-------|
| 0 | 8 | `offset` of the stored bytes; codec 2: index of the block; codecs 4 and 5: index of the first piece |
| 8 | 8 | `stored_size`; codec 2: position of the content inside the block; codecs 4 and 5: number of pieces |
| 16 | 8 | `size` (uncompressed) |
| 24 | 1 | `codec`: 0 = none, 1 = zstd (one frame with content size and checksum), 2 = in a block, 3 = xz, 4 = xz in pieces, 5 = zstd in pieces |
| 25 | 3 | reserved (0) |
| 28 | 4 | `digest`: index into DIGESTS (integrity digest of the uncompressed data) or NONE |

- Stored data is 16-byte aligned and lies after the end of the last section (the blob data
  area).
- `size` is at most 2^62 (D38: no limit of ours below what file systems hold; readers stream
  content larger than a window instead of holding it).
- Blob order on disk: startup code, module code, resources, sources, natives. Within each
  class, blobs are ordered by the first path that references them.
- Identical blobs are stored once; in blocks, identical content is one member whether the spec
  gave it raw or as a zstd frame.
- For codec none, `stored_size == size`. A raw blob of `DIGEST_LEAF` bytes or more may hold
  holes (D38): writers leave whole leaves of zeros unwritten, so a file of zeros stays sparse.
  The launcher reads such blobs from the file (`pread`), never through its mapping, where a
  hole on tmpfs would take a page of memory for every page read.
- **xz (version 5, D33).** A blob of codec 3 is one xz stream (`--best-compression`: xz -6
  with the x86 filter, no check of its own, since the file's integrity digest covers it) that
  decodes to exactly `size` bytes, at most `XZ_PIECE`, with no data after it and at most
  `XZ_MEMLIMIT` (64 MiB) of decoder memory. A library larger than `XZ_PIECE` (4 MiB) is a blob
  of codec 4 instead, with no
  stored bytes of its own: its content is that of `stored_size` (at least 2) consecutive xz
  blobs from index `offset` on, each `XZ_PIECE` bytes but the last, so that
  `(stored_size - 1) * XZ_PIECE < size <= stored_size * XZ_PIECE`. Their records follow the
  block records, their data lies where the library's would, and no other blob of codec 4 and no
  table references them. Only native files (NATIVE_EXT, NATIVE_LIB) may use codecs 3 and 4. The
  launcher decodes them with the statically linked liblzma in `_fsbpyr.read`, without the GIL,
  the pieces of one library in parallel on the routine that hashes the integrity digest.
- **zstd in pieces (version 6, D39).** Content larger than `ZSTD_PIECE` (8 MiB), of any file or
  module, may be a blob of codec 5: like codec 4, with no stored bytes of its own, its content
  is that of `stored_size` (at least 2) consecutive zstd blobs (codec 1: one frame each, with
  content size and checksum) from index `offset` on, each `ZSTD_PIECE` bytes but the last, so
  that `(stored_size - 1) * ZSTD_PIECE < size <= stored_size * ZSTD_PIECE`; their records follow
  the block records as codec 4's do. Writers compress the pieces on up to 8 threads; the
  launcher decodes them on up to 8, a window of `FSB_STREAM_WINDOW / ZSTD_PIECE` (8) at a time
  when it streams. A piece is a frame of its own: the bytes are the same whatever the number
  of threads.
- **Blocks (version 2, D30).** A blob of codec 2 has no stored bytes of its own: its content is
  the `size` bytes at `stored_size` in the content of block `offset`, a zstd blob of at most
  `MAX_BLOCK_SIZE` (1 MiB); the writer references blocks only from members. The writer packs
  every non-empty blob under 64 KiB except natives into blocks, one stream per placement class
  (startup code, code, resources and metadata, sources) filled in blob order: 64 KiB blocks for
  code, which an import decompresses whole, and 256 KiB for the rest. Block records follow all other blob records; a
  block's data sits where its first member would. Readers keep recently decompressed blocks
  (the launcher the last 32), since a package's modules share blocks. Blocks make payloads about
  5% smaller (aiagent: 157.4 → 149.6 MB) for about 3% more startup time on import-heavy apps.

### MODULES record (40 B), keyed by dotted module name

| Offset | Size | Field |
|--------|------|-------|
| 0 | 8 | `name` — Str |
| 8 | 8 | `path` — Str: site-relative origin (`pkg/__init__.py`, `pkg/mod.py`, `pkg/_x.cpython-314-x86_64-linux-gnu.so`, or `pkg` for a namespace package) |
| 16 | 1 | `kind`: 1 MODULE, 2 PACKAGE, 3 NAMESPACE, 4 EXTENSION |
| 17 | 1 | `flags`: bit 0 STARTUP, bit 1 FS_REQUIRED_ROOT |
| 18 | 2 | reserved (0) |
| 20 | 4 | `code` blob (marshalled code object, canonical form) or NONE |
| 24 | 4 | `source` blob or NONE |
| 28 | 4 | `file`: FILES index of the extension's shared object, or NONE |
| 32 | 4 | `dist`: DISTS index, or NONE (stdlib and runtime) |
| 36 | 4 | reserved (0) |

Invariants:

- MODULE and PACKAGE have `code`.
- NAMESPACE has no `code`, `source` or `file`.
- EXTENSION has `file` pointing at a NATIVE_EXT record, and no `code`.
- FS_REQUIRED_ROOT marks regular packages (PACKAGE) only: on first import every file below the
  package's directory is extracted, with the closures of its extensions (libraries outside the
  directory, such as `torchvision.libs`) and every NATIVE_LIB of the distributions its files
  belong to (by their `dist`: plugins it opens from a directory, such as openmm's), and the
  package's modules get `__file__`/`__path__` there.
- Every `code` blob is a zstd frame with the content-checksum flag set, or a member of a block
  (itself such a frame). `marshal.loads` is not safe against corrupt input, so the bytes it gets
  have passed a checksum. The checksum covers a block's content, not which slice of it a member
  selects: like every table field, a member's position and size are not checksummed.
  STARTUP code is decompressed once by the launcher into memory that it then hands to CPython's
  frozen-module table.

### FILES record (24 B), keyed by site-relative POSIX path

| Offset | Size | Field |
|--------|------|-------|
| 0 | 8 | `path` — Str |
| 8 | 1 | `kind`: 1 RESOURCE, 2 SOURCE, 3 NATIVE_EXT, 4 NATIVE_LIB, 5 METADATA |
| 9 | 1 | `flags`: bit 0 FS_REQUIRED |
| 10 | 2 | `mode`: permission bits (≤ 0o777) |
| 12 | 4 | `blob` |
| 16 | 4 | `dist`: DISTS index or NONE |
| 20 | 4 | `closure`: CLOSURES word index or NONE |

Paths are relative, use `/` as separator, and contain no empty, `.` or `..` components and no NUL.
Because the table is sorted, listing a directory is a prefix range scan.

### DISTS record (24 B), keyed by canonical distribution name (PEP 503)

| Offset | Size | Field |
|--------|------|-------|
| 0 | 8 | `name` — Str |
| 8 | 8 | `dist_info` — Str: the `*.dist-info` directory path |
| 16 | 8 | `version` — Str |

### CLOSURES

A sequence of u32 words. A closure starts at a word index `i`: `words[i] = n` (n ≥ 1), followed
by `n` FILES indices in strictly ascending order. Closures list the bundled shared objects that
must be materialized together with a NATIVE_EXT file (its transitive `DT_NEEDED` dependencies
inside the payload). Only NATIVE_EXT records carry a closure, every member is a NATIVE_LIB or
NATIVE_EXT record (an extension may link against another extension's shared object), and a record
never lists itself. The blob of every record the runtime extracts (NATIVE_EXT, NATIVE_LIB and
FS_REQUIRED files) has a digest, which the runtime verifies before publishing the file. Any other
file may be extracted on demand when the application asks for it as a path through
`importlib.resources` (D27); such a file may have no digest and is written as stored, exactly
as it would be read in memory.

### DIGESTS

An array of 32-byte integrity digests (see Integrity; format versions 1 to 3: plain SHA-256).
They are recorded for blobs the runtime extracts on its own (NATIVE_EXT, NATIVE_LIB,
FS_REQUIRED). Resources extracted on demand (D27) may have none.

### STARTUP

u32 MODULES indices, strictly ascending: exactly the modules flagged STARTUP. The launcher reads
this short list to build CPython's frozen-module table, so it never scans the MODULES table.

## Validation rules (readers MUST enforce)

- Trailer and header magic and version must match. `header_size` must be 208. Trailer `flags`
  must be 0. The launcher accepts only version 7; the Python reader also reads 1 to 6.
- A blob of codec 3 or 4 needs format version 5 and belongs only to native files, never to
  another file or a module; a blob of codec 5 needs version 6 and may belong to any file or
  module (its pieces are checksummed frames, so module code may be in them); no file or module
  names a piece directly. The Python reader and the
  writer enforce this; the launcher relies on the integrity digest, which only the writer's
  output passes.
- The launcher checks the integrity digest (above) before any other rule.
- `payload_offset` must be 65536-aligned (4096-aligned in versions 1 to 6), and the file size
  must match exactly.
- Every section must lie inside the payload, start after the header, be 8-byte aligned, and not
  overlap any other section.
- Every Str must lie inside STRTAB, be NUL-terminated, contain no NUL, and be valid UTF-8.
- Every index field must be NONE or in range. Enum fields must hold known values, and flag
  fields must hold known bits only. Reserved fields must be 0.
- Every blob must be 16-byte aligned, lie inside the blob data area, and have `size` ≤ 2^62.
  For codec none, `stored_size == size`. A blob of codec 2 instead names a block (in range, codec
  zstd, `size` ≤ 1 MiB) and lies inside its content (`stored_size + size` ≤ the block's
  `size`); codec 2 is invalid in version 1.
- FILES paths, module paths and `dist_info` paths obey the path rules above. Module names are
  dotted names whose parts are Python identifiers once `-` is read as `_` and a leading digit is
  allowed: CPython imports file stems such as `_sysconfigdata__linux_x86_64-linux-gnu` and
  mypyc's `81d243bd2c585b0f4821__mypyc` through `importlib.import_module`.
  Dist names are canonical.
- The module kind invariants (including checksummed code: a zstd frame, zstd frames in pieces, or inside a block), the
  STARTUP rule (the STARTUP section lists exactly the flagged modules) and the closure rules
  hold.
- Every zstd frame a reader decompresses must have the content-checksum flag set, declare its
  content size, equal to `size`, and end where its stored bytes end.
- On decompression, the output must be exactly `size` bytes, with no trailing data, and must
  pass the zstd checksum. Readers never allocate more than `size + 1` bytes for a blob, and a
  member's whole block (at most `MAX_BLOCK_SIZE`).
- Tables must be strictly sorted.

## Build information

`build_info` is JSON with sorted keys. Since `schema` 1 (REP-3) it is a manifest of what the
executable was built from; a reader treats a missing key as "not recorded" (older builds) and
an unknown `schema` as unreadable. The same inputs give the same `build_info`, whatever the
project's directory: nothing in it names the builder's paths, time or host.

| Key | Content |
|-----|---------|
| `schema` | `1`. Incremented when a key changes meaning, never for a new key |
| `lock` | `{kind, path, sha256, hashed}`: `kind` is `uv.lock`, `script.lock` (uv's `<script>.lock` beside a single script), `pylock.toml`, `requirements`, `poetry.lock`, `pdm.lock`, `Pipfile.lock`, `inferred` or `unlocked`; `path` is relative to the project directory (`../uv.lock` for a workspace member), null if unlocked; `sha256` is of the file; `hashed` says whether it pins artifact hashes (a `Pipfile.lock` does, but its hashes carry no file names, so a distribution of it has no `artifact_sha256`). For `inferred` (a directory of scripts or a single script that declared nothing, whose versions were read from the venv it runs in), `path` is that venv, relative to the project directory (just its directory name when it lies outside), `sha256` is null, `hashed` is false and `pins` lists the `name==version` pins installed |
| `dists` | `[{name, version, source, artifact_sha256, backend}]`, sorted by name: `artifact_sha256` where the lock names the artifact, `backend` for `built-from-sdist` only |
| `runtime` | the runtime's id, `cp<python>-<triple>-<12 hex digits>` (a digest of the launcher, the PBS archive, the sysroot and the layout version: the default Debian 12 sysroot and the EL8 one (`--glibc 2.28`) give different ids); the stub records it too (`fsbpyr-stub-identity=<id>` in its `.rodata`), which is how a build proves a stub it was handed is the one of its own release (D46) |
| `runtime_tag` | the PBS distribution's tag and the target triple (`<tag>-<triple>`); it seeds `generation_id` |
| `python` | the target CPython's version (3.12 to 3.15) |
| `extension_suffixes` | the file name suffixes the runtime's interpreter imports extension modules by, in its order; asked of the interpreter itself since 0.25.0, as PYTHON.json's list lags (3.15.1's lacks `.abi3t.so`) |
| `fsbpyr` | the version of the fsbpyr that built the executable |
| `zstd` | the libzstd version that compressed the payload (see Determinism) |
| `resources` | `sha256` over every payload file's path, content digest and mode, cut to 32 hex digits: the name of the tree where resources extracted on demand live (D27) |
| `runtime_inputs` | `{pbs: {release, name, sha256}, sysroot, layout}`: the python-build-standalone archive and the glibc sysroot the runtime was made from, and its layout version |
| `runtime_components` | versions the runtime's interpreter reports: `openssl`, `sqlite`, `zlib`, `expat`, `libmpdec`. Read once when the runtime is built; an executable that builds another passes them on |
| `stub_sha256` | SHA-256 of the bytes before the payload (the stub and its zero padding): `head -c <payload_offset> app \| sha256sum`. The same in every app built by the same fsbpyr release for the same glibc baseline (the EL8 stub differs from the default one, D46); there is no digest of the builder |
| `settings` | `{entry, fs_packages, allow_system_libs, extras, include_source}`: `fs_packages` is the effective list (the built-in packages and the configured ones) |
| `glibc_baseline` | only in an app built with `--glibc 2.28` (D46): the glibc baseline it is built for, `"2.28"`; an app without the key is built for the default, 2.34; the stub for it is an asset of the companion release `el8-vX.Y.Z` (`fsbpyr-stub-3.X-<target>-glibc2.28`) or one placed next to fsbpyr, and a build accepts it only if its embedded `fsbpyr-stub-identity` equals the runtime id. `fsbpyr inspect --json` reports it as `glibc_baseline` for every app |
| `release_asset` | only in fsbpyr's own executable: the release asset (without `.xz`) it is released as, `fsbpyr-3.X-<target>` (e.g. `fsbpyr-3.14-x86_64-linux-gnu`), the target being the host's name from the target table, which `fsbpyr self update` fetches the same Python's build of. 0.21.0 recorded `fsbpyr-<target>` for the newest Python (published until 0.21.0 only); a value that is not today's form makes `self update` use the asset of the Python the executable runs on |
| `project` | `{name, version}` of the application |
| `fsbpyr_version` | the builder's version, with the commit where there is one (`fsbpyr inspect`) |
| `git` | `{commit, tag}` of the project's HEAD (`tag`: the highest `vX.Y.Z` on it, or null); null without git or outside a repository |
| `uv` | the version of the uv that built the wheel and installed everything |
| `glibc` | the oldest glibc the application runs on (the stub's requirement, raised by bundled native code), e.g. `2.34` |
| `system` | `{libraries, may_load}`: the system libraries the target must provide, and with `--hints` those the Python code may open by name |
| `zstd_level` | the zstd level of the app's content and of every block |
| `native_compression` | `xz -6` or `zstd`: how native files are stored |
| `interpreter_reexec` | what a child process of this executable that asks for Python (`-m MODULE`, `-c CODE`, a `.py` file of the payload) gets: `true` runs it as the interpreter would (D23), `false` runs the app, `null` (not set) refuses it with exit 70 (D44) |

The keys come from `src/fsbpyr/assemble.py` (the runtime's) and `src/fsbpyr/build.py` (the
application's); see D28, D26, NAT-2, D29, D33, D23 and D27 for their history.

`dists[].source` is one of:

- `registry`, `git`, `url`, `path`: the lock's source of the package, and `artifact_sha256` is the
  SHA-256 of the wheel in the lock that shares a tag with the installed wheel's `WHEEL` file.
  A build from `requirements.txt` or without a lock cannot tell which hash belongs to the
  installed wheel, so it records no `artifact_sha256`, and sources by PEP 610 (`direct_url.json`).
- `local`: the project and its editable or directory workspace members. The lock holds no hash
  for them; uv builds their wheels at staging.
- `built-from-sdist`: the lock lists wheels for the package (or only an sdist), but none shares
  a tag with the installed one, so it was built on the build host, pure-Python ones included.
  `artifact_sha256` is the sdist's, and `backend` is the installed `WHEEL` file's `Generator:`
  line. These bytes depend on the host's build backend and environment; the build prints their
  names. Staging sets `SOURCE_DATE_EPOCH` (the user's, else the project's last commit time, else
  315532800) for them.

`fsbpyr inspect --json` has its own `"schema": 1` (the report's, not `build_info`'s) and, with
`--files`, a `files` list of `{dist, path, kind, size, sha256}` taken from the RECORD files in
the payload. `settings` is
`build_info`'s, and `sizes` is the stored size by part (see docs/performance.md, "Where the
bytes go"); neither changes the format.

## Determinism

The same spec always encodes to the same bytes with the same zstd library version. The zstd
version is part of the build environment and is recorded in `build_info` (Phase 4). A
self-hosted fsbpyr binary carries its own statically linked libzstd, so the stage2 == stage3
check is not affected.
