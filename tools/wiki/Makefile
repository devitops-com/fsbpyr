# The PDF copies of the wiki's two guides: fsbpyr-User-Guide.pdf and
# fsbpyr-Developer-Guide.pdf.
#
# build.sh runs this file in the directory of the built wiki (site.py's output, with the drawn
# images), which is where the PDFs go:
#   make -C SITE -f tools/wiki/Makefile VERSION=X.Y.Z SOURCE='...' DATE=YYYY-MM-DD \
#        SOURCE_DATE_EPOCH=<seconds>
# _Sidebar.md gives each guide's pages and their order (pdf/wiki.lua). The title page names the
# release and the repository commit the wiki was built from (SOURCE) and that commit's date.
# Dates inside the PDF (creation date, \today) are SOURCE_DATE_EPOCH's, so that the same
# commit gives the same file.

TOOLS   := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
PDF_DIR := $(TOOLS)/pdf
BUILD   := .pdf-build

# A guide: its file name part, the sidebar heading that lists its pages, and the file name
# prefix of those pages.
GUIDES                  := User-Guide Developer-Guide
SIDEBAR_User-Guide      := User Guide
PREFIX_User-Guide       := User-
SIDEBAR_Developer-Guide := Developer Guide
PREFIX_Developer-Guide  := Dev-

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

.PHONY: pdf

pdf: $(foreach g,$(GUIDES),fsbpyr-$(g).pdf)

# One rule per guide: its pages, sidebar heading and title page.
define GUIDE_RULE
fsbpyr-$(1).pdf: _Sidebar.md $(wildcard $(PREFIX_$(1))*.md) $(wildcard images/*) \
        $(wildcard $(PDF_DIR)/*)
	@test -n "$(VERSION)" -a -n "$(SOURCE)" -a -n "$(DATE)" -a -n "$(SOURCE_DATE_EPOCH)" || \
	    { echo "tools/wiki/Makefile needs VERSION, SOURCE, DATE, SOURCE_DATE_EPOCH" >&2; exit 2; }
	@$(PDF_DIR)/check-toolchain.sh >/dev/null
	@mkdir -p $(BUILD)
	@sed -e 's/@VERSION@/$(VERSION)/g' -e 's|@SOURCE@|$(SOURCE)|g' -e 's/@DATE@/$(DATE)/g' \
	    $(PDF_DIR)/title-$(1).tex > $(BUILD)/title-$(1).tex
	SOURCE_DATE_EPOCH=$(SOURCE_DATE_EPOCH) FORCE_SOURCE_DATE=1 \
	    pandoc _Sidebar.md -o $$@ $(PANDOC_OPTS) -B $(BUILD)/title-$(1).tex \
	    -M guide="$(SIDEBAR_$(1))" -M prefix=$(PREFIX_$(1))
	@echo "$$@ ($$$$(pdfinfo $$@ | awk '/^Pages/{print $$$$2}') pages)"
endef
$(foreach g,$(GUIDES),$(eval $(call GUIDE_RULE,$(g))))
