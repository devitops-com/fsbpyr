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

.PHONY: help bootstrap build install uninstall test test-ci test-prereqs test-container test-corpus test-portability test-el8 test-el8-stub test-el8-portability test-vm check check-c release-prep release release-check wiki-lint wiki-build wiki-publish clean

help: ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

# --- Setup ---

# Debian trixie: the runtime (linked with the clang/lld 23 it fetches), fsbpyr built from
# source, `make install`, with the uv, Python, curl and zstd you installed (a missing one is
# named; nothing is installed for you).
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
SCRATCHENV := mkdir -p "$(TESTTMP)" && tmp=$$(mktemp -d "$(TESTTMP)/fsbpyr-test.XXXXXX") \
	&& unit="fsbpyr-test-$$$$" contain= && if systemd-run --user --scope --quiet true 2>/dev/null; then \
		contain="systemd-run --user --scope --quiet --collect --unit=$$unit -p TasksMax=$(TEST_TASKS) \
			-p MemoryMax=$(TEST_MEMORY) -p MemorySwapMax=0 -p OOMPolicy=continue --"; fi \
	&& trap 'systemctl --user kill --signal=SIGKILL "$$unit.scope" 2>/dev/null; \
		rm -rf "$${tmp:?}" 2>/dev/null || { sleep 2; rm -rf "$${tmp:?}"; }' EXIT && TMPDIR="$$tmp" \
	FSBPYR_TEST_DOWNLOADS="$${FSBPYR_TEST_DOWNLOADS:-$(TESTTMP)/fsbpyr-test-downloads}"
TESTENV := $(WITH_LLVM23) $(SCRATCHENV)
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
		tests/e2e/test_stub_hardening.py tests/e2e/test_multicall.py tests/e2e/test_scripts.py \
		tests/e2e/test_loose_directory.py tests/e2e/test_single_script.py \
		tests/e2e/test_install_links.py tests/e2e/test_pid1.py tests/e2e/test_unbuffered.py \
		tests/e2e/test_file_overlay.py tests/e2e/test_pipfile_lock.py \
		-k "not (sanitized and not release)" --cov --cov-report=term --cov-report=xml

# Nothing in the suite skips: a test whose tool or environment is missing fails, naming it.
# This lists what the full suite lacks here, all at once (CONTRIBUTING.md, Running the tests).
test-prereqs: ## List the tools and environment conditions the full test suite needs that this machine lacks
	$(TESTENV) $(UVRUN) python -m tests.support.requires

# Everything but the portability tests (`podman run` inside podman) and the corpus, in the
# Containerfile's `test` stage: Debian trixie as `tester`, with the packages and users
# tools/test-setup.sh adds.
# A copy of the checkout is the suite's. The stage has no podman, so the tests that need one
# (-m "not podman") are left out; every other test runs, and fails where the container lacks
# what it needs. Two volumes keep what a run would fetch again: uv's cache and Pythons, and
# the runtime's pinned downloads (FSBPYR_TEST_DOWNLOADS). Needs podman, which is yours to
# install; --network pasta:-4 is bootstrap's, for the same reason. --privileged, as in
# full-suite.yml: `unshare --pid --mount-proc` (test_pid1) needs it in a container; in a
# rootless podman it adds no privilege on the host. TMPDIR is a tmpfs that allows exec and
# set-id bits (podman's default is nosuid): a symlink target of 4,000 bytes (test_cache_root.py)
# fails on the container's overlay file system with "File name too long", the set-id tests
# need the bits, and the builds are big.
test-container: ## Run the whole suite but the portability tests and the corpus in a Debian trixie container (podman; PYTHON=3.X)
	podman build --target test -t fsbpyr-test -f Containerfile .
	podman run --rm --network pasta:-4 -v "$(CURDIR)":/checkout:ro,Z \
		-v fsbpyr-test-cache:/home/tester/.cache -v fsbpyr-test-downloads:/home/tester/downloads \
		--privileged --pids-limit 8192 --tmpfs /var/tmp:mode=1777,exec,suid -e TMPDIR=/var/tmp \
		-e FSBPYR_TEST_DOWNLOADS=/home/tester/downloads \
		fsbpyr-test bash -c 'tar -C /checkout --exclude="./.venv*" --exclude=./dist -cf - . | tar -xf - \
			&& uv python install "$$(cat .python-version)" 3.12 3.13 3.14 3.15 \
			&& clang23="$$(bash tools/bootstrap.sh --llvm23)" && export PATH="$${clang23%/*}:$$PATH" \
			&& $(UVRUN) pytest -n $(JOBS) --dist loadfile -m "not podman" \
				--ignore=tests/e2e/test_portability.py --ignore=tests/e2e/test_corpus.py'

