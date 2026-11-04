# Performance

Where the time, memory and bytes of an fsbpyr executable go, measured with the release 0.30.0 (very
large files also with 0.31.0), so that a regression can be traced to the part that grew. The mechanisms are described on
[Dev-Launcher](Dev-Launcher.md), [Dev-Executable-Format](Dev-Executable-Format.md) and
[Dev-Security](Dev-Security.md).

## How the numbers were measured

- **Machine:** Intel Core Ultra 9 285 (8 performance cores, CPUs 0–7; 16 efficiency cores, CPUs
  8–23; SHA-NI and AVX2), Linux 6.12 (Debian 13), XFS on an NVMe SSD (Samsung 9100 PRO). x86-64
  only, on 2026-11-03.
- **Software:** the released `fsbpyr-3.14-x86_64-linux-gnu` of 0.30.0 (`0.30.0-8d35b93c`; its 3.15
  variant for the 3.15 rows), uv 0.13.6, which built that release, and CPython 3.14.10 from
  python-build-standalone 20261026 (uv's, the build the release embeds) for bare runs and venvs.
  Apps use the default stub (glibc 2.34) unless a section says `--glibc 2.28`.
- **Runs:** started with `os.posix_spawn`, stdout on `/dev/null`, and reaped with `wait4`, which
  gives the run's wall time, its CPU time and its peak RSS (`ru_maxrss`, the largest process of the
  run). Variants that are compared run interleaved, round by round, after warm-up runs of each,
  so that a drift of the CPU clock hits all alike; figures are medians (p50). Warm: the executable
  is in the page cache and the files it extracts are in its cache. Runs of 30 ms and more vary by
  a few percent between rounds; compare columns, not tables.
- **Apps:** fixtures of the repository, `noop` (a project whose `main()` returns at once),
  [`hello`](../tests/fixtures/projects/hello) (prints its arguments),
  [`heavy`](../tests/fixtures/projects/heavy) (each command imports one native dependency: `json`
  orjson, `model` pydantic, `crypto` cryptography, `np` numpy, `onnx` onnxruntime and numpy) and
  [`mp`](../tests/fixtures/projects/mp), and corpus apps:
  [`pandas-csv`](../tests/corpus/pandas-csv), [`nlp`](../tests/corpus/nlp) (nltk, spaCy,
  sentencepiece and tiktoken; 53 locked packages) and [`torch`](../tests/corpus/torch) (CPU
  torch 2.14.1: trains, scripts, compiles and loads a model).
- **A hybrid CPU moves small runs.** A run right after a heavy multi-threaded one may land on an
  efficiency core: `hello` after `heavy model` took 13.1 ms and twice the CPU time, 7.9 ms on
  its own. Small apps are measured against each other, not interleaved with heavy ones.

**No budget.** No test compares the time of a start, or of anything else fsbpyr does, with a
budget: the time limits in the tests are timeouts, and Hypothesis runs without a per-example
deadline ([`tests/conftest.py`](../tests/conftest.py)), since a time limit fails a slow or loaded
machine, not a wrong result.

## Starting a no-op app

Two rounds of 201 runs of each variant, interleaved, after 5 warm-ups; the integrity check is the
median `integrity_ns` of 201 further runs with `FSBPYR_TRACE=1`.

| Python | Bare CPython (`-I -S -c pass`) | App | App less the integrity check | Integrity check | Size |
|---|---|---|---|---|---|
| 3.14 | 5.45–5.47 ms | 7.68–7.69 ms | 6.01–6.02 ms (1.10×) | 1.67 ms | 27.9 MB |
| 3.15 | 5.44–5.45 ms | 7.77–7.78 ms | 6.11–6.12 ms (1.12×) | 1.66 ms | 29.7 MB |

