# The wiki's tools: PDF copies of its two guides, its diagrams, and a check of its links.
#
# The top-level Makefile runs this file in a checkout of the wiki (make -C WIKI -f .../Makefile:
# `make wiki-pdf`, `make wiki-diagrams`, `make wiki-lint`), so the pages, the images and git
# are the current directory's. The wiki holds documents only; what makes them is here.
#
#   pdf       fsbpyr-User-Guide-<version>.pdf and fsbpyr-Developer-Guide-<version>.pdf, written
#             into the wiki checkout to be committed there
#   diagrams  images/*.svg of the wiki from diagrams/*.dot here and the release's import graphs
#   lint      the pages' wiki links, images, anchors and repository links (at the release's tag)
#
# The wiki is the source. _Sidebar.md gives each guide's pages and their order (pdf/wiki.lua);
# _Footer.md gives the fsbpyr version the wiki describes; the title page names the wiki commit
# that last changed the guide. A PDF is rebuilt when a page of its guide, an image, the
# sidebar, the footer or a file of pdf/ changes.

TOOLS := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
REPO  := $(abspath $(TOOLS)/../..)
# Where the rendered title pages and the release tree go: dist/ of the repository.
BUILD ?= $(REPO)/dist/wiki

PDF_DIR       := $(TOOLS)/pdf
PDF_TEMPLATES := $(addprefix $(PDF_DIR)/,header.tex titlepage.tex wiki.lua tables.lua)
IMAGES        := $(wildcard images/*.svg)

# The version the wiki describes, from the footer every page shows ("Describes fsbpyr X.Y.Z").
VERSION := $(shell sed -n 's/^Describes fsbpyr \([0-9][0-9.]*\) .*/\1/p' _Footer.md)

# The release the wiki describes, as a tree: the import graphs are drawn from its src/, and the
# lint checks repository links against it. Taken from this repository's tag.
TREE := $(BUILD)/v$(VERSION)

# A guide: its file name part, the sidebar heading that lists its pages, and the file name
# prefix of those pages.
GUIDES                  := User-Guide Developer-Guide
SIDEBAR_User-Guide      := User Guide
PREFIX_User-Guide       := User-
SIDEBAR_Developer-Guide := Developer Guide
PREFIX_Developer-Guide  := Dev-
PDF := $(foreach g,$(GUIDES),fsbpyr-$(g)-$(VERSION).pdf)

# What a guide is made from in the wiki, and the newest commit that changed it. The title
# page names that commit, not HEAD: a commit that adds the built PDFs, or one that changes
# only the other guide or Home, changes none of these and leaves the PDF as it is.
# Uncommitted changes are marked: the title page must not name a commit that does not hold
# what is printed. Dates inside the PDF (creation date, \today) are that commit's
# (SOURCE_DATE_EPOCH), so that the same commit gives the same file.
guide_sources = _Sidebar.md _Footer.md '$(PREFIX_$(1))*.md' images
guide_commit  = $(shell git log -1 --format=%h --abbrev=12 -- $(call guide_sources,$(1)))$(if $(shell git status --porcelain -- $(call guide_sources,$(1))),-modified)
guide_date    = $(shell git log -1 --format=%cs -- $(call guide_sources,$(1)))
guide_epoch   = $(shell git log -1 --format=%ct -- $(call guide_sources,$(1)))

PANDOC_OPTS := --from=gfm --pdf-engine=lualatex --toc --toc-depth=2 \
               --number-sections -V secnumdepth=2 \
               --resource-path=. \
               -L $(PDF_DIR)/wiki.lua -L $(PDF_DIR)/tables.lua \
               -H $(PDF_DIR)/header.tex -H $(PDF_DIR)/titlepage.tex \
               -V documentclass=scrartcl -V papersize=a4 \
               -V geometry:"top=2.4cm,bottom=2.2cm,left=2cm,right=2cm" \
               -V lang=en-GB -V fontsize=10pt \
               -V mainfont="DejaVu Serif"     -V mainfontoptions="Scale=0.90" \
               -V sansfont="DejaVu Sans"      -V sansfontoptions="Scale=0.90" \
               -V monofont="DejaVu Sans Mono" -V monofontoptions="Scale=0.78" \
               -V colorlinks=true -V linkcolor=black -V urlcolor=black -V toccolor=black \
               --highlight-style=tango

.PHONY: pdf diagrams lint FORCE

pdf: $(PDF)

# One rule per guide: each has its own pages, sidebar heading, commit and title page.
# The title page is rendered from its template with version, commit and date. It is rewritten
# only when that text changes, so running make again rebuilds nothing; a commit that changes
# the guide changes the title page and rebuilds its PDF.
define GUIDE_RULE
$(BUILD)/title-$(1).tex: $(PDF_DIR)/title-$(1).tex FORCE
	@mkdir -p $(BUILD)
	@test -n "$(VERSION)" || { echo "_Footer.md names no fsbpyr version" >&2; exit 1; }
	@sed -e 's/@VERSION@/$(VERSION)/g' -e 's/@COMMIT@/$(call guide_commit,$(1))/g' \
	    -e 's/@DATE@/$(call guide_date,$(1))/g' $$< > $$@.new
	@cmp -s $$@.new $$@ && rm $$@.new || mv $$@.new $$@

fsbpyr-$(1)-$(VERSION).pdf: _Sidebar.md _Footer.md $(wildcard $(PREFIX_$(1))*.md) $(IMAGES) \
        $(PDF_TEMPLATES) $(BUILD)/title-$(1).tex
	@$(PDF_DIR)/check-toolchain.sh >/dev/null
	SOURCE_DATE_EPOCH=$(call guide_epoch,$(1)) FORCE_SOURCE_DATE=1 \
	    pandoc _Sidebar.md -o $$@ $(PANDOC_OPTS) -B $(BUILD)/title-$(1).tex \
	    -M guide="$(SIDEBAR_$(1))" -M prefix=$(PREFIX_$(1))
	@echo "$$@ ($$$$(pdfinfo $$@ | awk '/^Pages/{print $$$$2}') pages)"
endef
$(foreach g,$(GUIDES),$(eval $(call GUIDE_RULE,$(g))))

$(TREE)/src/fsbpyr/__init__.py:
	@test -n "$(VERSION)" || { echo "_Footer.md names no fsbpyr version" >&2; exit 1; }
	@git -C $(REPO) rev-parse -q --verify 'v$(VERSION)^{commit}' >/dev/null || \
	    { echo "no tag v$(VERSION) in $(REPO): git fetch --tags" >&2; exit 1; }
	rm -rf $(TREE) && mkdir -p $(TREE)
	git -C $(REPO) archive v$(VERSION) | tar -x -C $(TREE)

diagrams: $(TREE)/src/fsbpyr/__init__.py
	sh $(TOOLS)/render.sh $(TREE)

lint: $(TREE)/src/fsbpyr/__init__.py
	python3 -I $(TOOLS)/lint.py . $(TREE)

FORCE:
