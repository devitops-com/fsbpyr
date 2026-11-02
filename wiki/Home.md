# fsbpyr

**fsbpyr turns a Python command-line program into one executable file** that carries CPython,
the program and its dependencies: a packaged project, a directory of scripts, a single
script, or the tools a requirements file names. The file runs on Linux x86-64 or aarch64 with
glibc 2.34 or newer (2.28, for EL8, with `--glibc 2.28`), with no Python, virtual environment or
installation step.

```sh
curl -LsSf https://github.com/devitops-com/fsbpyr/releases/latest/download/install.sh | sh
~/.local/bin/fsbpyr build            # in a project with a console script: writes dist/<script>
~/.local/bin/fsbpyr build report.py  # one script: writes dist/report
```

**Security is built in, on by default, with nothing to install.** Every release is signed twice,
with Ed25519 and the post-quantum ML-DSA-65, on the maintainer's own machine, so that even a stolen
GitHub account cannot ship you an update: `fsbpyr self update` refuses anything those keys did not
sign. Every executable checks its whole file before it runs, and you can sign your own apps with
one option. [Security](User-Security.md) covers all of it.

> This wiki describes **fsbpyr @VERSION@**. Links into the repository point at the
> [commit](../) the wiki was published from, whose code is that of v@VERSION@. For a later
> release, the
> repository's [changelog](https://github.com/devitops-com/fsbpyr/blob/main/CHANGELOG.md) says
> what changed.

## User Guide

For people who build and ship apps with fsbpyr.

The whole guide as one PDF: [fsbpyr-User-Guide.pdf](fsbpyr-User-Guide.pdf)

1. [What fsbpyr is](User-Overview.md): goals and main use cases
2. [Installing fsbpyr](User-Installing.md): every Python and architecture, the installer, updating,
   building fsbpyr from source
3. [**Security**](User-Security.md): what protects you, the messages to watch for, checking and
   restoring fsbpyr, signing your own apps
4. [Building an app](User-Building.md): what fsbpyr builds, several commands, locks, every option
5. [Scripts and tools](User-Scripts.md): projects without a build system, directories of scripts,
   single scripts, tools from a requirements file, deploying them
6. [Settings in `pyproject.toml`](User-Settings.md): `fs-packages`, `allow-system-libs`, `interpreter-reexec`
7. [Inspecting an executable](User-Inspecting.md)
8. [Running a built app](User-Running.md): the cache, environment variables, services, checking a target
9. [Other commands](User-Other-Commands.md): `cache`, `runtime`, `self`, `completion`, `man`
10. [Worked examples](User-Examples.md)
11. [Troubleshooting](User-Troubleshooting.md)

## Developer Guide

For people who work on fsbpyr or want to know how it works.

The whole guide as one PDF: [fsbpyr-Developer-Guide.pdf](fsbpyr-Developer-Guide.pdf)

1. [Overview](Dev-Overview.md): components, goals, use cases
2. [Architecture](Dev-Architecture.md): how the parts fit, with diagrams and import graphs
3. [**Security**](Dev-Security.md): the threat model, every defence and where it sits in the code,
   the release chain, the known limits
4. Python modules: [build side](Dev-Build-Modules.md), [payload](Dev-Payload-Modules.md),
   [runtime](Dev-Runtime-Modules.md), [boot (inside the executable)](Dev-Boot-Modules.md)
5. [The C launcher](Dev-Launcher.md)
6. [Executable format](Dev-Executable-Format.md)
7. [Self-hosting](Dev-Self-Hosting.md): fsbpyr builds itself in three stages, from source or
   from a release
8. [Compatibility](Dev-Compatibility.md), [Performance](Dev-Performance.md)
9. [Testing](Dev-Testing.md)
10. [The repository](Dev-Repository.md): plans, CI and releases, signing and the audit, other documents
