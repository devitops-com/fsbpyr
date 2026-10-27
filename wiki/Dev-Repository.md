# The repository

The wiki describes how fsbpyr works. Plans, decisions, the continuous integration setup and the
release process live in the repository, versioned with the code. This page points to them. Links
go to the `v@VERSION@` tag; the `main` branch has the current state.

Repository: <https://github.com/devitops-com/fsbpyr>

## Layout

| Path | Contents |
|---|---|
| [`src/fsbpyr/`](../src/fsbpyr) | the Python package: build side ([Build modules](Dev-Build-Modules.md)), [`payload/`](Dev-Payload-Modules.md), [`runtime/`](Dev-Runtime-Modules.md), [`boot/`](Dev-Boot-Modules.md) |
| [`src/fsbpyr/launcher/`](../src/fsbpyr/launcher) | the C launcher ([Launcher](Dev-Launcher.md)) |
| [`stubs/_fsbpyr.pyi`](../stubs/_fsbpyr.pyi) | the type stub for `_fsbpyr`, the built-in module that `launcher/module.c` implements for the boot code |
| [`tests/`](../tests) | unit, integration and end-to-end tests, C probes, the fuzzer, fixtures, the corpus and the support code the tests share, among it what they need from the machine (`tests/support/requires.py`, which `make test-prereqs` runs) ([Testing](Dev-Testing.md)) |
| [`docs/`](../docs) | design documents, below |
| [`tools/`](../tools) | `bootstrap.sh` (`make bootstrap`), `check-c.py` (`make check-c`), `test-setup.sh`, `portability-apps.sh`, `vm-smoke.py` with its pinned kernels and root file systems `vm-pins.toml` (`make test-vm`, [Testing](Dev-Testing.md)), and the watchers for new upstream and toolchain releases |
| [`tools/release/`](../tools/release) | the release scripts (`check-release.sh` checks a published release as its users get it), the pinned uv (`uv.version`) and LLVM (`llvm23`), the installer template `install.sh.in`, the SBOM generator; `el8-stub.py` takes the EL8 stub out of a runtime built with `--glibc 2.28`, checks that it needs no glibc symbol above 2.28 and carries the expected runtime id, and writes it under its release asset name |
| [`wiki/`](../wiki) | this wiki's pages and the Graphviz sources of its diagrams, changed with the code they describe; `make wiki-publish` publishes them to the GitHub and GitLab wikis ([CONTRIBUTING.md](../CONTRIBUTING.md#the-wiki)) |
| [`tools/wiki/`](../tools/wiki) | the wiki's build: the link check (`make wiki-lint`), the published form of the pages, the diagrams and import graphs, the PDFs (pandoc filters and LaTeX templates) and the publishing (`make wiki-build`, `make wiki-publish`) |
| [`spikes/`](../spikes) | experiments kept for reference: Phase 0's static link, initialisation path and marshal spikes, and Phase 9's trial links of other Python versions |
| [`Makefile`](../Makefile) | every developer task: `make help` lists them |
| [`Containerfile`](../Containerfile) | the Debian trixie image for bootstrap and tests on hosts other than trixie |
| [`.github/workflows/`](../.github/workflows) | CI: the full suite, the release build and reproduction, the release, the upstream watch, an attestation example for apps, and a separate workflow, `el8.yml`, that links, tests and publishes the glibc 2.28 (EL8) stubs in the companion release `el8-vX.Y.Z` |

## Documents in `docs/`

