# Startup timings

Where the time of a start goes, release by release, so that a regression can be traced to
the part that grew. Add a row whenever a release changes the launcher, the payload format or
the boot code, and keep the old rows.

## How these were measured

- Machine: Intel Core Ultra 9 285 (8 performance cores, CPUs 0–7; 16 efficiency cores,
  CPUs 8–23; SHA-NI and AVX2), Linux 6.12 (Debian 13).
- Warm: the executable is in the page cache and its natives are already extracted. A first
  start after a reboot also reads the whole file from disk; see "Cold start (0.20.0)".
- Each figure is the median (p50) of 31 to 200 runs started with `subprocess` from Python.
  Variants that are compared are run interleaved, so that a drift of the CPU clock hits both.
  Runs of 100 ms and more vary by about ±1.5 ms between rounds; repeat before trusting a
  difference of a few milliseconds.
- "Bare CPython" is `python3.14 -I -S -c pass` (uv-managed CPython 3.14; 3.14.9 in the rows of
  0.24.0 and later) or, in the Python 3.15 row, `python3.15 -I -S -c pass` (uv-managed 3.15.1).
- "Cache check" is the difference to a build of the same app whose cache check compares sizes
  only (a temporary measurement build, never released).

## Starting a no-op app

The app's `main()` returns at once; on Python 3.14 the executable is 27.8 MB (0.24.0 and 0.25.0:
27,786,048 and 27,786,288 bytes), 20.9 MB of it launcher and static CPython and 6.7 MB the
standard library; on 3.15 it is 29.7 MB (22.1 and 7.4 MB).

| Release | Bare CPython | App | App without the integrity check | Integrity check |
|---|---|---|---|---|
| Phase 2 (D17, PIE stub) | 4.84 ms | 4.95 ms (1.02×) | — | — |
| Phase 6 (`hello`, which prints a line) | — | 1.11× | — | — |
| 0.6.0, check through OpenSSL's `SHA256()` (not released) | 5.2–5.3 ms | 9.5 ms | 6.0–6.2 ms (1.16×) | 3.4 ms |
| 0.6.0 | 5.1–5.4 ms | 8.1 ms | 6.0 ms (1.11–1.17×) | 2.0 ms |
| 0.7.0 | 5.2 ms | 7.9 ms | 6.15 ms (1.18×; app minus `integrity_ns`) | 1.7–1.8 ms |
| 0.9.0 (two rounds of 201) | 5.4–5.5 ms | 7.9–8.1 ms | 6.0–6.2 ms (1.11–1.12×) | 1.9–2.0 ms |
| 0.14.0 (two rounds of 201) | 5.50–5.51 ms | 7.83–7.86 ms | 6.06–6.09 ms (1.10×) | 1.77 ms |
| 0.15.0 (two rounds of 201; bare: PBS CPython 3.14.8) | 5.53–5.54 ms | 7.82 ms | 6.06 ms (1.10×) | 1.76 ms |
| 0.24.0 (two rounds of 201; bare: uv CPython 3.14.9) | 5.43–5.46 ms | 7.61–7.66 ms | 5.97–6.02 ms (1.10×) | 1.64 ms |
| 0.25.0 (two rounds of 201; as 0.24.0) | 5.43–5.46 ms | 7.63–7.66 ms | 5.99–6.02 ms (1.10×) | 1.64 ms |
| 0.25.0 on Python 3.15 (two rounds of 201; bare: uv CPython 3.15.1) | 5.50–5.55 ms | 7.65–7.68 ms | 6.01–6.03 ms (1.09×) | 1.64–1.65 ms |

The rows from 0.24.0 on were measured with `subprocess.run` as in the budget test, 5 warm-up
runs and then 201 runs per variant per round, all variants of a round interleaved; the integrity
check is `integrity_ns` of 201 further runs with `FSBPYR_TRACE=1`, interleaved with them.
"App without the integrity check" is the app's p50 minus that p50, as in the 0.7.0 row (no
measurement build). The 0.24.0 app was built by the release's `fsbpyr-3.14-x86_64-linux-gnu`,
the 0.25.0 ones from the checkout; 0.24.0 (Debian 12 sysroot, glibc 2.34 baseline, LLVM 23) and
0.25.0 (`_dl_find_object` wrapper, Python 3.15) start 0.16 to 0.21 ms faster than 0.15.0 did
in its session (bare CPython 0.07 to 0.11 ms faster, the integrity check 0.12 ms); 0.24.0 and
0.25.0 do not differ on 3.14.

Budget (`test_startup_is_within_budget_of_bare_cpython`): everything but the integrity check
within 1.5× bare CPython (1.15× until 0.7.0, 1.2× until 0.10.0; the owner raised it twice on
2026-10-09 because the launcher sat at the edge, 1.20–1.21× in 0.9.1 and 0.10.0 alike, and failed
about one run in three). The check is budgeted apart: it costs what hashing the file costs.

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

