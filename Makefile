SHELL := /bin/bash

# Where `make install` puts fsbpyr, its man page and its shell completions. The defaults are
# the per-user locations that man (through PATH: ~/.local/bin -> ~/.local/share/man), bash-completion
# and fish search without configuration; zsh needs ZSH_COMPLETION_DIR in $fpath before compinit.
PREFIX              ?= $(HOME)/.local
BINDIR              ?= $(PREFIX)/bin
MANDIR              ?= $(PREFIX)/share/man
BASH_COMPLETION_DIR ?= $(PREFIX)/share/bash-completion/completions
ZSH_COMPLETION_DIR  ?= $(PREFIX)/share/zsh/site-functions
FISH_COMPLETION_DIR ?= $(PREFIX)/share/fish/vendor_completions.d

# uv's installer puts uv in ~/.local/bin, which a fresh login may not have on PATH yet.
export PATH := $(HOME)/.local/bin:$(PATH)

# No target installs or updates a tool: uv must not install a Python on its own either. The one
# .python-version pins is the user's to install (`uv python install`).
export UV_PYTHON_DOWNLOADS := never

# PYTHON=3.12 (3.12 to 3.15, on the command line only: an exported one is ignored) runs the
# checks, the tests and `make build` on that Python's variant of fsbpyr (Phase 9), in the locked
# environment .venv-3.12; without it nothing changes. A variant's executable is dist/fsbpyr-3.12.
UV  := uv
ifeq ($(origin PYTHON),command line)
export UV_PROJECT_ENVIRONMENT := .venv-$(PYTHON)
UVRUN := $(UV) run --python $(PYTHON) --locked
BIN   := dist/fsbpyr-$(PYTHON)
PYTHON_MINOR := $(PYTHON)
else
UVRUN := $(UV) run --locked
BIN   := dist/fsbpyr
PYTHON_MINOR := $(shell cut -d. -f1,2 .python-version)
endif
# What builds a runtime here (`make build PYTHON=3.X`, the tests, check-c) links with clang/lld
# 23: the pinned python-build-standalone release is LLVM 23 bitcode, and Debian trixie ships
# clang 22. LLVM's release tarball that tools/release/llvm23 pins, fetched once into fsbpyr's
# cache by tools/bootstrap.sh --llvm23, comes first on PATH. FSBPYR_LLVM23=0, as for make
# bootstrap, leaves PATH alone, which then needs a clang/lld 23 or newer of its own.
ifeq ($(FSBPYR_LLVM23),0)
WITH_LLVM23 :=
else
WITH_LLVM23 := clang23="$$(bash tools/bootstrap.sh --llvm23)" && export PATH="$${clang23%/*}:$$PATH" &&
endif

.PHONY: help bootstrap build install uninstall test test-ci test-container test-corpus test-portability check check-c release-prep release clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

# --- Setup ---

# Debian trixie: the runtime, fsbpyr built from source, `make install`, with the uv, Python
# and clang/lld 22 you installed (a missing one is named; nothing is installed for you).
# Anywhere else the build runs in a Debian trixie podman container (podman is yours to
# install) and the install on this host. See tools/bootstrap.sh.
bootstrap: ## From source to an installed fsbpyr: runtime, build, install (PYTHON=3.X: that variant's three stages, no install)
	@bash tools/bootstrap.sh$(if $(filter command line,$(origin PYTHON)), --python $(PYTHON))

# --- Build ---

# The installed fsbpyr builds the checkout: it carries its own runtime, so no compiler is
# needed, only uv. Without an installed fsbpyr it stops: `make bootstrap` installs one. Its
# --version names the vX.Y.Z tag on HEAD, else `untagged`.
#
# PYTHON=3.12 builds that variant instead, dist/fsbpyr-3.12, from source in its environment
# with its own runtime (fsbpyr runtime build, cached after the first run): the installed fsbpyr
# runs on one Python only. make bootstrap PYTHON=3.12 also checks it in three stages.
build: ## Build dist/fsbpyr with the installed fsbpyr (make bootstrap installs one); PYTHON=3.X: dist/fsbpyr-3.X from source
	@bash tools/bootstrap.sh --uv
ifeq ($(origin PYTHON),command line)
	$(WITH_LLVM23) $(UVRUN) fsbpyr runtime build --python $(PYTHON)
	$(UVRUN) fsbpyr build . --python $(PYTHON) -o $(BIN)
else
	@fsbpyr="$$(command -v $(BINDIR)/fsbpyr || command -v fsbpyr || true)"; \
	if [ -z "$$fsbpyr" ]; then \
		echo "ERROR: no fsbpyr in $(BINDIR) or on PATH; make bootstrap builds and installs one" >&2; \
		exit 1; \
	fi; \
	echo "$$fsbpyr build . -o $(BIN)"; \
	"$$fsbpyr" build . -o $(BIN)
endif

# --- Install ---

