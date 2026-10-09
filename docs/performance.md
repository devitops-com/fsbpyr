# Startup timings

Where the time of a start goes, release by release, so that a regression can be traced to
the part that grew. Add a row whenever a release changes the launcher, the payload format or
the boot code, and keep the old rows.

## How these were measured

- Machine: Intel Core Ultra 9 285 (8 performance cores, CPUs 0–7; 16 efficiency cores,
  CPUs 8–23; SHA-NI and AVX2), Linux 6.12 (Debian 13).
- Warm: the executable is in the page cache and its natives are already extracted. A first
  start after a reboot also reads the whole file from disk; that is not measured here.
- Each figure is the median (p50) of 31 to 200 runs started with `subprocess` from Python.
  Variants that are compared are run interleaved, so that a drift of the CPU clock hits both.
  Runs of 100 ms and more vary by about ±1.5 ms between rounds; repeat before trusting a
  difference of a few milliseconds.
- "Bare CPython" is `python3.14 -I -S -c pass` (uv-managed CPython 3.14).
- "Cache check" is the difference to a build of the same app whose cache check compares sizes
  only (a temporary measurement build, never released).

## Starting a no-op app

The app's `main()` returns at once; the executable is 27.6 MB, about 21 MB of it launcher and
static CPython.

| Release | Bare CPython | App | App without the integrity check | Integrity check |
|---|---|---|---|---|
| Phase 2 (D17, PIE stub) | 4.84 ms | 4.95 ms (1.02×) | — | — |
| Phase 6 (`hello`, which prints a line) | — | 1.11× | — | — |
| 0.6.0, check through OpenSSL's `SHA256()` (not released) | 5.2–5.3 ms | 9.5 ms | 6.0–6.2 ms (1.16×) | 3.4 ms |
| 0.6.0 | 5.1–5.4 ms | 8.1 ms | 6.0 ms (1.11–1.17×) | 2.0 ms |
| 0.7.0 | 5.2 ms | 7.9 ms | 6.15 ms (1.18×; app minus `integrity_ns`) | 1.7–1.8 ms |
| 0.9.0 (two rounds of 201) | 5.4–5.5 ms | 7.9–8.1 ms | 6.0–6.2 ms (1.11–1.12×) | 1.9–2.0 ms |

Budget (`test_startup_is_within_budget_of_bare_cpython`): everything but the integrity check
within 1.2× bare CPython (1.15× until 0.7.0; the owner raised it on 2026-10-09 because the
launcher sat at the 1.15× edge). The check is budgeted apart: it costs what hashing the file
costs.

## The integrity check

Hashing the whole executable before anything else (D31), as `FSBPYR_TRACE` reports it
(`integrity_ns`), launcher of 0.6.0/0.7.0:

| Executable | Up to 8 threads | One core | Up to 8 threads, no SHA-NI | One core, no SHA-NI |
|---|---|---|---|---|
| no-op app, 28 MB | 2.0 ms | 6.9 ms | 7.1 ms | 34 ms |
| no-op app plus 120 MB of data, 148 MB | 8.9 ms | 38 ms | 30 ms | 184 ms |
| fsbpyr itself, 28 MB (0.6.0) | 1.6 ms | — | — | — |
| `heavy` test app, 76.5 MB | 4.2–4.9 ms (0.6.0), 5.0–5.8 ms (0.7.0) | — | — | — |
| aiagent, 149.7 MB | 7.0 ms (0.6.0), 8.4 ms (0.7.0) | — | — | — |

"No SHA-NI" was forced with `OPENSSL_ia32cap=:~0x20000000`, "one core" with `taskset -c 2`.
The 0.6.0 to 0.7.0 differences of about 1 ms come from separate sessions and were not
measured side by side.

Thread count: one thread per 4 pieces of 1 MiB, at most 8 and never more than the usable
cores. On this machine 5–6 threads beat 8 for files under about 40 MB (21 MB: 2.1 against
2.4 ms); `mmap` beat `pread` at every size (21 MB: 2.2 against 3.1 ms).

## The cache check

Each process hashes the extracted files it uses once, before it first uses them (D31; since
0.7.0 with the launcher's threaded routine, D32).

| Run (natives hashed) | 0.6.0, one thread | 0.7.0, performance cores only | 0.7.0, all 24 cores |
|---|---|---|---|
| `heavy json` (0.2 MB) | ≈0 | — | ≈0 |
| `heavy model`, pydantic (4.7 MB) | +1.6 ms | — | +1.6 ms |
| `heavy crypto` (14.8 MB) | +5.1 ms | — | +1.3 ms |
| `heavy np`, numpy (38 MB) | +9.7 ms | +4.8–4.9 ms | +4.2–4.8 ms |
| `heavy onnx` (69 MB, numpy's included) | +21.6 ms | +8.2–9.4 ms | +14–18 ms |
| aiagent commands (7.4 MB) | +0.9–3.3 ms | — | +0.5–1.5 ms |

The routine alone, timed in a running app on the cached files, takes 5.1 ms for onnx's 69 MB
(16–20 GB/s on files of 24–31 MB); one thread manages about 3.5 GB/s. The rest of the run's
cost is fresh-process work around it: each library is mapped once to be hashed and once
more to be loaded.

## Whole runs

| Run | 0.7.0 |
|---|---|
| `heavy json` | 34.5 ms |
| `heavy crypto` | 43.2 ms |
| `heavy model` | 79.2 ms |
| `heavy np` | 120.1–120.6 ms (performance cores only: 70 ms) |
| `heavy onnx` | 195.4–195.8 ms (performance cores only: 131 ms) |
| `aiagent version` | 142.8 ms |
| `aiagent skills list` | 146.2 ms |
| `aiagent --help` | 182.6 ms |

Blocks (D30, 0.5.0) cost aiagent's `version`, `--help` and `skills list` 4.7, 5.3 and 5.6 ms
against 0.4.0; `hello` did not change.

## `--best-compression` (0.8.0)

Native libraries as xz -6 with the x86 filter instead of zstd 9 (D33). Libraries over 4 MiB are
stored in pieces of 4 MiB, each its own xz stream, so the launcher decodes one library on up to
8 threads; the pieces of all libraries are compressed on min(8, cores) threads, largest first.
Each xz -6 encoder holds about 55 MiB, so a build with 8 of them needs about 0.5 GiB more.

| App | Size, zstd 9 | Size, xz -6 (one stream) | Size, xz -6 (4 MiB pieces) | Build, zstd 9 | Build, xz (one stream) | Build, xz (pieces) |
|---|---|---|---|---|---|---|
| aiagent | 149.7 MB | 139.0 MB | 140.0 MB | 18.5 s | 24.4 s | 21.1 s |
| `heavy` | 76.5 MB | 67.9 MB | 68.8 MB | 5.2 s | 11.7 s | 7.4 s |

| Run | First run, zstd | First run, xz in pieces | First run, xz, one core | Later runs, zstd | Later runs, xz |
|---|---|---|---|---|---|
| `heavy np` | 152.6 ms | 223.4 ms | 282.7 ms | 120.5 ms | 120.9 ms |
| `heavy onnx` | 278.9 ms | 354.4 ms | 544.7 ms | 193.6 ms | 191.3 ms |
| `heavy crypto` | 58.6 ms | 83.4 ms | 140.9 ms | 42.6 ms | 42.7 ms |
| `aiagent version` | 145.4 ms | 190.3 ms | 221.1 ms | 142.3 ms | 141.7 ms |

"First run" starts with an empty cache, so it decodes and writes every native the run imports;
medians of 11 runs; "one core" runs the whole process on one CPU (`taskset -c 2`), so the
pieces decode one after the other. Later runs (31) only check the cache. With one xz stream per library the
first runs took 261.6 ms (`np`), 554.6 ms (`onnx`), 125.1 ms (`crypto`) and 195.7 ms (`aiagent
version`), against 132.2, 241.8, 59.4 and 146.7 ms with zstd in that session: one library
dominated each closure (numpy: OpenBLAS, 24 MB; onnx: its 31 MB extension) and one stream
decodes on one thread. On `heavy`'s four largest libraries, pieces of 8, 4, 2 and 1 MiB cost
+2.2%, +3.6%, +6.4% and +9.9% of xz size and decoded in 220, 125, 87 and 75 ms against 408 ms
for one stream each (8 threads).

## A PyTorch app (0.8.0, D34)

`torchopt`: CPU-only torch 2.14.1, a 100-step Adam loop; 233 MB with zstd 9; the cache on disk
(XFS). Times are torch's import as the app measures it.

| | First run | Later runs | Cache |
|---|---|---|---|
| Before D34: the whole package extracted twice, each file synced | 42.5 s | 895 ms | 1.2 GB |
| Without fsync only | 2.3 s | about 920 ms | 1.2 GB |
| torch a built-in fs-package, files checked one at a time | 1.43 s | 683 ms | 709 MB |
| D34: torch a built-in fs-package, checks in batches | 1.34 s | 620 ms | 709 MB |
| The same script in its venv | 1.28 s (cold) | 465–500 ms | — |

Of the later runs' 620 ms, about 60 ms check 12,151 cached files (about 120 ms one at a time;
with size checks only, 560 ms); about 95 ms more go to walking their records in Python. Since
D35 the launcher does both: see below.

### The three compression methods (0.8.0)

The same app built with `--fast-compression`, by default and with `--best-compression`; first
runs (3) start with an empty cache, later runs (11) with a full one; medians. "Anonymous" is
the peak of the process's private memory (sampled every 2 ms); "RSS" is the kernel's peak
resident size, which also counts the executable's pages while the integrity check maps them.

| | venv | zstd 3 | zstd 9 | xz -6 in pieces |
|---|---|---|---|---|
| Size | — | 254.8 MB | 233.0 MB | 214.7 MB |
| Build (uv staging included) | — | 17.1 s | 24.8 s | 32.3 s |
| First run | — | 2.11 s | 2.09 s | 2.19 s |
| First run: torch import | 1.28 s (cold) | 1.35 s | 1.34 s | 1.44 s |
| Later runs | 1.16 s | 1.37 s | 1.37 s | 1.36 s |
| Later runs: torch import | 447 ms | 612 ms | 613 ms | 610 ms |
| Later runs: training loop | 380 ms | 415 ms | 415 ms | 415 ms |
| Integrity check | — | 16.7 ms | 15.7 ms | 12.5 ms |
| Anonymous memory, later / first run | 227 MB | 233 / 447 MB | 233 / 447 MB | 233 / 489 MB |
| RSS, later / first run | 314 MB | 478 / 625 MB | 477 / 607 MB | 477 / 630 MB |
| Disk (executable + cache) | 925 MB (venv 803 + Python 122) | 967 MB | 945 MB | 927 MB |

The first run decodes 713 MB more than a later one and writes a 712 MB cache: about 0.74 s
with zstd and 0.84 s with xz. The method matters only for size, build and first run; later
runs are the same. A first run holds the largest library it extracts in memory
(libtorch_cpu.so, 438 MB); xz adds about 42 MB of decoder state. The training loop runs about
9% slower than in the venv in every build; not explained yet.

## The cache sync in the launcher (D35)

`_fsbpyr.sync` walks, checks and restores cached files in C, without the GIL; Python only asks
for it. Measured on the torch app above (default build, later runs: medians of 31 runs of each
build, interleaved; first runs: 5 each, empty cache):

| | 0.8.0 | D35 | venv |
|---|---|---|---|
| Later runs | 1363 ms | 1279 ms | 1208 ms |
| Later runs: torch import | 615 ms | 538 ms | 470 ms |
| First run | 1967 ms | 1908 ms | — |
| First run: torch import | 1249 ms | 1199 ms | — |

Where the difference to the venv goes, later runs, medians of 21 runs of each, interleaved
(another session than the table above):

| | Executable | venv | Difference |
|---|---|---|---|
| Whole run | 1220 ms | 1158 ms | +62 ms |
| torch import | 509 ms | 447 ms | +62 ms, the cache check of torch's 12,151 files 53.5 ms of it |
| Training loop | 386 ms | 380 ms | +6 ms |
| The rest: start and exit | 325 ms | 331 ms | −6 ms |
| Start alone (`FSBPYR_SELFTEST=1`; `python -c "import torchopt.cli"`) | 20.6 ms | 10.5 ms | +10 ms; the executable's own check 14.0 ms of it |

The venv checks nothing: it opens only the files an import needs, and the dynamic loader maps a
library without reading it whole. The executable hashes torch's 712 MB of cached files and its
own 233 MB on every run. The import also decompresses its code (66 MB from 24 MB stored, 1,756
reads), where the venv reads `.pyc` files; how the two compare was not measured.

`_fsbpyr.sync` of all 12,151 files of torch's cache, timed inside the app (medians of 9; a
fresh record of checked files each time):

