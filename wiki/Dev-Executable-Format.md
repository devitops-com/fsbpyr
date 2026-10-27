# Executable format

This page specifies the file format of the executables fsbpyr builds, format version 7 (fsbpyr
v0.27.0): the byte layout, the trailer, the integrity digest, the payload header (with the
entry point of an app of several commands and of an app of scripts), the eight sections and
their records, the codecs, the rules a reader must enforce, the build information
and determinism. It carries over [`docs/format.md`][format-md], checked against the two
definitions in code, [`payload/format.py`][format.py] and the launcher's
[`format.h`][format.h], and adds how fsbpyr's own two readers check the rules. The last section
gives the measurement of [`docs/executable-parts.md`][parts-md]: which parts of executables are
the same in every app and which are the app's own. How fsbpyr writes a payload is in
[Payload modules](Dev-Payload-Modules.md); how the launcher reads one is in
[Launcher](Dev-Launcher.md).

`docs/format.md` calls `format.py` the single source of truth; `format.h` mirrors it, and
`tests/integration/test_launcher_format.py` compiles a probe against `format.h` and compares
every size, offset and constant with the Python definitions. Numbers such as D31 name design
decisions recorded in [`docs/IMPLEMENTATION_PLAN.md`][plan].

## Layout

![Byte layout of an executable: the stub ELF from offset 0, zero padding, the payload from payload_offset (header, eight tables, blob data), and the 64-byte trailer ending in the digest](images/format-layout.svg)

All integers are little-endian. An executable is four parts, back to back:

```
0                     payload_offset                         file_size - 64      file_size
| stub ELF | zero pad |  payload (header, tables, blob data)  |  trailer (64 B)  |
```

- The stub is an unmodified ELF: the [launcher](Dev-Launcher.md) with CPython linked in. The loader
  maps only its `PT_LOAD` segments, so the appended bytes are inert.