# The man page and the completions come from the executable being installed (one source, D40).
install: ## Install dist/fsbpyr, its man page and completions under PREFIX (default ~/.local)
	@[ "$(origin PYTHON)" != "command line" ] || { echo "ERROR: install puts the default variant's dist/fsbpyr in place; PYTHON is for builds and tests" >&2; exit 1; }
	@[ -x $(BIN) ] || $(MAKE) --no-print-directory build
	@mkdir -p dist/share
	$(BIN) man > dist/share/fsbpyr.1
	$(BIN) completion bash > dist/share/fsbpyr.bash
	$(BIN) completion zsh > dist/share/_fsbpyr
	$(BIN) completion fish > dist/share/fsbpyr.fish
	install -Dm755 $(BIN) $(DESTDIR)$(BINDIR)/fsbpyr
	install -Dm644 dist/share/fsbpyr.1 $(DESTDIR)$(MANDIR)/man1/fsbpyr.1
	install -Dm644 dist/share/fsbpyr.bash $(DESTDIR)$(BASH_COMPLETION_DIR)/fsbpyr
	install -Dm644 dist/share/_fsbpyr $(DESTDIR)$(ZSH_COMPLETION_DIR)/_fsbpyr
	install -Dm644 dist/share/fsbpyr.fish $(DESTDIR)$(FISH_COMPLETION_DIR)/fsbpyr.fish

uninstall: ## Remove what `make install` installed
	rm -f $(DESTDIR)$(BINDIR)/fsbpyr $(DESTDIR)$(MANDIR)/man1/fsbpyr.1 \
		$(DESTDIR)$(BASH_COMPLETION_DIR)/fsbpyr $(DESTDIR)$(ZSH_COMPLETION_DIR)/_fsbpyr \
		$(DESTDIR)$(FISH_COMPLETION_DIR)/fsbpyr.fish

# --- Checks ---

# The tests' temporary files (gigabytes of builds) go under TESTTMP, on a regular file system:
# /tmp is often a tmpfs mounted nosuid, where a set-gid start cannot be tested. Each run gets a
# directory of its own there, removed when the run ends (uv and pytest leave files in TMPDIR);
# after Ctrl-C, the tests' subprocesses may still be writing there, hence the second try.
# The runtime's pinned downloads are kept in TESTTMP between runs (tests/support/downloads.py),
# unless FSBPYR_TEST_DOWNLOADS names another place.
# A runaway in the tests (an app restarting itself through sys.executable, 2026-10-24) is held
# in a cgroup of the run ($(CONTAINED), before the command) where systemd's user manager offers
# one: at most TEST_TASKS tasks and TEST_MEMORY bytes of memory (70% of the machine's), no swap;
# only the process that runs out of memory is killed (OOMPolicy=continue), and whatever is left
# in the cgroup is killed when the run ends. Without a user manager (CI's containers, which
# have limits of their own) the run is not wrapped. tests/support/contain.py contains each run
# of an app besides.
TESTTMP ?= $(HOME)/tmp
TEST_TASKS ?= 8192
TEST_MEMORY ?= $(shell awk '/^MemTotal:/ { printf "%d", $$2 * 1024 * 0.7 }' /proc/meminfo)
TESTENV := $(WITH_LLVM23) mkdir -p "$(TESTTMP)" && tmp=$$(mktemp -d "$(TESTTMP)/fsbpyr-test.XXXXXX") \
	&& unit="fsbpyr-test-$$$$" contain= && if systemd-run --user --scope --quiet true 2>/dev/null; then \
		contain="systemd-run --user --scope --quiet --collect --unit=$$unit -p TasksMax=$(TEST_TASKS) \
			-p MemoryMax=$(TEST_MEMORY) -p MemorySwapMax=0 -p OOMPolicy=continue --"; fi \
	&& trap 'systemctl --user kill --signal=SIGKILL "$$unit.scope" 2>/dev/null; \
		rm -rf "$${tmp:?}" 2>/dev/null || { sleep 2; rm -rf "$${tmp:?}"; }' EXIT && TMPDIR="$$tmp" \
	FSBPYR_TEST_DOWNLOADS="$${FSBPYR_TEST_DOWNLOADS:-$(TESTTMP)/fsbpyr-test-downloads}"
CONTAINED := $$contain
# `make test` runs the tests in JOBS processes (pytest-xdist), each test file in one of them;
# the real-world corpus is left to `make test-corpus`.
JOBS ?= 8

test: ## Run the test suite but the corpus in JOBS processes (default 8; temporary files in a directory under TESTTMP, ~/tmp, removed afterwards)
	$(TESTENV) $(CONTAINED) $(UVRUN) pytest -n $(JOBS) --dist loadfile --ignore=tests/e2e/test_corpus.py

# The core of the suite, quicker than `make test`: unit and integration tests, real builds,
# executables run with the release launcher, and a real executable's boot imports and
# cache-sync contract (D37). Left to `make test`: the ASan/UBSan launcher variant, the
# portability tests (podman) and the other end-to-end tests. -k also matches marker names, so it keeps the release half of
# the tests marked `sanitized`. Branch coverage below pyproject.toml's fail_under fails the run;
# coverage.xml is the report.
test-ci: ## Run the core of the test suite
	$(TESTENV) $(CONTAINED) $(UVRUN) pytest tests/unit tests/integration tests/e2e/test_build.py \
		tests/e2e/test_runtime_app.py tests/e2e/test_imports.py \
		tests/e2e/test_stub_hardening.py \
		-k "not (sanitized and not release)" --cov --cov-report=term --cov-report=xml

