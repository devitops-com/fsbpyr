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

> This wiki describes **fsbpyr @VERSION@**. Links into the repository point at the
> [commit](../) the wiki was published from, whose code is that of v@VERSION@. For a later
> release, the
> repository's [changelog](https://github.com/devitops-com/fsbpyr/blob/main/CHANGELOG.md) says
> what changed.

## User Guide

For people who build and ship apps with fsbpyr.

The whole guide as one PDF: [fsbpyr-User-Guide.pdf](fsbpyr-User-Guide.pdf)

1. [What fsbpyr is](User-Overview.md): goals and main use cases
2. [Installing fsbpyr](User-Installing.md)
3. [Building an app](User-Building.md): what fsbpyr builds, several commands, locks, every option
4. [Scripts and tools](User-Scripts.md): projects without a build system, directories of scripts,
   single scripts, tools from a requirements file, deploying them
5. [Settings in `pyproject.toml`](User-Settings.md): `fs-packages`, `allow-system-libs`, `interpreter-reexec`
6. [Inspecting an executable](User-Inspecting.md)
7. [Running a built app](User-Running.md): the cache, environment variables, services, checking a target
8. [Other commands](User-Other-Commands.md): `cache`, `runtime`, `self`, `completion`, `man`
9. [Worked examples](User-Examples.md)
10. [Troubleshooting](User-Troubleshooting.md)

## Developer Guide

For people who work on fsbpyr or want to know how it works.

The whole guide as one PDF: [fsbpyr-Developer-Guide.pdf](fsbpyr-Developer-Guide.pdf)

1. [Overview](Dev-Overview.md): components, goals, use cases
2. [Architecture](Dev-Architecture.md): how the parts fit, with diagrams and import graphs
3. Python modules: [build side](Dev-Build-Modules.md), [payload](Dev-Payload-Modules.md),
   [runtime](Dev-Runtime-Modules.md), [boot (inside the executable)](Dev-Boot-Modules.md)
4. [The C launcher](Dev-Launcher.md)
5. [Executable format](Dev-Executable-Format.md)
6. [Self-hosting](Dev-Self-Hosting.md): fsbpyr builds itself in three stages, from source or
   from a release
7. [Compatibility](Dev-Compatibility.md), [Performance](Dev-Performance.md), [Security](Dev-Security.md)
8. [Testing](Dev-Testing.md)
9. [The repository](Dev-Repository.md): plans, CI and releases, other documents