# The real-world corpus (tests/corpus, TST-3), as full-suite.yml's corpus job runs it: each app
# in one of JOBS processes. It needs the network (gigabytes of wheels on a first run, kept in
# uv's cache) and up to a few gigabytes of temporary space per app while it runs.
test-corpus: ## Run the real-world corpus in JOBS processes (PYTHON=3.X; network)
	$(TESTENV) $(CONTAINED) $(UVRUN) pytest -n $(JOBS) --dist load tests/e2e/test_corpus.py

# The portability matrix (tests/e2e/test_portability.py): 12 images pulled and run with podman,
# on $(BIN)'s builds of the fixtures (tools/portability-apps.sh); needs llvm-objdump.
# Run `make build` first.
test-portability: ## Run the portability matrix on 12 distributions (podman, dist/fsbpyr)
	rm -rf dist/portability-apps
	tools/portability-apps.sh $(BIN) dist/portability-apps $(PYTHON_MINOR)
	$(TESTENV) FSBPYR_PORTABILITY_APPS="$(CURDIR)/dist/portability-apps" \
		$(UVRUN) pytest tests/e2e/test_portability.py

# The EL8 test mode (FSBPYR_TEST_GLIBC=2.28, tests/support/baseline.py): the tests that build and
# run executables use the glibc 2.28 stub, built here from Rocky Linux 8.10's sysroot, and every
# `fsbpyr build` of a test is a `--glibc 2.28` one. What differs between an EL8 build and a
# default one is the stub, so these are the tests that run it: the launcher's (edges, due, slots,
# gc, restore, find_object), the _fsbpyr contract (runtime_app, imports, stub_hardening, cache
# root, streaming) and the build of apps with natives from manylinux_2_28 wheels (compat,
# natives). tests/e2e/test_el8.py compares an EL8 build with a default one, so it runs with
# both runtimes, not in the mode. full-suite.yml does not run any of it; el8.yml, which publishes
# the EL8 stubs in the companion release el8-vX.Y.Z, does.
EL8_TESTS := tests/e2e/test_runtime_app.py tests/e2e/test_imports.py tests/e2e/test_stub_hardening.py \
	tests/e2e/test_edges.py tests/e2e/test_cache_root.py tests/e2e/test_gc.py tests/e2e/test_slots.py \
	tests/e2e/test_due.py tests/e2e/test_restore.py tests/e2e/test_find_object.py \
	tests/e2e/test_streaming.py tests/e2e/test_stream_windows.py tests/e2e/test_unbuffered.py \
	tests/e2e/test_digests.py tests/e2e/test_compat.py tests/e2e/test_natives.py \
	tests/unit/test_test_modes.py

test-el8-stub: ## Run the stub-dependent tests with the glibc 2.28 (EL8) stub, and tests/e2e/test_el8.py, in JOBS processes (PYTHON=3.X)
	$(TESTENV) $(CONTAINED) env FSBPYR_TEST_GLIBC=2.28 $(UVRUN) pytest -n $(JOBS) --dist loadfile -m "not podman" $(EL8_TESTS)
	$(TESTENV) $(CONTAINED) $(UVRUN) pytest tests/e2e/test_el8.py