## Integrity check (0.20.0)

The same check with the launcher since D36 (files read in pieces, not mapped), for apps of 28 MB
(the `hello` fixture), 244 MB and 1.07 GB (the same plus 206 and 996 MB of random data, which
does not compress), `integrity_ns` from `FSBPYR_TRACE`, median of 31 runs each, warm page
cache, fsbpyr 0.20.0-7064cb77:

| Executable | Up to 8 threads | One core | Up to 8 threads, no SHA-NI | One core, no SHA-NI |
|---|---|---|---|---|
| 27.7 MB | 1.72 ms | 7.8 ms | 7.3 ms | 45.7 ms |
| 244 MB | 11.4 ms | 83.5 ms | 58.3 ms | 411 ms |
| 1,072 MB | 47.0 ms | 311 ms | 235 ms | 1,564 ms |
| per GB (from 1,072 MB) | 44 ms | 290 ms | 219 ms | 1,459 ms |

"One core" is `taskset -c 2`, "no SHA-NI" `OPENSSL_ia32cap=:~0x20000000`. Compared with the
table above (mmap-based launcher of 0.6.0/0.7.0), one core takes 7.8 against 6.9 ms for the
28 MB app; the other cells were not measured for 0.6.0/0.7.0 at these sizes. The time grows
with the size, about linearly past 244 MB: about 44 ms per GB on this machine, 0.3 s per GB on a single core and 1.5 s
per GB on a single core without SHA instructions. The check reads the file from the page cache;
after a reboot the disk read comes on top (see "Cold start").

Reproduce: from a checkout, with `fsbpyr` installed:

```sh
D=~/fsbpyr-bench; mkdir -p $D; cp -r tests/fixtures/projects/hello $D/small
cp -r $D/small $D/big; head -c 996M /dev/urandom > $D/big/src/hello/blob.bin  # 206M: 244 MB
fsbpyr build $D/small -o $D/small.app; fsbpyr build --fast-compression $D/big -o $D/big.app
for i in $(seq 31); do FSBPYR_TRACE=1 $D/big.app 2>&1 >/dev/null |
  sed -n 's/.*"integrity_ns": \([0-9]*\).*/\1/p'; done | sort -n | sed -n 16p  # median, ns
```

For the other columns put `taskset -c 2`, `env OPENSSL_ia32cap=:~0x20000000` or both in front
of `$D/big.app`.

## Cold start (0.20.0)

Starts with the page cache empty of the executable and, separately, of the files extracted to
the cache. `spikes/bench.py --cold` skips the warm-up runs and, before each sample, writes the
executable out and drops its pages with `posix_fadvise(POSIX_FADV_DONTNEED)`, with `--cache DIR`
those of every file under DIR as well. That needs no root, but the files must be on a disk file
system (here XFS on an NVMe SSD, Samsung 9100 PRO): on tmpfs nothing is evicted. `fincore`
showed the 73.5 MB executable at 73.5M resident before and 0B after. Median of 51 runs, two
rounds each (the second in brackets), fsbpyr 0.20.0-7064cb77. The `hello` fixture stands in
for the no-op app; it also prints a line, so it runs a little above the no-op rows of
"Starting a no-op app".

| App | Warm | Executable evicted | Executable and cache evicted |
|---|---|---|---|
| `hello` (27.7 MB; extracts nothing) | 8.84 ms (9.08) | 15.18 ms (15.13) | same as the previous column |
| `heavy np` (77.1 MB; extracts 5 files, 37 MB) | 128.1 ms (130.6) | 139.9 ms (142.4) | 170.4 ms (163.3) |

Evicting the executable adds 6 ms for `hello` and 12 ms for `heavy np`, the time to
read the whole file from this SSD, which D31 does at every start; `heavy np` adds
another 21 to 30 ms for reading the 37 MB of natives it loads. On a slower disk it grows with
the size of the file, since the integrity check needs all of it before the app starts.

Reproduce: the `D` and the `hello` app of the previous section, and `heavy` built the same way:

```sh
cp -r tests/fixtures/projects/heavy $D/heavy; fsbpyr build $D/heavy -o $D/heavy.app
C=$D/cache; FSBPYR_CACHE_DIR=$C $D/heavy.app np  # fills the cache
python3 spikes/bench.py -n 51 --cache $C "warm=$D/heavy.app np"
FSBPYR_CACHE_DIR=$C python3 spikes/bench.py -n 51 --cold "exe=$D/heavy.app np"
python3 spikes/bench.py -n 51 --cold --cache $C "exe+cache=$D/heavy.app np"
python3 spikes/bench.py -n 51 --cold "hello=$D/small.app"
```

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

