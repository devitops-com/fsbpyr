# Executable format

Format version 7 of the executables fsbpyr v@VERSION@ builds, from
[`docs/format.md`][format-md], checked against [`payload/format.py`][format.py] (the single
source of truth, used by the reader and the writer) and the launcher's mirror of it,
[`format.h`][format.h]. `tests/integration/test_launcher_format.py` compiles a probe against
`format.h` and compares every size, offset and constant with the Python definitions. The writer
is in [Payload modules](Dev-Payload-Modules.md), the launcher's reading in
[Launcher](Dev-Launcher.md). Numbers such as D31 name design decisions recorded in
[`docs/IMPLEMENTATION_PLAN.md`][plan].

## Layout

![Byte layout of an executable: the stub ELF from offset 0, zero padding, the payload from payload_offset (header, eight tables, blob data), and the 64-byte trailer ending in the digest](images/format-layout.svg)

All integers are little-endian. An executable is four parts, back to back:

```
0                     payload_offset                         file_size - 64      file_size
| stub ELF | zero pad |  payload (header, tables, blob data)  |  trailer (64 B)  |
```

- The stub is an unmodified ELF: the [launcher](Dev-Launcher.md) with CPython linked in. The loader
  maps only its `PT_LOAD` segments, so the appended bytes are inert.
