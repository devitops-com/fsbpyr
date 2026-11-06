# The repository

Plans, decisions, CI and the release process live in the repository. Links go to the commit the
wiki was published from (the code of v@VERSION@); `main` has the current state.

Repository: <https://github.com/devitops-com/fsbpyr>

## Layout

| Path | Contents |
|---|---|
| [`src/fsbpyr/`](../src/fsbpyr) | the Python package: build side ([Build modules](Dev-Build-Modules.md)), [`payload/`](Dev-Payload-Modules.md), [`runtime/`](Dev-Runtime-Modules.md), [`boot/`](Dev-Boot-Modules.md) |
| [`src/fsbpyr/launcher/`](../src/fsbpyr/launcher) | the C launcher ([Launcher](Dev-Launcher.md)) |
| [`stubs/_fsbpyr.pyi`](../stubs/_fsbpyr.pyi) | the type stub of `_fsbpyr`, the boot code's built-in module (`launcher/module.c`) |
| [`tests/`](../tests) | unit, integration and end-to-end tests, C probes, the fuzzer, fixtures, the corpus, support code ([Testing](Dev-Testing.md)) |
| [`docs/`](../docs) | design documents, below |
| [`tools/`](../tools) | `bootstrap.sh`, `check-c.py`, `test-setup.sh`, `portability-apps.sh`, `runner-disk.sh`, `vm-smoke.py` and its pins `vm-pins.toml`, and watchers for upstream and toolchain releases |
| [`tools/release/`](../tools/release) | release scripts, pinned uv (`uv.version`) and LLVM (`llvm23`), the installer template `install.sh.in`, the SBOM generator, and `el8-stub.py`, which takes a `--glibc 2.28` runtime's stub out and checks it |
| [`wiki/`](../wiki) | this wiki's pages and diagram sources, changed with the code ([CONTRIBUTING.md](../CONTRIBUTING.md#the-wiki)) |
| [`tools/wiki/`](../tools/wiki) | the wiki's build: link check (`make wiki-lint`), pages, diagrams, PDFs, and publishing to the GitHub and GitLab wikis (`make wiki-build`, `make wiki-publish`) |
| [`spikes/`](../spikes) | experiments of Phases 0 and 9, kept for reference |
| [`Makefile`](../Makefile) | every developer task (`make help`) |
| [`Containerfile`](../Containerfile) | the Debian trixie image for bootstrap and tests on other hosts |
| [`.github/workflows/`](../.github/workflows) | CI: the full suite, release build and reproduction, release, upstream watch, an attestation example for apps, and `el8.yml`, which links a release's glibc 2.28 (EL8) stubs and leaves them in a draft of the companion release `el8-vX.Y.Z` |

## Documents in `docs/`