| Run | 0.7.0 | 0.14.0 | 0.15.0 |
|---|---|---|---|
| `heavy json` | 34.5 ms | 37.5 ms | 37.8 ms |
| `heavy crypto` | 43.2 ms | 42.5 ms | 42.4 ms |
| `heavy model` | 79.2 ms | 76.3 ms | 76.2 ms |
| `heavy np` | 120.1–120.6 ms (performance cores only: 70 ms) | 67.9 ms | 66.5 ms |
| `heavy onnx` | 195.4–195.8 ms (performance cores only: 131 ms) | 142.8 ms | 143.2 ms |
| `aiagent version` | 142.8 ms | 132.9 ms | 133.0 ms |
| `aiagent skills list` | 146.2 ms | 139.3 ms | 139.2 ms |
| `aiagent --help` | 182.6 ms | 169.2 ms | 170.7 ms |

0.14.0 and 0.15.0: medians of 31 runs each, interleaved, peak RSS 23 (`json`) to 68 MB (`onnx`),
the same in both; each release's own builds. Later runs did not change with 0.15.0: it changed
building, decoding and extraction, which they do not do.

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

## Build options (0.20.0)

The `hello` and `heavy` test apps built by the released 0.20.0 by default, with `--no-source`
and with `--best-compression`. `heavy` has 15 distributions; numpy, onnxruntime, cryptography,
cffi, protobuf, pydantic-core and orjson bring native files.

| App | Default (zstd 9) | `--no-source` | `--best-compression` |
|---|---|---|---|
| `hello` | 27.7 MB | 27.7 MB | 27.7 MB |
| `heavy` | 77.1 MB | 73.7 MB (−4.3%) | 68.9 MB (−10.6%) |

`--no-source` leaves out 3.3 MB of `heavy` and 208 bytes of `hello` (its two modules; the
standard library keeps its sources). `hello` has no native files, so `--best-compression` does
not change its size.

Later runs: 3 warm-ups, then 31 runs of each, interleaved; the medians of two such series (ten
for `heavy model`: the second run of each pair was often the slower, so four of the ten ran
`--no-source` first). Other builds and tests ran on the machine meanwhile, so compare the
columns, not other tables:

| Run | Default | `--no-source` |
|---|---|---|
| `hello` | 7.5–7.6 ms | 7.5–7.6 ms |
| `heavy json` | 30.4–30.8 ms | 30.2–30.7 ms |
| `heavy model` (pydantic) | 75.7–77.9 ms | 75.7–77.7 ms |

Leaving out the sources does not change a start: nothing reads them on the normal path.

## Where the bytes go (DX-5)

`fsbpyr inspect` (and `inspect --json`, under `sizes`) splits the stored size of an executable
into the parts of its layout, from the reader's records alone. The rows add up to the file's
size less the 64-byte trailer. Blocks of small blobs mix owners, so a block's stored size is
split by its members' raw sizes (the rounding remainder goes to its first member); a blob
shared by several owners counts for the first in path order. "Standard library and boot" is
every blob of a module or file that belongs to no distribution.