- `payload_offset` is a multiple of 65536 (4096 up to version 6), so that the payload can be
  mapped with `mmap` directly on kernels with 4K, 16K and 64K pages (version 7). It is tested
  on 4K-page kernels, and 64K-page aarch64 kernels are smoke-tested in QEMU (`make test-vm`,
  [`tools/vm-smoke.py`][vm-smoke], see [Testing](Dev-Testing.md#the-page-size-smoke-test)), not on
  hardware. 16K-page kernels are not supported, and not tested: an owner decision of
  2026-10-27, as the comment of [`tools/vm-pins.toml`][vm-pins] says.
- The bytes between the end of the stub and `payload_offset` are zeros.
- The payload ends exactly where the trailer begins:
  `payload_offset + payload_size + 64 == file_size`.

The format places the eight tables only under the rules in [Validation rules](#validation-rules).
fsbpyr's writer (`_layout` in [`payload/writer.py`][writer.py]) puts them right after the header
in section-number order, each aligned to 8 bytes, and starts the blob data at the next 16-byte
boundary after the last one; the diagram shows that order.

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
| 32 | 32 | `digest`: the integrity digest of everything before it (versions 1 and 2: the SHA-256 of the payload) |

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

It is the one digest construction of the format (D31: version 3; D32: version 4), computed by
one routine in each language (`tree_digest` in `format.py`; `fsb_tree_digests` and its
one-buffer form `fsb_tree_digest` in the launcher's `integrity.c`), for two things:

- **the executable**: its last 32 bytes are the digest of everything before them: the stub, the
  padding, the payload and the first 32 bytes of the trailer;
- **every file the runtime extracts**: DIGESTS holds its digest (format versions 1 to 3: plain
  SHA-256).

Standard tools give the same digest, so a published one can be checked without trusting fsbpyr:

```sh
head -c -32 app | split -b 1M --filter='openssl dgst -sha256 -binary' | sha256sum
tail -c 32 app | od -An -tx1 | tr -d ' \n'   # the stored digest
split -b 1M --filter='openssl dgst -sha256 -binary' < cached.so | sha256sum   # a cached file
```

`fsbpyr inspect` shows the stored digest, which proves nothing by itself;
`fsbpyr inspect --verify` and the shell recipe compute it from the bytes.

**The launcher's check.** The launcher checks its file before it reads any other byte of it:
before the trailer, the payload or CPython. It hashes the pieces on min(8, usable cores) threads,
one per 4 pieces, with the SHA-256 of the statically linked OpenSSL (SHA-NI where the CPU has
it). It reads the file a piece at a time on its threads and never maps it, so a file that shrinks
or fails to read while it is hashed (a failing disk, a network file system) fails the check like
a changed one, and the file's size never shows in the process's resident memory. On a mismatch
it prints
`fsbpyr: cannot start <path>: integrity check failed: the file was modified or damaged`
and exits with status 70. The time the check took is `integrity_ns` in `FSBPYR_TRACE`.

**What it detects** is any change to the file that leaves the stored digest alone: damage,
truncation, a patch. **What it cannot detect** is a change that also rewrites the digest or
removes the check, since the checking code is in the same file. Provenance against deliberate
modification needs a signature checked with a key from outside the file. Damage to the
launcher's own code can also crash it before the check runs, without the message.

**Cached files.** Files extracted to the cache (natives, FS_REQUIRED packages, resources asked
for as paths) run outside the file, so each process hashes them against DIGESTS once, before it
first uses them, with the same routine. The launcher's `_fsbpyr.sync` takes up to 1024 files of
the expected size per parallel pass: those up to 64 KiB are read into a buffer, and at most 64
larger ones are held open and read a piece at a time by the hashing threads. A changed file, or
one that cannot be read, is reported on stderr and restored. When the files a pass holds leave
no descriptor or memory to open the next file or its directories, the pass is checked first; if
that is not enough, the run fails. A file changed while a process runs is noticed by the next
one. Files are written a window at a time, hashed as they are written, in pieces of at most
1 MiB (a piece of zeros is left a hole), and put in place only if their digest is right: a new
file is written unnamed in its directory and linked into place, one that replaces another (or
cannot be linked) is written under a temporary name and renamed. Nothing is synced to disk: a
file a crash left incomplete fails its check and is restored. Content is decoded a window of at
most 64 MiB at a time, the digest keeps a bounded number of piece digests, and files are synced
4096 at a time, so neither a file's size nor the number of files sets the memory a sync needs
(D38). [Launcher](Dev-Launcher.md#cachec-and-cacheh) has the details.

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
| 64 | 8 | `entrypoint`, a Str: `module:attr` or `module`; for several commands, newline-separated `name=target` lines (see [Several commands](#several-commands)) |
| 72 | 8 | `build_info`, a Str: JSON for `inspect` (see [Build information](#build-information)); the runtime reads its `resources` key (D27) |
| 80 | 8 × 16 | section table: `(u64 offset, u64 count)` for each section, in section-number order |

**Str** is `(u32 offset, u32 length)` into the string table (STRTAB). Every string is stored as
UTF-8 followed by one NUL byte, which the length does not count, so C code can use
`strtab + offset` directly as a C string.

All offsets inside the payload are relative to the payload's start. The value `0xFFFFFFFF`
(`NONE`) means "no reference" in every u32 index field.

The launcher accepts a payload only if the bytecode magic equals its own CPython's and
`python_hexversion` has the same major and minor version
([Launcher](Dev-Launcher.md#from-execve-to-the-boot-code), step 6).

### Several commands

A build of a project with several console scripts
([Several commands in one executable](User-Building.md#several-commands-in-one-executable)) stores
them all in `entrypoint`, as lines `name=target` joined by newlines and
sorted by name, a target being `module:attr` or `module` (`command_table` in
[`closure.py`][closure.py]); a single command stays the plain target. The format version stays
7: the launcher treats the entry point as an opaque string, which it only hands to the boot code
(`_fsbpyr.info()`), and the boot code stored in the same file reads the table. A command name
matches `[A-Za-z0-9][A-Za-z0-9_.+-]*` (`COMMAND_NAME` in [`links.py`][links.py]; the build
fails on any other), so no name contains `=`, `/` or a newline; the payload writer itself
rejects only a NUL, as in any string.

The boot code takes a newline in the entry point as the sign of a table
(`_fsbpyr_rt/commands.py`). At start it runs the command that the basename of `argv[0]` names,
as it is or without `.py`, else the command that `argv[1]` names exactly, which it then removes
from `sys.argv`. A start that names no command lists the commands on stderr
(`usage: NAME COMMAND [ARGUMENT...]`) and exits with status 2. An argument that is a path is
never a command: it is the restart idiom or a file argument. `argv[0]` is the name a link was
started under. [Boot modules](Dev-Boot-Modules.md) describes the start.

### Scripts

A target below the module `_fsbpyr_scripts` is the entry point of a project without a build
system, a directory of scripts or a single script
([Projects without a build system](User-Scripts.md#projects-without-a-build-system),
[A directory of scripts](User-Scripts.md#a-directory-of-scripts),
[A single script](User-Scripts.md#a-single-script)). Before it starts such a target, the boot code puts
the target's module directory first on `sys.path`: the executable's directory joined with the
dotted name's path without its last part. A target without `:attr` is run as `__main__`, and the
selftest only finds it, never imports it.

A directory below `_fsbpyr_scripts` whose name is no Python name (`sub-dir`) is recorded under
a mangled name (`sub_dir`), and so is a file (`check-disk` is `check_disk.py`): each character
no identifier holds becomes `_`, and a name that starts with a digit gets a `_` in front. The
boot code's path hook maps a `sys.path` entry named by the on-disk name to the recorded one, so
modules are still looked up by exact dotted names.

A module of this root has `__file__` and `spec.origin` `<directory of the executable>/<path
below the root>`, a file next to the executable (D24), and the path hook claims those
directories too (an entry with `.` or `..` components is normalized first; one that leads above
the executable's directory is not claimed). So the code is the payload's, while the files a
script reads next to its `__file__` are the host's; the module's code objects keep the
payload's `co_filename`. None of this changes the format.

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

The MODULES, FILES and DISTS tables are sorted strictly ascending by their key's UTF-8 bytes
(`memcmp` order, the shorter first on a common prefix, as Python orders `bytes`). Lookups are
binary searches. Keys are unique. Each of the three records starts with its key, a Str.

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

- Stored data is 16-byte aligned and lies after the end of the last section (the blob data
  area).
- `size` is at most 2^62 (D38: no limit of fsbpyr's below what file systems hold; readers
  stream content larger than a window instead of holding it).
- Blob order on disk: startup code, module code, resources, sources, natives. Within each class,
  blobs are ordered by the first path that references them.
- Identical blobs are stored once; in blocks, identical content is one member whether the spec
  gave it raw or as a zstd frame.
- **Raw blobs and holes.** For codec 0, `stored_size == size`. A raw blob of `DIGEST_LEAF`
  bytes or more may hold holes (D38): writers leave whole leaves of zeros unwritten, so a file
  of zeros stays sparse. The launcher reads such blobs from the file (`pread`), never through its
  mapping, where a hole on tmpfs would take a page of memory for every page read.
- **Blocks (version 2, D30).** A blob of codec 2 has no stored bytes of its own: its content is
  the `size` bytes at `stored_size` in the content of block `offset`, a zstd blob (codec 1) of
  at most `MAX_BLOCK_SIZE` (1 MiB); the writer references blocks only from members. The writer
  packs every non-empty blob under 64 KiB except natives into blocks, one stream per placement
  class (startup code, code, resources and metadata, sources) filled in blob order: 64 KiB
  blocks for code, which an import decompresses whole, and 256 KiB for the rest. Block records
  follow all other blob records; a block's data sits where its first member's would. Readers
  keep recently decompressed blocks (the launcher the last 32), since a package's modules share
  blocks.
- **xz (version 5, D33).** A blob of codec 3 is one xz stream (`--best-compression`: xz -6 with
  the x86 filter, no check of its own, since the file's integrity digest covers it) that decodes
  to exactly `size` bytes, at most `XZ_PIECE` (4 MiB), with no data after it and at most
  `XZ_MEMLIMIT` (64 MiB) of decoder memory. A library larger than `XZ_PIECE` is a blob of codec 4
  instead, with no stored bytes of its own: its content is that of `stored_size` (at least 2)
  consecutive xz blobs from index `offset` on, each `XZ_PIECE` bytes but the last, so that
  `(stored_size - 1) * XZ_PIECE < size <= stored_size * XZ_PIECE`. Their records follow the block
  records, their data lies where the library's would, and no other blob of codec 4 and no table
  references them. Only native files (NATIVE_EXT, NATIVE_LIB) may use codecs 3 and 4. The
  launcher decodes them with the statically linked liblzma in `_fsbpyr.read`, without the GIL,
  the pieces of one library in parallel on the routine that hashes the integrity digest.
- **zstd in pieces (version 6, D39).** Content larger than `ZSTD_PIECE` (8 MiB), of any file or
  module, may be a blob of codec 5: like codec 4, with no stored bytes of its own, its content is
  that of `stored_size` (at least 2) consecutive zstd blobs (codec 1: one frame each, with content
  size and checksum) from index `offset` on, each `ZSTD_PIECE` bytes but the last, so that
  `(stored_size - 1) * ZSTD_PIECE < size <= stored_size * ZSTD_PIECE`; their records follow the
  block records as codec 4's do. Writers compress the pieces on up to 8 threads; the launcher
  decodes them on up to 8, a window of `FSB_STREAM_WINDOW / ZSTD_PIECE` (8) at a time when it
  streams. A piece is a frame of its own, so the bytes are the same whatever the number of
  threads.

#### Decoding a blob

What the launcher's `fsb_read_blob` ([`payload.c`][payload.c]) does, per codec, after the
record's checks ([Validation rules](#validation-rules)):

- **0**: take bytes `[offset, offset + size)` of the payload.
- **1**: the stored bytes must be exactly one frame: at least 6 bytes, the zstd magic
  `28 b5 2f fd`, the content-checksum flag set (bit 2 of byte 4, the frame header descriptor),
  a declared content size equal to `size`, and a compressed frame size equal to `stored_size`.
  Decompress; the output must be exactly `size` bytes and pass the checksum.
- **2**: read blob `offset` as a block: codec 1, `size` at most 1 MiB; decompress it as codec
  1; `stored_size + size` must not exceed the block's size; take
  `[stored_size, stored_size + size)` of it.
- **3**: decode one xz stream with a 64 MiB memory limit; it must consume exactly
  `stored_size` bytes and give exactly `size`.
- **4 and 5**: piece *i* (0 to `stored_size - 1`) is blob `offset + i`; it must have codec 3
  (for 4) or 1 (for 5) and size `XZ_PIECE` or `ZSTD_PIECE`, the last one the rest of `size`;
  decode each as its codec and put piece *i* at `i * XZ_PIECE` or `i * ZSTD_PIECE` of the
  content.

Readers never allocate more than `size + 1` bytes for a blob, and a member's whole block (at most
`MAX_BLOCK_SIZE`).

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
  belong to (by their `dist`: plugins it opens from a directory, such as openmm's), and the
  package's modules get `__file__` and `__path__` there.
- Every `code` blob is a zstd frame with the content-checksum flag set, zstd frames in pieces,
  or a member of a block (itself such a frame). `marshal.loads` is not safe against corrupt
  input, so the bytes it gets have passed a checksum. The checksum covers a block's content,
  not which slice of it a member selects: like every table field, a member's position and size
  are not checksummed. STARTUP code is decompressed once by the launcher into memory that it
  then hands to CPython's frozen-module table.

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

Paths are relative, use `/` as separator, and contain no empty, `.` or `..` components, no
backslash and no NUL (`check_path` in `format.py`). Because the table is sorted, listing a
directory is a prefix range scan: the files below `dir` are the run of keys from the first
starting with `dir/` to the first starting with `dir0` (`'0'` follows `'/'`).

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

A sequence of u32 words. A closure starts at a word index `i`: `words[i] = n` (n ≥ 1), followed
by `n` FILES indices in strictly ascending order. Closures list the bundled shared objects that
must be materialized together with a NATIVE_EXT file (its transitive `DT_NEEDED` dependencies
inside the payload). Only NATIVE_EXT records carry a closure, every member is a NATIVE_LIB or
NATIVE_EXT record (an extension may link against another extension's shared object), and a
record never lists itself. The blob of every record the runtime extracts (NATIVE_EXT,
NATIVE_LIB and FS_REQUIRED files) has a digest, which the runtime verifies before publishing the
file. Any other file may be extracted on demand when the application asks for it as a path
through `importlib.resources` (D27); such a file may have no digest and is written as stored,
exactly as it would be read in memory.

### DIGESTS

An array of 32-byte integrity digests (see [Integrity digest](#integrity-digest); format
versions 1 to 3: plain SHA-256). They are recorded for blobs the runtime extracts on its own
(NATIVE_EXT, NATIVE_LIB, FS_REQUIRED). Resources extracted on demand (D27) may have none.

### STARTUP

u32 MODULES indices, strictly ascending: exactly the modules flagged STARTUP. The launcher reads
this short list to build CPython's frozen-module table, so it never scans the MODULES table.

## Validation rules

The rules readers must enforce, as `Payload` in [`payload/reader.py`][reader.py] checks them
([Who checks what](#who-checks-what) says which the launcher checks):

- Trailer and header magic and version must match. `header_size` must be 208. Trailer `flags`
  must be 0. The launcher accepts only version 7; the Python reader also reads 1 to 6.
- A blob of codec 3 or 4 needs format version 5 and belongs only to native files, never to
  another file or a module; a blob of codec 5 needs version 6 and may belong to any file or
  module (its pieces are checksummed frames, so module code may be in them); no file or module
  names a piece directly. The Python reader and the writer enforce this; the launcher relies on
  the integrity digest, which only the writer's output passes.
- The launcher checks the integrity digest before any other rule.
- `payload_offset` must be 65536-aligned (4096-aligned in versions 1 to 6), and the file size
  must match exactly.
- Every section must lie inside the payload, start after the header, be 8-byte aligned, and not
  overlap any other section.
- Every Str must lie inside STRTAB, be NUL-terminated, contain no NUL, and be valid UTF-8.
- Every index field must be NONE or in range. Enum fields must hold known values, and flag
  fields must hold known bits only. Reserved fields must be 0.
- Every blob must be 16-byte aligned, lie inside the blob data area, and have `size` ≤ 2^62. For
  codec 0, `stored_size == size`. A blob of codec 2 instead names a block (in range, codec zstd,
  `size` ≤ 1 MiB) and lies inside its content (`stored_size + size` ≤ the block's `size`);
  codec 2 is invalid in version 1.
- FILES paths, module paths and `dist_info` paths obey the path rules above. Module names are
  dotted names whose parts are Python identifiers once `-` is read as `_` and a leading digit is
  allowed: CPython imports file stems such as `_sysconfigdata__linux_x86_64-linux-gnu` and
  mypyc's `81d243bd2c585b0f4821__mypyc` through `importlib.import_module`. Dist names are
  canonical.
- The module kind invariants (including checksummed code: a zstd frame, zstd frames in pieces,
  or inside a block), the STARTUP rule (the STARTUP section lists exactly the flagged modules)
  and the closure rules hold.
- Every zstd frame a reader decompresses must have the content-checksum flag set, declare its
  content size, equal to `size`, and end where its stored bytes end.
- On decompression, the output must be exactly `size` bytes, with no trailing data, and must
  pass the zstd checksum. Readers never allocate more than `size + 1` bytes for a blob, and a
  member's whole block (at most `MAX_BLOCK_SIZE`).
- Tables must be strictly sorted.

### Who checks what

fsbpyr has two readers. The **Python reader** (`Payload` in [`payload/reader.py`][reader.py])
parses and validates every table when it opens a payload, as its docstring says, raises
`PayloadError` for a broken rule, and reads versions 1 to 7; `fsbpyr inspect` uses it
([Payload modules](Dev-Payload-Modules.md)). The **launcher** checks the integrity digest first and
then checks lazily: the trailer, header and section table once, and each record when it is
read. Its C code does not check every rule above (the table covers the C code only; the
[boot modules](Dev-Boot-Modules.md) use what `_fsbpyr` returns):

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

What each version changed (the version checks of `Payload` in [`payload/reader.py`][reader.py]
and the `Codec` comments in [`payload/format.py`][format.py]):

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

`build_info` is JSON with sorted keys. Since `schema` 1 it is a manifest of what the executable
was built from; a reader treats a missing key as "not recorded" (older builds) and an unknown
`schema` as unreadable. The same inputs give the same `build_info`, whatever the project's
directory: nothing in it names the builder's paths, time or host.

| Key | Content |
|---|---|
| `schema` | `1`. Incremented when a key changes meaning, never for a new key |
| `lock` | `{kind, path, sha256, hashed}`: `kind` is `uv.lock`, `script.lock` (uv's `<script>.lock` beside a single script), `pylock.toml`, `requirements`, `poetry.lock`, `pdm.lock`, `Pipfile.lock`, `inferred` or `unlocked`; `path` is relative to the project directory (`../uv.lock` for a workspace member), null if unlocked; `sha256` is of the file; `hashed` says whether it pins artifact hashes (a `Pipfile.lock` does, but the hashes of its registry entries carry no file names, so such a distribution has no `artifact_sha256`). For `inferred` (a directory of scripts or a single script that declared nothing, whose versions were read from the venv it runs in), `path` is that venv, relative to the project directory (just its directory name when it lies outside), `sha256` is null, `hashed` is false, and a fifth key, `pins`, lists the `name==version` pins installed |
| `dists` | `[{name, version, source, artifact_sha256, backend}]`, sorted by name: `artifact_sha256` where the lock names the artifact, `backend` for `built-from-sdist` only (below) |
| `runtime` | the runtime's id, `cp<python>-<triple>-<12 hex digits>` (a digest of the launcher, the PBS archive, the sysroot and the layout version, so the default and the glibc 2.28 runtime of one Python and target have different ids). The stub records it too, as `fsbpyr-stub-identity=<id>` in its `.rodata`: that is how a build proves that a stub it was handed (a glibc 2.28 one) is the one of its own launcher sources, Python, target and sysroot ([Launcher](Dev-Launcher.md#how-it-is-compiled-and-linked)) |
| `runtime_tag` | the PBS distribution's tag and the target triple (`<tag>-<triple>`); it seeds `generation_id` |
| `python` | the target CPython's version (3.12 to 3.15) |
| `extension_suffixes` | the file name suffixes the runtime's interpreter imports extension modules by, in its order; asked of the interpreter itself (`pbs.extension_suffixes`), as `PYTHON.json`'s list lags (3.15.1's lacks `.abi3t.so`) |
| `fsbpyr` | the version of the fsbpyr that built the executable |
| `zstd` | the libzstd version that compressed the payload (see [Determinism](#determinism)) |
| `resources` | `sha256` over every payload file's path, content digest and mode, cut to 32 hex digits: the name of the tree where resources extracted on demand live (D27) |
| `runtime_inputs` | `{pbs: {release, name, sha256}, sysroot, layout}`: the python-build-standalone archive and the glibc sysroot the runtime was made from, and its layout version. `sysroot` is the sysroot's identity, its name and 12 hex digits of a digest of its packages' SHA-256: `debian12-glibc2.36-<arch>-…` for the default baseline, `rocky8-glibc2.28-<arch>-…` for glibc 2.28; a runtime taken from an fsbpyr executable reads its baseline back from it |
| `runtime_components` | versions the runtime's interpreter reports: `openssl`, `sqlite`, `zlib`, `expat`, `libmpdec`. Read once when the runtime is built; an executable that builds another passes them on |
| `stub_sha256` | SHA-256 of the bytes before the payload (the stub and its zero padding): `head -c <payload_offset> app \| sha256sum`. The same in every app built by the same fsbpyr release for the same Python, target and glibc baseline; the glibc 2.28 stub is another file than the default one (D46), so each baseline has its own value. There is no digest of the builder |
| `settings` | `{entry, fs_packages, allow_system_libs, extras, include_source}`: `fs_packages` is the effective list (the built-in packages and the configured ones) |
| `glibc_baseline` | only in an app built with `--glibc 2.28` (`baseline_record` in `build.py`, D46): `"2.28"`, the glibc baseline it is built for. An app without the key is built for the default, 2.34. Such an app's stub is linked against Rocky Linux 8.10's glibc 2.28: the stub of the companion release `el8-vX.Y.Z` (`fsbpyr-stub-3.X-<target>-glibc2.28`), one placed beside fsbpyr, or that of a runtime built with `fsbpyr runtime build --glibc 2.28`; a build takes a stub it did not link only if its recorded `fsbpyr-stub-identity` is the runtime id. `fsbpyr inspect --json` reports `glibc_baseline` for every app (`2.34` where the key is missing); the text report adds the line `baseline   glibc 2.28` where it is recorded |
| `release_asset` | only in fsbpyr's own executable: the release asset (without `.xz`) it is released as, `fsbpyr-3.X-<target>` (e.g. `fsbpyr-3.14-x86_64-linux-gnu`), the target being the host's name from the target table, which `fsbpyr self update` fetches the same Python's build of. 0.21.0 recorded `fsbpyr-<target>` for the newest Python (published until 0.21.0 only); a value that is not today's form makes `self update` use the asset of the Python the executable runs on |
| `project` | `{name, version}` of the application |
| `fsbpyr_version` | the builder's version, with the commit where there is one (`fsbpyr inspect`) |
| `git` | `{commit, tag}` of the project's HEAD (`tag`: the highest `vX.Y.Z` on it, or null); null without git or outside a repository |
| `uv` | the version of the uv that built the wheel and installed everything |
| `glibc` | the oldest glibc the application runs on (the stub's requirement, raised by bundled native code), e.g. `2.34`; for an app built with `--glibc 2.28` never below `2.28`, even where the stub needs only older symbol versions (`glibc_report` in `build.py`) |
| `system` | `{libraries, may_load}`: the system libraries the target must provide, and with `--hints` those the Python code may open by name |
| `zstd_level` | the zstd level of the app's content and of every block |
| `native_compression` | `xz -6` or `zstd`: how native files are stored |
| `interpreter_reexec` | what a child process of this executable that asks for Python (`-m MODULE`, `-c CODE`, a `.py` file of the payload) gets: `true` runs it as the interpreter would (D23), `false` runs the app, `null` (not set) refuses it with exit 70 (D44) |

The keys come from [`src/fsbpyr/assemble.py`][assemble.py] (the runtime's) and
[`src/fsbpyr/build.py`][build.py] (the application's).

`dists[].source` is one of:

- `registry`, `git`, `url`, `path`: the lock's source of the package, and `artifact_sha256` is
  the SHA-256 of the wheel in the lock that shares a tag with the installed wheel's `WHEEL` file
  (`uv.lock`, `script.lock`, `pylock.toml`, `poetry.lock` and `pdm.lock` name their wheels; a
  `Pipfile.lock` registry entry does not, and gets none). A build from `requirements.txt`, an
  `inferred` build or one without a lock cannot tell which hash belongs to the installed wheel,
  so it records no `artifact_sha256`, and sources by PEP 610 (`direct_url.json`).
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
the payload. Its `settings` is `build_info`'s, and `sizes` is the stored size by part (see
[Performance](Dev-Performance.md)); neither changes the format.

## Determinism

The same spec always encodes to the same bytes with the same zstd library version. The zstd
version is part of the build environment and is recorded in `build_info` (`zstd`). A
self-hosted fsbpyr binary carries its own statically linked libzstd, so the check that fsbpyr's
second and third bootstrap stages are byte-identical (`make bootstrap`, step 3 of
[`tools/bootstrap.sh`](../tools/bootstrap.sh))
is not affected.

## What executables share

From [`docs/executable-parts.md`][parts-md]: which parts of executables built for one Python are
the same in every app, and which are the app's own. Measured on 2026-10-24 with fsbpyr at commit
42d8ecb (a developer build), CPython 3.14.9, x86-64, for three apps: `hello` and `mp`
(pure-Python fixtures in `tests/fixtures/projects`) and `pandas-csv` (a corpus app: pandas and
numpy, with native extensions). The launcher sources and the boot code of v0.27.0 differ from
those of 42d8ecb (four more `_fsbpyr_rt` modules: `commands`, `links`, `restart`, `service`).
The stub and padding rows give v0.27.0's sizes: the 0.27.0 release's executable for CPython
3.14.9 on x86-64 (`fsbpyr-3.14-x86_64-linux-gnu`) has a stub of 20,939,049 bytes and 32,471
bytes of padding, counted from its trailer's `payload_offset` with the stub's zero tail counted
as padding, as `fsbpyr inspect --json`'s `sizes` counts them (2026-10-27), and it builds every
app for the default baseline with those bytes (`Runtime.from_executable` hands on its stub
prefix). The other rows are 42d8ecb's and were not measured again. Sizes are MiB and KiB; "→"
gives the uncompressed size and the size stored in the file.

| Part | `hello` (26.51 MiB) | `mp` (26.51 MiB) | `pandas-csv` (62.01 MiB) | The same in all three? |
|---|---|---|---|---|
| Stub ELF: the launcher with CPython and its libraries linked in; it records the runtime id it was linked as | 19.97 MiB | 19.97 MiB | 19.97 MiB | yes, byte for byte (the default glibc 2.34 stub) |
| Zero padding to the payload's 64 KiB boundary | 31.7 KiB | 31.7 KiB | 31.7 KiB | yes |
| Payload header (208 bytes) | | | | partly: the Python version and bytecode magic are; the app name, entry point (for an app of several commands, the table of their names and targets) and build information are not, nor is the native cache generation, which depends on the files an app extracts (`hello` and `mp` extract none and share it) |
| Tables: strings and the blob, module, file and distribution records | 99.4 KiB | 99.5 KiB | 551.2 KiB | no |
| Standard library bytecode, 551 modules | 10.80 → 4.09 MiB | same | same | the same content |
| Standard library sources | 10.13 → 2.17 MiB | same | same | the same content |
| Standard library data files (29) | 168 → 34 KiB | same | same | the same content |
| Startup modules (4; the boot module is one), code and sources | 64 → 22 KiB | same | same | the same content |
| fsbpyr's boot and runtime modules (11 at 42d8ecb; 15 in v0.27.0), code and sources | 256 → 94 KiB | same | same | the same content |
| The app's own package (for a project without a build system, its files under `_fsbpyr_scripts`): code, sources, metadata | 2 → 1 KiB | 9 → 3 KiB | 2 → 1 KiB | no |
| Third-party bytecode (1,847 modules) | — | — | 39.81 → 13.04 MiB | the app's own |
| Third-party sources | — | — | 28.75 → 5.51 MiB | the app's own |
| Third-party data files (527) | — | — | 4.22 → 1.01 MiB | the app's own |
| Third-party dist-info metadata (37 files) | — | — | 469 → 151 KiB | the app's own |
| Native extensions (64) | — | — | 32.06 → 9.14 MiB | the app's own |
| Native libraries bundled in wheels (3) | — | — | 26.19 → 6.21 MiB | the app's own |
| Trailer: payload offset and size, whole-file digest | 64 bytes | 64 bytes | 64 bytes | no |

- **A minimal app is almost all runtime.** 26.41 MiB of `hello`'s 26.51 MiB (99.6%) is the same
  in every app built for this Python and glibc baseline with this fsbpyr: the stub, the padding
  and 6.41 MiB of standard library and boot code. Every app carries the whole standard library
  (551 modules, with their sources), not only the modules it imports; what is `hello`'s own is
  about 100 KiB, mostly its tables.
- **A second stub exists per Python and target.** An app built with `--glibc 2.28` (D46)
  carries the stub linked against the EL8 sysroot instead, whose size was not measured. A
  released fsbpyr pairs that stub with its own standard library (`paired` in
  [`runtime/stubs.py`][stubs]), and the boot code is generated for each build either way, so
  the standard library and boot rows hold the same content with both stubs. What differs is the
  stub and its padding, and the build information computed from the stub or the baseline
  (`runtime`, `runtime_inputs`, `stub_sha256`, `glibc`, `system`, `glibc_baseline`); the
  third-party wheels are chosen for `manylinux_2_28` instead of `manylinux_2_34`
  (`Target.platform_for`), so the native files of an app with dependencies may differ too. The
  26.41 MiB figure above is for the default stub.
- **The same content is not always the same bytes.** Modules and files under 64 KiB are packed
  into blocks of 64 KiB (code) and 256 KiB (the rest) in the order of their paths (D30), so a
  third-party module stored next to a standard-library one in path order shares its block.
  `hello` and `mp` store 97.0% of their blob data in byte-identical blocks and frames; the three
  apps together 82.5% of `hello`'s (5.29 of 6.41 MiB), since numpy's and pandas' modules fall
  into blocks of the standard library's.
- **Outside the blob data,** the stub and the padding are identical; the header's identity
  fields, the tables and the trailer's digest are each app's own.

How it was measured: each executable was built with `fsbpyr build --python 3.14 --no-smoke` and
read with fsbpyr's own payload reader ([`src/fsbpyr/payload/reader.py`][reader.py]). Every
module's code and source and every file was compared across the apps by name and by the SHA-256
of its uncompressed content, and stored sizes were attributed per blob, a block's to its members
in proportion to their size. For the byte-level figure, every stored region (a zstd frame, a
block, a piece) was compared by the SHA-256 of its stored bytes.

[format-md]: ../docs/format.md
[parts-md]: ../docs/executable-parts.md
[plan]: ../docs/IMPLEMENTATION_PLAN.md
[format.py]: ../src/fsbpyr/payload/format.py
[format.h]: ../src/fsbpyr/launcher/format.h
[payload.c]: ../src/fsbpyr/launcher/payload.c
[reader.py]: ../src/fsbpyr/payload/reader.py
[writer.py]: ../src/fsbpyr/payload/writer.py
[assemble.py]: ../src/fsbpyr/assemble.py
[build.py]: ../src/fsbpyr/build.py
[closure.py]: ../src/fsbpyr/closure.py
[links.py]: ../src/fsbpyr/links.py
[vm-smoke]: ../tools/vm-smoke.py
[vm-pins]: ../tools/vm-pins.toml
[stubs]: ../src/fsbpyr/runtime/stubs.py