- `payload_offset` is a multiple of 65536, so that the payload can be mapped with `mmap`
  directly on kernels with 4K, 16K and 64K pages. 4K-page kernels are tested, 64K-page aarch64
  ones smoke-tested in QEMU ([`tools/vm-smoke.py`][vm-smoke],
  [Testing](Dev-Testing.md#the-page-size-smoke-test)), 16K-page ones neither supported nor
  tested ([`tools/vm-pins.toml`][vm-pins]).
- The bytes between the end of the stub and `payload_offset` are zeros.
- The payload ends where the trailer begins: `payload_offset + payload_size + 64 == file_size`.

The [validation rules](#validation-rules) are all the format asks of where the eight tables go.
fsbpyr's writer (`_layout` in [`payload/writer.py`][writer.py]) puts them after the header in
section-number order, each 8-byte aligned, and the blob data at the next 16-byte boundary.

## Trailer

64 bytes at the end of the file. Python: `TRAILER = struct.Struct("<8sIIQQ32s")`; C:
`struct fsb_trailer`.

| Offset | Size | Field |
|---|---|---|
| 0 | 8 | magic `FSBPYR\0T` |
| 8 | 4 | `format_version` (7) |
| 12 | 4 | `flags` (reserved, 0) |
| 16 | 8 | `payload_offset`: absolute file offset of the payload |
| 24 | 8 | `payload_size` |
| 32 | 32 | `digest`: the integrity digest of everything before it ([since version 3](#format-versions)) |

## Integrity digest

The integrity digest of some bytes is the SHA-256 over the concatenated SHA-256 digests of each
1 MiB piece (`DIGEST_LEAF`, 2^20 bytes) of them; the last piece may be shorter, and no bytes
have no pieces. In Python:

```python
root = hashlib.sha256()
for start in range(0, len(data), 1 << 20):
    root.update(hashlib.sha256(data[start : start + (1 << 20)]).digest())
digest = root.digest()
```

It is the format's one digest construction, computed by one routine in each language
(`tree_digest` in `format.py`; `fsb_tree_digests` and its one-buffer form `fsb_tree_digest` in
the launcher's `integrity.c`). The executable's last 32 bytes are the digest of everything
before them, the trailer's first 32 bytes included, and [DIGESTS](#digests) holds the digest of
every file the runtime extracts.

Standard tools give the same digest, so a published one can be checked without trusting fsbpyr:

```sh
head -c -32 app | split -b 1M --filter='openssl dgst -sha256 -binary' | sha256sum
tail -c 32 app | od -An -tx1 | tr -d ' \n'   # the stored digest
split -b 1M --filter='openssl dgst -sha256 -binary' < cached.so | sha256sum   # a cached file
```

`fsbpyr inspect --verify` and the recipe compute it from the bytes; the launcher checks it before
it reads any other byte of its file ([integrity.c](Dev-Launcher.md#integrityc-and-integrityh)).
It catches damage, truncation and patches, not a change that also rewrites the digest or removes
the check ([Known limits](Dev-Security.md#known-limits)); provenance needs a signature checked
with a key from outside the file ([Security](User-Security.md#signing-your-own-apps), which
signs a file's SHA-256, not this digest). Each process hashes the cached files it uses
(natives, FS_REQUIRED packages, resources asked for as paths) against DIGESTS once, before it
first uses them, and restores a changed or unreadable one
([cache.c](Dev-Launcher.md#cachec-and-cacheh)).

## Payload header

208 bytes at payload offset 0. C: `struct fsb_header`. Python:
`HEADER = struct.Struct("<8sIII4s32s" + "II" * 3 + "QQ" * 8)`.

| Offset | Size | Field |
|---|---|---|
| 0 | 8 | magic `FSBPYR\0H` |
| 8 | 4 | `format_version` (7) |
| 12 | 4 | `header_size` (208) |
| 16 | 4 | `python_hexversion` of the target CPython (`PY_VERSION_HEX`) |
| 20 | 4 | bytecode magic (`importlib.util.MAGIC_NUMBER`) of the target CPython (3.12 to 3.15) |
| 24 | 32 | `generation_id`: the native cache generation |
| 56 | 8 | `app_name`, a Str |
| 64 | 8 | `entrypoint`, a Str: `module:attr` or `module`, or [several commands](#several-commands) |
| 72 | 8 | `build_info`, a Str: JSON for `inspect` ([Build information](#build-information)); the runtime reads its `resources` (D27) and `interpreter_reexec` (D44) keys |
| 80 | 8 × 16 | section table: `(u64 offset, u64 count)` for each section, in section-number order |

**Str** is `(u32 offset, u32 length)` into the string table (STRTAB). Every string is UTF-8
followed by one NUL byte that the length does not count, so C code can use `strtab + offset` as
a C string.

All offsets inside the payload are relative to its start. `0xFFFFFFFF` (`NONE`) means "no
reference" in every u32 index field.

The launcher also requires its own CPython's bytecode magic and the same major and minor
`python_hexversion` ([step 6](Dev-Launcher.md#from-execve-to-the-boot-code)).

### Several commands

A build with several console scripts
([Several commands in one executable](User-Building.md#several-commands-in-one-executable)) stores
them in `entrypoint` as `name=target` lines sorted by name, a target being `module:attr` or
`module` (`command_table` in [`closure.py`][closure.py]); a single command stays the plain
target. The launcher hands the entry point unread to the boot code (`_fsbpyr.info()`), which
parses the table ([Boot modules](Dev-Boot-Modules.md#several-commands-in-one-executable)), so
the format version stays 7. A command name matches `[A-Za-z0-9][A-Za-z0-9_.+-]*`
(`COMMAND_NAME` in [`links.py`][links.py]; the build fails on any other), so it holds no `=`,
`/` or newline; the payload writer itself rejects only a NUL, as in any string.

### Scripts

A target below the module `_fsbpyr_scripts` is the entry point of a project without a build
system, a directory of scripts or a single script
([Projects without a build system](User-Scripts.md#projects-without-a-build-system),
[A directory of scripts](User-Scripts.md#a-directory-of-scripts),
[A single script](User-Scripts.md#a-single-script)). None of it changes the format. The boot code
puts the target's module directory first on `sys.path` (`_script_directory` in
[`_fsbpyr_boot.py`](../src/fsbpyr/boot/_fsbpyr_boot.py)); a target without `:attr` runs as
`__main__`, and the selftest only finds it. A name below `_fsbpyr_scripts` that is no Python name
is recorded mangled (`sub-dir` as `sub_dir`, `check-disk` as `check_disk.py`), and its modules
show their `__file__` beside the executable (D24)
([The scripts root](Dev-Boot-Modules.md#the-scripts-root)).

## Sections

| # | Section | Count means | Record size | Alignment |
|---|---|---|---|---|
| 0 | STRTAB | bytes | 1 | 8 |
| 1 | BLOBS | records | 32 | 8 |
| 2 | MODULES | records | 40 | 8 |
| 3 | FILES | records | 24 | 8 |
| 4 | DISTS | records | 24 | 8 |
| 5 | CLOSURES | u32 words | 4 | 8 |
| 6 | DIGESTS | records | 32 | 8 |
| 7 | STARTUP | u32 words | 4 | 8 |

MODULES, FILES and DISTS records start with their key, a Str, and are sorted strictly ascending
by its UTF-8 bytes (`memcmp` order, the shorter first on a common prefix, as Python orders
`bytes`); keys are unique, and lookups are binary searches.

### BLOBS record (32 bytes)

Python: `BLOB = struct.Struct("<QQQBBHI")`; C: `struct fsb_blob`.

| Offset | Size | Field |
|---|---|---|
| 0 | 8 | `offset` of the stored bytes; codec 2: index of the block; codecs 4 and 5: index of the first piece |
| 8 | 8 | `stored_size`; codec 2: position of the content inside the block; codecs 4 and 5: number of pieces |
| 16 | 8 | `size` (uncompressed) |
| 24 | 1 | `codec` (below) |
| 25 | 3 | reserved (0): a u8 and a u16 |
| 28 | 4 | `digest`: index into DIGESTS (integrity digest of the uncompressed data), or NONE |

| Codec | Name | Content | Since |
|---|---|---|---|
| 0 | NONE | the stored bytes; `stored_size == size` | 1 |
| 1 | ZSTD | one zstd frame with content size and checksum | 1 |
| 2 | BLOCK | `size` bytes at position `stored_size` of the content of block `offset` | 2 |
| 3 | XZ | one xz stream, native files only | 5 |
| 4 | PIECES | the content of `stored_size` xz blobs from index `offset` on | 5 |
| 5 | ZSTD_PIECES | the content of `stored_size` zstd blobs from index `offset` on | 6 |

- Stored data lies after the last section (the blob data area). `size` may reach 2^62 (D38: no
  limit of fsbpyr's below what file systems hold; readers stream large content).
- Blob order: startup code, module code, resources, sources, natives, each class by the first
  path that references a blob; the records follow it, then the block records, then the pieces'
  (`_Blobs` in [`payload/writer.py`][writer.py]). Identical blobs are stored once; in blocks,
  identical content is one member whether the spec gave it raw or as a zstd frame.
- **Raw blobs and holes (D38).** Writers leave whole leaves of zeros of a raw blob unwritten, so
  one of `DIGEST_LEAF` bytes or more may hold holes and a file of zeros stays sparse. The
  launcher reads such blobs with `pread` ([payload.c](Dev-Launcher.md#payloadc-and-payloadh)).
- **Blocks (D30).** A blob of codec 2 has no stored bytes: its content is in block `offset`, a
  zstd blob of at most `MAX_BLOCK_SIZE` (1 MiB) that only members reference. The writer packs
  every non-empty blob under 64 KiB except natives into blocks, one stream per placement class
  (startup code, code, resources and metadata, sources) in blob order: 64 KiB blocks for code,
  which an import decompresses whole, 256 KiB for the rest. A block's data sits where its first
  member's would. Readers keep recently decompressed blocks (the launcher the last 32), since a
  package's modules share blocks.
- **xz (D33).** A blob of codec 3 is one xz stream (`--best-compression`: xz -6, with the x86
  BCJ filter on x86-64, no check of its own, since the integrity digest covers it) of exactly
  `size` bytes, at most `XZ_PIECE` (4 MiB), with no data after it and at most `XZ_MEMLIMIT`
  (64 MiB) of decoder memory. A larger library is a blob of codec 4: the content of
  `stored_size` (at least 2) consecutive xz blobs from `offset` on, each `XZ_PIECE` bytes but the
  last:
  `(stored_size - 1) * XZ_PIECE < size <= stored_size * XZ_PIECE`. The pieces' data lies where
  the library's would, and nothing else references them. The launcher decodes xz with the static
  liblzma in `_fsbpyr.read`, without the GIL, pieces in parallel.
- **zstd in pieces (D39).** Content over `ZSTD_PIECE` (8 MiB), of any file or module, may be
  codec 5: as codec 4, with zstd pieces (codec 1 frames with content size and checksum) of
  `ZSTD_PIECE` bytes. Writers and the launcher use up to 8 threads for them, the launcher
  decoding a window of `FSB_STREAM_WINDOW / ZSTD_PIECE` (8) pieces at a time when it streams;
  each piece is its own frame, so the bytes do not depend on the number of threads.

### MODULES record (40 bytes), keyed by dotted module name

Python: `MODULE = struct.Struct("<IIIIBBHIIIII")`; C: `struct fsb_module`.

| Offset | Size | Field |
|---|---|---|
| 0 | 8 | `name`, a Str |
| 8 | 8 | `path`, a Str: the site-relative origin (`pkg/__init__.py`, `pkg/mod.py`, `pkg/_x.cpython-314-x86_64-linux-gnu.so`, or `pkg` for a namespace package) |
| 16 | 1 | `kind`: 1 MODULE, 2 PACKAGE, 3 NAMESPACE, 4 EXTENSION |
| 17 | 1 | `flags`: bit 0 STARTUP, bit 1 FS_REQUIRED_ROOT |
| 18 | 2 | reserved (0) |
| 20 | 4 | `code` blob (marshalled code object, canonical form), or NONE |
| 24 | 4 | `source` blob, or NONE |
| 28 | 4 | `file`: FILES index of the extension's shared object, or NONE |
| 32 | 4 | `dist`: DISTS index, or NONE (standard library and runtime) |
| 36 | 4 | reserved (0) |

Invariants:

- MODULE and PACKAGE have `code`.
- NAMESPACE has no `code`, `source` or `file`.
- EXTENSION has `file` pointing at a NATIVE_EXT record, and no `code`.
- FS_REQUIRED_ROOT marks regular packages (PACKAGE) only: on first import every file below the
  package's directory is extracted, with the closures of its extensions (libraries outside the
  directory, such as `torchvision.libs`) and every NATIVE_LIB of the distributions its files
  belong to (plugins it opens from a directory, such as openmm's), and its modules get
  `__file__` and `__path__` there.
- Every `code` blob is a checksummed zstd frame, zstd frames in pieces, or a member of a block,
  since `marshal.loads` is not safe against corrupt input. The checksum covers a block's
  content, not which slice a member selects: like every table field, a member's position and
  size are not checksummed. The launcher decompresses STARTUP code once, into memory it hands to
  CPython's frozen-module table.

### FILES record (24 bytes), keyed by site-relative POSIX path

Python: `FILE = struct.Struct("<IIBBHIII")`; C: `struct fsb_file`.

| Offset | Size | Field |
|---|---|---|
| 0 | 8 | `path`, a Str |
| 8 | 1 | `kind`: 1 RESOURCE, 2 SOURCE, 3 NATIVE_EXT, 4 NATIVE_LIB, 5 METADATA |
| 9 | 1 | `flags`: bit 0 FS_REQUIRED |
| 10 | 2 | `mode`: permission bits (at most `0o777`) |
| 12 | 4 | `blob` |
| 16 | 4 | `dist`: DISTS index, or NONE |
| 20 | 4 | `closure`: CLOSURES word index, or NONE |

Paths are relative, use `/` as separator, and contain no empty, `.` or `..` component, no
backslash and no NUL (`check_path` in `format.py`). The files below `dir` are the run of keys
from the first starting with `dir/` to the first starting with `dir0` (`'0'` follows `'/'`).

### DISTS record (24 bytes), keyed by canonical distribution name (PEP 503)

Python: `DIST = struct.Struct("<IIIIII")`; C: `struct fsb_dist`.

| Offset | Size | Field |
|---|---|---|
| 0 | 8 | `name`, a Str |
| 8 | 8 | `dist_info`, a Str: the `*.dist-info` directory path |
| 16 | 8 | `version`, a Str |

A canonical name is lowercase, with runs of `-`, `_` and `.` collapsed to `-`
(`canonical_dist_name` in `format.py`).

### CLOSURES

u32 words. A closure starts at word `i`: `words[i] = n` (n ≥ 1), then `n` FILES indices,
strictly ascending: the bundled shared objects to materialize with a NATIVE_EXT file (its
transitive `DT_NEEDED` dependencies inside the payload). Only NATIVE_EXT records carry a closure;
every member is a NATIVE_LIB or NATIVE_EXT record (an extension may link against another
extension's shared object), never the record itself.

### DIGESTS

32-byte integrity digests (format versions 1 to 3: plain SHA-256) of the blobs the runtime
extracts on its own (NATIVE_EXT, NATIVE_LIB, FS_REQUIRED), verified before it publishes the
file. Any other file may be extracted on demand when the application asks for it as a path
through `importlib.resources` (D27); it may have no digest and is written as stored, exactly as
it would be read in memory.

### STARTUP

u32 MODULES indices, strictly ascending: exactly the modules flagged STARTUP. The launcher builds
CPython's frozen-module table from this list and never scans MODULES.

## Validation rules

The writer's output keeps these, and the Python reader checks them all
([Who checks what](#who-checks-what)):

- Trailer and header magic and version must match, `header_size` must be 208, trailer `flags`
  0. The launcher accepts only version 7; the Python reader also reads 1 to 6.
- A blob of codec 3 or 4 needs format version 5 and belongs only to native files; one of codec 5
  needs version 6 and may belong to any file or module (its pieces are checksummed frames, so
  module code may be in them); no file or module names a piece directly.
- `payload_offset` must be 65536-aligned (4096-aligned in versions 1 to 6), and the file size
  must match exactly.
- Every section must lie inside the payload, start after the header, be 8-byte aligned, and not
  overlap any other section.
- Every Str must lie inside STRTAB, be NUL-terminated, contain no NUL, and be valid UTF-8.
- Every index field must be NONE or in range, enum fields known values, flag fields known bits
  only, reserved fields 0.
- Every blob must be 16-byte aligned, lie inside the blob data area, and have `size` ≤ 2^62;
  codec 0 needs `stored_size == size`. A blob of codec 2 instead names a block (in range, codec
  zstd, `size` ≤ 1 MiB) and lies inside its content (`stored_size + size` ≤ the block's `size`);
  codec 2 is invalid in version 1.
- FILES paths, module paths and `dist_info` paths obey the path rules above. Module names are
  dotted names whose parts are Python identifiers once `-` is read as `_` and a leading digit is
  allowed: CPython imports file stems such as `_sysconfigdata__linux_x86_64-linux-gnu` and
  mypyc's `81d243bd2c585b0f4821__mypyc` through `importlib.import_module`. Dist names are
  canonical.
- The module kind invariants (checksummed code included), the STARTUP rule (the section lists
  exactly the flagged modules) and the closure rules hold.
- Every zstd frame a reader decompresses must be at least 6 bytes with the zstd magic, have the
  content-checksum flag (bit 2 of byte 4, the frame header descriptor) set, declare its content
  size, equal to `size`, and end where its stored bytes end; the output must be exactly `size`
  bytes, with no trailing data, and pass the checksum. Readers never allocate more than
  `size + 1` bytes for a blob, and a member's whole block (at most `MAX_BLOCK_SIZE`). The
  launcher's decoding of each codec is in [payload.c](Dev-Launcher.md#payloadc-and-payloadh).
- Tables must be strictly sorted.

### Who checks what

The **Python reader** (`Payload` in [`payload/reader.py`][reader.py], which `fsbpyr inspect`
uses; [Payload modules](Dev-Payload-Modules.md)) validates every table when it opens a payload
and raises `PayloadError` for a broken rule. The **launcher** checks the integrity digest before
any other rule, then the trailer, header and section table once, and each record when it reads
it; what its C code leaves unchecked is left to the digest, which only the writer's output
passes. The [boot modules](Dev-Boot-Modules.md) use what `_fsbpyr` returns.

| Rule | The launcher |
|---|---|
| integrity digest | checked first, over the whole file |
| trailer: magic, version 7, flags 0, alignment, exact file size | checked; also that the payload is at least 208 bytes |
| header: magic, version, `header_size` | checked |
| sections inside the payload, after the header, 8-byte aligned | checked |
| sections do not overlap | not checked |
| Str inside STRTAB and NUL-terminated | checked (`fsb_str`) |
| Str contains no NUL, is valid UTF-8 | not checked by `fsb_str` |
| index fields in range | checked when a record is fetched through the index |
| enum values | module `kind` 1 to 4, file `kind` 1 to 5, codec 0 to 5 checked |
| flag bits, reserved fields | not checked |
| file `mode` at most `0o777` | checked |
| blob placement, `size` ≤ 2^62, codec 0 sizes, block members, piece counts | checked (`fsb_get_blob`) |
| codecs 3 and 4 only for natives; codec allowed by the version | not checked: left to the integrity digest |
| module kind invariants | a STARTUP module's code must be codec 1 or 2 and 1 byte to 16 MiB; the others are not checked in C |
| STARTUP rule | each listed module must be flagged STARTUP; not that every flagged module is listed, nor the order |
| closure rules | count at least 1 and at most the FILES count, members in range and strictly ascending; member kinds and self-listing not checked |
| path rules | checked for every file before it is extracted (`fsb_cache_safe`) |
| zstd frames and exact output sizes | checked on every decode |
| xz streams | checked: one stream, all stored bytes consumed, exact size, 64 MiB memory limit |
| tables sorted | not checked: lookups binary-search on the assumption |
| dist names canonical | not checked |

## Constants

| `format.py` | `format.h` | Value |
|---|---|---|
| `FORMAT_VERSION` | `FSB_FORMAT_VERSION` | 7 |
| `READABLE_VERSIONS` | | 1 to 7 |
| `PAYLOAD_ALIGN` | `FSB_PAYLOAD_ALIGN` | 65536 |
| `LEGACY_PAYLOAD_ALIGN` | | 4096 (versions 1 to 6) |
| `BLOB_ALIGN` | `FSB_BLOB_ALIGN` | 16 |
| `TABLE_ALIGN` | `FSB_TABLE_ALIGN` | 8 |
| `NONE` | `FSB_NONE` | `0xFFFFFFFF` |
| `DIGEST_SIZE` | `FSB_DIGEST_SIZE` | 32 |
| `DIGEST_LEAF` | `FSB_DIGEST_LEAF` | 2^20 (1 MiB) |
| `MAX_MODE` | `FSB_MAX_MODE` | `0o777` |
| `MAX_BLOB_SIZE` | `FSB_MAX_BLOB_SIZE` | 2^62 |
| `MAX_BLOCK_SIZE` | `FSB_MAX_BLOCK_SIZE` | 2^20 (1 MiB) |
| `XZ_MEMLIMIT` | `FSB_XZ_MEMLIMIT` | 64 MiB |
| `XZ_PIECE` | `FSB_XZ_PIECE` | 4 MiB |
| `ZSTD_PIECE` | `FSB_ZSTD_PIECE` | 8 MiB |
| `TRAILER_MAGIC` | `FSB_TRAILER_MAGIC` | `FSBPYR\0T` |
| `HEADER_MAGIC` | `FSB_HEADER_MAGIC` | `FSBPYR\0H` |
| | `FSB_MAGIC_SIZE` | 8 |
| `ZSTD_MAGIC` | (in `payload.c`) | `28 b5 2f fd` |
| `MAX_STRTAB_SIZE` | | `0xFFFFFFFF` (the writer's limit) |
| `BLOCK_SIZE`, `CODE_BLOCK_SIZE`, `BLOCK_MEMBER_MAX` | | 256 KiB, 64 KiB, 64 KiB (the writer's block sizes) |

The enums have the same values in both: `Section`/`enum fsb_section`, `Codec`/`enum fsb_codec`,
`ModuleKind`/`enum fsb_module_kind`, `ModuleFlag`/`enum fsb_module_flag`,
`FileKind`/`enum fsb_file_kind`, `FileFlag`/`enum fsb_file_flag`.

## Format versions

From the version checks of `Payload` in [`payload/reader.py`][reader.py] and the `Codec` comments
in [`payload/format.py`][format.py]:

| Version | Change |
|---|---|
| 1 | the trailer's digest is the SHA-256 of the payload; DIGESTS hold plain SHA-256 digests |
| 2 | blocks: codec 2 (D30) |
| 3 | the trailer's digest is the integrity digest of the whole file before it (D31) |
| 4 | DIGESTS hold integrity digests (D32) |
| 5 | xz: codecs 3 and 4 (D33) |
| 6 | zstd in pieces: codec 5 (D39) |
| 7 | `payload_offset` aligned to 65536 instead of 4096 |

## Build information

`build_info` is JSON with sorted keys: a manifest of what the executable was built from. A reader
treats a missing key as "not recorded" and an unknown `schema` as unreadable. Nothing in it names
the builder's paths, time or host, so the same inputs give the same `build_info` from any
project directory. The keys come from [`src/fsbpyr/assemble.py`][assemble.py] (the runtime's) and
[`src/fsbpyr/build.py`][build.py] (the application's).

| Key | Content |
|---|---|
| `schema` | `1`. Incremented when a key changes meaning, never for a new key |
| `lock` | `{kind, path, sha256, hashed}`: `kind` is `uv.lock`, `script.lock` (uv's `<script>.lock` beside a single script), `pylock.toml`, `requirements`, `poetry.lock`, `pdm.lock`, `Pipfile.lock`, `inferred` or `unlocked`; `path` relative to the project directory (`../uv.lock` for a workspace member), null if unlocked; `sha256` the file's; `hashed` whether it pins artifact hashes (a `Pipfile.lock` does). `inferred` (scripts that declared nothing, versions read from their venv): `path` is the venv (its name when outside the project), `sha256` null, `hashed` false, plus `pins`, the `name==version` pins installed |
| `dists` | `[{name, version, source, artifact_sha256, backend}]`, sorted by name: `artifact_sha256` where the lock names the artifact, `backend` for `built-from-sdist` only (below) |
| `runtime` | the runtime id, `cp<python>-<triple>-<12 hex digits>`, a digest of the launcher sources and flags for the target, the PBS pin, the sysroot and the layout version: the stub's inputs, which several releases may share, not a release. The stub records it as `fsbpyr-stub-identity=<id>` in its `.rodata` ([Launcher](Dev-Launcher.md#how-it-is-compiled-and-linked)) |
| `runtime_tag` | the PBS distribution's tag and the target triple (`<tag>-<triple>`); it seeds `generation_id` |
| `python` | the target CPython's version (3.12 to 3.15) |
| `extension_suffixes` | the suffixes the runtime's interpreter imports extension modules by, in its order, asked of the interpreter (`pbs.extension_suffixes`): `PYTHON.json`'s list lags (3.15.1's lacks `.abi3t.so`) |
| `fsbpyr` | the version of the fsbpyr that built the executable |
| `zstd` | the libzstd version that compressed the payload ([Determinism](#determinism)) |
| `resources` | `sha256` over every payload file's path, content digest and mode, cut to 32 hex digits: the name of the tree where resources extracted on demand live (D27) |
| `runtime_inputs` | `{pbs: {release, name, sha256}, sysroot, layout}`: the python-build-standalone archive, the glibc sysroot (its name and 12 hex digits of a digest of its packages' SHA-256: `debian12-glibc2.36-<arch>-…` or `rocky8-glibc2.28-<arch>-…`) and the layout version of the runtime; a runtime taken from an fsbpyr executable reads its baseline back from it |
| `runtime_components` | versions the runtime's interpreter reports (`openssl`, `sqlite`, `zlib`, `expat`, `libmpdec`), and `zstd` from the `libzstd.a` the launcher links (`zstd_version` in [`runtime/pbs.py`](../src/fsbpyr/runtime/pbs.py); absent when the archive gives none). Read when the runtime is built; an executable that builds another passes them on |
| `stub_sha256` | SHA-256 of the stub and padding, `head -c <payload_offset> app \| sha256sum`: the same in every app built by one fsbpyr release for one Python, target and glibc baseline (each baseline has its own stub, D46). There is no digest of the builder |
| `settings` | `{entry, fs_packages, allow_system_libs, extras, include_source}`: `fs_packages` is the effective list, built-in and configured |
| `glibc_baseline` | only with `--glibc 2.28` (`baseline_record` in `build.py`, D46): `"2.28"`, the stub linked against Rocky Linux 8.10's glibc ([Launcher](Dev-Launcher.md#how-it-is-compiled-and-linked)); without it, 2.34. `fsbpyr inspect --json` reports it for every app; the text report adds `baseline   glibc 2.28` where recorded |
| `release_asset` | only in fsbpyr's own executable: its release asset without `.xz`, `fsbpyr-3.X-<target>` (e.g. `fsbpyr-3.14-x86_64-linux-gnu`, the host's target name), whose same-Python build `fsbpyr self update` fetches; another form (0.21.0's `fsbpyr-<target>`) makes it use the asset of the Python it runs on |
| `project` | `{name, version}` of the application |
| `fsbpyr_version` | the builder's version, with the commit where there is one (`fsbpyr inspect`) |
| `git` | `{commit, tag}` of the project's HEAD (`tag`: the highest `vX.Y.Z` on it, or null); null without git or outside a repository |
| `uv` | the version of the uv that built the wheel and installed everything |
| `glibc` | the oldest glibc the application runs on (the stub's requirement, raised by bundled native code), e.g. `2.34`; with `--glibc 2.28` never below `2.28` (`glibc_report` in `build.py`) |
| `system` | `{libraries, may_load}`: the system libraries the target must provide, and with `--hints` those the Python code may open by name |
| `zstd_level` | the zstd level of the app's content and of every block |
| `native_compression` | `xz -6` or `zstd`: how native files are stored |
| `interpreter_reexec` | what a child of this executable that asks for Python (`-m MODULE`, `-c CODE`, a `.py` file of the payload) gets: `true` runs it as the interpreter would (D23), `false` runs the app, `null` (not set) refuses it with exit 70 (D44) |

`dists[].source` is one of:

- `registry`, `git`, `url`, `path`: the lock's source. `artifact_sha256` is that of the lock's
  wheel sharing a tag with the installed `WHEEL` file, for locks that name wheels (`uv.lock`,
  `script.lock`, `pylock.toml`, `poetry.lock`, `pdm.lock`). A `Pipfile.lock` registry entry
  names no file and gets none; a `file` entry with exactly one hash gets that hash of the file it
  names (`_entry` in [`pipenv.py`](../src/fsbpyr/pipenv.py)) when it is a wheel for the target,
  or as the sdist's when built from it. `requirements.txt`, `inferred` and unlocked builds record
  none, and sources by PEP 610 (`direct_url.json`).
- `local`: the project and its editable or directory workspace members, which the lock holds no
  hash for; uv builds their wheels at staging.
- `built-from-sdist`: no wheel in the lock (if any) shares a tag with the installed one, so it
  was built on the build host, pure-Python ones included: `artifact_sha256` is the sdist's,
  `backend` the `WHEEL` file's `Generator:` line. These bytes depend on the host's backend and
  environment; the build prints their names, and staging sets `SOURCE_DATE_EPOCH` (the user's,
  else the project's last commit time, else 315532800).

The `"schema": 1` of `fsbpyr inspect --json` is the report's own; its `--files` list comes from
the RECORD files in the payload, and its `sizes` ([Performance](Dev-Performance.md)) from the
stored sizes. Neither changes the format.

## Determinism

The same spec always encodes to the same bytes with the same zstd library version, which is
recorded in `build_info` (`zstd`). A self-hosted fsbpyr carries its own static libzstd, so the
check that fsbpyr's second and third bootstrap stages are byte-identical (`make bootstrap`, step
3 of [`tools/bootstrap.sh`](../tools/bootstrap.sh); [Self-hosting](Dev-Self-Hosting.md)) is not
affected.

## What executables share

Measured with the released fsbpyr 0.30.0 (`fsbpyr-3.14-x86_64-linux-gnu`, CPython 3.14.10,
x86-64) for `hello` and `mp` (pure-Python fixtures in `tests/fixtures/projects`) and `pandas-csv`
(a corpus app with pandas and numpy), the method of [`docs/executable-parts.md`][parts-md]. The
stub and padding (21,002,921 and 34,135 bytes, the stub's zero tail counted as padding, as
`inspect --json`'s `sizes` counts them) are the release's, which every app it builds for the
default baseline shares (`Runtime.from_executable` hands on its stub prefix). "→" gives the
uncompressed and the stored size.

| Part | `hello` (26.61 MiB) | `mp` (26.61 MiB) | `pandas-csv` (62.11 MiB) | The same in all three? |
|---|---|---|---|---|
| Stub ELF: the launcher with CPython and its libraries, recording its runtime id | 20.03 MiB | 20.03 MiB | 20.03 MiB | yes, byte for byte (the default glibc 2.34 stub) |
| Zero padding to the payload's 64 KiB boundary | 33.3 KiB | 33.3 KiB | 33.3 KiB | yes |
| Payload header (208 bytes) | | | | partly: the Python version and bytecode magic are; the app name, entry point, build information and native cache generation (from the files an app extracts; `hello` and `mp` extract none and share it) are not |
| Tables: strings and the blob, module, file and distribution records | 99.8 KiB | 100.4 KiB | 551.6 KiB | no |
| Standard library bytecode, 551 modules | 10.80 → 4.09 MiB | same | same | the same content |
| Standard library sources | 10.13 → 2.17 MiB | same | same | the same content |
| Standard library data files (29) | 168 → 34 KiB | same | same | the same content |
| Startup modules of the standard library (4: `encodings`, `encodings.aliases`, `encodings.utf_8`, `linecache`), code and sources | 64 → 22 KiB | same | same | the same content |
| fsbpyr's boot and runtime modules (15: `_fsbpyr_boot`, the fifth startup module, and `_fsbpyr_rt`'s 14), code and sources | 358 → 132 KiB | same | same | the same content |
| The app's own package (or its files under `_fsbpyr_scripts`): code, sources, metadata | 2 → 1 KiB | 13 → 4 KiB | 2 → 1 KiB | no |
| Third-party bytecode (1,847 modules) | — | — | 39.81 → 13.04 MiB | the app's own |
| Third-party sources | — | — | 28.75 → 5.51 MiB | the app's own |
| Third-party data files (527) | — | — | 4.22 → 1.01 MiB | the app's own |
| Third-party dist-info metadata (37 files) | — | — | 469 → 152 KiB | the app's own |
| Native extensions (64) | — | — | 32.06 → 9.14 MiB | the app's own |
| Native libraries bundled in wheels (3) | — | — | 26.19 → 6.21 MiB | the app's own |
| Trailer: payload offset and size, whole-file digest | 64 bytes | 64 bytes | 64 bytes | no |

What follows from it, and how it was measured, is in
[Performance](Dev-Performance.md#what-executables-share).

With `--glibc 2.28` (D46) the stub is the EL8 one (21,004,065 bytes and 32,991 of padding for
3.14 on x86-64 in 0.30.0), paired with the same
standard library (`paired` in [`runtime/stubs.py`][stubs]). It changes the padding, the build
information taken from the stub or the baseline (`runtime`, the `sysroot` of `runtime_inputs`,
`stub_sha256`, `glibc`, `system`, `glibc_baseline`) and the offsets its length moves, not the
records, `python_hexversion` or bytecode magic. Third-party wheels are chosen for
`manylinux_2_28` instead of `manylinux_2_34` (`Target.platform_for`), so native files may
differ.

[format-md]: ../docs/format.md
[parts-md]: ../docs/executable-parts.md
[plan]: ../docs/IMPLEMENTATION_PLAN.md
[format.py]: ../src/fsbpyr/payload/format.py
[format.h]: ../src/fsbpyr/launcher/format.h
[reader.py]: ../src/fsbpyr/payload/reader.py
[writer.py]: ../src/fsbpyr/payload/writer.py
[assemble.py]: ../src/fsbpyr/assemble.py
[build.py]: ../src/fsbpyr/build.py
[closure.py]: ../src/fsbpyr/closure.py
[links.py]: ../src/fsbpyr/links.py
[vm-smoke]: ../tools/vm-smoke.py
[vm-pins]: ../tools/vm-pins.toml
[stubs]: ../src/fsbpyr/runtime/stubs.py