`hello` and `heavy` (tests/fixtures/projects) and fsbpyr itself (the project at this commit,
`fsbpyr build` with the checkout's own runtime), built from the commit that added this
section; bytes, so the sums are exact:

| Part | `hello` | `heavy` | fsbpyr |
|---|---:|---:|---:|
| stub | 20,913,529 | 20,913,529 | 20,913,529 |
| zero padding to the page boundary | 647 | 647 | 647 |
| payload header | 208 | 208 | 208 |
| index and string tables | 100,268 | 392,724 | 119,556 |
| blob alignment padding (16 bytes) | 1,674 | 5,098 | 2,000 |
| standard library and boot modules | 6,685,090 | 6,655,982 | 6,689,534 |
| distributions: code | 403 | 7,280,431 | 797,947 |
| distributions: sources | 65 | 3,296,238 | 312,259 |
| distributions: resources | 212 | 1,129,629 | 103,483 |
| distributions: natives | 0 | 37,394,211 | 0 |
| **file less trailer** | **27,702,096** | **77,068,697** | **28,939,163** |

The floor of every app is the stub (20.9 MB: the launcher with CPython and its libraries linked
in statically, D2) plus the standard library (6.7 MB), together 27.6 MB at this commit
(0.25.0's: see "Starting a no-op app"). `hello` adds about 100 KB of tables and a few hundred
bytes of its own. In `heavy`, onnxruntime and numpy hold 31 of the 37 MB of natives.

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

### The same app with 0.9.0

The released 0.9.0 (the installed `fsbpyr`), measured as above in another session, where the
venv itself ran 13% faster than in the 0.8.0 table: compare columns, not the two tables.

| | venv | zstd 3 | zstd 9 | xz -6 in pieces |
|---|---|---|---|---|
| Size | — | 254.8 MB | 233.0 MB | 214.7 MB |
| Build (uv staging included) | — | 16.1 s | 22.2 s | 27.4 s |
| First run | — | 1.73 s | 1.70 s | 1.72 s |
| First run: torch import | — | 1.11 s | 1.09 s | 1.11 s |
| Later runs | 1.01 s | 1.08 s | 1.07 s | 1.07 s |
| Later runs: torch import | 411 ms | 465 ms | 465 ms | 464 ms |
| Later runs: training loop | 346 ms | 353 ms | 352 ms | 352 ms |
| Integrity check | — | 18.1 ms | 16.3 ms | 12.2 ms |
| Anonymous memory, later / first run | 227 MB | 231 / 440 MB | 231 / 440 MB | 231 / 483 MB |
| RSS, later / first run | 314 MB | 578 / 631 MB | 578 / 614 MB | 577 / 637 MB |

0.8.0 and 0.9.0 (zstd 9) and the venv, later runs, medians of 21 runs of each, interleaved:

| | 0.8.0 | 0.9.0 | venv |
|---|---|---|---|
| Whole run | 1146 ms | 1066 ms | 1003 ms |
| torch import | 543 ms | 466 ms | 411 ms |
| Training loop | 353 ms | 351 ms | 345 ms |
| Peak RSS | 478 MB | 577 MB | 314 MB |

The training loop's 9% difference to the venv in the 0.8.0 table did not show up again (2%).
Peak RSS grew by 99 MB with 0.9.0, anonymous memory did not: RSS counts the pages of the cached
files mapped for the check, and passes of 1024 files keep up to 573 MB of torch's files mapped at
once, against 480 MB with 0.8.0's 256 (computed from the cache's file sizes; libtorch_cpu.so alone
is 438 MB). Those pages are the page cache's, clean, and released as each pass ends.

### Resident memory with 0.9.1 (D36)

The checks read files a leaf at a time, through a 64 KiB buffer per thread, instead of mapping
them, and restored files are written in pieces of 1 MiB. The torch app (zstd 9), later runs, each
build with a cache of its own written by its own first run:

| Peak RSS | 0.9.0 | 0.9.1 | venv |
|---|---|---|---|
| Once the executable is checked (start to `main`) | 226 MB | 14 MB | — |
| torch import (the cache check) | 576 MB | 241 MB | — |
| Training loop | 450 MB | 348 MB | — |
| Whole run (21 runs, interleaved) | 577 MB | 349 MB | 314 MB |
| Whole run, time | 1067 ms | 1071 ms | 1004 ms |
| torch import, time | 467 ms | 473 ms | 411 ms |

Phases: medians of 11 runs, RSS sampled from `/proc` about every millisecond between markers
the app writes; the split into anonymous and file-backed memory is unreliable while the hashing
threads fault pages in, so only the total counts. Resident after the loop (`/proc/self/smaps`):

| Mapping | 0.9.0 | 0.9.1 | venv |
|---|---|---|---|
| `libtorch_cpu.so` | 156 MB | 60 MB | 60 MB |
| the executable / `python3.14` | 42 MB | 42 MB | 11 MB |
| heap and anonymous | 226 MB | 226 MB | 222 MB |
| `libtorch_python.so` | 20 MB | 13 MB | 14 MB |

`libtorch_cpu.so` written whole (one `write()`), or in pieces of 4 MiB, stays in XFS's page cache
as 2 MiB folios, which the dynamic loader maps whole: 156 MB resident. Written in pieces of 1 MiB,
256 KiB or 64 KiB, or dropped from the page cache and read back, 60 MB.

The executable's own check (`integrity_ns`, interleaved): the no-op app's 27.6 MB took 1.93 ms
against 1.82 ms with 0.9.0 (201 runs each), the torch app's 233 MB 13.9 ms against 14.8 ms (21
each). Reading each thread's leaves through a whole 1 MiB buffer instead of 64 KiB cost the
no-op app's start more (the code review measured 1.94 against 1.71 ms) and left 8 MB more heap
in the torch app (127 against 119 MB), since glibc keeps the freed buffers.

### The same app with 0.14.0 and 0.15.0 (D39)

0.15.0 uses up to 8 cores in every phase: modules compiled on worker processes, zstd in pieces
of 8 MiB compressed and decoded on up to 8 threads, extracted files written on up to 8. Each
release's installed `fsbpyr`, measured in separate sessions as above (first runs 5, later runs
21, medians; builds include uv's staging):

