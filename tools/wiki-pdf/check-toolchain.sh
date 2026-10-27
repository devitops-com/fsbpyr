#!/usr/bin/env bash
#
# Check whether the toolchain for "make wiki-pdf" is complete.
#
# Why: whatever is missing here otherwise surfaces in the middle of the LaTeX run, as
#
#   ! LaTeX Error: File `fancyhdr.sty' not found.
#
# -- with a line number from a file that no longer exists once the run is over, and with no
# hint at all about which package supplies it.
#
# What is checked: the programs, the packages header.tex loads, the packages of pandoc's
# template that were found missing on RHEL 9.8 (framed, selnolig, ctablestack), fvextra and the
# fonts. Not everything the template loads (KOMA-Script, fontspec, unicode-math, babel,
# hyperref, ...): on a TeX installation without those the run still stops in LaTeX.
#
# An error, not a warning: without these pieces no PDF is produced.
#
# The dnf names were measured on RHEL 9.8 (dnf provides, then installed). The apt names are
# the Debian equivalents of the same TeX packages.

set -uo pipefail

if command -v dnf >/dev/null 2>&1; then
  PM=dnf
  INSTALL="sudo dnf install"
else
  PM=apt
  INSTALL="sudo apt install"
fi

missing=()    # plain text: what is missing
packages=()   # what to install for it
notes=()      # special cases with no package

want() {      # $1 description  $2 dnf package  $3 apt package
  missing+=("$1")
  local p
  if [[ ${PM} == dnf ]]; then p=$2; else p=$3; fi
  [[ -n ${p} ]] && packages+=("${p}")
  return 0
}

prog() { command -v "$1" >/dev/null 2>&1 || want "$1 (program)" "$2" "$3"; }
sty()  { kpsewhich "$1.sty" >/dev/null 2>&1 || want "$1.sty" "$2" "$3"; }

prog lualatex      texlive-luatex      texlive-luatex
prog rsvg-convert  librsvg2-tools      librsvg2-bin
prog pandoc        pandoc              pandoc
prog pdfinfo       poppler-utils       poppler-utils
prog git           git                 git

# wiki.lua and tables.lua use pandoc 3's Lua API (pandoc.Figure, Blocks:walk,
# pandoc.write, pandoc.system.list_directory).
if command -v pandoc >/dev/null 2>&1; then
  version=$(pandoc --version | sed -n '1s/^pandoc[^ ]* //p')
  if [[ ${version%%.*} -lt 3 ]]; then
    missing+=("pandoc 3 or newer (found ${version})")
    notes+=("pandoc 3 from https://github.com/jgm/pandoc/releases (a .deb, or the static
      Linux tarball) if the distribution's pandoc is older.")
  fi
fi

if command -v kpsewhich >/dev/null 2>&1; then
  # From header.tex
  sty fancyhdr   texlive-fancyhdr    texlive-latex-extra
  sty etoolbox   texlive-etoolbox    texlive-latex-recommended
  sty longtable  texlive-tools       texlive-latex-recommended
  sty array      texlive-tools       texlive-latex-recommended
  sty calc       texlive-tools       texlive-latex-recommended
  sty booktabs   texlive-booktabs    texlive-latex-extra
  sty float      texlive-float       texlive-latex-extra
  sty graphicx   texlive-graphics    texlive-latex-recommended
  sty caption    texlive-caption     texlive-latex-recommended
  sty mdframed   texlive-mdframed    texlive-latex-extra
  # mdframed loads needspace.
  sty needspace  texlive-needspace   texlive-latex-extra
  sty xcolor     texlive-xcolor      texlive-latex-recommended
  # From pandoc itself: framed via --highlight-style, selnolig under LuaTeX,
  # ctablestack via luatexbase behind selnolig.
  sty framed       texlive-framed       texlive-latex-extra
  sty selnolig     texlive-selnolig     texlive-latex-extra
  sty ctablestack  texlive-ctablestack  texlive-latex-extra

  # fvextra wraps over-long code lines (breaklines, breakanywhere in header.tex).
  # The RHEL repositories do not carry it.
  if ! kpsewhich fvextra.sty >/dev/null 2>&1; then
    if [[ ${PM} == dnf ]]; then
      missing+=("fvextra.sty (in no RHEL package)")
      notes+=("fvextra from CTAN into your own TeX tree:
      cd \"\${TMPDIR:-\$HOME/tmp}\" && curl -sSLO https://mirrors.ctan.org/macros/latex/contrib/fvextra.zip
      unzip -q fvextra.zip && cd fvextra && pdflatex fvextra.ins
      mkdir -p ~/texmf/tex/latex/fvextra && cp fvextra.sty ~/texmf/tex/latex/fvextra/")
    else
      want "fvextra.sty" "" texlive-latex-extra
    fi
  fi
fi

# Fonts. The typesetting is set to DejaVu (PANDOC_OPTS in the Makefile); if one of the three
# is missing, fontspec stops the run.
if command -v fc-list >/dev/null 2>&1; then
  for font in "DejaVu Serif:dejavu-serif-fonts" \
                 "DejaVu Sans:dejavu-sans-fonts" \
                 "DejaVu Sans Mono:dejavu-sans-mono-fonts"; do
    name=${font%%:*}
    package=${font##*:}
    # Not grep -q: it may exit before fc-list is done, and pipefail then reports a failure.
    fc-list : family | tr ',' '\n' | grep -xF "${name}" >/dev/null \
      || want "font ${name}" "${package}" fonts-dejavu
  done
fi

if [[ ${#missing[@]} -eq 0 ]]; then
  echo "toolchain for make wiki-pdf is complete"
  exit 0
fi

{
  echo "make wiki-pdf cannot run -- missing:"
  printf '  %s\n' "${missing[@]}"
  if [[ ${#packages[@]} -gt 0 ]]; then
    echo
    echo "  Fix (once, as root):"
    echo "      ${INSTALL} $(printf '%s\n' "${packages[@]}" | sort -u | tr '\n' ' ')"
  fi
  for h in "${notes[@]:-}"; do
    [[ -n ${h} ]] || continue
    echo
    echo "  ${h}"
  done
} >&2
exit 2
