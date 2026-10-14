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

UV  := uv
BIN := dist/fsbpyr
# The man page and the completions come from the built executable (one source, D40).
DOCS := dist/share/fsbpyr.1 dist/share/fsbpyr.bash dist/share/_fsbpyr dist/share/fsbpyr.fish
# What the executable is built from: rebuilt when any of it changes.
SOURCES := $(shell git ls-files src pyproject.toml uv.lock 2>/dev/null)

.PHONY: help bootstrap build install uninstall test check release clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

# --- Setup ---

# The locked environment (with the dev group), then the runtime that `fsbpyr build` links
# executables against: it needs clang and lld 22 or newer (apt.llvm.org) and network access,
# and is cached, so a second run only checks it is there.
bootstrap: ## Set up .venv from uv.lock and build the Python runtime (needs clang/lld >= 22)
	$(UV) sync --locked
	$(UV) run --locked fsbpyr runtime build

# --- Build ---

build: $(BIN) ## Build fsbpyr as a single executable -> dist/fsbpyr

# fsbpyr builds itself. Its --version names the vX.Y.Z tag on HEAD, else `untagged`.
$(BIN): $(SOURCES)
	$(UV) run --locked fsbpyr build . -o $@

dist/share/fsbpyr.1: $(BIN)
	@mkdir -p $(@D)
	$(BIN) man > $@.tmp && mv $@.tmp $@

dist/share/fsbpyr.bash: $(BIN)
	@mkdir -p $(@D)
	$(BIN) completion bash > $@.tmp && mv $@.tmp $@

dist/share/_fsbpyr: $(BIN)
	@mkdir -p $(@D)
	$(BIN) completion zsh > $@.tmp && mv $@.tmp $@

dist/share/fsbpyr.fish: $(BIN)
	@mkdir -p $(@D)
	$(BIN) completion fish > $@.tmp && mv $@.tmp $@

# --- Install ---

install: $(BIN) $(DOCS) ## Install fsbpyr, its man page and completions under PREFIX (default ~/.local)
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