| | venv | zstd 3 | zstd 9 | xz -6 in pieces |
|---|---|---|---|---|
| Size, 0.14.0 / 0.15.0 | — | 255.3 / 255.5 MB | 233.3 / 234.4 MB | 214.8 / 214.9 MB |
| Build, 0.14.0 | — | 16.28 s | 22.75 s | 27.16 s |
| Build, 0.15.0 | — | 6.02 s | 6.99 s | 14.54 s |
| First run, 0.14.0 | — | 1705 ms | 1679 ms | 1796 ms |
| First run, 0.15.0 | — | 1294 ms | 1292 ms | 1608 ms |
| First run: torch import, 0.14.0 / 0.15.0 | — | 1105 / 692 ms | 1080 / 687 ms | 1195 / 1014 ms |
| Later runs, 0.14.0 / 0.15.0 | 1003 ms | 1078 / 1076 ms | 1079 / 1076 ms | 1075 / 1078 ms |
| Later runs: torch import | 411 ms | 477 / 475 ms | 479 / 476 ms | 477 / 478 ms |
| Integrity check (21 runs) | — | 13.2 / 13.6 ms | 12.8 / 12.8 ms | 11.8 / 12.2 ms |
| Peak RSS, first run | — | 527 / 552 MB | 509 / 534 MB | 532 / 529 MB |
| Peak RSS, later runs | 313 / 298 MB | 348 / 345 MB | 346 / 343 MB | 346 / 344 MB |

A first run now writes torch's 12,139 small files on up to 8 threads and decodes
libtorch_cpu.so (418 MiB) in zstd pieces on up to 8 (about 100 ms instead of 566 in one frame):
387 ms less with zstd. xz pieces were parallel before; they gain the file writes only. Later
runs are the same. The builds' peak and their cores: below.

## Builds (0.14.0, 0.15.0; D39)

Each release's installed `fsbpyr` (`/usr/bin/time`: wall time, CPU, peak RSS of the largest
process; 0.15.0's workers each have their own):

| App | 0.14.0 | 0.15.0 | 0.15.0 CPU | Peak RSS, 0.14.0 / 0.15.0 |
|---|---|---|---|---|
| no-op app (`hello`) | 0.44 s | 0.34 s | 157% | 143 / 144 MB |
| `heavy` | 4.76 s | 1.72 s | 408% | 160 / 321 MB |
| aiagent (79 distributions) | 17.59 s | 6.79 s | 356% | 537 / 503 MB |
| torch app, `--fast-compression` | 16.28 s | 6.02 s | 381% | 245 / 339 MB |
| torch app, default | 22.75 s | 6.99 s | 452% | 246 / 422 MB |
| torch app, `--best-compression` | 27.16 s | 14.54 s | 624% | 630 / 622 MB |

The 1 GB rule (D38) counts all the build's processes: their proportional set size (PSS,
shared pages divided among the processes that map them) peaked at 673 MiB for the torch app's
default build and 647 MiB for its best compression (9 processes; their RSS summed, which counts
shared pages once per process, 1258 and 1234 MiB). The workers are bounded: 32 jobs or 64 MiB of
sources in flight. What was measured on the way (development builds, the dev venv):

- One core or eight build the same bytes: compile()'s constant sharing depends on what was
  interned before, so a worker's code could differ from the process's by one reference; the
  canonical bytecode merges equal immutable values (D39).
- The main process held about 440 MiB while 8 compression threads overlapped 8 compile workers:
  glibc keeps freed memory in its arenas and above its mmap threshold. Compressing the files
  read whole on the workers instead took the build from 729 to 595 MiB PSS. `mallopt` was not
  adopted.
- The classifier's own work and the closure search stay on one thread (the GIL): about 1 s of
  the torch build; the natives' scan finds names in one pass now.
- The stdlib fragment of a runtime compiles in 0.31 s instead of 1.40 (the compile workers).

## Very large files (D38, D39)

`tests/e2e/test_large.py`'s app: one sparse file of zeros, built compressed (zstd) and raw,
run (the integrity check, a stream through it, an extraction), and `inspect --verify` of the
raw one; each process's peak RSS (`VmHWM`). Wall times, one run each:

| | 0.14.0 | 0.15.0 | 0.15.1 |
|---|---|---|---|
| 5 GiB compressed: build / run | 3.5 / 3.6 s | 1.0 / 2.4 s | — |
| 5 GiB raw: build / run | 0.4 / 3.0 s | 0.6 / 3.0 s | — |
| 1 TiB compressed: build / run | 643.6 / 733.2 s | 150.3 / 484.9 s | — |
| 1 TiB raw: build / run / inspect | 26.1 / 614.9 / 5.1 s | 65.7 / 606.3 / 4.8 s | 14.2 s (build) |
| Peak, builds | 88–91 MiB | 240–414 MiB | 106 MiB (raw) |
| Peak, runs | 118–184 MiB | 118–158 MiB | — |

Compressed, the file is zstd pieces of 8 MiB since 0.15.0, compressed and decoded on up to 8
threads. 0.15.0's raw build was slower: it sent each 1 MiB leaf of the file to a thread, holes
included, a million of them each holding 1 MiB of zeros; 0.15.1 finds holes on the calling
thread and reads only data on the threads (64 GiB raw: 1.0 s, 0.14.0 1.9 s). A raw run reads
and writes the whole file once and did not change.

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

### Writing on 8 threads (0.15.0, D39)

A sync checks every file first, then writes the missing and changed ones: files of up to 8 MiB
on up to 8 threads, in chunks of neighbours; larger ones, xz and pieces by the calling thread,
their pieces decoded in parallel. Extraction into an empty cache on XFS (`_fsbpyr.sync` timed in
the app; medians of 3 to 5 runs):

| | One thread (0.14.0's way) | 8 threads, temporaries in `.scratch` | 8 threads, unnamed files (0.15.0) | 0.15.0, 8 performance cores | 0.15.0, one core |
|---|---|---|---|---|---|
| torch's 12,139 small files (235 MB) | 302 ms | 307–362 ms | 139–146 ms | 120 ms | 296 ms |
| 120,000 files of 19 kB on average (2.4 GB, 20 a directory) | — | — | 0.65 s | 0.56 s | 3.05 s |
| torch's 12 libraries (708 MB on disk) | — | — | 123–127 ms | 125 ms | 389 ms |

Written on 8 threads with temporary names in the tree's one `.scratch` directory, files took
longer than on one: every create and every rename locks that directory. Summed over the
threads (all 24 cores usable), creating took 868 ms (one thread: 50) and the chmod, rename and
close 2294 ms (51), while decoding, hashing and writing scaled (329 against 255 ms). A new file is now written
unnamed in its own directory (`O_TMPFILE`) and linked into place once it has the payload's
digest; a killed process leaves nothing behind. The descriptor table is grown before the writers
start: grown while they ran, each step waited for an RCU grace period (`expand_files`), with
every writer that opened a file meanwhile (200 against 176 ms in a C harness). What is left is
the file system's: threads writing into one large directory wait for one another there
(`filename_create`, torch has directories of thousands of headers), and on XFS's buffer locks.
The same 12,139 files written by a standalone program with temporaries in one shared directory,
one per thread, in the target directory, or unnamed: 92, 44, 30 and 25 ms on 8 threads (127 ms
on one).

## Fast exit (evaluated, dropped)

Ending a run by flushing stdout and stderr, running the `atexit` handlers and calling `os._exit`
instead of letting the interpreter finalize (Phase 7; medians of 21 interleaved runs, 201 for the
no-op app):

| Run | Normal exit | Fast exit |
|---|---|---|
| torch app (0.9.1, zstd 9) | 1070 ms | 857 ms |
| the same script in its venv | 1004 ms | 783 ms |
| no-op app (0.9.1) | 9.4 ms | 8.3 ms |

What it saves is the libraries' own teardown, as much in a venv as in an executable. What it
skips: buffered files the app never closed lose their data, non-daemon threads are killed,
`__del__` methods do not run. Dropped (D37): an exit that leaves debris for something else to
clean up is not worth it.

## Building a runtime

`fsbpyr runtime build` links the launcher with PBS's static CPython under full LTO (since 0.23.0
with clang and lld 23; the figures here are from 0.15.0 and were not measured again): 51.4 s on
one core (49.8 s of CPU), 2.3 GB peak RSS, over D38's 1 GB, in this one command (app builds
reuse the runtime and never link). lld's `--lto-partitions=8` failed: CPython's static
`compress` (`_bz2module.c`) and `lzma_free` (`_lzmamodule.c`) clash with zlib's and liblzma's
once split, and it took 4.9 GB on 1.7 cores. The link printed one warning, unseen until 0.15.0
showed every warning: `cache_sync`'s frame was 8888 bytes, over the 8192 limit, with
`fsb_cache_sync` and `make_root`'s 4 KiB path buffer inlined into it; the buffer is on the heap
and the link prints nothing now.

## Known effects

- **OpenBLAS on a hybrid CPU.** numpy's OpenBLAS starts one busy-waiting thread per core at
  import: `heavy np` uses 1.4–1.9 s of CPU in a 120 ms run and takes 113 ms on all 24 cores,
  64 ms on the 8 performance cores, 59 ms with `OPENBLAS_NUM_THREADS=1`. While those threads
  occupy every core, a thread that sleeps and is woken may land on an efficiency core and stay
  there: joining the hashing threads cost `heavy np` +38–41 ms; spinning on non-blocking joins
  (for at most 2 ms, then a normal join) +4–5 ms (D32).
- **Whatever the run loads is hashed.** The cache check grows with the natives a run imports,
  about 0.1–0.3 ms per MB on this machine; the integrity check with the executable's size.
