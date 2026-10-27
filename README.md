# fsbpyr

`fsbpyr build` turns a Python command-line program into one executable file that carries
CPython (3.12 to 3.15), the program and its locked dependencies: a packaged project, a project
without a build system, a directory of scripts, a single script, or the tools a requirements
file names. The executable runs on Linux x86-64 or aarch64 with glibc 2.34 or newer, with no
Python, virtual environment or installation step on the target.

## Quickstart

<!-- quickstart: tests/e2e/test_build.py runs these sh blocks -->
```sh
curl -LsSf https://github.com/devitops-com/fsbpyr/releases/latest/download/install.sh | sh
```

The installer picks the build for this machine and the default Python, 3.14, and puts it in
`~/.local/bin` (unless `$FSBPYR_INSTALL_DIR` or `$XDG_BIN_HOME` names another directory).

Then, in a project whose `pyproject.toml` names a console script, such as this one for a
`main()` in `src/hello/cli.py`:

```toml
[project]
name = "hello"
version = "1.0.0"
requires-python = ">=3.14"

[project.scripts]
hello = "hello.cli:main"

[build-system]
requires = ["uv_build>=0.12.17,<0.13.0"]
build-backend = "uv_build"
```

(Without a `[build-system]` table fsbpyr builds the project's files as scripts, as `uv run` runs
them, and `src/` stays part of the module path.)

```sh
~/.local/bin/fsbpyr build                 # writes dist/hello
dist/hello
~/.local/bin/fsbpyr inspect dist/hello
```
<!-- /quickstart -->

These lines call fsbpyr by its path: when that directory is not on `PATH` yet, the installer adds
it for new shells, not for the one it ran in (it prints the `source` line that does that).

## Documentation

The [wiki](https://github.com/devitops-com/fsbpyr/wiki) describes the latest release.

- **User Guide:** [what fsbpyr is](https://github.com/devitops-com/fsbpyr/wiki/User-Overview), [installing](https://github.com/devitops-com/fsbpyr/wiki/User-Installing),
  [building an app](https://github.com/devitops-com/fsbpyr/wiki/User-Building), [scripts and tools](https://github.com/devitops-com/fsbpyr/wiki/User-Scripts),
  [settings](https://github.com/devitops-com/fsbpyr/wiki/User-Settings), [inspecting](https://github.com/devitops-com/fsbpyr/wiki/User-Inspecting),
  [running](https://github.com/devitops-com/fsbpyr/wiki/User-Running), [other commands](https://github.com/devitops-com/fsbpyr/wiki/User-Other-Commands),
  [examples](https://github.com/devitops-com/fsbpyr/wiki/User-Examples), [troubleshooting](https://github.com/devitops-com/fsbpyr/wiki/User-Troubleshooting).
- **Developer Guide:** [overview](https://github.com/devitops-com/fsbpyr/wiki/Dev-Overview), [architecture](https://github.com/devitops-com/fsbpyr/wiki/Dev-Architecture),
  the modules, the launcher, the executable format, compatibility, performance, security and
  testing, all reached from the [wiki's home page](https://github.com/devitops-com/fsbpyr/wiki).

`fsbpyr --help`, `fsbpyr build --help` and `fsbpyr man` describe the commands of the fsbpyr you
run. Building fsbpyr from source and verifying a release are in
[CONTRIBUTING.md](CONTRIBUTING.md); reporting a vulnerability is in [SECURITY.md](SECURITY.md).

## Licence

MIT, with a runtime exception for the launcher and the boot code that every built executable
carries: see [LICENSE](LICENSE).