# The portability matrix (podman) with apps built `--glibc 2.28`: the 12 images and, only in this
# mode, rockylinux:8 and almalinux:8. The apps are built by the tests with the EL8 runtime (as
# without FSBPYR_PORTABILITY_APPS); el8.yml builds them with tools/portability-apps.sh instead,
# with the release's fsbpyr and the stub it has just linked.
test-el8-portability: ## Run the portability matrix with apps built for glibc 2.28, on the 12 distributions and rockylinux:8, almalinux:8 (podman; PYTHON=3.X)
	$(TESTENV) $(CONTAINED) env FSBPYR_TEST_GLIBC=2.28 \
		$(UVRUN) pytest tests/e2e/test_portability.py

test-el8: test-el8-stub test-el8-portability ## Run the EL8 (glibc 2.28) test mode and its portability matrix: what el8.yml runs, without the corpus subset and the VM

# aarch64 apps in a QEMU system VM (tools/vm-smoke.py) whose kernel has 64 KiB pages (EL8's),
# under TCG: no KVM, about a minute per boot. fsbpyr builds for
# its own host, so the apps are aarch64 executables you built on one: VM_ARGS="--app dist/hello
# --run 'hello => hello'" (see the script's header). Without VM_ARGS only the boot is checked.
# Needs qemu-system-aarch64, which is yours to install; the kernels and root file systems it
# fetches are pinned in tools/vm-pins.toml and kept in FSBPYR_TEST_DOWNLOADS. 16K-page kernels are
# not tested (owner decision 2026-10-27).
test-vm: ## Run aarch64 apps in a QEMU VM with a 64K-page kernel (qemu-system-aarch64; VM_ARGS=...)
	$(SCRATCHENV) $(CONTAINED) $(UVRUN) python tools/vm-smoke.py $(VM_ARGS)

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

# After `make release`, once the release is published: checks it as its users get it
# (tools/release/check-release.sh says what), in a directory under TESTTMP and in the tests'
# cgroup; it changes nothing outside them but uv's cache.
release-check: ## Check the published release (VERSION=X.Y.Z, default the latest): assets, attestations, EL8 stubs, installer, update
	@$(SCRATCHENV) $(CONTAINED) bash tools/release/check-release.sh$(if $(filter command line,$(origin VERSION)), '$(VERSION)')

# --- Wiki ---

# The wiki's pages are wiki/ here, edited with the code they describe; the GitHub and GitLab
# wikis are their published copy (tools/wiki/publish.py says how). wiki-lint checks the pages
# (the tests do too); wiki-build builds the wiki of this checkout into dist/wiki/site to look at.
# wiki-publish, run by an admin once a release is out, builds the wiki of the release tag
# (VERSION=X.Y.Z, default the newest) with its PDFs and drawn diagrams and pushes it to both
# wikis; it stops if either was changed by hand (FORCE=1 replaces such changes), and DRY_RUN=1
# stops before pushing. Building needs Graphviz, rsvg-convert, pandoc 3 and LuaLaTeX
# (tools/wiki/pdf/check-toolchain.sh names what is missing). Only a VERSION, FORCE=1 and
# DRY_RUN=1 given on the make command line count: an exported FORCE never replaces anything.
wiki_flag = $(and $(filter command line,$(origin $(1))),$(filter 1,$($(1))))
wiki-lint: ## Check the wiki pages in wiki/: links, anchors, images, repository paths and lines
	@python3 -I tools/wiki/lint.py .

wiki-build: ## Build the wiki of this checkout into dist/wiki/site (pages, diagrams, PDFs) to look at
	@python3 -I tools/wiki/publish.py build

wiki-publish: ## Publish the wiki of release VERSION=X.Y.Z (default the newest) to the GitHub and GitLab wikis
	@python3 -I tools/wiki/publish.py publish$(if $(filter command line,$(origin VERSION)), --version '$(VERSION)')$(if $(call wiki_flag,FORCE), --force)$(if $(call wiki_flag,DRY_RUN), --dry-run)

clean: ## Remove dist/
	rm -rf dist