- **The builder's CPython compiles the app's modules.** A build by the installed `fsbpyr` (its
  runtime's CPython 3.14.8) and one from a development venv on 3.14.7 differed in 3 of the torch
  app's 5,840 modules: CPython's compiler changed between the two patch releases. Each is
  reproducible; both run on 3.14.8 (one bytecode magic per minor version). The stdlib is
  compiled by the runtime's own interpreter.
- **All 24 cores can be slower than the 8 performance cores.** Work of 8 threads spread over
  all 24 lands partly on efficiency cores: 120,000 files took 0.65 s on all, 0.56 s on CPUs 0–7.

## Other tools (Phase 10, 0.19.0)

The same three apps built four ways and run next to their venv: `hello` (prints its arguments),
`heavy` (one native dependency per command) and aiagent (83 distributions). Every tool ran
CPython 3.14.8 from python-build-standalone 20261003: fsbpyr 0.19.0-c3850b07; PyInstaller
6.22.3 (hooks-contrib 2026.8) in onefile mode, from the app's venv on uv's CPython; pex 2.103.4
with `--scie eager --venv` (scie-jump 1.13.0); PyApp 0.30.0 with an embedded Python that has
the app and its locked dependencies installed (`PYAPP_DISTRIBUTION_PATH`, zstd -9,
`PYAPP_FULL_ISOLATION`, `PYAPP_SKIP_INSTALL`), so that like the others it needs no network at
run time. uv 0.13.1, cargo 1.95.0. Baselines: `rg --version` (ripgrep 14.1.1, Rust) and
`gh --version` (gh 2.101.0, Go).

Measured as above, with these differences: each tool ran with a `HOME` and `TMPDIR` of its own
(`PATH=/usr/bin:/bin`); later runs are medians of 31 interleaved rounds after 3 warm-up runs of
each variant; first runs are medians of 11 interleaved rounds, the tool's `HOME` emptied before
each run; peak RSS is `ru_maxrss` from `wait4`, the largest process of the run (PyInstaller's
onefile parent or its child), not their sum. All 24 cores, warm page cache; a cold page cache
was not measured in that run (it needs no root: see "Cold start (0.20.0)"). Bare CPython: 5.2–5.4 ms in a check of 101 rounds; in the main
run, where it always ran right after aiagent's `--help`, it took 10.3 ms.

### What it took to get the venv's output

fsbpyr, pex and PyApp printed what the venv printed for all 8 commands with no options beyond
the build itself. PyInstaller needed two: `--collect-data heavy` (`heavy onnx` failed with
`FileNotFoundError` for `relu_matmul.onnx`, read through `importlib.resources`) and
`--copy-metadata aiagent` (`aiagent version` printed `0.0.0+unknown`). PyApp's
`PYAPP_EXEC_SPEC` runs `import m;m.f()`, so the function's return value is dropped: `hello 7`
exits 0 where the venv and fsbpyr exit 7.

### Size and build

Builds with warm caches (the second of two; the first pex build also downloaded the Python
distribution). PyApp's time includes preparing its embedded Python (unpacking it, installing
the app with uv, compressing it) and a `cargo build` per app.

| App | | fsbpyr | PyInstaller onefile | pex `--scie eager` | PyApp |
|---|---|---|---|---|---|
| hello | size | 27.7 MB | 13.2 MB | 78.2 MB | 61.9 MB |
| | build | 0.37 s | 5.84 s | 0.76 s | 8.89 s |
| heavy | size | 77.1 MB | 51.0 MB | 127.0 MB | 102.6 MB |
| | build | 1.72 s | 15.44 s | 5.82 s | 9.35 s |
| aiagent | size | 150.2 MB | 101.6 MB | 184.9 MB | 148.5 MB |
| | build | 5.93 s | 38.24 s | 14.95 s | 10.07 s |

### Later runs

Caches filled, page cache warm. Bare CPython 5.2–5.4 ms, `rg --version` 1.0 ms,
`gh --version` 35.5 ms (46.5 MB).

| Run | venv | fsbpyr | PyInstaller onefile | pex `--scie eager` | PyApp |
|---|---|---|---|---|---|
| `hello` | 7.9 ms | 8.1 ms | 132.3 ms | 10.1 ms | 7.8 ms |
| `heavy json` | 25.5 ms | 30.8 ms | 461.7 ms | 32.1 ms | 25.5 ms |
| `heavy crypto` | 35.6 ms | 41.7 ms | 464.7 ms | 40.0 ms | 32.3 ms |
| `heavy model` | 70.4 ms | 75.1 ms | 497.1 ms | 73.9 ms | 67.6 ms |
| `heavy np` | 56.5 ms | 62.3 ms | 486.4 ms | 57.3 ms | 54.8 ms |
| `heavy onnx` | 96.8 ms | 112.0 ms | 521.1 ms | 98.9 ms | 95.9 ms |
| `aiagent version` | 122.5 ms | 132.7 ms | 671.2 ms | 124.4 ms | 120.9 ms |
| `aiagent --help` | 156.6 ms | 166.9 ms | 713.4 ms | 159.3 ms | 155.9 ms |

