# Security policy

## Reporting a vulnerability

Report it privately, as a
[GitHub security advisory](https://github.com/devitops-com/fsbpyr/security/advisories/new)
(the repository's Security tab, "Report a vulnerability"), not as a public issue. Reports are
acknowledged within 7 days. Say which fsbpyr is affected (`fsbpyr --version`) and how to
reproduce the problem; for a built executable, the output of `fsbpyr inspect --json` on it
helps too.

The assets, the attackers in and out of scope, what each defence stops and the known limits are
in [docs/security.md](docs/security.md).

## Supported versions

Only the latest [release](https://github.com/devitops-com/fsbpyr/releases) is supported: fixes
come in a new release, and older releases are not patched.

## The runtime in every executable

Every executable fsbpyr builds, fsbpyr's own included, carries its own runtime: CPython 3.14
from [python-build-standalone](https://github.com/astral-sh/python-build-standalone) (PBS),
linked statically into the launcher together with the libraries that PBS links into CPython's
built-in modules: OpenSSL, SQLite, expat, zlib, libffi, liblzma, mpdecimal, bzip2, zstd,
ncurses, libedit and libuuid. The runtime uses none of the target machine's copies of them,
so updating the system's Python or OpenSSL changes nothing for it. A fix in any of them
reaches an executable only when it is rebuilt with an fsbpyr that carries the fixed runtime.
`fsbpyr inspect --json APP` names the runtime an executable carries (`build_info.runtime`).

The app's own dependencies, and the libraries their wheels bundle, come from the app's lock
file. Updating them is up to the app, and takes a rebuild as well.

## Fixes to the runtime

When a PBS release fixes a security issue in CPython 3.14 or in one of the libraries linked
into it, an fsbpyr patch release built on that PBS release follows. The targets are 7 days
after the PBS release for high or critical issues, and 30 days otherwise.

The patch release's CHANGELOG entry, under `### Security`, names the components it updates
with their old and new versions, and the old and new runtime ids, and says "rebuild your
executables": an executable built before it keeps the old runtime, and rebuilding it with the
new fsbpyr is the only fix.

From 20261013 on, PBS releases ship CPython as LLVM 23 bitcode, which fsbpyr links only with
clang and lld 23 or newer. Since 0.23.0 fsbpyr's releases link with LLVM's clang and lld
23.1.3, from LLVM's release tarball that `tools/release/llvm23` pins by SHA-256 (SEC-4 in
[docs/improvement-plan.md](docs/improvement-plan.md)), so such a PBS release no longer waits
for a toolchain. A PBS release on a newer LLVM major needs that pin moved first, and can take
longer than the targets above.