# Everything but the portability tests (`podman run` inside podman) and the corpus, in the
# Containerfile's `test` stage: Debian trixie as `tester`, with the packages and users
# tools/test-setup.sh adds.
# A copy of the checkout is the suite's; FSBPYR_TESTS_REQUIRE_ALL=container fails any skip
# outside the allowlist in tests/conftest.py. Two volumes keep what a run would fetch again:
# uv's cache and Pythons, and the runtime's pinned downloads (FSBPYR_TEST_DOWNLOADS). Needs
# podman, which is yours to install; --network pasta:-4 is bootstrap's, for the same reason.
# TMPDIR is a tmpfs: a symlink target of 4,000 bytes (test_cache_root.py) fails on the
# container's overlay file system with "File name too long", and the builds are big.
test-container: ## Run the whole suite but the portability tests and the corpus in a Debian trixie container (podman; PYTHON=3.X)
	podman build --target test -t fsbpyr-test -f Containerfile .
	podman run --rm --network pasta:-4 -v "$(CURDIR)":/checkout:ro,Z \
		-v fsbpyr-test-cache:/home/tester/.cache -v fsbpyr-test-downloads:/home/tester/downloads \
		--tmpfs /var/tmp:mode=1777 -e TMPDIR=/var/tmp \
		-e FSBPYR_TEST_DOWNLOADS=/home/tester/downloads -e FSBPYR_TESTS_REQUIRE_ALL=container \
		fsbpyr-test bash -c 'tar -C /checkout --exclude="./.venv*" --exclude=./dist -cf - . | tar -xf - \
			&& uv python install $(if $(filter command line,$(origin PYTHON)),$(PYTHON),"$$(cat .python-version)") \
			&& clang23="$$(bash tools/bootstrap.sh --llvm23)" && export PATH="$${clang23%/*}:$$PATH" \
			&& $(UVRUN) pytest -n $(JOBS) --dist loadfile --ignore=tests/e2e/test_portability.py \
				--ignore=tests/e2e/test_corpus.py'

# The real-world corpus (tests/corpus, TST-3), as full-suite.yml's corpus job runs it: each app
# in one of JOBS processes. It needs the network (gigabytes of wheels on a first run, kept in
# uv's cache) and up to a few gigabytes of temporary space per app while it runs.
test-corpus: ## Run the real-world corpus in JOBS processes (PYTHON=3.X; network)
	$(TESTENV) $(CONTAINED) $(UVRUN) pytest -n $(JOBS) --dist load tests/e2e/test_corpus.py

# The portability matrix (tests/e2e/test_portability.py): 11 images pulled and run with podman,
# on $(BIN)'s builds of the fixtures (tools/portability-apps.sh); needs llvm-objdump.
# FSBPYR_TESTS_REQUIRE_ALL=portability fails any skip. Run `make build` first.
test-portability: ## Run the portability matrix on 11 distributions (podman, dist/fsbpyr)
	rm -rf dist/portability-apps
	tools/portability-apps.sh $(BIN) dist/portability-apps $(PYTHON_MINOR)
	$(TESTENV) FSBPYR_PORTABILITY_APPS="$(CURDIR)/dist/portability-apps" FSBPYR_TESTS_REQUIRE_ALL=portability \
		$(UVRUN) pytest tests/e2e/test_portability.py

check: ## Static checks: ruff + mypy
	$(UVRUN) ruff check
	$(UVRUN) mypy

# clang's static analyzer over the launcher, compiled with its flags against the cached PBS
# headers (a runtime that is not cached is fetched). Informational: full-suite.yml runs it
# with continue-on-error; it exits 1 when the analyzer has findings.
check-c: ## Static analysis of the launcher's C sources (clang --analyze; informational)
	$(WITH_LLVM23) $(UVRUN) python tools/check-c.py

# --- Release ---

# A release is two steps. release-prep ends a branch with the release commit (VERSION=X.Y.Z
# sets the version in it; without, the current version is released), so that the full suite
# tests exactly what is released once the branch is merged. release then tags main's HEAD, that
# commit, once its full suite run passed, and pushes the tag: CI builds and publishes it (a
# VERSION there must be HEAD's). Only a VERSION given on the make command line counts: an
# exported one (another tool's) is ignored.
release-prep: ## End this branch with the release commit: set the version (VERSION=X.Y.Z), promote CHANGELOG
	@bash tools/release/prepare.sh$(if $(filter command line,$(origin VERSION)), '$(VERSION)')

release: ## Tag main's tested HEAD and push the tag; CI builds, attests and publishes the release
	@bash tools/release/release.sh$(if $(filter command line,$(origin VERSION)), '$(VERSION)')

clean: ## Remove dist/
	rm -rf dist