| Document | What it is | In the wiki |
|---|---|---|
| [`IMPLEMENTATION_PLAN.md`](../docs/IMPLEMENTATION_PLAN.md) | the requirements, the numbered design decisions (D1, D2, ...), the architecture as planned, and the phases with their status | referenced only; the decision numbers that comments in the code cite are defined here |
| [`improvement-plan.md`](../docs/improvement-plan.md) | a review of the project (2026-10-16, revised against 0.20.0) and the improvements it proposed | referenced only |
| [`format.md`](../docs/format.md) | the executable format | [Executable format](Dev-Executable-Format.md) |
| [`executable-parts.md`](../docs/executable-parts.md) | which parts of executables are shared across apps | [Executable format](Dev-Executable-Format.md) |
| [`compatibility.md`](../docs/compatibility.md) | differences from a venv, target requirements, the tested distributions and apps | [Compatibility](Dev-Compatibility.md) |
| [`performance.md`](../docs/performance.md) | measurements per release | [Performance](Dev-Performance.md) |
| [`security.md`](../docs/security.md) | the threat model | [Security](Dev-Security.md) |
| [`eval.md`](../docs/eval.md) | fsbpyr compared with similar projects (updated 2026-10-25, for 0.25.0) | referenced only |
| [`spikes.md`](../docs/spikes.md) | Phase 0 findings, 2026-10-06 | referenced only |
| [`use-case-research.md`](../docs/use-case-research.md) | research on use cases that need no questions (2026-10-25, against 0.25.0), which the 0.26.0 work built on | referenced only; the use cases 0.26.0 implements are in [Overview](Dev-Overview.md#by-how-the-code-is-kept) |
| `python_cli_runtime_manifest.pdf` | the project's original kick-off document, superseded | not used |

## Other files

| File | Contents |
|---|---|
| [`README.md`](../README.md) | the repository's front page for users, with a quickstart that `tests/e2e/test_build.py` runs ([Testing](Dev-Testing.md#the-readmes-quickstart-as-a-test)) and links to this wiki |
| [`CHANGELOG.md`](../CHANGELOG.md) | changes per release, with **Breaking:** marks |
| [`CONTRIBUTING.md`](../CONTRIBUTING.md) | building from source, how to contribute and run the tests, the test tiers and what the tests need, the CI and release flow, and verifying a release |
| [`SECURITY.md`](../SECURITY.md) | how to report a vulnerability ([Security](Dev-Security.md)) |
| [`LICENSE`](../LICENSE) | MIT, with a runtime exception for the launcher and boot modules |

## Continuous integration and releases

The wiki does not describe them. In short, and in detail in
[`CONTRIBUTING.md`](../CONTRIBUTING.md): the
full test suite runs in GitHub Actions for every Python and target, a release is tagged only from
a commit whose full run passed, and the release workflow builds, attests and publishes the
executables. How users verify a download is in
[Installing](User-Installing.md#verifying-a-download).

## Developer tasks

`make help` in a checkout:

| Target | Does |
|---|---|
| `make bootstrap` | from source to an installed fsbpyr: runtime, three-stage build, install |
| `make build` | build `dist/fsbpyr` with the installed fsbpyr (`PYTHON=3.X`: a variant from source) |
| `make install`, `make uninstall` | install or remove `dist/fsbpyr`, its man page and completions |
| `make check` | ruff and mypy |
| `make check-c` | clang's static analyser over the launcher (informational) |
| `make test-ci`, `make test`, `make test-corpus`, `make test-container`, `make test-portability` | the test tiers ([Testing](Dev-Testing.md)) |
| `make test-prereqs` | list the tools and environment conditions the full test suite needs that this machine lacks ([Testing](Dev-Testing.md#when-the-machine-lacks-something)) |
| `make test-vm` | aarch64 apps in a QEMU virtual machine with EL8's 64K-page kernel ([Testing](Dev-Testing.md)) |
| `make test-el8`, `make test-el8-stub`, `make test-el8-portability` | the EL8 test mode: the tests that run the stub with the glibc 2.28 runtime, and the portability matrix with apps built for glibc 2.28 ([Testing](Dev-Testing.md)) |
| `make release-prep`, `make release`, `make release-check` | the release process (`CONTRIBUTING.md`) |
| `make clean` | remove `dist/` |
