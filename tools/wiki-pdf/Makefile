# PDF copies of the wiki's two guides, made from the wiki pages themselves.
#
# `make wiki-pdf` (the top-level Makefile) runs this file in a checkout of the wiki
# (make -C WIKI -f .../Makefile), so the pages, the images and git are the current
# directory's. It writes fsbpyr-User-Guide-<version>.pdf and fsbpyr-Developer-Guide-<version>.pdf
# there, to be committed to the wiki.
#
# The wiki is the source. _Sidebar.md gives each guide's pages and their order (wiki.lua);
# _Footer.md gives the fsbpyr version the wiki describes; the title page names the wiki commit
# that last changed the guide. A PDF is rebuilt when a page of its guide, an image, the
# sidebar, the footer or a file of this directory changes.

TOOLS := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
# Where the rendered title pages go; the top-level Makefile passes dist/wiki-pdf.
BUILD ?= $(TOOLS)/build

PDF_TEMPLATES := $(TOOLS)/header.tex $(TOOLS)/titlepage.tex $(TOOLS)/wiki.lua $(TOOLS)/tables.lua
IMAGES        := $(wildcard images/*.svg)

# The version the wiki describes, from the footer every page shows ("Describes fsbpyr X.Y.Z").
VERSION := $(shell sed -n 's/^Describes fsbpyr \([0-9][0-9.]*\) .*/\1/p' _Footer.md)

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
               -L $(TOOLS)/wiki.lua -L $(TOOLS)/tables.lua \
               -H $(TOOLS)/header.tex -H $(TOOLS)/titlepage.tex \
               -V documentclass=scrartcl -V papersize=a4 \
               -V geometry:"top=2.4cm,bottom=2.2cm,left=2cm,right=2cm" \
               -V lang=en-GB -V fontsize=10pt \
               -V mainfont="DejaVu Serif"     -V mainfontoptions="Scale=0.90" \
               -V sansfont="DejaVu Sans"      -V sansfontoptions="Scale=0.90" \
               -V monofont="DejaVu Sans Mono" -V monofontoptions="Scale=0.78" \
               -V colorlinks=true -V linkcolor=black -V urlcolor=black -V toccolor=black \
               --highlight-style=tango

.PHONY: pdf clean FORCE

pdf: $(PDF)

# One rule per guide: each has its own pages, sidebar heading, commit and title page.
# The title page is rendered from its template with version, commit and date. It is rewritten
# only when that text changes, so running make again rebuilds nothing; a commit that changes
# the guide changes the title page and rebuilds its PDF.
define GUIDE_RULE
$(BUILD)/title-$(1).tex: $(TOOLS)/title-$(1).tex FORCE
	@mkdir -p $(BUILD)
	@test -n "$(VERSION)" || { echo "_Footer.md names no fsbpyr version" >&2; exit 1; }
	@sed -e 's/@VERSION@/$(VERSION)/g' -e 's/@COMMIT@/$(call guide_commit,$(1))/g' \
	    -e 's/@DATE@/$(call guide_date,$(1))/g' $$< > $$@.new
	@cmp -s $$@.new $$@ && rm $$@.new || mv $$@.new $$@

fsbpyr-$(1)-$(VERSION).pdf: _Sidebar.md _Footer.md $(wildcard $(PREFIX_$(1))*.md) $(IMAGES) \
        $(PDF_TEMPLATES) $(BUILD)/title-$(1).tex
	@$(TOOLS)/check-toolchain.sh >/dev/null
	SOURCE_DATE_EPOCH=$(call guide_epoch,$(1)) FORCE_SOURCE_DATE=1 \
	    pandoc _Sidebar.md -o $$@ $(PANDOC_OPTS) -B $(BUILD)/title-$(1).tex \
	    -M guide="$(SIDEBAR_$(1))" -M prefix=$(PREFIX_$(1))
	@echo "$$@ ($$$$(pdfinfo $$@ | awk '/^Pages/{print $$$$2}') pages)"
endef
$(foreach g,$(GUIDES),$(eval $(call GUIDE_RULE,$(g))))

clean:
	rm -f fsbpyr-*-Guide-*.pdf
	rm -rf $(BUILD)

FORCE:
