# fsbpyr executable format — version 1

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
- `payload_offset` is a multiple of 4096, so the payload can be mapped with `mmap` directly.
- The payload ends exactly where the trailer begins:
  `payload_offset + payload_size + 64 == file_size`.

## Trailer (64 bytes, at EOF)

| Offset | Size | Field |
|--------|------|-------|
| 0 | 8 | magic `FSBPYR\0T` |
| 8 | 4 | `format_version` (= 1) |
| 12 | 4 | `flags` (reserved, 0) |
| 16 | 8 | `payload_offset` (absolute file offset) |
| 24 | 8 | `payload_size` |
| 32 | 32 | SHA-256 of the payload bytes |

The payload digest is checked only on demand (`fsbpyr inspect --verify`, `FSBPYR_VERIFY=1`),
never on a normal start.

## Payload header (208 bytes, at payload offset 0)

| Offset | Size | Field |
|--------|------|-------|
| 0 | 8 | magic `FSBPYR\0H` |
| 8 | 4 | `format_version` (= 1) |
| 12 | 4 | `header_size` (= 208) |
| 16 | 4 | `python_hexversion` of the target CPython (`PY_VERSION_HEX`) |
| 20 | 4 | bytecode magic (`importlib.util.MAGIC_NUMBER`) |
| 24 | 32 | `generation_id` (native cache generation, §7 of the plan) |
| 56 | 8 | `app_name` — Str |
| 64 | 8 | `entrypoint` — Str (`module:attr` or `module`) |
| 72 | 8 | `build_info` — Str (JSON, read only by `inspect`) |
| 80 | 8 × 16 | section table: `(u64 offset, u64 count)` for each section below, in this order |

**Str** is `(u32 offset, u32 length)` into the string table. Every string is stored as UTF-8
followed by one NUL byte, which the length does not count, so C code can use `strtab + offset`
directly as a C string.

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
| 0 | 8 | `offset` of the stored bytes |
| 8 | 8 | `stored_size` |
| 16 | 8 | `size` (uncompressed) |
| 24 | 1 | `codec`: 0 = none, 1 = zstd (a single frame with content checksum) |
| 25 | 3 | reserved (0) |
| 28 | 4 | `digest`: index into DIGESTS (SHA-256 of the uncompressed data) or NONE |

- Stored data is 16-byte aligned and lies after the end of the last section (the blob data
  area).
- `size` is at most 2^40 (1 TiB).
- Blob order on disk: startup code, module code, resources, sources, natives. Within each
  class, blobs are ordered by the first path that references them.
- Identical blobs are stored once.
- For codec none, `stored_size == size`.

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
- Every `code` blob is a zstd frame with the content-checksum flag set. `marshal.loads` is not
  safe against corrupt input, so integrity is verified before bytecode is ever unmarshalled.
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
by `n` FILES indices in strictly ascending order. Closures list the bundled shared libraries that
must be materialized together with a NATIVE_EXT file. Only NATIVE_EXT records carry a closure,
every member is a NATIVE_LIB record, and a record never lists itself.

### DIGESTS

An array of 32-byte SHA-256 digests. They are recorded for blobs that may be extracted to disk.

### STARTUP

u32 MODULES indices, strictly ascending: exactly the modules flagged STARTUP. The launcher reads
this short list to build CPython's frozen-module table, so it never scans the MODULES table.

## Validation rules (readers MUST enforce)

- Trailer and header magic and version must match. `header_size` must be 208. Trailer `flags`
  must be 0.
- `payload_offset` must be 4096-aligned, and the file size must match exactly.
- Every section must lie inside the payload, start after the header, be 8-byte aligned, and not
  overlap any other section.
- Every Str must lie inside STRTAB, be NUL-terminated, contain no NUL, and be valid UTF-8.
- Every index field must be NONE or in range. Enum fields must hold known values, and flag
  fields must hold known bits only. Reserved fields must be 0.
- Every blob must be 16-byte aligned, lie inside the blob data area, and have `size` ≤ 2^40.
  For codec none, `stored_size == size`.
- FILES paths, module paths and `dist_info` paths obey the path rules above. Module names are
  dotted Python identifiers. Dist names are canonical.
- The module kind invariants (including zstd code frames), the STARTUP rule (the STARTUP section
  lists exactly the flagged modules) and the closure rules hold.
- Every zstd frame a reader decompresses must have the content-checksum flag set.
- On decompression, the output must be exactly `size` bytes, with no trailing data, and must
  pass the zstd checksum. Readers never allocate more than `size + 1` bytes.
- Tables must be strictly sorted.

## Determinism

The same spec always encodes to the same bytes with the same zstd library version. The zstd
version is part of the build environment and is recorded in `build_info` (Phase 4). A
self-hosted fsbpyr binary carries its own statically linked libzstd, so the stage2 == stage3
check is not affected.
