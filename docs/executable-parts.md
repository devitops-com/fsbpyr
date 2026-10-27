# What fsbpyr executables share

Which parts of executables built for one Python are the same in every app, and which are the
app's own. Measured on 2026-10-24 with fsbpyr at commit 42d8ecb (a developer build), CPython
3.14.9, x86-64, for three apps: `hello` and `mp` (pure-Python fixtures in
`tests/fixtures/projects`) and `pandas-csv` (a corpus app: pandas and numpy, with native
extensions). The stub's launcher sources, the boot code (new `_fsbpyr_rt` modules: `commands`,
`links`, `restart`, `service`) and the stub's variants (one linked against the EL8 sysroot, D46)
changed on the branch after that commit and were not re-measured: the sizes of those rows are
those of 42d8ecb; the payload layout and the standard library rows are unchanged. Sizes are MiB and KiB; "→" gives the uncompressed size and the size stored in the
file. The layout itself is described in [format.md](format.md).

| Part | `hello` (26.51 MiB) | `mp` (26.51 MiB) | `pandas-csv` (62.01 MiB) | The same in all three? |
|---|---|---|---|---|
| Stub ELF: the launcher with CPython and its libraries linked in; it records the runtime id it was linked as (D46) | 19.97 MiB | 19.97 MiB | 19.97 MiB | yes, byte for byte (the default glibc 2.34 stub) |
| Zero padding to the payload's 64 KiB boundary | 31.9 KiB | 31.9 KiB | 31.9 KiB | yes |
| Payload header (208 bytes) | | | | partly: the Python version and bytecode magic are; the app name, entry point (for an app of several commands, the table of their names and targets) and build information are not, nor is the native cache generation, which depends on the files an app extracts (`hello` and `mp` extract none and share it) |
| Tables: strings and the blob, module, file and distribution records | 99.4 KiB | 99.5 KiB | 551.2 KiB | no |
| Standard library bytecode, 551 modules | 10.80 → 4.09 MiB | same | same | the same content |
| Standard library sources | 10.13 → 2.17 MiB | same | same | the same content |
| Standard library data files (29) | 168 → 34 KiB | same | same | the same content |
| Startup modules (4; the boot module is one), code and sources | 64 → 22 KiB | same | same | the same content |
| fsbpyr's boot and runtime modules (11 at 42d8ecb; 15 at HEAD), code and sources | 256 → 94 KiB | same | same | the same content |
| The app's own package (for a project without a build system, its files under `_fsbpyr_scripts`): code, sources, metadata | 2 → 1 KiB | 9 → 3 KiB | 2 → 1 KiB | no |
| Third-party bytecode (1,847 modules) | — | — | 39.81 → 13.04 MiB | the app's own |
| Third-party sources | — | — | 28.75 → 5.51 MiB | the app's own |
| Third-party data files (527) | — | — | 4.22 → 1.01 MiB | the app's own |
| Third-party dist-info metadata (37 files) | — | — | 469 → 151 KiB | the app's own |
| Native extensions (64) | — | — | 32.06 → 9.14 MiB | the app's own |
| Native libraries bundled in wheels (3) | — | — | 26.19 → 6.21 MiB | the app's own |
| Trailer: payload offset and size, whole-file digest | 64 bytes | 64 bytes | 64 bytes | no |

- **A minimal app is almost all runtime.** 26.41 MiB of `hello`'s 26.51 MiB (99.6%) is the
  same in every app built for this Python and glibc baseline with this fsbpyr: the stub, the padding and 6.41 MiB
  of standard library and boot code. Every app carries the whole standard library (551
  modules, with their sources), not only the modules it imports; what is `hello`'s own is
  about 100 KiB, mostly its tables.
- **A second stub exists per Python and target.** An app built with `--glibc 2.28` (D46)
  carries the stub linked against the EL8 sysroot; the standard library, the boot code and the
  layout of the tables are the same bytes in both, so what differs is the stub, its padding and
  the build information (`runtime`, `runtime_inputs.sysroot`, `stub_sha256`, `glibc_baseline`
  and `glibc`); the header's runtime fields (`python_hexversion`, `bytecode_magic`) are the same. The 26.41 MiB figure above is for the default stub.
- **The same content is not always the same bytes.** Modules and files under 64 KiB are packed
  into blocks of 64 KiB (code) and 256 KiB (the rest) in the order of their paths (D30), so a
  third-party module stored next to a standard-library one in path order shares its block.
  `hello` and `mp` store 97.0% of their blob data in byte-identical blocks and frames; the
  three apps together 82.5% of `hello`'s (5.29 of 6.41 MiB), since numpy's and pandas' modules
  fall into blocks of the standard library's.
- **Outside the blob data,** the stub and the padding are identical; the header's identity
  fields, the tables and the trailer's digest are each app's own.

How it was measured: each executable was built with `fsbpyr build --python 3.14 --no-smoke`
and read with fsbpyr's own payload reader (`src/fsbpyr/payload/reader.py`). Every module's
code and source and every file was compared across the apps by name and by the SHA-256 of its
uncompressed content, and stored sizes were attributed per blob, a block's to its members in
proportion to their size. For the byte-level figure, every stored region (a zstd frame, a
block, a piece) was compared by the SHA-256 of its stored bytes.