| Files | Size | Every file mapped, 256 per pass | Up to 64 KiB read, 1024 per pass |
|---|---|---|---|
| all 12,151 | 712 MB | 80.0 ms | 53.5 ms |
| the 12 native libraries | 476 MB | 16.1 ms | 16.2 ms |
| the 12,139 others (headers, sources, data) | 235 MB | 64.9 ms | 38.1 ms |

The libraries hash at about 30 GB/s; the small files cost their system calls, page faults and
helper threads. A standalone program checking the 12,139 files the same way, with variants
(medians of 9):

| Variant (an `fstatat` before each open unless noted) | Time |
|---|---|
| all mapped, 256 per pass (as 0.8.0 checked them) | 58 ms |
| all mapped, 1024 per pass | 55 ms |
| read up to 64 KiB, 256 per pass | 38 ms |
| read up to 64 KiB, 256 per pass, no `fstatat` | 36 ms |
| read up to 64 KiB, 1024 per pass, no `fstatat` (D35) | 34 ms |
| no `fstatat`, 1024 per pass, one helper per 4 MiB of data: read up to 16 KiB / 64 KiB / 1 MiB | 36 / 36 / 46 ms |
| one helper per 4 MiB of data instead of per 4 leaves: all mapped, 256 / 1024 per pass | 70 / 62 ms |