| Document | What it is | In the wiki |
|---|---|---|
| [`IMPLEMENTATION_PLAN.md`](../docs/IMPLEMENTATION_PLAN.md) | requirements, design decisions (D1, D2, ...) that code comments cite, planned architecture, phases | referenced only |
| [`improvement-plan.md`](../docs/improvement-plan.md) | a project review against 0.20.0, with proposed improvements | referenced only |
| [`format.md`](../docs/format.md) | the executable format | [Executable format](Dev-Executable-Format.md) |
| [`executable-parts.md`](../docs/executable-parts.md) | which parts of executables apps share | [Executable format](Dev-Executable-Format.md) |
| [`compatibility.md`](../docs/compatibility.md) | differences from a venv, target requirements, tested distributions and apps | [Compatibility](Dev-Compatibility.md) |
| [`performance.md`](../docs/performance.md) | measurements per release | [Performance](Dev-Performance.md) |
| [`security.md`](../docs/security.md) | the threat model | [Security](Dev-Security.md) |
| [`signing.md`](../docs/signing.md) | signed releases and apps: formats, commands, keys, the release pipeline | [Security](Dev-Security.md), [User security](User-Security.md) |
| [`eval.md`](../docs/eval.md) | fsbpyr compared with similar projects, for 0.25.0 | referenced only |
| [`spikes.md`](../docs/spikes.md) | Phase 0 findings | referenced only |
| [`use-case-research.md`](../docs/use-case-research.md) | use cases that need no questions, against 0.25.0 | referenced only; [Overview](Dev-Overview.md#by-how-the-code-is-kept) has those implemented |
| `python_cli_runtime_manifest.pdf` | the original kick-off document, superseded | not used |

## Other files

| File | Contents |
|---|---|
| [`README.md`](../README.md) | the front page for users, with a quickstart that `tests/e2e/test_build.py` runs ([Testing](Dev-Testing.md#the-readmes-quickstart-as-a-test)) |
| [`CHANGELOG.md`](../CHANGELOG.md) | changes per release, with **Breaking:** marks |
| [`CONTRIBUTING.md`](../CONTRIBUTING.md) | building from source, contributing, the tests and what they need, CI, releases, verifying a release |
| [`SECURITY.md`](../SECURITY.md) | how to report a vulnerability ([Security](Dev-Security.md)) |
| [`LICENSE`](../LICENSE) | MIT, with a runtime exception for the launcher and boot modules |

## Continuous integration and releases

[`CONTRIBUTING.md`](../CONTRIBUTING.md) describes them. CI builds, attests and stops at a draft:
[`release.yml`](../.github/workflows/release.yml) and [`el8.yml`](../.github/workflows/el8.yml)
hold no key and publish nothing. Users verify a download as
[Security](User-Security.md#checking-an-installed-fsbpyr) says.

## Release keys

The admin signs on their own machine and pushes only the signature
([Security](Dev-Security.md#signatures) says why); [`docs/signing.md`](../docs/signing.md) §5
has the whole procedure.

A release is one command, on main, at the release commit, and it asks nothing:

```console
$ git checkout main && git pull --ff-only hub main    # after looking at what you pull
$ make release
```

Two variables, set once:

| Variable | What it names |
|---|---|
| `FSBPYR_RELEASE_KEY_FILE` | The release key's file; `KEY=PATH` overrides it. |
| `FSBPYR_RELEASE_PASSPHRASE_FILE` | The absolute path of a file holding its passphrase, or of a program that prints it. |

A program, an executable file that starts with `#!` or is an ELF file, or one you may run but
not read, is run with nothing on its standard input, and what it writes on its standard error is
dropped, since the report keeps what each step prints. Any other file is read, x bits or not: on exFAT or NTFS, or a vfat mounted
by hand, every file has them. The passphrase is one line. The file must be yours or root's, and
neither your group nor others may write it, nor read it if it holds the passphrase; like the key,
it stays outside the checkout. Beside the key, it does not stop someone with your account.

`make release` checks that the key opens with the passphrase and is pinned by this tree, runs the
audit, tags and pushes, waits for both workflows, signs and publishes both releases (neither
before both are signed), and runs `make release-check`, `make wiki-publish` and `make mirror`. It
never pulls, which would sign whatever GitHub's main says. Rerun it after any stop; finished
steps are skipped. A failed `el8.yml` does not hold the release back: it says so, and `make
release-sign` signs the companion once a run passes. A merge into main between the tag and the
signature makes `make release-sign` refuse.

Each step's output also goes into a JSON report, rewritten at each step. Reports stay under
`$XDG_STATE_HOME/fsbpyr-release/reports` (or `$FSBPYR_RELEASE_STATE/reports`), one per run, named after the tag and the
time, and `latest.json` leads to the newest:

```console
$ cd ~/.local/state/fsbpyr-release/reports
$ jq '{tag, commit, result, failed_step, warnings}' latest.json
$ jq -r '.steps[] | select(.result != "success") | .title, .output[]' latest.json
```

`result` is `running`, `success`, `failed` or `interrupted`. Each step has its `name`, `result`,
`exit_code`, times and `output`, one string per line. A run that was killed outright stays at
`running`, with `running_step` saying where.

`make release-sign` refuses unless your HEAD, GitHub's main and tag and the sums file's
attestation name one commit, every job of the attested run ran on GitHub's machines, the tree is
clean, the key is outside the checkout and the audit passes; the attested sums then vouch for
the draft's files. It checks the signature against the pins of this tag and the previous
release and rereads the draft and the `.sig` before publishing
([§5.3](../docs/signing.md#53-release-pipeline)). What you tag, K signs.

- **K**, the release key, is an encrypted file on a medium plugged in to sign; **R**, the recovery
  key, stays offline, apart from it; neither reaches GitHub.
  `bash tools/release/release-signing.sh new-key NAME` makes each; fsbpyr stores no private key.
- **The pins** are the `.pub` files in [`src/fsbpyr/release-keys/`](../src/fsbpyr/release-keys); a signature
  by any of them passes. `make release` refuses a tree without any, which would burn the version.
  The suite compares them with [Security](User-Security.md#release-keys)'s "Release keys".
- **One-time setup** ([§5.2](../docs/signing.md#52-one-time-setup)): make and pin K and R, with
  the first signed version in
  [`tools/release/first-signed-release`](../tools/release/first-signed-release); remove the
  organization's GitHub App's access or trust it as yourself; start the audit's record with
  `bash tools/release/audit.sh --accept`.
- **Key changes** ([§5.4](../docs/signing.md#54-changing-keys)): a planned change of K takes two
  releases; the first pins old K, new K and R and is signed by the old K, the second by the new.
  After K is lost or stolen, R signs the release that pins a new K and R
  (`make release-sign KEY=~/release-r.key`); after R is lost, the next release pins a new one;
  with both lost, users reinstall.

After a merge with no release, `make mirror` on main (pulled and looked at) brings GitLab's main
and release tags along and removes branches main fully holds from both remotes, pushing only what
your checkout and GitHub agree on, else nothing; a rerun changes nothing.

### The audit

`make release-audit` compares GitHub with your machine's record, to see a tampered account or
repository before a user does: tags, that your main holds GitHub's, releases with their files,
signatures and immutability, drafts not yours, the latest release, and the settings someone
changes to stay, from members and apps to rulesets, secrets, runners and the wiki's head. `make
release` and `make release-sign` stop if it fails. Run it from cron too, on any machine with a
clone (it needs no key), not in GitHub Actions, which whoever holds the account could turn off.
Its record, with the sums files and signatures read so far, is in
`$XDG_STATE_HOME/fsbpyr-release`.

Exit 1: treat GitHub as tampered with until each finding is explained; exit 2: it could not
check, never a pass. Accept your own change, such as a new secret, with `bash
tools/release/audit.sh --accept` after reading the difference; `make wiki-publish` records the
wiki's new head itself. If GitHub was tampered with, stop releasing, recover the account and warn
users through CVEs, advisories and a mailing list kept ready, since an advisory needs the
account.

## Developer tasks

| Target | Does |
|---|---|
| `make bootstrap` | from source to an installed fsbpyr ([Self-hosting](Dev-Self-Hosting.md)) |
| `make build` | build `dist/fsbpyr` with the installed fsbpyr (`PYTHON=3.X`: a variant from source) |
| `make install`, `make uninstall` | install or remove `dist/fsbpyr`, its man page and completions |
| `make check` | ruff and mypy |
| `make check-c` | clang's static analyser over the launcher (informational) |
| `make test-ci`, `make test`, `make test-corpus`, `make test-container`, `make test-portability` | the test tiers ([Testing](Dev-Testing.md#make-targets)) |
| `make test-prereqs` | [what the full suite needs](Dev-Testing.md#when-the-machine-lacks-something) that this machine lacks |
| `make test-vm` | [aarch64 apps in a QEMU VM](Dev-Testing.md#the-page-size-smoke-test) with EL8's 64K-page kernel |
| `make test-el8`, `make test-el8-stub`, `make test-el8-portability` | [the EL8 test mode](Dev-Testing.md#the-el8-test-mode) |
| `make release-prep`, `make release` | end a branch with the release commit; then [everything else](#release-keys) |
| `make release-assets`, `make release-sign`, `make release-check`, `make release-audit` | the parts of `make release` |
| `make clean` | remove `dist/` |
