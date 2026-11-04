# Payload modules

The package [`src/fsbpyr/payload/`](../src/fsbpyr/payload) is the Python side of the executable
format. The byte layouts are on [Executable format](Dev-Executable-Format.md); the launcher,
which reads the same bytes in C, is on [Launcher](Dev-Launcher.md).

## From a build's inputs to an executable

![Flow of a payload: the build's inputs become Module, File and Dist specs; the runtime comes from the cache or from the running fsbpyr, whose standard library a build for glibc 2.28 pairs with its release's glibc 2.28 stub; the writer turns a PayloadSpec into an executable beside the output, which is committed and later read by fsbpyr inspect and by the launcher](images/payload-flow.svg)

A `PayloadSpec` holds the header fields and three tuples of `Module`, `File` and `Dist` objects,
whose bytes are `Blob`s. Three sources fill it:

- the runtime: the standard library and the stub come from a cached runtime or the running
  fsbpyr executable (`convert`, below); for `--glibc 2.28` a released fsbpyr takes its release's
  glibc 2.28 stub instead (`stubs.paired`,
  [Runtime modules](Dev-Runtime-Modules.md#runtimes-for-glibc-228-el8));
- the boot code: `assemble.boot_modules` compiles `src/fsbpyr/boot/` into `Module`s
  ([Boot modules](Dev-Boot-Modules.md));
- the application: `classify` turns the installed distributions into `Module`, `File` and `Dist`
  objects, whose blobs hold `Extent`s of the build's `Spill` instead of bytes.

`assemble.build_temporary` combines them and calls `writer.write_temporary`, which writes the
executable under a temporary name beside the output; `build.build` smoke-tests it (unless
`--no-smoke`) and renames it over the output with `writer.commit`
([Build modules](Dev-Build-Modules.md)).

## How the Python code maps onto the format

| Format element | Written from | Written by | Read back as |
|---|---|---|---|
| Trailer (magic, version, payload offset and size, digest) | `format.TRAILER` | `writer.write_temporary`, `writer.assemble` | `reader.Executable` attributes `payload_offset`, `payload_size`, `digest` |
| Header (identity fields, three strings, section table) | `PayloadSpec` fields, `format.HEADER` | `writer._layout` | `reader.Payload` attributes `python_hexversion`, `bytecode_magic`, `generation_id`, `app_name`, `entrypoint`, `build_info` |
| STRTAB | every name, path and version string | `writer._Strings` (each string once, NUL-terminated) | `Payload._str` |
| BLOBS | `spec.Blob` | `writer._Blobs` | `reader.BlobRecord` |
| MODULES | `spec.Module` | `writer.write_payload` (sorted by name) | `reader.ModuleRecord` |
| FILES | `spec.File` | `writer.write_payload` (sorted by path) | `reader.FileRecord` |
| DISTS | `spec.Dist` | `writer.write_payload` (sorted by name) | `reader.DistRecord` |
| CLOSURES | `File.closure` (paths) | count word, then sorted FILES indices | `Payload.closure` |
| DIGESTS | `Blob.sha256` | in blob order, for the blobs that have one | `Payload.digest` |
| STARTUP | `ModuleFlag.STARTUP` on a `Module` | indices of the flagged modules | `Payload.startup_modules` |
| Integrity digest | the bytes | `format.tree_digest`, `TreeHasher`, `file_digest` | `Executable.verify`, `Executable.verify_sha256` |

`app_name`, `entrypoint` and `build_info` are ordinary STRTAB strings. The payload modules carry
`entrypoint`, a target or the `name=target` lines `closure.command_table` makes for several
commands, as an opaque string that the boot code reads at every start
([Boot modules](Dev-Boot-Modules.md#several-commands-in-one-executable)). The Python files of a
project without a build system are ordinary records below `_fsbpyr_scripts`; no record type or
flag marks them.

## Modules

### `format`

[`src/fsbpyr/payload/format.py`](../src/fsbpyr/payload/format.py)

Format version 7 as `struct` layouts, enums and constants, the format's single source of truth
([docs/format.md](../docs/format.md); the launcher's `format.h` mirrors it, checked by a test),
with the integrity digest, the shared thread helpers, and the string, path and name checks of
writer and reader. The constants are in
[Executable format, Constants](Dev-Executable-Format.md#constants); `THREADS` is at most 8 and
never more than the usable cores.

Classes:

- `PayloadError` (a `ValueError`): the spec or the executable's bytes violate the format.
- `Section`: the eight sections in header order (STRTAB, BLOBS, MODULES, FILES, DISTS,
  CLOSURES, DIGESTS, STARTUP).
- `Codec`: NONE, ZSTD, BLOCK, XZ, PIECES (xz pieces), ZSTD_PIECES.
- `ModuleKind`: MODULE, PACKAGE, NAMESPACE, EXTENSION.
- `ModuleFlag`: STARTUP, FS_REQUIRED_ROOT.
- `FileKind`: RESOURCE, SOURCE, NATIVE_EXT, NATIVE_LIB, METADATA.
- `FileFlag`: FS_REQUIRED.
- `Threads`: up to `THREADS` threads for one task, or the caller's own thread for a count under 2
  or on a pool's thread (no pool inside a pool); `map` keeps the order.
- `TreeHasher`: `tree_digest` of bytes given in pieces of any size, holding at most one leaf.

Public functions:

- `extractable(kind, flags)`: whether a file is written to the cache at run time (native and
  FS_REQUIRED files) and so needs an integrity digest, which writer and reader enforce.
- `tree_digest(data)`: SHA-256 over the SHA-256 of each 1 MiB piece, as the launcher's
  `fsb_tree_digest`.
- `pooled_thread()`: marks a pool's threads, so that `parallel` runs inline there.
- `parallel(function, items)`: `function` of each item, in order, on `Threads`.
- `is_hole(fd, start, length)`: whether a range of a file lies inside a hole (`SEEK_DATA`);
  `OSError` when the file is shorter.
- `file_digest(fd, size, offset=0)`: `tree_digest` of a range of a file, a leaf at a time on up
  to 8 threads in passes of 4096 leaves, so memory does not grow with the file; leaves inside
  holes are not read.
- `executable_digests(fd, size)`: an executable's integrity digest and the plain SHA-256 of the
  whole file from one read (`inspect --verify`): up to 7 threads read and hash its leaves, holes
  unread, at most 64 leaves ahead of the calling thread, which feeds the SHA-256 in order.
- `align(value, alignment)`: `value` rounded up.
- `encode_text(text)`: UTF-8 bytes, rejecting lone surrogates and NUL.
- `check_path(path)`: a site-relative POSIX path with no empty, `.` or `..` component, no
  backslash and no NUL.
- `is_module_part(part)` and `check_module_name(name)`: dotted names whose parts are identifiers
  once `-` is read as `_`, a leading digit allowed (CPython's
  `_sysconfigdata__linux_x86_64-linux-gnu`, mypyc's shared `<hex>__mypyc` module).
- `canonical_dist_name(name)`: PEP 503 normalization.

Imports: `fsbpyr.compat` (top).
Imported by: `fsbpyr.assemble`, `fsbpyr.build`, `fsbpyr.classify`, `fsbpyr.cli`,
`fsbpyr.compiler`, `fsbpyr.inspection`, `fsbpyr.licensing`, `fsbpyr.links`,
`fsbpyr.payload.convert`, `fsbpyr.payload.reader`, `fsbpyr.payload.spec`, `fsbpyr.payload.spill`,
`fsbpyr.payload.writer`, `fsbpyr.runtime.notices`, `fsbpyr.runtime.registry`,
`fsbpyr.runtime.stdlib`, `fsbpyr.signing`, `fsbpyr.sizes`.

### `spec`

[`src/fsbpyr/payload/spec.py`](../src/fsbpyr/payload/spec.py)

The writer's input: frozen dataclasses for the header fields, modules, files, distributions and
blobs. A blob's stored bytes are `bytes` or an `Extent`, a range of a file identified by its
integrity digest and never read whole: `Extent.leaves` reads 1 MiB at a time (up to 32 leaves at
once, on up to 8 threads), skips leaves inside holes of a file it opens by path, and raises
`OSError` at the end if the bytes do not hash to the identity, so a file that changed while the
build read it fails the build.

The `Blob` class methods choose the codec. `Blob.code` always compresses into checksummed zstd
(marshalled code is never stored raw on its own); `Blob.zstd` makes one frame, or one per 8 MiB
piece above that (`ZSTD_PIECES`), and stores the bytes raw when compression does not make them
smaller; `Blob.xz`, for native files only, does the same with 4 MiB xz pieces (`PIECES`). The
zstd level is `ZSTD_LEVEL` 9, or `ZSTD_FAST_LEVEL` 3 with `--fast-compression`; native files with
`--best-compression` use xz preset 6 (`XZ_PRESET`) with the build host's BCJ filter.

Classes:

- `Extent(path, offset, size, identity, fd=-1)`: `size` bytes at `offset` of a file; `identity`
  is their `tree_digest`; `fd`, when given, is read instead of `path` (a spill's). Methods
  `leaves`, `pieces`, `read` (whole, small extents only) and `of_file(path)`.
- `Blob(stored, size, codec, sha256=None, pieces=(), known=None)`: one resource's stored bytes
  and codec; `sha256` is the integrity digest for DIGESTS, `known` a content digest not listed.
  Constructors `raw`, `raw_file` (an Extent of a file, never read into memory), `code`, `zstd`,
  `xz` and `from_xz`; `content` and `content_digest`.
- `Module(name, path, kind, code=None, source=None, file=None, dist=None, flags)`: a MODULES
  record; `file` is the path of an extension's NATIVE_EXT file, `dist` a canonical name.
- `File(path, kind, blob, mode=0o644, flags, dist=None, closure=())`: a FILES record; `closure`
  lists the paths of the bundled libraries extracted with the file.
- `Dist(name, dist_info, version)`: a DISTS record.
- `PayloadSpec(python_hexversion, bytecode_magic, generation_id, app_name, entrypoint,
  build_info="{}", modules, files, dists, blocks=True, zstd_level=ZSTD_LEVEL)`: the whole
  payload; `blocks` turns block packing on, `zstd_level` is the blocks' level.

Public functions:

- `stored_size(stored)` and `stored_bytes(stored)`: length and bytes of either representation.
- `zstd_options(level)`: parameters for one frame with its content size and a checksum (the
  launcher checks both); a level outside 1 to 22 is a `PayloadError`.
- `xz_pieces(data)`: the content of each xz stream of a native file, 4 MiB each but the last.
- `xz_stream(piece)`: one xz stream with the host's BCJ filter, LZMA2 preset 6 and no integrity
  check of its own (the executable's digest covers it).

Rules it enforces (`Blob.__post_init__`): size 0 to 2^62; codec NONE stores exactly the size;
one xz stream holds at most 4 MiB; a listed digest is 32 bytes; pieces exist exactly for codecs
PIECES and ZSTD_PIECES, at least two, the parent stores no bytes, every piece has the pieces'
codec and the full piece size except the last (1 to that size), and the sizes add up.

Imports: `fsbpyr.compat`, `fsbpyr.targets`, `fsbpyr.payload.format` (all top).
Imported by: `fsbpyr.assemble`, `fsbpyr.build`, `fsbpyr.classify`, `fsbpyr.compiler`,
`fsbpyr.config`, `fsbpyr.payload.convert`, `fsbpyr.payload.spill`, `fsbpyr.payload.writer`,
`fsbpyr.runtime.notices`, `fsbpyr.runtime.registry`, `fsbpyr.runtime.stdlib`.

### `spill`

[`src/fsbpyr/payload/spill.py`](../src/fsbpyr/payload/spill.py)

The spill is one temporary file in the output's directory, created with the prefix
`.fsbpyr-spill-` and unlinked at once. Compressed bytes are appended to it and blobs hold
`Extent`s of it, read through its descriptor, so no blob's bytes stay in memory and nothing is
left on disk however the build ends. `build.build` opens one `Spill` around classification and
writing; `classify` uses `keep` for blobs it makes in memory and `compress_files` for native
files (with digests) and files too large to read whole.

Classes:

- `Spill(directory)`: `add(data)` appends bytes and returns their Extent (the place is reserved
  under a lock and written outside it, so appends from threads are each whole); `appender()`
  starts a blob written a piece at a time; `keep(blob, content=None)` moves the blob's stored
  bytes, and its pieces', into the spill; `compress_files(paths, xz=False, level,
  digest=False)` (below); `close()`.
- `Appender`: a compressor's output appended as one Extent; it holds the spill's lock until
  `finish()` returns the Extent or `abandon()` gives the space up.
- `_File` (private): a file read a piece at a time, whole 1 MiB leaves inside holes returned as
  zeros without reading; `piece` also returns each leaf's digest; `OSError` when the file turns
  out shorter than it was.

`compress_files` cuts every file into pieces of 8 MiB (zstd) or 4 MiB (xz), sorts all pieces
largest first, and compresses them in batches of 64 on up to `THREADS` threads, each read,
hashed, compressed and appended on its thread; at most one batch is in memory. A file whose
compressed pieces are not smaller than its content becomes an Extent of the original file;
otherwise one piece is a ZSTD or XZ blob and several a ZSTD_PIECES or PIECES blob, as
`Blob.zstd` and `Blob.from_xz` make them. A piece inside a hole is neither read nor compressed:
the zeros of its size are compressed once per call, under a lock, and every such piece takes
those bytes, the ones compressing it would give (D15).

Imports: `fsbpyr.compat`, `fsbpyr.payload.format`, `fsbpyr.payload.spec` (all top).
Imported by: `fsbpyr.build`, `fsbpyr.classify`.

### `writer`

[`src/fsbpyr/payload/writer.py`](../src/fsbpyr/payload/writer.py)

The deterministic encoder and executable assembler. `write_payload` validates the spec, sorts
modules, files and distributions by their key's UTF-8 bytes, and orders, deduplicates and packs
the blobs as [BLOBS record](Dev-Executable-Format.md#blobs-record-32-bytes) describes, packing
only when `blocks` is on. Blobs bound for a block are keyed by the SHA-256 of their content, so a
runtime blob that `convert` returns raw packs as the zstd frame it was built as. The blob data is
streamed out first (blocks compressed ahead on up to `THREADS` threads), then the header and
tables are written in front of it. Identical specs give identical bytes with the same zstd
library version ([docs/format.md, Determinism](../docs/format.md#determinism)).

Classes (all private):

- `_Placement`: the placement classes in disk order: STARTUP, CODE, RESOURCE (resources and
  metadata), SOURCE, NATIVE.
- `_Strings`: the string table, each string once, NUL-terminated; over 4 GiB is a
  `PayloadError`.
- `_Sink`: writes the payload into a seekable file from a base offset on, each write at its
  place; whole 1 MiB pieces of zeros are left unwritten, so runs of zeros stay sparse.
- `_Blobs`: deduplication, blob indices in placement order, DIGESTS order, block packing, and
  writing the blob data area and its records.
- `_Ahead`: makes items (blocks) on a thread pool, a bounded window ahead of the one asked for.

Public functions:

- `write_payload(spec, out, base=0)`: write the payload into the seekable `out` from `base` on;
  returns its size.
- `encode_payload(spec)`: the payload in memory (the runtime registry writes `stdlib.fsbp` with
  it).
- `assemble(stub, payload)`: an executable image in memory: stub, zero padding to 64 KiB,
  payload, trailer ending with the digest of everything before it. Used by the tests.
- `write_executable(path, stub, spec)`: `write_temporary`, then `commit`. Used by the tests.
- `write_temporary(path, stub, spec)`: the executable, mode 0755, in a new `.{name}.XXXX.tmp`
  beside `path`: stub, payload at the next 64 KiB boundary, trailer; the digest is computed by
  reading the file back (`file_digest`), written, and the file fsynced. On any failure the
  temporary file is removed.
- `commit(tmp, path)`: rename `tmp` over `path` (removing `tmp` if that fails), then fsync the
  directory.

Rules it enforces (`_validate`, before anything is written): the
[validation rules](Dev-Executable-Format.md#validation-rules) a spec can break (text, paths and
names, canonical distribution names, mode at most `0o777`, a digest on every file extracted at
run time, xz only on native files and never on a source, FS_REQUIRED_ROOT only on a PACKAGE, the
module kind invariants with checksummed code, where raw bytes to be packed into a block count,
and the closure rules, with no duplicate member); besides: `generation_id` 32 bytes,
`bytecode_magic` 4 bytes, `python_hexversion` within 32 bits, `app_name` a single path component
(not empty, `.` or `..`, no `/` or NUL), unique keys, a known distribution for every file and
module, and no path both a file and a directory.

Imports: `fsbpyr.compat`, `fsbpyr.payload.format`, `fsbpyr.payload.spec` (all top).
Imported by: `fsbpyr.assemble`, `fsbpyr.build`, `fsbpyr.runtime.registry`.

### `reader`

[`src/fsbpyr/payload/reader.py`](../src/fsbpyr/payload/reader.py)

The validating reader, used by `fsbpyr inspect` (with `sizes` and `licensing`), the runtime
registry, `links` (whether a file at a command link's path is an earlier build of the project),
`signing` (the build information of a file it signs or verifies) and the tests' pure-Python
stand-in of the launcher's `_fsbpyr` module. `Payload` checks every table on construction
against the [validation rules](Dev-Executable-Format.md#validation-rules) of the file's version (versions 1 to 7; the launcher reads only 7), raising `PayloadError`, and
also refuses a piece shared by two blobs; lookups are binary searches. Decoding a frame or stream
allocates at most its declared size plus one byte (a block member: its whole block, at most
1 MiB; a blob in pieces: the pieces and the joined result), decodes a chunk at a time and must
give exactly the recorded size. For an executable mapped from its file, `Executable` drops the
pages of blob data already read (`madvise(MADV_DONTNEED)`), so resident memory does not grow with
the blobs read. `Executable.verify` recomputes the trailer's digest, reading a file a leaf at a
time (format versions 1 and 2: the SHA-256 of the payload); `Executable.verify_sha256` also
returns the plain SHA-256 of the whole file, from the same read.

Classes:

- `BlobRecord`, `ModuleRecord`, `FileRecord`, `DistRecord`: frozen dataclasses of decoded
  records, each with its table index.
- `Payload(buffer, release=None)`: a parsed payload. Methods `startup_modules`, `blobs`,
  `modules`, `files`, `dists`, `blob`, `stored` (the stored bytes, for copying a blob verbatim),
  `read_blob` (the content; pieces decoded in parallel; the last 32 blocks kept decompressed),
  `contains` (search a blob's content a chunk at a time), `closure`, `digest`, `find_module`,
  `find_file`, `find_dist`, `list_dir`, `is_dir`, `list_modules`, `release`; property
  `data_start`.
- `Executable(buffer, mapping=None, fd=-1)`: stub, payload and trailer. `open(path)` maps the
  file read-only; `parse(image)` takes bytes. `payload`, `stub_prefix()` (stub plus padding,
  reusable for new payloads), `verify()`, `close()`, and use as a context manager.

Public functions:

- `zstd_frame_has_checksum(frame)`: the zstd magic followed by a frame descriptor with the
  content-checksum flag.

Imports: `fsbpyr.compat`, `fsbpyr.payload.format` (both top).
Imported by: `fsbpyr.inspection`, `fsbpyr.licensing`, `fsbpyr.links`, `fsbpyr.payload.convert`,
`fsbpyr.runtime.registry`, `fsbpyr.signing`, `fsbpyr.sizes`.

### `convert`

[`src/fsbpyr/payload/convert.py`](../src/fsbpyr/payload/convert.py)

Turns a parsed payload back into a `PayloadSpec`, keeping every stored blob byte for byte: zstd
frames, xz streams and pieces are copied as stored, while a block member comes back as its raw
content, which the next write packs into blocks again. Digests come from DIGESTS, closures become
paths, and `blocks` is on when the payload had any block member. The runtime registry reads a
cached runtime's `stdlib.fsbp` (`Runtime.load`) and the running fsbpyr executable
(`Runtime.from_executable`) with `reader.Payload` and this module
([Runtime modules](Dev-Runtime-Modules.md)).

Public functions:

- `spec_from_payload(payload)`: the spec of a payload of the current format version only; any
  other version is a `PayloadError`, because older DIGESTS hold plain SHA-256, not integrity
  digests, so every check of the extracted files would fail.

Imports: `fsbpyr.payload.format`, `fsbpyr.payload.reader`, `fsbpyr.payload.spec` (all top).
Imported by: `fsbpyr.runtime.registry`.

## Tests

The package's tests are in [`tests/unit/payload/`](../tests/unit/payload);
[Testing](Dev-Testing.md) describes the suite.

## See also

- [Executable format](Dev-Executable-Format.md): the record layouts and the validation rules.
- [Build modules](Dev-Build-Modules.md): `classify`, `assemble` and `build`, which make the spec.
- [Runtime modules](Dev-Runtime-Modules.md): the runtime the stub and standard library come from.
- [Launcher](Dev-Launcher.md): the C reader of the same bytes.