Mapping a large file still wins: its pages fault in on the hashing threads, in parallel.

## Known effects

- **OpenBLAS on a hybrid CPU.** numpy's OpenBLAS starts one busy-waiting thread per core at
  import: `heavy np` uses 1.4–1.9 s of CPU in a 120 ms run and takes 113 ms on all 24 cores,
  64 ms on the 8 performance cores, 59 ms with `OPENBLAS_NUM_THREADS=1`. While those threads
  occupy every core, a thread that sleeps and is woken may land on an efficiency core and stay
  there: joining the hashing threads cost `heavy np` +38–41 ms; spinning on non-blocking joins
  (for at most 2 ms, then a normal join) +4–5 ms (D32).
- **Whatever the run loads is hashed.** The cache check grows with the natives a run imports,
  about 0.1–0.3 ms per MB on this machine; the integrity check with the executable's size.

## Where to look when a start gets slower

1. `FSBPYR_TRACE=1 app ...`: `integrity_ns` (the whole-file check), `decompressed_bytes` and
   `stored_bytes` (payload reads), `extracted` (files written to the cache) and the modules
   loaded per distribution. Under cProfile, `_fsbpyr.sync` is the cache check, and the extraction
   on a first run.
2. `FSBPYR_IMPORTTIME=1 app ...`: CPython's import-time table, per module.
3. Compare with the tables above: the no-op app isolates the launcher and the integrity check,
   `heavy json` the payload imports, `heavy np` and `heavy onnx` the cache check and native
   loading, aiagent a large pure-Python import tree.
4. `uv run pytest -q tests/e2e/test_runtime_app.py -k budget` checks the startup budget.
