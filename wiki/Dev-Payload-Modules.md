# Payload modules

The package [`src/fsbpyr/payload/`](../src/fsbpyr/payload)
holds the Python side of the executable format: how a payload is described (`spec`), written
into an executable (`writer`), read back and validated (`reader`), and turned back into a
description (`convert`), with the format's constants and checks (`format`) and the temporary
file a build keeps compressed bytes in (`spill`). This page describes each module and how the
Python objects map onto the format. The byte layouts of the trailer, the header and every
record are on [Executable format](Dev-Executable-Format.md); the launcher, which reads the same
bytes in C at every start, is on [Launcher](Dev-Launcher.md).

## From a build's inputs to an executable

![Flow of a payload: the build's inputs become Module, File and Dist specs; the runtime comes from the cache or from the running fsbpyr, whose standard library a build for glibc 2.28 pairs with its release's glibc 2.28 stub; the writer turns a PayloadSpec into an executable beside the output, which is committed and later read by fsbpyr inspect and by the launcher](images/payload-flow.svg)

A payload is described by a `PayloadSpec`: header fields plus three tuples of `Module`, `File`
and `Dist` objects, whose bytes are `Blob`s. Three sources fill it, all on
[Build modules](Dev-Build-Modules.md) and [Runtime modules](Dev-Runtime-Modules.md):

