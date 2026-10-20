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

UV  := uv
BIN := dist/fsbpyr

.PHONY: help bootstrap build install uninstall test test-ci test-container check release clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

# --- Setup ---

# Debian trixie: the runtime, fsbpyr built from source, `make install`, with the uv, Python
# and clang/lld 22 you installed (a missing one is named; nothing is installed for you).
# Anywhere else the build runs in a Debian trixie podman container (podman is yours to
# install) and the install on this host. See tools/bootstrap.sh.
bootstrap: ## From source to an installed fsbpyr: runtime, build, install
	@bash tools/bootstrap.sh

# --- Build ---

# The installed fsbpyr builds the checkout: it carries its own runtime, so no compiler is
# needed, only uv. Without an installed fsbpyr it stops: `make bootstrap` installs one. Its
# --version names the vX.Y.Z tag on HEAD, else `untagged`.
build: ## Build dist/fsbpyr with the installed fsbpyr (make bootstrap installs one)
	@bash tools/bootstrap.sh --uv
	@fsbpyr="$$(command -v $(BINDIR)/fsbpyr || command -v fsbpyr || true)"; \
	if [ -z "$$fsbpyr" ]; then \
		echo "ERROR: no fsbpyr in $(BINDIR) or on PATH; make bootstrap builds and installs one" >&2; \
		exit 1; \
	fi; \
	echo "$$fsbpyr build . -o $(BIN)"; \
	"$$fsbpyr" build . -o $(BIN)

# --- Install ---

# The man page and the completions come from the executable being installed (one source, D40).
install: ## Install dist/fsbpyr, its man page and completions under PREFIX (default ~/.local)
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

test: ## Run the test suite
	$(UV) run --locked pytest

# What CI runs before a release: unit and integration tests, real builds, executables run
# with the release launcher, and a real executable's boot imports and cache-sync contract
# (D37). Left to `make test`: the ASan/UBSan launcher variant, the portability tests (podman)
# and the other end-to-end tests. -k also matches marker names, so it keeps the release half of
# the tests marked `sanitized`. Branch coverage below pyproject.toml's fail_under fails the run;
# coverage.xml is the report CI keeps.
test-ci: ## Run the core of the test suite, as CI does
	$(UV) run --locked pytest tests/unit tests/integration tests/e2e/test_build.py \
		tests/e2e/test_runtime_app.py tests/e2e/test_imports.py \
		tests/e2e/test_stub_hardening.py \
		-k "not (sanitized and not release)" --cov --cov-report=term --cov-report=xml

# Everything but the portability tests (`podman run` inside podman), in the Containerfile's
# `test` stage: Debian trixie as `tester`, with the packages and users tools/test-setup.sh adds.
# A copy of the checkout is the suite's; FSBPYR_TESTS_REQUIRE_ALL=container fails any skip
# outside the allowlist in tests/conftest.py. Two volumes keep what a run would fetch again:
# uv's cache and Pythons, and the runtime's pinned downloads (FSBPYR_TEST_DOWNLOADS). Needs
# podman, which is yours to install; --network pasta:-4 is bootstrap's, for the same reason.
# TMPDIR is a tmpfs: a symlink target of 4,000 bytes (test_cache_root.py) fails on the
# container's overlay file system with "File name too long", and the builds are big.
test-container: ## Run the whole suite but the portability tests in a Debian trixie container (podman)
	podman build --target test -t fsbpyr-test -f Containerfile .
	podman run --rm --network pasta:-4 -v "$(CURDIR)":/checkout:ro,Z \
		-v fsbpyr-test-cache:/home/tester/.cache -v fsbpyr-test-downloads:/home/tester/downloads \
		--tmpfs /var/tmp:mode=1777 -e TMPDIR=/var/tmp \
		-e FSBPYR_TEST_DOWNLOADS=/home/tester/downloads -e FSBPYR_TESTS_REQUIRE_ALL=container \
		fsbpyr-test bash -c 'tar -C /checkout --exclude=./.venv --exclude=./dist -cf - . | tar -xf - \
			&& uv python install "$$(cat .python-version)" \
			&& uv run --locked pytest --ignore=tests/e2e/test_portability.py'

check: ## Static checks: ruff + mypy
	$(UV) run --locked ruff check
	$(UV) run --locked mypy

# --- Release ---

# VERSION=X.Y.Z sets the version in the release commit itself (no separate bump commit to push
# first); without it the current version is released. Only a VERSION given on the make command
# line counts: an exported one (another tool's) is ignored.
release: ## Cut a release: promote CHANGELOG, tag + push; the tag makes CI build, attest and publish it (VERSION=X.Y.Z: bump to it in the same commit)
	@bash tools/release/release.sh$(if $(filter command line,$(origin VERSION)), '$(VERSION)')

clean: ## Remove dist/
	rm -rf dist