Peak RSS of the same runs:

| Run | venv | fsbpyr | PyInstaller onefile | pex `--scie eager` | PyApp |
|---|---|---|---|---|---|
| `hello` | 13.4 MB | 13.8 MB | 18.9 MB | 13.4 MB | 13.4 MB |
| `heavy json` | 15.8 MB | 23.1 MB | 34.0 MB | 16.3 MB | 15.6 MB |
| `heavy crypto` | 25.0 MB | 31.4 MB | 41.7 MB | 25.4 MB | 24.9 MB |
| `heavy model` | 27.5 MB | 36.2 MB | 42.2 MB | 28.0 MB | 27.7 MB |
| `heavy np` | 31.4 MB | 38.7 MB | 45.9 MB | 31.2 MB | 31.0 MB |
| `heavy onnx` | 60.8 MB | 67.6 MB | 74.1 MB | 61.2 MB | 61.0 MB |
| `aiagent version` | 40.2 MB | 54.5 MB | 52.1 MB | 40.3 MB | 40.1 MB |
| `aiagent --help` | 47.0 MB | 62.6 MB | 58.5 MB | 47.3 MB | 47.0 MB |

### First runs

Each tool's `HOME` emptied before every run; what the run left on disk in brackets.

| Run | fsbpyr | PyInstaller onefile | pex `--scie eager` | PyApp |
|---|---|---|---|---|
| `hello` | 13.1 ms (0) | 129.2 ms (0) | 1,389.6 ms (265.5 MB) | 278.6 ms (311.3 MB) |
| `heavy np` | 87.8 ms (38.3 MB) | 486.8 ms (0) | 1,970.4 ms (462.5 MB) | 616.7 ms (501.4 MB) |
| `heavy onnx` | 153.4 ms (69.0 MB) | 515.8 ms (0) | 2,001.9 ms (462.7 MB) | 673.8 ms (502.0 MB) |
| `aiagent version` | 138.5 ms (7.4 MB) | 669.2 ms (0) | 3,185.8 ms (676.5 MB) | 1,171.9 ms (702.2 MB) |

Peak RSS of a first run: fsbpyr 13.9 to 88.8 MB, PyInstaller 18.9 to 74.0 MB, pex 35.7 to
63.4 MB, PyApp 65.3 to 150.8 MB. After all later runs of all three apps the caches held 96.5 MB
(fsbpyr), 814.4 MB (pex) and 1,519.8 MB (PyApp: each app's unpacked Python plus its cached
archive). PyInstaller keeps nothing, but every start writes a new `_MEIxxxxxx` directory and
removes it at exit: 35.0 MB in 2 files for `hello` (`libpython3.14.so.1.0` and
`base_library.zip`), 130.4 MB in 71 files for `heavy`, 178.6 MB in 2,115 files for aiagent
(the archive's table of contents; `strace` showed the same two files for `hello`).

### What the numbers say

- **Later runs:** pex and PyApp run from the directories their first run unpacked, without
  checks, and match the venv (pex +1 to +7 ms for its bootstrap). fsbpyr is 1.03 to 1.21× the
  venv: +0.2 ms (`hello`) to +15 ms (`heavy onnx`), which is the integrity check (1.75 ms for
  `hello`, 3.5 ms for `heavy`, 7.4 ms for aiagent, `FSBPYR_TRACE`) and the cache check of the
  natives a run loads (see "The cache check"). PyInstaller onefile is 4.6 to 18× the venv: each
  start unpacks 35 to 179 MB.
- **First runs:** fsbpyr's are 5 ms (`hello`) to 41 ms (`heavy onnx`) above its later runs,
  since it extracts only the natives a run imports. PyApp unpacks the whole Python and every
  dependency first (0.28 to 1.17 s), pex unpacks the interpreter and builds a venv (1.4 to
  3.2 s).
- **Disk:** fsbpyr keeps only extracted natives (0 to 69 MB per app here), pex and PyApp a
  full unpacked environment per app (265 to 702 MB).
- **Memory:** fsbpyr's later runs use 0.4 to 15.6 MB more than the venv (its own mapped pages;
  see D36); pex and PyApp match the venv, PyInstaller uses 5 to 18 MB more.
- **Size and build:** PyInstaller's files are the smallest (only what its import analysis finds)
  and its builds of `heavy` and aiagent the slowest; fsbpyr builds fastest; pex's files are the
  largest.
- **Next to compiled CLIs:** fsbpyr's `hello` (8.1 ms) is slower than ripgrep's
  `--version` (1.0 ms) and faster than gh's (35.5 ms).

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
