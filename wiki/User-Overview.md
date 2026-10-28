# What fsbpyr is

fsbpyr turns a Python command-line program into **one executable file**: a packaged project, a
project without a build system, a directory of scripts, a single script, or the tools a
requirements file names. The file carries CPython (3.12, 3.13, 3.14 or 3.15), the program and
every dependency, native extensions included. The machine that runs it needs only Linux
x86-64 or aarch64 with glibc 2.34 or newer, or glibc 2.28 or newer for an app built with
`--glibc 2.28` (the distributions are listed [below](#supported-linux-distributions)), and
the few system libraries its native dependencies link, which the build names. It needs no
Python, no virtual environment and no installation step.

```sh
fsbpyr build            # in a project with a console script: writes dist/<script>
dist/<script> --help    # runs anywhere the target requirements above are met
fsbpyr build report.py  # one script: writes dist/report
```

## Supported Linux distributions

The launcher at the start of every executable is linked against one of two sysroots, and
that decides where the app runs. fsbpyr @VERSION@ is tested on the distributions of the first
table for every Python (3.12 to 3.15), and `--glibc 2.28` builds on those of the second for
the default Python, 3.14. Other distributions on x86-64 or aarch64 with glibc 2.34 or newer
(2.28 or newer for a `--glibc 2.28` build) are supported but not tested.

**Default build**, the launcher linked against Debian 12's sysroot: glibc 2.34 or newer.

| Distribution | Versions | glibc | x86-64 | aarch64 |
|---|---|---|---|---|
| Red Hat Enterprise Linux and its rebuilds | 9 (tested on Rocky Linux 9), 10 (tested on AlmaLinux 10) | 2.34, 2.39 | yes | yes |
| Amazon Linux | 2023 | 2.34 | yes | yes |
| Ubuntu | 22.04, 24.04, 26.04 | 2.35, 2.39, 2.43 | yes | yes |
| Debian | 12, 13 | 2.36, 2.41 | yes | yes |
| SUSE Linux Enterprise Server | 15 SP7 (tested on SUSE's BCI base image 15.7) | 2.38 | yes | yes |
| openSUSE Leap | 16.0 | 2.40 | yes | yes |
| Fedora | the current release (the tests use `fedora:latest`) | 2.43 or newer | yes | yes |
| Arch Linux | rolling release | 2.44 or newer | yes | no (no aarch64 image to test on) |

**`fsbpyr build --glibc 2.28`**, the launcher linked against Rocky Linux 8.10's sysroot:
glibc 2.28 or newer ([Building for EL8](User-Building.md#building-for-el8-glibc-228)).

| Distribution | Versions | glibc | x86-64 | aarch64 |
|---|---|---|---|---|
| Red Hat Enterprise Linux and its rebuilds | 8 (tested on Rocky Linux 8 and AlmaLinux 8) | 2.28 | yes | yes |
| Every distribution of the first table | as there | as there | yes | as there |

What else a target needs, and what is not supported (musl-based distributions such as Alpine,
aarch64 kernels with 16K pages), is in [What the target needs](User-Running.md#what-the-target-needs).

## Main goals

- **One command, one file, no questions.** `fsbpyr build` turns what it finds into a single
  executable: a project maintained with uv, pip, Poetry, PDM or Pipenv, a directory of scripts,
  a script. Several commands become one executable with a link per command. Only the system's
  loader, glibc, the system libraries manylinux wheels may link (libstdc++, libgcc_s, libz and
  a few more) and libraries you allow stay outside it.
- **Fast start.** Python modules are imported straight from the file, without unpacking. Only
  native code (extension modules and shared libraries), the packages named in `fs-packages`
  and resources the app asks for as file paths are extracted, once, into a cache. Apart from
  the integrity check, which reads the whole file, the work a start does grows with what the
  app imports, not with the size of its dependencies.
- **Real packages work.** fsbpyr is tested on 65 small applications that use real libraries,
  from PyTorch and scikit-learn to Django and FastAPI: each one's commands must give the same
  exit code, output and errors as in a virtual environment. 35 of them need no settings at all.
- **Integrity and reproducibility.** Every executable checks its whole file before it runs. The
  same sources and lock, built by the same fsbpyr and uv versions, give the same bytes, and
  fsbpyr's own releases rebuild themselves byte for byte.
- **Portable.** Executables run on any glibc 2.34+ distribution of the same architecture (2.28+,
  for EL8, when built with `--glibc 2.28`), and native code that needs more is reported at build
  time.

## Main use cases

**By kind of program.** These come from the apps fsbpyr is tested on (the
[test corpus](User-Examples.md)):

| You want to ship... | Tested with | Typical settings |
|---|---|---|
| A data tool that reads and writes files (CSV, Parquet, HDF5, NetCDF) | `pandas-csv`, `dataframes`, `hdf5`, `xarray`, `geo` | none (`pandas-csv`, `dataframes`), `fs-packages` (`xarray`, `geo`) or `interpreter-reexec` (`hdf5`) |
| A scientific or numerical program | `scipy`, `sympy`, `cvxpy`, `ortools`, `astropy`, `qiskit`, `pyscf` | none, or `fs-packages` for packages that read files next to their modules (`cvxpy`, `astropy`, `qiskit`, `pyscf`) |
| A machine-learning training or inference tool | `torch`, `transformers`, `sklearn`, `jax`, `onnx`, `boosting` | none (`torch`, `jax`, `onnx`) or `fs-packages`; `interpreter-reexec` when workers start as new interpreters (`sklearn`); `allow-system-libs` for OpenMP (`boosting`) |
| A web service or network daemon | `fastapi`, `django`, `flask-jinja`, `aiohttp`, `gunicorn`, `grpc`, `celery` | none, or `fs-packages` for templates, static and other data files (`django`, `flask-jinja`, `grpc`) |
| A developer tool | `black`, `lxml`, `pillow`, `pytest-plugin` | usually none |

**By how the code is kept.** Each of these builds without a question to answer:

| You have... | What you get | More |
|---|---|---|
| A packaged project with one console script | one executable | [Building](User-Building.md#a-first-build) |
| A toolkit with several console scripts (`acme-run`, `acme-db`) | one executable, a link per command | [Building](User-Building.md#several-commands-in-one-executable) |
| A package run as `python -m tool`, or one module with a main guard | that module as the command | [Building](User-Building.md#a-project-without-console-scripts) |
| A Poetry, PDM or Pipenv project with its lock | a build locked by `poetry.lock`, `pdm.lock` or `Pipfile.lock` | [Building](User-Building.md#locks-from-poetry-pdm-and-pipenv) |
| A requirements file with `-e ./libs/common` lines or commit-pinned VCS lines | the local packages installed as regular ones, not editable; the VCS lines at their commits | [Building](User-Building.md#requirements-files) |
| A `uv init --no-package` project: `main.py`, helpers, a `uv.lock` | the program as `uv run main.py` runs it | [Scripts and tools](User-Scripts.md#projects-without-a-build-system) |
| A directory of loose scripts copied to hosts with the wrong Python or none | one executable, a command per script | [Scripts and tools](User-Scripts.md#a-directory-of-scripts) |
| Monitoring plugins (Nagios, Icinga, Checkmk) and cron jobs | the same, named as the agents call them | [Scripts and tools](User-Scripts.md#monitoring-plugins-and-cron-fleets) |
| One script, with or without a PEP 723 block | the script and the modules it imports | [Scripts and tools](User-Scripts.md#a-single-script) |
| A requirements file of tools (`httpie==3.2.4`, a `pip freeze`) | their commands in one executable | [Scripts and tools](User-Scripts.md#a-tool-from-its-requirements-file) |
| Any of these for RHEL 8, Rocky Linux 8 or AlmaLinux 8 hosts (glibc 2.28) | the same kind of executable, its launcher linked for glibc 2.28 and its wheels chosen for `manylinux_2_28`: `--glibc 2.28` | [Building](User-Building.md#building-for-el8-glibc-228) |
| Any of these as a systemd service, a container's process or a cron job | output line by line to the journal, SIGTERM honoured as process 1, a cache found without `HOME` | [Running](User-Running.md#under-systemd-in-a-container-from-cron) |

## Where to go next

1. [Installing fsbpyr](User-Installing.md)
2. [Building an app](User-Building.md), with every option
3. [Scripts and tools](User-Scripts.md): projects without a build system, directories of scripts,
   single scripts, tools from a requirements file
4. [Settings in `pyproject.toml`](User-Settings.md): which apps need them, and how to tell
5. [Inspecting an executable](User-Inspecting.md)
6. [Running a built app](User-Running.md): the cache, environment variables, services, helper processes
7. [Other commands](User-Other-Commands.md): `cache`, `runtime`, `self`, `completion`, `man`
8. [Worked examples](User-Examples.md)
9. [Troubleshooting](User-Troubleshooting.md)

How fsbpyr works inside is in the [Developer Guide](Dev-Overview.md).