- the runtime: the standard library's modules and files, and the stub's bytes, come from a
  cached runtime (`registry.Runtime.load`, which reads the runtime's `stdlib.fsbp` with
  `reader.Payload` and `convert.spec_from_payload`) or, when fsbpyr itself runs as a built
  executable, from that executable (`Runtime.from_executable`, the same two steps on the
  running file); for `--glibc 2.28` a released fsbpyr keeps that standard library and takes
  the stub of its release for glibc 2.28 instead (`stubs.paired`,
  [Runtime modules](Dev-Runtime-Modules.md#runtimes-for-glibc-228-el8));
- the boot code: `assemble.boot_modules` compiles `src/fsbpyr/boot/` into `Module`s
  ([Boot modules](Dev-Boot-Modules.md));
- the application: `classify` turns the installed distributions into `Module`, `File` and
  `Dist` objects. With a `Spill` (every `fsbpyr build` has one), compressed bytes go into the
  spill file and the blobs hold `Extent`s of it instead of bytes.

`assemble.build_temporary` puts the three together into one `PayloadSpec` and calls
`writer.write_temporary`, which writes the executable under a temporary name beside the output.
`build.build` smoke-tests that file (unless `--no-smoke`) and then calls `writer.commit`, which
renames it over the output; for an executable with several commands, `build` then makes the
command links beside it (`links.make_links`, [Build modules](Dev-Build-Modules.md)), which the
payload modules have no part in. Afterwards `reader.Executable` reads the file for
`fsbpyr inspect` (with `sizes` and `licensing`), for the runtime registry, and for the tests'
pure-Python stand-in of the launcher's `_fsbpyr` module.

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
| Integrity digest | the bytes | `format.tree_digest`, `TreeHasher`, `file_digest` | `Executable.verify` |

A few things the Python code decides that the layout tables only show the result of:

- **Header strings.** `app_name`, `entrypoint` and `build_info` are STRTAB strings like any
  other: the writer checks the text rules of `encode_text` (no NUL, no lone surrogate) and, for
  `app_name`, that it is a single path component, and nothing else. `entrypoint` holds the
  app's target, `module` or `module:attr`, or, for an executable with several commands, lines
  `name=target` sorted by name, which `closure.command_table` makes after checking the names
  ([Build modules](Dev-Build-Modules.md)). The payload modules carry the table as an opaque string;
  the boot code reads it at every start
  ([Boot modules](Dev-Boot-Modules.md#several-commands-in-one-executable)). Likewise the Python
  files of a project without a build system are ordinary records below the top-level package
  `_fsbpyr_scripts`; no record type or flag marks them.

- **Blob representation.** A blob's stored bytes are either in memory (`bytes`) or an `Extent`:
  a range of a file on disk with its integrity digest as identity. Everything a build spills is
  an Extent of the spill, and a file compressed from disk whose compressed form is not smaller
  is an Extent of the file itself; the writer streams Extents into the executable, checking
  each against its identity as it reads.
- **Codecs.** The `Blob` constructors choose the codec: `Blob.code` always compresses, into
  checksummed zstd (marshalled code is never stored raw on its own), `Blob.zstd` makes one
  frame, or one frame per 8 MiB piece above that (`ZSTD_PIECES`), and stores the bytes raw when
  compression does not make them smaller; `Blob.xz` does the same with xz streams of 4 MiB
  pieces (`PIECES`), for native files only.
- **Placement, deduplication and blocks.** The writer stores identical blobs once, orders blob
  data by placement class (startup code, code, resources and metadata, sources, natives), and
  packs small blobs into zstd blocks, one stream of blocks per class.
- **Determinism.** Modules, files and distributions are sorted by their key's UTF-8 bytes,
  blobs by class and the first path that refers to them; the writer's docstring states that
  identical specs always give identical bytes, and
  [docs/format.md, Determinism](../docs/format.md#determinism)
  adds: with the same zstd library version.
- **Sparse output.** Whole 1 MiB pieces of zeros inside a blob are not written, so a file of
  runs of zeros stays sparse in the executable.

## Modules

### `format`

[`src/fsbpyr/payload/format.py`](../src/fsbpyr/payload/format.py)

The binary layout of format version 7 as `struct` layouts, enums and constants, which
[docs/format.md](../docs/format.md) names as
the format's single source of truth (the launcher's `format.h` mirrors it, and a test checks
that the two agree). It also holds the integrity digest (`tree_digest` and its streaming
forms), the thread helpers that compression, hashing and decoding share, and the string, path
and name checks that writer and reader both apply. Among its constants: `FORMAT_VERSION` 7,
`READABLE_VERSIONS` 1 to 7 (the Python reader reads older files too), `PAYLOAD_ALIGN` 65536
(4096 for versions 1 to 6), `BLOB_ALIGN` 16, `TABLE_ALIGN` 8, `NONE` `0xFFFFFFFF`,
`DIGEST_LEAF` 1 MiB, `XZ_PIECE` 4 MiB, `ZSTD_PIECE` 8 MiB, `XZ_MEMLIMIT` 64 MiB,
`CODE_BLOCK_SIZE` 64 KiB, `BLOCK_SIZE` 256 KiB, `BLOCK_MEMBER_MAX` 64 KiB, `MAX_BLOCK_SIZE`
1 MiB, `MAX_BLOB_SIZE` 2^62, and `THREADS`, at most 8 and never more than the usable cores.

Classes:

- `PayloadError` (a `ValueError`): the spec or the executable's bytes violate the format.
- `Section`: the eight sections in header order (STRTAB, BLOBS, MODULES, FILES, DISTS,
  CLOSURES, DIGESTS, STARTUP).
- `Codec`: NONE, ZSTD, BLOCK, XZ, PIECES (xz pieces), ZSTD_PIECES.
- `ModuleKind`: MODULE, PACKAGE, NAMESPACE, EXTENSION.
- `ModuleFlag`: STARTUP, FS_REQUIRED_ROOT.
- `FileKind`: RESOURCE, SOURCE, NATIVE_EXT, NATIVE_LIB, METADATA.
- `FileFlag`: FS_REQUIRED.
- `Threads`: up to `THREADS` threads for one task, or the caller's own thread when one would
  do (a count under 2, or a call made on a pool's thread already: no pool inside a pool);
  `map` keeps the items' order.
- `TreeHasher`: `tree_digest` of bytes given in pieces of any size, holding at most one leaf.

Public functions:

- `extractable(kind, flags)`: whether a file is written to the cache at run time (native
  files and FS_REQUIRED files), and so needs an integrity digest.
- `tree_digest(data)`: SHA-256 over the SHA-256 of each 1 MiB piece; the launcher's
  `fsb_tree_digest` computes the same.
- `pooled_thread()`: initializer that marks a pool's threads, so that `parallel` called there
  runs inline.
- `parallel(function, items)`: `function` of each item, in order, on `Threads`.
- `is_hole(fd, start, length)`: whether a range of a file lies inside a hole (`SEEK_DATA`);
  `OSError` when the file is shorter than the range.
- `file_digest(fd, size, offset=0)`: `tree_digest` of a range of a file, read a leaf at a time
  on up to 8 threads in passes of 4096 leaves, so that the file's size does not count in
  memory; leaves inside holes are not read.
- `align(value, alignment)`: `value` rounded up.
- `encode_text(text)`: UTF-8 bytes, rejecting lone surrogates and NUL.
- `check_path(path)`: a site-relative POSIX path with no empty, `.` or `..` component, no
  backslash and no NUL.
- `is_module_part(part)` and `check_module_name(name)`: dotted names whose parts are
  identifiers once `-` is read as `_`, a leading digit allowed (CPython's
  `_sysconfigdata__linux_x86_64-linux-gnu`, mypyc's shared `<hex>__mypyc` module).
- `canonical_dist_name(name)`: PEP 503 normalization.

Rules it enforces: the text, path and module-name rules above; that extracted files carry a
digest is defined here (`extractable`) and enforced by the writer and the reader.

Imports: `fsbpyr.compat` (top).
Imported by: `fsbpyr.assemble`, `fsbpyr.build`, `fsbpyr.classify`, `fsbpyr.cli`,
`fsbpyr.compiler`, `fsbpyr.inspection`, `fsbpyr.licensing`, `fsbpyr.payload.convert`,
`fsbpyr.payload.reader`, `fsbpyr.payload.spec`, `fsbpyr.payload.spill`,
`fsbpyr.payload.writer`, `fsbpyr.runtime.notices`, `fsbpyr.runtime.registry`,
`fsbpyr.runtime.stdlib`, `fsbpyr.sizes`.

### `spec`

[`src/fsbpyr/payload/spec.py`](../src/fsbpyr/payload/spec.py)

The writer's input model: frozen dataclasses for the header fields, the modules, files and
distributions, and the blobs that hold their bytes. A blob's stored bytes are in memory or an
`Extent` of a file, which is never read whole: `Extent.leaves` reads it 1 MiB at a time (up to
32 leaves at once, on up to 8 threads), does not read leaves inside holes of a file it opens by
path, and raises `OSError` at the end if the bytes do not hash to the Extent's identity, so a
file that changed while the build read it fails the build. The `Blob` class methods make the
build's compression choices; the default zstd level is `ZSTD_LEVEL` 9, `ZSTD_FAST_LEVEL` 3 is
what `--fast-compression` keeps, and native files with `--best-compression` use xz preset 6
(`XZ_PRESET`) with the build host's BCJ filter.

Classes:

- `Extent(path, offset, size, identity, fd=-1)`: `size` bytes at `offset` of a file; `identity`
  is their `tree_digest`, and `fd`, when given, is read instead of `path` (a spill's). Methods
  `leaves`, `pieces`, `read` (whole, for small extents only) and `of_file(path)`.
- `Blob(stored, size, codec, sha256=None, pieces=(), known=None)`: the stored bytes of one
  resource and its codec; `sha256` is the integrity digest that goes into DIGESTS, `known` a
  content digest known but not listed. Constructors `raw`, `raw_file` (an Extent of a file,
  never read into memory), `code`, `zstd`, `xz` and `from_xz`; `content` and
  `content_digest`.
- `Module(name, path, kind, code=None, source=None, file=None, dist=None, flags)`: a MODULES
  record; `file` is the path of an extension's NATIVE_EXT file, `dist` a canonical name.
- `File(path, kind, blob, mode=0o644, flags, dist=None, closure=())`: a FILES record;
  `closure` lists the paths of the bundled libraries extracted with the file.
- `Dist(name, dist_info, version)`: a DISTS record.
- `PayloadSpec(python_hexversion, bytecode_magic, generation_id, app_name, entrypoint,
  build_info="{}", modules, files, dists, blocks=True, zstd_level=ZSTD_LEVEL)`: the whole
  payload; `entrypoint` is a target or a table of commands (above), `blocks` turns block
  packing on, `zstd_level` is the blocks' level.

Public functions:

- `stored_size(stored)` and `stored_bytes(stored)`: the length and the bytes of either
  representation (bytes or Extent).
- `zstd_options(level)`: compression parameters for one frame with its content size and a
  checksum (the launcher checks both); a level outside 1 to 22 is a `PayloadError`.
- `xz_pieces(data)`: the content of each xz stream of a native file, 4 MiB each but the last.
- `xz_stream(piece)`: one xz stream of a piece, with the host's BCJ filter and LZMA2 preset 6
  and no integrity check of its own (the executable's digest covers it).

Rules it enforces (in `Blob.__post_init__`): size between 0 and 2^62; for codec NONE the
stored size equals the size; one xz stream holds at most 4 MiB; a listed digest is 32 bytes;
pieces exist exactly for codecs PIECES and ZSTD_PIECES, there are at least two, the parent
stores no bytes of its own, every piece has the pieces' codec and the full piece size except
the last (1 to that size), and the sizes add up. `zstd_options` rejects levels outside 1 to 22.

Imports: `fsbpyr.compat`, `fsbpyr.targets`, `fsbpyr.payload.format` (all top).
Imported by: `fsbpyr.assemble`, `fsbpyr.build`, `fsbpyr.classify`, `fsbpyr.compiler`,
`fsbpyr.config`, `fsbpyr.payload.convert`, `fsbpyr.payload.spill`, `fsbpyr.payload.writer`,
`fsbpyr.runtime.notices`, `fsbpyr.runtime.registry`, `fsbpyr.runtime.stdlib`.

### `spill`

[`src/fsbpyr/payload/spill.py`](../src/fsbpyr/payload/spill.py)

"Spill" is where a build keeps what it compressed: one temporary file in the output's
directory, created with the prefix `.fsbpyr-spill-` and unlinked at once, so that only its
descriptor is left. Compressed bytes are appended to it, and the blobs hold `Extent`s of it
(read through the descriptor) instead of the bytes, so no blob's bytes stay in memory however
much a build packages, and nothing is left on disk however the build ends. `build.build` opens
one `Spill` around classification and the writing of the executable; the writer then streams
the Extents into the executable. `classify` uses `keep` for the blobs it makes in memory and
`compress_files` for native files (with digests) and for files too large to read whole.

Classes:

- `Spill(directory)`: the temporary file. `add(data)` appends bytes and returns their Extent
  (the place is reserved under a lock, the write happens outside it, so appends from threads
  are each whole); `appender()` starts a blob written a piece at a time; `keep(blob,
  content=None)` returns the blob with its stored bytes, and its pieces', moved into the
  spill; `compress_files(paths, xz=False, level, digest=False)` compresses whole files into
  the spill (below); `close()`.
- `Appender`: bytes appended a piece at a time (a compressor's output) as one Extent; it holds
  the spill's lock until `finish()` returns the Extent or `abandon()` gives the space up.
- `_File` (private): a file read a piece at a time, whole 1 MiB leaves inside holes returned
  as zeros without reading; `piece` also returns each leaf's digest.

`compress_files` cuts every file into pieces of 8 MiB (zstd) or 4 MiB (xz), sorts the pieces
of all files largest first, and compresses them in batches of 64 on up to `THREADS` threads,
each piece read from its file, hashed and compressed on its thread and appended to the spill
as it is done. A file whose compressed pieces are not smaller than its content becomes an
Extent of the original file; otherwise one piece is a ZSTD or XZ blob and several are a
ZSTD_PIECES or PIECES blob, as `Blob.zstd` and `Blob.from_xz` would make them. At most one
batch of pieces is in memory.

Rules it enforces: an `Extent` it hands out is checked against its identity when the writer
reads it (by `Extent.leaves`); `_File` raises `OSError` when a file turns out shorter than it
was.

Imports: `fsbpyr.compat`, `fsbpyr.payload.format`, `fsbpyr.payload.spec` (all top).
Imported by: `fsbpyr.build`, `fsbpyr.classify`.

### `writer`

[`src/fsbpyr/payload/writer.py`](../src/fsbpyr/payload/writer.py)

The deterministic encoder and executable assembler. `write_payload` validates the spec, sorts
modules, files and distributions by key, and builds the blob table: identical blobs are stored
once, blob data is placed by class and by the first path that refers to it, and every non-empty
blob under 64 KiB that is not a native file goes into a block when `blocks` is on (blocks of 64
KiB for startup code and code, 256 KiB for the rest, one stream of blocks per class). Blobs
bound for a block are keyed by the SHA-256 of their content, so the same content raw or as a
zstd frame at any level is one member: a runtime blob that comes back raw from an executable
(`convert`) packs the same as the frame it was built as. The blob data is streamed into the
output first, each blob at a 16-byte boundary and each block where its first member would be
(blocks compressed ahead on up to `THREADS` threads); then the header and the tables are written
in front of it. Block records follow the blob records, and the records of the pieces of PIECES
and ZSTD_PIECES blobs follow the block records.

Classes (all private):

- `_Placement`: the five placement classes in disk order: STARTUP, CODE, RESOURCE, SOURCE,
  NATIVE.
- `_Strings`: the string table, each string once, NUL-terminated; more than 4 GiB is a
  `PayloadError`.
- `_Sink`: writes the payload's bytes into a seekable file from a base offset on, each write
  at its place; whole 1 MiB pieces of zeros are left unwritten.
- `_Blobs`: deduplication, blob indices in placement order, DIGESTS order, block packing, and
  the writing of the blob data area and its records.
- `_Ahead`: makes items (blocks) on a thread pool, a bounded window ahead of the one asked
  for.

Public functions:

- `write_payload(spec, out, base=0)`: write the payload of `spec` into the seekable `out` from
  `base` on; returns its size.
- `encode_payload(spec)`: the payload in memory (the runtime registry writes `stdlib.fsbp`
  with it).
- `assemble(stub, payload)`: an executable image in memory: stub, zero padding to 64 KiB,
  payload, trailer ending with the digest of everything before it. Used by the tests.
- `write_executable(path, stub, spec)`: `write_temporary`, then `commit`. Used by the tests.
- `write_temporary(path, stub, spec)`: the executable, mode 0755, in a new
  `.{name}.XXXX.tmp` beside `path`: the stub, the payload at the next 64 KiB boundary, the
  trailer without its digest; then the digest is computed by reading the file back
  (`file_digest`), written, and the file fsynced. On any failure the temporary file is
  removed.
- `commit(tmp, path)`: rename `tmp` over `path` (removing `tmp` if that fails), then fsync
  the directory so the rename persists.

Rules it enforces (`_validate`, before anything is written):

- header: `generation_id` is 32 bytes, `bytecode_magic` 4 bytes, `python_hexversion` fits in
  32 bits, and `app_name` is a single path component (not empty, `.` or `..`, no `/` or NUL);
- distributions: unique, canonical names, valid text, `dist_info` a safe path;
- files: unique safe paths, mode at most `0o777`, a known distribution, a digest on every file
  extracted at run time, xz only on native files, no path used as both a file and a
  directory; a closure only on a NATIVE_EXT file, without duplicates or itself, every member a
  native file of the payload;
- modules: unique, valid names and paths, FS_REQUIRED_ROOT only on a PACKAGE, a known
  distribution; MODULE and PACKAGE have code and no file, NAMESPACE has neither code nor source
  nor file, EXTENSION has a NATIVE_EXT file and no code; code is checksummed (a zstd frame,
  zstd pieces, or raw bytes that will be packed into a block); a source is never xz.

Imports: `fsbpyr.compat`, `fsbpyr.payload.format`, `fsbpyr.payload.spec` (all top).
Imported by: `fsbpyr.assemble`, `fsbpyr.build`, `fsbpyr.runtime.registry`.

### `reader`

[`src/fsbpyr/payload/reader.py`](../src/fsbpyr/payload/reader.py)

The validating reader used on the build side, by `fsbpyr inspect`, and by the tests. `Payload`
parses and checks every table when it is constructed, then answers lookups with binary searches.
It reads format versions 1 to 7, where the launcher reads only 7. Reading one frame or stream
allocates no more than its declared size plus one byte (a block member: its whole block, at most
1 MiB; a blob in pieces: the pieces and the joined result): zstd and xz content is decoded a
chunk at a time and must come out at exactly the recorded size. For an executable mapped from
its file, `Executable` passes a callback that drops the pages of blob data already read
(`madvise(MADV_DONTNEED)`), so reading blobs does not make resident memory grow with them.
`Executable.verify` recomputes the trailer's digest, for an executable opened from its file by
reading the file a leaf at a time (format versions 1 and 2: the SHA-256 of the payload).

Classes:

- `BlobRecord`, `ModuleRecord`, `FileRecord`, `DistRecord`: frozen dataclasses of decoded
  records, each with its table index.
- `Payload(buffer, release=None)`: a parsed payload. Methods `startup_modules`, `blobs`,
  `modules`, `files`, `dists`, `blob`, `stored` (the stored bytes, for copying a blob
  verbatim), `read_blob` (the content; pieces decoded in parallel; the last 32 blocks kept
  decompressed), `contains` (search a blob's content a chunk at a time), `closure`, `digest`,
  `find_module`, `find_file`, `find_dist`, `list_dir`, `is_dir`, `list_modules`, `release`;
  property `data_start`.
- `Executable(buffer, mapping=None, fd=-1)`: stub, payload and trailer. `open(path)` maps the
  file read-only; `parse(image)` takes bytes. `payload`, `stub_prefix()` (stub plus padding,
  reusable for new payloads), `verify()`, `close()`, and use as a context manager.

Public functions:

- `zstd_frame_has_checksum(frame)`: the zstd magic followed by a frame descriptor with the
  content-checksum flag.

Rules it enforces (the reader's part of the validation rules in docs/format.md):

- trailer: magic, a readable version, flags 0, the payload offset aligned (65536 from version
  7, 4096 before), payload and trailer filling the file exactly, and the payload's version
  equal to the trailer's;
- header: magic, a readable version, header size 208; every section inside the payload, after
  the header, 8-byte aligned and not overlapping another;
- strings inside STRTAB, NUL-terminated, without NUL, valid UTF-8;
- blobs: reserved fields zero, a known codec; data blobs 16-byte aligned inside the blob data
  area, at most 2^62, codec NONE with equal sizes, xz only from version 5 and at most 4 MiB;
  block members (from version 2) inside a zstd block of at most 1 MiB; blobs in pieces (xz from
  version 5, zstd from version 6) naming consecutive pieces of the right codec and sizes, no
  piece shared by two blobs; digest indices in range;
- distributions with canonical names and safe `dist_info` paths;
- files: mode at most `0o777`, safe paths, known kinds and flags, indices in range, xz only on
  native files, no file pointing at a piece, a digest on every extracted file, closures well
  formed (ascending, in range, on a NATIVE_EXT file, only native members, not itself);
- modules: reserved fields zero, valid names and paths, the kind invariants (code a zstd
  frame, a block member or zstd pieces), no xz source, FS_REQUIRED_ROOT only on a PACKAGE;
- MODULES, FILES and DISTS strictly sorted, and STARTUP listing exactly the flagged modules;
- on decompression: a zstd frame with a content checksum, its declared content size equal to
  the record's, ending where the stored bytes end; xz within 64 MiB of decoder memory; the
  output exactly the recorded size, with nothing after the frame or stream.

Imports: `fsbpyr.compat`, `fsbpyr.payload.format` (both top).
Imported by: `fsbpyr.inspection`, `fsbpyr.licensing`, `fsbpyr.payload.convert`,
`fsbpyr.runtime.registry`, `fsbpyr.sizes`.

### `convert`

[`src/fsbpyr/payload/convert.py`](../src/fsbpyr/payload/convert.py)

Turns a parsed payload back into a `PayloadSpec`, keeping every stored blob byte for byte (no
recompression): zstd frames, xz streams and pieces are copied as stored, while a block member
comes back as its raw content, which the next write packs into its own blocks again. Digests
come along from DIGESTS, closures are turned back into paths, and the spec's `blocks` is on
when the payload had any block member. The runtime registry uses it twice: to load a cached
runtime's `stdlib.fsbp`, and to take the runtime out of the running fsbpyr executable
([Runtime modules](Dev-Runtime-Modules.md)).

Public functions:

- `spec_from_payload(payload)`: the spec of a payload of the current format version only.

Rules it enforces: a payload of another version is a `PayloadError`, because older DIGESTS
hold plain SHA-256, which a current file would present as integrity digests, so every check
of the extracted files would fail.

Imports: `fsbpyr.payload.format`, `fsbpyr.payload.reader`, `fsbpyr.payload.spec` (all top).
Imported by: `fsbpyr.runtime.registry`.

## Tests

The package's tests are in
[`tests/unit/payload/`](../tests/unit/payload);
[Testing](Dev-Testing.md) describes the suite.

## See also

- [Executable format](Dev-Executable-Format.md): the record layouts and the validation rules.
- [Build modules](Dev-Build-Modules.md): `classify`, `assemble` and `build`, which make the spec.
- [Runtime modules](Dev-Runtime-Modules.md): the runtime the stub and standard library come from.
- [Launcher](Dev-Launcher.md): the C reader of the same bytes.