The 3.14 executable is 21.0 MB of stub (the launcher with CPython linked in statically) and 6.8 MB
of standard library and boot code ([Where the bytes go](#where-the-bytes-go)). Everything the
launcher and the boot code do at every start is in the "App" column: the instance slot, the
`PYTHONUNBUFFERED`, `FSBPYR_INSTALL_LINKS` and `JOURNAL_STREAM` lookups, `sys.argv[0]` from
`AT_EXECFN`, the restart idiom's scan of `argv` and the test for a table of commands; each
imports its `_fsbpyr_rt` module only when its cheap test passes. The instance slot (an `open` and
an `fcntl`, `fsb_hold_slot` in [`launcher/slots.c`](../src/fsbpyr/launcher/slots.c)) costs
nothing measurable: with `FSBPYR_MAX_INSTANCES=0` the app took 7.73–7.77 ms.

## The integrity check

Every start hashes the whole executable before it reads anything else of it (`fsb_check_integrity`
in [`launcher/integrity.c`](../src/fsbpyr/launcher/integrity.c), called from `fsb_payload_open`
in [`launcher/payload.c`](../src/fsbpyr/launcher/payload.c)). The digest is SHA-256 over the
SHA-256 of each 1 MiB leaf. `fsb_parallel` in
[`launcher/parallel.c`](../src/fsbpyr/launcher/parallel.c) hashes the leaves on one thread per 4
leaves (`LEAVES_PER_THREAD`), at most 8 (`FSB_PARALLEL_MAX_THREADS`) and never more than the
cores the process may use (`sched_getaffinity`). The SHA-256 is OpenSSL's from the static runtime,
with SHA-NI where the CPU has it. Each thread reads its leaves with `pread` through a 64 KiB
buffer of its own (`READ_PIECE`), never mapping the file, so its size stays out of resident
memory; a leaf inside a hole is not read, and a leaf of zeros takes the digest of zeros unhashed
([Very large files](#very-large-files)); a pass hashes at most 4096 leaves (`FSB_PASS_LEAVES`). `FSBPYR_TRACE` reports the time as
`integrity_ns`.

`hello` (27.9 MB) and the same app with 206 MB and 996 MB of random data, which does not
compress, built with `--fast-compression` (244 MB and 1,073 MB); median of 31 runs, warm page
cache:

| Executable | Up to 8 threads | One core | Up to 8 threads, no SHA-NI | One core, no SHA-NI |
|---|---|---|---|---|
| 27.9 MB | 1.65 ms | 6.9 ms | 6.4 ms | 34.8 ms |
| 244 MB | 9.6 ms | 61.6 ms | 44.3 ms | 305 ms |
| 1,073 MB | 40.3 ms | 266 ms | 181 ms | 1,340 ms |
| per GB (from 1,073 MB) | 38 ms | 248 ms | 168 ms | 1,250 ms |

"One core" is `taskset -c 2`, "no SHA-NI" `OPENSSL_ia32cap=:~0x20000000`. Past a few hundred MB
the time grows linearly with the size. The check reads the file from the page cache; after a
reboot the disk read comes on top ([Cold start](#cold-start)). Reproduce, from a checkout, with
`fsbpyr` installed:

```sh
D=~/fsbpyr-bench; mkdir -p $D; cp -r tests/fixtures/projects/hello $D/small
cp -r $D/small $D/big; head -c 996M /dev/urandom > $D/big/src/hello/blob.bin  # 206M: 244 MB
fsbpyr build $D/small -o $D/small.app; fsbpyr build --fast-compression $D/big -o $D/big.app
for i in $(seq 31); do FSBPYR_TRACE=1 $D/big.app 2>&1 >/dev/null |
  sed -n 's/.*"integrity_ns": \([0-9]*\).*/\1/p'; done | sort -n | sed -n 16p  # median, ns
```

For the other columns put `taskset -c 2`, `env OPENSSL_ia32cap=:~0x20000000` or both in front of
`$D/big.app`. An earlier launcher mapped the file instead of reading it; mapping was faster at
every size, but put the file's pages into resident memory.

## Cold start

Starts with the page cache empty of the executable and, separately, of the files extracted to
its cache. [`spikes/bench.py`](../spikes/bench.py) `--cold` skips the warm-up runs and, before
each sample, writes the executable out and drops its pages with `posix_fadvise(POSIX_FADV_DONTNEED)`,
with `--cache DIR` those of every file under DIR as well; that needs no root, but the files must
be on a disk file system (on tmpfs nothing is evicted). 51 runs, two rounds. numpy's OpenBLAS ran
on one thread (`OPENBLAS_NUM_THREADS=1`), so that the table shows the disk, not OpenBLAS's
spinning threads ([Known effects](#known-effects)).

| App | Warm | Executable evicted | Executable and cache evicted |
|---|---|---|---|
| `hello` (27.9 MB; extracts nothing) | 7.8–8.0 ms | 13.6–13.7 ms | same |
| `heavy np` (77.3 MB; extracts 38 MB) | 57.7–57.8 ms | 73.3–73.8 ms | 82.0–82.4 ms |

Evicting the executable adds 6 ms for `hello` and 16 ms for `heavy np`: the integrity check reads
the whole file from the SSD before the app starts, so on a slower disk this grows with the size.
`heavy np` adds another 8.5 ms for the 38 MB of natives it loads. Reproduce with the `D` above:

```sh
cp -r tests/fixtures/projects/heavy $D/heavy; fsbpyr build $D/heavy -o $D/heavy.app
C=$D/cache; FSBPYR_CACHE_DIR=$C $D/heavy.app np  # fills the cache
export OPENBLAS_NUM_THREADS=1
python3 spikes/bench.py -n 51 --cache $C "warm=$D/heavy.app np"
FSBPYR_CACHE_DIR=$C python3 spikes/bench.py -n 51 --cold "exe=$D/heavy.app np"
python3 spikes/bench.py -n 51 --cold --cache $C "exe+cache=$D/heavy.app np"
python3 spikes/bench.py -n 51 --cold "hello=$D/small.app"
```

## Whole runs and the cache check

Each process hashes the extracted files it uses once, before it first uses them, with the same
digest as the executable's: `fsb_cache_sync` in [`launcher/cache.c`](../src/fsbpyr/launcher/cache.c),
reached from Python as `_fsbpyr.sync`, walks, checks and restores them in C without the GIL. A
run therefore costs what its venv's run costs, plus the integrity check (above) and the check of
the natives it loads.

Later runs, caches filled, against the app's venv on the same CPython (31 interleaved rounds, from
[Other tools](#other-tools)); the integrity check and the bytes decompressed from the payload are
medians of 11 runs with `FSBPYR_TRACE=1`:

| Run | venv | fsbpyr | Integrity check | Decompressed |
|---|---|---|---|---|
| `hello` | 7.7 ms | 8.0 ms | 1.7 ms | 0.9 MB |
| `heavy json` | 25.7 ms | 35.5 ms | 3.6 ms | 2.2 MB |
| `heavy crypto` | 36.8 ms | 42.9 ms | 3.6 ms | 2.7 MB |
| `heavy model` | 68.1 ms | 75.8 ms | 6.0 ms | 6.5 MB |
| `heavy np` | 56.1 ms | 63.4 ms | 3.6 ms | 5.4 MB |
| `heavy onnx` | 114.5 ms | 135.0 ms | 3.6 ms | 6.2 MB |
| `nlp` | 457.2 ms | 518.8 ms | 6.9 ms | 39.0 MB |

fsbpyr adds 0.3 ms (`hello`) to 62 ms (`nlp`), 1.04 to 1.38 times the venv: the integrity check,
the modules decompressed from the payload where the venv reads `.pyc` files, and the check of what
the run uses from the cache. `nlp` has spaCy and thinc as `fs-packages`, 232 MB in its cache.

## Compression and build options

The methods are set in [`payload/spec.py`](../src/fsbpyr/payload/spec.py): zstd level 9 by default
(`ZSTD_LEVEL`), level 3 with `--fast-compression` (`ZSTD_FAST_LEVEL`), and with
`--best-compression` xz preset 6 (`XZ_PRESET`) for native files, after the host target's BCJ
filter (the x86 filter on x86-64, none on aarch64: `xz_filter` in
[`targets.py`](../src/fsbpyr/targets.py); Python's `lzma` has no ARM64 filter). Large content is
stored in pieces that decode in parallel: 4 MiB for xz (`FSB_XZ_PIECE`), 8 MiB for zstd
(`FSB_ZSTD_PIECE`, both in [`launcher/format.h`](../src/fsbpyr/launcher/format.h)). An xz -6
encoder holds about 55 MiB, so a build compressing on 8 threads needs about 0.5 GiB more.

| App | Default (zstd 9) | `--fast-compression` | `--best-compression` | `--no-source` |
|---|---|---|---|---|
| `hello` | 27.9 MB | — | 27.9 MB | 27.9 MB (208 bytes less) |
| `heavy` (15 distributions) | 77.3 MB | 82.3 MB (+6.5%) | 69.1 MB (−10.6%) | 73.9 MB (−4.3%) |
| `torch` | 234.9 MB | 256.0 MB (+9.0%) | 215.4 MB (−8.3%) | — |

`hello` has no native files, so `--best-compression` changes nothing; `--no-source` leaves out its
two modules' sources (the standard library keeps its own).

`heavy` with zstd 9 and with xz in pieces. First runs start with an empty cache, so they decode
and write every native the run imports (11 runs each); "one core" runs the whole process on CPU 2
(`taskset -c 2`), so the pieces decode one after the other. Later runs only check the cache (31,
interleaved):

| Run | First, zstd 9 | First, xz | First, zstd 9, one core | First, xz, one core | Later, zstd 9 | Later, xz |
|---|---|---|---|---|---|---|
| `heavy np` | 82 ms | 179 ms | 105 ms | 257 ms | 62 ms | 63 ms |
| `heavy onnx` | 175 ms | 277 ms | 193 ms | 497 ms | 146 ms | 139 ms |
| `heavy crypto` | 49 ms | 77 ms | 66 ms | 129 ms | 40 ms | 42 ms |

The method matters for size, build and first run; later runs are the same. Leaving out the
sources does not slow a start: nothing reads them on the normal path. In 51 runs of each pair,
interleaved and in both orders, `hello` took 8.02–8.05 ms against 7.72–7.73 ms with
`--no-source`, `heavy json` 32.1–36.0 against 31.5–33.0 ms and `heavy model` 78.2–78.6 against
77.7–78.1 ms.

## Where the bytes go

`fsbpyr inspect` (and `inspect --json`, under `sizes`) splits the stored size of an executable
into the parts of its layout, from the reader's records alone (`measure` in
[`sizes.py`](../src/fsbpyr/sizes.py)); the rows add up to the file's size less the 64-byte
trailer. Blocks of small blobs mix owners, so a block's stored size is split by its members' raw
sizes; a blob shared by several owners counts for the first in path order. "Standard library and
boot" is every blob of a module or file that belongs to no distribution. Bytes, built by 0.30.0
(fsbpyr is the release asset itself):

| Part | `hello` | `heavy` | fsbpyr |
|---|---:|---:|---:|
| stub | 21,002,921 | 21,002,921 | 21,002,921 |
| zero padding to the 64 KiB boundary | 34,135 | 34,135 | 34,135 |
| payload header | 208 | 208 | 208 |
| index and string tables | 102,164 | 395,108 | 126,748 |
| blob alignment padding (16 bytes) | 1,733 | 5,233 | 2,212 |
| standard library and boot modules | 6,759,382 | 6,737,183 | 6,778,668 |
| distributions: code | 404 | 7,280,431 | 1,078,012 |
| distributions: sources | 64 | 3,294,390 | 401,063 |
| distributions: resources | 145 | 1,122,560 | 116,165 |
| distributions: natives | 0 | 37,394,048 | 0 |
| **file less trailer** | **27,901,156** | **77,266,217** | **29,540,132** |

The floor of every app is the stub plus the standard library, 27.8 MB. `hello` adds about 100 KB
of tables and a few hundred bytes of its own. In `heavy`, onnxruntime and numpy hold 31 of the
37 MB of natives. The stub records the runtime id it was linked as; the payload starts at a
64 KiB boundary (`PAYLOAD_ALIGN` in [`payload/format.py`](../src/fsbpyr/payload/format.py)).

## What executables share

[docs/executable-parts.md](../docs/executable-parts.md)'s method, repeated with 0.30.0 for `hello`
and `mp` (pure-Python fixtures, 26.61 MiB each) and `pandas-csv` (pandas and numpy, 62.11 MiB),
each built with `fsbpyr build --python 3.14` and read with fsbpyr's payload reader
([`payload/reader.py`](../src/fsbpyr/payload/reader.py)): every module's code and source and every
file compared across the apps by name and by the SHA-256 of its content, stored sizes attributed
per blob (a block's to its members in proportion to their size), and every stored region (a zstd
frame, a block, a piece) compared by the SHA-256 of its stored bytes. The table is on
[Dev-Executable-Format](Dev-Executable-Format.md#what-executables-share).

- **A minimal app is almost all runtime.** 26.51 MiB of `hello`'s 26.61 MiB (99.6%) is the same in
  every app built for this Python with this fsbpyr: the stub (20.03 MiB, the same bytes in all
  three), the zero padding to the payload's 64 KiB boundary (33.3 KiB) and 6.45 MiB of standard
  library and boot code. Every app carries the whole standard library (551 modules, with their
  sources), not only what it imports; `hello`'s own part is about 100 KiB, mostly its tables.
- **The same content is not always the same bytes.** Modules and files under 64 KiB are packed
  into blocks of 64 KiB (code) and 256 KiB (the rest) in the order of their paths, so a
  third-party module stored next to a standard-library one shares its block. `hello` and `mp`
  store 96.7% of their blob data in byte-identical blocks and frames; the three apps together
  80.4% of `hello`'s (5.18 of 6.45 MiB), since numpy's and pandas' modules fall into blocks of
  the standard library's.
- **What is each app's own:** the header's identity fields (the app name; the entry point, which
  for an app of several commands is the table of their names and targets; the build information;
  the native cache generation, which depends on the files an app extracts), the tables (99.8 KiB
  for `hello`, 551.6 KiB for `pandas-csv`), the app's own package, its third-party code, data and
  natives, and the trailer's digest.

An app built with `--glibc 2.28` carries the EL8 stub instead (21,004,065 bytes and 32,991 of
padding for 3.14 on x86-64); a release fsbpyr pairs it with its own standard library and boot
code (`paired` in [`runtime/stubs.py`](../src/fsbpyr/runtime/stubs.py)), so those parts are the
same in both, and the stub, its padding and the build information's runtime fields differ.

## A large app: PyTorch

The corpus `torch` app built three ways, and its venv. First runs start with an empty cache (5
runs each); later runs are 21 interleaved rounds. "torch import" is the `torch` line of CPython's
import-time table, cumulative (`FSBPYR_IMPORTTIME=1`; `-X importtime` for the venv).

| | venv | zstd 3 | zstd 9 | xz -6 in pieces |
|---|---|---|---|---|
| Size | — | 256.0 MB | 234.9 MB | 215.4 MB |
| Build (uv staging included) | — | 7.3 s | 8.4 s | 17.1 s |
| First run | — | 2.02 s | 2.02 s | 2.37 s |
| First run: torch import | — | 690 ms | 696 ms | 1,024 ms |
| Later runs | 1.72 s | 1.80 s | 1.81 s | 1.81 s |
| Later runs: torch import | 408 ms | 483 ms | 479 ms | 482 ms |
| Integrity check | — | 13.4 ms | 12.7 ms | 11.6 ms |
| Peak RSS, later / first run | 373 MB | 410 / 607 MB | 408 / 589 MB | 409 / 584 MB |
| Disk (executable + cache) | 918 MB (venv 810 + Python 108) | 968 MB | 947 MB | 927 MB |

A first run writes a 712 MB cache: torch's 12,151 files, the small ones on up to 8 threads, and
libtorch_cpu.so decoded in pieces on up to 8. Later runs cost the venv's time plus about 80 ms:
the integrity check of the 235 MB executable and the check of the cached files torch imports,
about 0.1 ms per MB. Extracted files are not synced to disk (`restore` in `launcher/cache.c`): one
a crash left incomplete fails its digest and is written again.

## Builds

The second of two builds, warm caches (`/usr/bin/time`: wall time, CPU, and the peak RSS of the
largest process):

| App | Wall | CPU | Peak RSS |
|---|---|---|---|
| `hello` | 0.56 s | 0.77 s | 142 MiB |
| `heavy` | 1.96 s | 7.08 s | 324 MiB |
| `heavy`, `--best-compression` | 4.21 s | 25.22 s | 530 MiB |
| `pandas-csv` | 2.43 s | 11.91 s | 271 MiB |
| `nlp` | 3.79 s | 17.07 s | 346 MiB |
| `torch`, `--fast-compression` | 7.34 s | 24.09 s | 340 MiB |
| `torch`, default | 8.41 s | 32.95 s | 417 MiB |
| `torch`, `--best-compression` | 17.13 s | 101.94 s | 639 MiB |

The 1 GB memory rule (D38 in [IMPLEMENTATION_PLAN.md](../docs/IMPLEMENTATION_PLAN.md)) counts
all of a build's processes: their proportional set size (PSS, shared pages divided among the
processes that map them), summed from `/proc/PID/smaps_rollup` every 50 ms, peaked at 347 MiB for
`heavy`, 429 MiB for `nlp`, 708 MiB for `torch` and 728 MiB for `torch --best-compression`, with
up to 9 processes (the build and 8 compile workers; their RSS summed, which counts shared pages
once per process, 1,314 and 1,337 MiB for `torch`). The workers are bounded: 4 jobs each and
64 MiB of sources in flight (`_AHEAD` and `_AHEAD_BYTES` in
[`compiler.py`](../src/fsbpyr/compiler.py)).

What keeps a build's bytes the same on one core or eight: `compile()`'s constant sharing depends
on what was interned before, so a worker's code could differ from the process's by one reference;
the canonical bytecode merges equal immutable values
([`bytecode.py`](../src/fsbpyr/bytecode.py)). The classifier's own work and the closure search
stay on one thread (the GIL).

## Very large files

[`tests/e2e/test_large.py`](../tests/e2e/test_large.py)'s app, with its own build script: one
sparse file of zeros, built compressed (zstd) and raw, run (the integrity check, a stream through
it, an extraction), and `inspect --verify` of the raw one; each process's peak RSS (`VmHWM`). Wall
times, one run each, of release 0.30.0 and of 0.31.0, measured on its code before the release
(e1ebe49, which differs from v0.31.0 only in the version number). One sparse file of 5 GiB is 5.4
GB, 40 MB on disk, raw, and 28.1 MB compressed; 1 TiB is 1.1 TB, 40 MB on disk, and 69.8 MB:

| | 0.30.0 | 0.31.0 |
|---|---|---|
| 5 GiB compressed: build / run | 1.1 / 2.4 s | 0.3 / 1.9 s |
| 5 GiB raw: build / run / `inspect --verify` | 0.4 / 3.6 / 1.5 s | 0.4 / 1.8 / 1.2 s |
| 1 TiB compressed: build / run | 149.5 / 486.6 s | 4.8 / 394.2 s |
| 1 TiB raw: build / run / `inspect --verify` | 14.7 / 991.1 / 324.3 s | 14.3 / 347.5 / 216.2 s |
| 1 TiB raw: the launcher's check at start | 68.8 s | 0.4 s |
| Peak RSS, builds / runs | 106–435 / 118–158 MiB | 106–180 / 119–158 MiB |

The runs had a task limit; the compressed ones also `MemoryMax=8G`, the raw ones of 0.31.0
`MemoryMax=48G`. Under 8G the 0.30.0 raw run took 1,151 s and its `inspect` 336 s, since the
page cache counts against the limit.

Compressed, the file is zstd pieces of 8 MiB, compressed and decoded on up to 8 threads; a piece
inside a hole is not compressed: the zeros of its size are compressed once per build and every
such piece takes those bytes, the ones compressing it gives (D15; the 1 TiB executables are the
same bytes). A raw build finds holes on the calling thread and reads only data on the threads
(`is_hole` and `file_digest` in `payload/format.py`); the executable and the extracted file stay
sparse. A raw window is read on up to 8 threads, a leaf each (`raw_window` in
`launcher/payload.c`), every leaf is written by the thread that hashes it (`write_leaf` in
`launcher/cache.c`), a leaf inside a hole is not read (`fsb_in_hole`: reading a hole fills
page-cache pages with zeros), and a leaf of zeros takes the digest of zeros unhashed and is not
written. 0.30.0 read each 64 MiB window with one `pread`, read and hashed the holes, and wrote
on one thread. On 64 GiB, 19 of the raw run's 27 s are the test app's own `bytes.count`, on one
thread; extraction runs at 24.6 GiB/s stored raw (0.30.0: 2.2) and 18.7 GiB/s compressed (6.8).
`inspect --verify` takes the integrity digest and the plain SHA-256 from one read
(`executable_digests` in `payload/format.py`), other threads reading and hashing the leaves;
0.30.0 read the file twice. The SHA-256 is one thread's and sets the time: 216 s for 1.1 TB.

## Building a runtime

`fsbpyr runtime build` links the launcher with python-build-standalone's static CPython under full
LTO with clang and lld 23 (`COMPILE_FLAGS` and `LINK_FLAGS` in
[`runtime/stub.py`](../src/fsbpyr/runtime/stub.py)) and compiles the standard library. From
v0.30.0's tree, with the archive and the sysroot already in the cache: 41.3 s, on one core
(41.9 s of CPU), 2.23 GiB peak RSS, over the 1 GB rule, in this one command (app builds reuse
the runtime and never link); `--glibc 2.28`, against the Rocky Linux 8.10 sysroot, 41.9 s and the
same peak. Both gave the release's runtime ids. lld's `--lto-partitions=8` fails: CPython's static
`compress` (`_bz2module.c`) and `lzma_free` (`_lzmamodule.c`) clash with zlib's and liblzma's once
split. `build_stub` fails a link whose frames go over 8192 bytes (`-Wframe-larger-than=8192`). A
release fsbpyr builds an app for glibc 2.28 without linking: it takes the stub its companion
release publishes.

## Known effects

- **OpenBLAS on a hybrid CPU.** numpy's OpenBLAS starts one busy-waiting thread per core at
  import. `heavy np` (31 runs, two rounds) took 63–67 ms and 0.8–0.9 s of CPU on all 24 cores,
  53 ms and 0.26 s on the 8 performance cores (`taskset -c 0-7`), and 64–65 ms and 0.11 s with
  `OPENBLAS_NUM_THREADS=1`. While those threads occupy every core, a thread that sleeps and is
  woken may land on an efficiency core and stay there, so the launcher's hashing threads are
  joined by spinning for at most 2 ms before a normal join (`SPIN_NS` in `launcher/parallel.c`).
- **All 24 cores can be slower than the 8 performance cores**, as above: work spread over all
  of them lands partly on efficiency cores.
- **Whatever the run loads is hashed.** The cache check grows with the natives a run imports,
  about 0.1 ms per MB on this machine; the integrity check with the executable's size.
- **The builder's CPython compiles the app's modules.** Builds by two CPython patch releases can
  differ in a few modules, since CPython's compiler changes between them; each is reproducible,
  and both run on either (one bytecode magic per minor version). The standard library is compiled
  by the runtime's own interpreter.

## Designs measured and replaced

These were measured to choose a design and are not repeated: the launcher once mapped the file for
its check (replaced by reads, to keep the file out of resident memory); the cache check moved
from Python into the launcher (`_fsbpyr.sync`, without the GIL); extraction writes on up to 8
threads into unnamed files (`O_TMPFILE`), linked into place once their digest is known, since
temporary names in one directory serialized the threads; and a fast exit (`os._exit` after the
`atexit` handlers) was not adopted, since what it saves is the libraries' own teardown, as much in
a venv, at the price of buffered data and non-daemon threads.

## Other tools

`hello`, `heavy` and `nlp` built four ways and run next to their venv, every tool on CPython
3.14.10 (python-build-standalone 20261026): fsbpyr 0.30.0; PyInstaller 6.22.3 (hooks-contrib
2026.9) in onefile mode, from a venv of the app's lock; pex 2.107.0 with `--scie eager --venv`;
PyApp 0.31.0 with an embedded Python that has the app and its locked dependencies installed
(`PYAPP_DISTRIBUTION_PATH`, zstd -9, `PYAPP_FULL_ISOLATION`, `PYAPP_SKIP_INSTALL`), so that like
the others it needs no network at run time; uv 0.13.6, cargo 1.95.0. Each ran with a `HOME` and
`TMPDIR` of its own and `PATH=/usr/bin:/bin`, in the app's project directory. Later runs are
medians of 31 interleaved rounds after 3 warm-ups; first runs of 11 rounds, the tool's `HOME`
emptied before each; peak RSS is the median of 5 runs under `/usr/bin/time -f %M` (the largest
process). Baselines: bare CPython 5.5 ms, `rg --version` (ripgrep 14.1.1) 0.9 ms, `gh --version`
(gh 2.102.0) 35.6 ms.

fsbpyr, pex and PyApp printed what the venv printed, exit status and output, for all seven
commands with nothing beyond the build. PyInstaller needed `--collect-data heavy` (`heavy onnx`
reads `relu_matmul.onnx` through `importlib.resources`) and, for `nlp`, `--collect-data
sentencepiece` (its normalization data) and `--collect-submodules tiktoken_ext` (tiktoken finds
its encodings as plugins of that namespace).

Size and build, the second of two builds with warm caches; PyInstaller's in a new work directory,
PyApp's including the preparation of its embedded Python and a `cargo build`:

| App | | fsbpyr | PyInstaller onefile | pex `--scie eager` | PyApp |
|---|---|---|---|---|---|
| `hello` | size | 27.9 MB | 13.3 MB | 79.8 MB | 36.5 MB |
| | build | 0.56 s | 5.60 s | 2.19 s | 7.80 s |
| `heavy` | size | 77.3 MB | 51.2 MB | 128.6 MB | 77.2 MB |
| | build | 1.96 s | 15.33 s | 7.69 s | 8.59 s |
| `nlp` | size | 117.1 MB | 103.3 MB | 162.6 MB | 104.0 MB |
| | build | 3.79 s | 43.32 s | 12.20 s | 9.18 s |

Later runs, caches filled:

| Run | venv | fsbpyr | PyInstaller onefile | pex `--scie eager` | PyApp |
|---|---|---|---|---|---|
| `hello` | 7.7 ms | 8.0 ms | 136.3 ms | 10.2 ms | 8.0 ms |
| `heavy json` | 25.7 ms | 35.5 ms | 469.8 ms | 33.3 ms | 26.1 ms |
| `heavy crypto` | 36.8 ms | 42.9 ms | 471.7 ms | 41.8 ms | 36.1 ms |
| `heavy model` | 68.1 ms | 75.8 ms | 505.9 ms | 75.2 ms | 69.0 ms |
| `heavy np` | 56.1 ms | 63.4 ms | 495.4 ms | 58.1 ms | 56.0 ms |
| `heavy onnx` | 114.5 ms | 135.0 ms | 562.6 ms | 118.9 ms | 112.1 ms |
| `nlp` | 457.2 ms | 518.8 ms | 1,302.7 ms | 458.2 ms | 458.8 ms |

Peak RSS of the same runs:

| Run | venv | fsbpyr | PyInstaller onefile | pex `--scie eager` | PyApp |
|---|---|---|---|---|---|
| `hello` | 11.0 MB | 14.9 MB | 20.3 MB | 11.5 MB | 10.8 MB |
| `heavy json` | 16.1 MB | 24.4 MB | 35.7 MB | 16.7 MB | 16.1 MB |
| `heavy crypto` | 25.5 MB | 33.3 MB | 43.4 MB | 26.2 MB | 25.7 MB |
| `heavy model` | 28.1 MB | 38.0 MB | 43.7 MB | 28.8 MB | 28.4 MB |
| `heavy np` | 31.3 MB | 40.4 MB | 47.5 MB | 32.2 MB | 31.8 MB |
| `heavy onnx` | 61.3 MB | 69.9 MB | 76.1 MB | 62.5 MB | 62.4 MB |
| `nlp` | 128.2 MB | 149.9 MB | 135.4 MB | 129.8 MB | 129.2 MB |

First runs, with what the run left on disk:

| Run | fsbpyr | PyInstaller onefile | pex `--scie eager` | PyApp |
|---|---|---|---|---|
| `hello` | 8.2 ms (0) | 136.6 ms (0) | 1,455.2 ms (263.7 MB) | 186.7 ms (144.1 MB) |
| `heavy np` | 80.9 ms (38.3 MB) | 495.2 ms (0) | 2,020.2 ms (460.7 MB) | 445.3 ms (332.5 MB) |
| `heavy onnx` | 188.7 ms (69.0 MB) | 573.1 ms (0) | 2,100.0 ms (461.0 MB) | 528.8 ms (332.7 MB) |
| `nlp` | 585.6 ms (232.2 MB) | 1,293.8 ms (0) | 3,799.3 ms (643.0 MB) | 1,780.0 ms (505.1 MB) |

After all later runs of the three apps the caches held 321.7 MB (fsbpyr), 844.1 MB (pex) and
1,967.6 MB (PyApp: each app's unpacked Python and the cached archives). PyInstaller keeps nothing:
every start unpacks its archive into a new `_MEI` directory under `TMPDIR` and removes it at exit.

- **Later runs:** pex and PyApp run from the environments their first run unpacked, without
  checks: PyApp within 2.4 ms of the venv, pex 1 to 8 ms above it (its bootstrap). fsbpyr is
  1.04 to 1.38 times the venv ([Whole runs](#whole-runs-and-the-cache-check)). PyInstaller onefile
  is 2.8 to 18 times the venv, since each start unpacks.
- **First runs:** fsbpyr's are 0.1 ms (`hello`) to 67 ms (`nlp`) above its later runs: it extracts
  only the natives a run imports and the `fs-packages`. PyApp unpacks the whole Python and every
  dependency first (0.19 to 1.78 s), pex unpacks the interpreter and builds a venv (1.5 to 3.8 s).
- **Disk:** fsbpyr keeps what runs extracted (0 to 232 MB per app here), pex and PyApp a whole
  environment per app (144 to 643 MB).
- **Memory:** fsbpyr's later runs take 4 to 22 MB more than the venv, PyInstaller's 7 to 20 MB;
  pex and PyApp are within 2 MB of it.
- **Size and build:** PyInstaller's files are the smallest (what its import analysis finds, and
  what its options add) and its builds of `heavy` and `nlp` the slowest; fsbpyr builds fastest;
  pex's files are the largest.
- **Next to compiled tools:** fsbpyr's `hello` (8.0 ms) is slower than ripgrep's `--version`
  (0.9 ms) and faster than gh's (35.6 ms).

## Where to look when a start gets slower

1. `FSBPYR_TRACE=1 app ...` prints one `fsbpyr-trace` JSON line on stderr at exit
   ([`_fsbpyr_rt/trace.py`](../src/fsbpyr/boot/_fsbpyr_rt/trace.py), counters from
   `_fsbpyr.stats` in `launcher/module.c`): `integrity_ns` (the whole-file check), `reads`,
   `decompressed_bytes`, `stored_bytes` and `hole_bytes` (payload reads), `extracted` (files written to the
   cache) and `modules` (the modules loaded per distribution). Under cProfile, `_fsbpyr.sync` is
   the cache check, and the extraction on a first run.
2. `FSBPYR_IMPORTTIME=1 app ...`: CPython's import-time table, per module (the launcher sets
   `config.import_time`).
3. Compare with the tables above: the no-op app isolates the launcher and the integrity check,
   `heavy json` the payload imports, `heavy np` and `heavy onnx` the cache check and native
   loading, `nlp` a large import tree.
4. Compare the app's start with `python3 -I -S -c pass`, interleaved, as under
   [How the numbers were measured](#how-the-numbers-were-measured).

## See also

- [Dev-Launcher](Dev-Launcher.md): the start sequence these numbers time.
- [Dev-Executable-Format](Dev-Executable-Format.md): blocks, pieces and the digest layout.
- [Dev-Security](Dev-Security.md): why the checks run at every start.
- [User-Running](User-Running.md): the environment variables named here.
