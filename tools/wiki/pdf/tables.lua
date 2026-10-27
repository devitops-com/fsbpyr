--[[ Compute column widths from the content and set header rows in bold.

Why at all:

The wiki pages write separator rows as `|---|---|---|`. pandoc's pipe-table reader derives
the column widths from the length of those dashes -- all the same, so all columns get the
same width. A column holding a short name then gets as much room as a column with three
lines of prose, and the narrow columns stand half empty.

Setting the dashes in proportion is pandoc's own answer, but it makes the sources unreadable
and has to be redone with every change of content. So the widths are measured here instead.

Per column:

  min  = the longest unbreakable word  (below it the cell runs into the margin)
  nat  = the longest cell              (above it more width achieves nothing)

  If the sum of the nat values fits the line, they are used and the rest is distributed in
  proportion: the table fills the text width. If it does not fit, every column first gets
  its min, and only the remaining room is distributed, in proportion to (nat - min). So no
  narrow column starves and no wide column gets more than it can fill.

Character widths are approximations. Code (`code`) is weighted separately: the monospaced
font is loaded with Scale=0.78 and runs somewhat narrower per character than the main font.

Needs pandoc 3 (pandoc.write, pandoc.Inlines, Blocks:walk), as wiki.lua does.
--]]

local stringify = pandoc.utils.stringify

--- Length in characters, not bytes. Non-ASCII characters take several bytes in UTF-8;
--- measured with `#`, columns holding them would come out too wide.
local ulen = pandoc.text.len

-- Width of the text block in characters of the table font (\small).
-- 17 cm text width, DejaVu Serif 9pt: about 95 characters. Deliberately tight -- a value
-- too large lets the estimate say "fits" where it does not.
local LINE_CHARS = 95

-- Lower bound per column in characters. Prevents columns so narrow that every word stands
-- on a line of its own.
local MIN_COLUMN = 4

-- Upper bound per column in characters before normalisation. A cell of 400 characters must
-- not pull the whole table to itself; it wraps anyway.
local MAX_COLUMN = 70

--- Estimated width of an inline tree in characters.
-- `Code` is weighted because the monospaced font is loaded with Scale=0.78; `Strong` is
-- slightly wider.
local function width(inlines, factor)
  factor = factor or 1.0
  local n = 0
  for _, el in ipairs(inlines or {}) do
    local t = el.t
    if t == 'Code' then
      n = n + ulen(el.text) * 0.92 * factor
    elseif t == 'Strong' then
      n = n + width(el.content, factor * 1.06)
    elseif t == 'Emph' or t == 'Link' or t == 'Span' or t == 'SmallCaps' then
      n = n + width(el.content, factor)
    elseif t == 'Str' then
      n = n + ulen(el.text) * factor
    elseif t == 'Space' or t == 'SoftBreak' then
      n = n + factor
    elseif t == 'LineBreak' then
      n = n + factor
    else
      n = n + ulen(stringify(el)) * factor
    end
  end
  return n
end

-- Characters at which an identifier in code may break. Exactly these places get an
-- \allowbreak in `Code` below; the two lists must agree, or the width estimate counts on
-- breaks that do not exist in the typeset text. ASCII only: Lua's character classes match
-- bytes, and a multi-byte character in one would match parts of other characters.
local CODE_BREAKS = '[/_=%.]'
local CODE_UNBREAKABLE = '[^/_=%.]+'
-- In running text the estimate breaks at hyphens only and counts every other word as
-- unbreakable: hyphenation could split it, but a column narrower than its longest word
-- reads badly even then.
local TEXT_UNBREAKABLE = '[^%-]+'

--- The longest unbreakable piece of a cell.
-- Code and running text are measured apart because they break at different places.
local function longest_word(inlines)
  local m = 0

  local function pieces(text, pattern)
    for word in text:gmatch('%S+') do
      for piece in word:gmatch(pattern) do
        local l = ulen(piece)
        if l > m then m = l end
      end
    end
  end

  local function visit(list)
    for _, el in ipairs(list or {}) do
      if el.t == 'Code' then
        pieces(el.text, CODE_UNBREAKABLE)
      elseif el.t == 'Link' then
        -- pandoc sets a link's address with \url, and the template loads the url package
        -- with the option `hyphens`: the address breaks at slashes. Without this branch it
        -- counts as one unbreakable word, and a column holding a link would get almost the
        -- whole line.
        pieces(stringify(el.content), CODE_UNBREAKABLE)
      elseif el.content then
        visit(el.content)
      else
        pieces(stringify(el), TEXT_UNBREAKABLE)
      end
    end
  end

  visit(inlines)
  return m
end

--[[ Break points in long identifiers.

Within a word LaTeX breaks only at hyphens. A path such as
`src/fsbpyr/boot/_fsbpyr_rt/native.py` is therefore unbreakable and runs over the margin of
a table column or of a justified line.

Rather than widening the column to the length of the path (the name column would then be as
wide as the explanation), the path gets break points. The identifier is split into several
Code elements with an `\allowbreak` between them. What matters is splitting into REAL Code
elements instead of a raw LaTeX block: the width estimate above keeps measuring the text,
and `\allowbreak` has no width.

The same characters count as break points in `longest_word`; the two must agree, or the width
estimate counts on breaks that do not exist in the typeset text.
--]]

-- From this length on the split pays off. Shorter identifiers fit anyway.
local CODE_BREAK_FROM = 12

function Code(el)
  if ulen(el.text) < CODE_BREAK_FROM then return nil end
  if not el.text:find(CODE_BREAKS) then return nil end

  local raw = {}
  -- Cut after every break character; the character stays with the left part.
  for piece in el.text:gmatch('[^/_=%.]*[/_=%.]?') do
    if piece ~= '' then raw[#raw + 1] = piece end
  end

  -- Merge very short pieces with the next one. Otherwise a leading slash gives a break
  -- point after the first character, and a single "/" is left at the end of a line.
  local parts = {}
  local buffer = ''
  for _, piece in ipairs(raw) do
    buffer = buffer .. piece
    if ulen(buffer) >= 3 then
      parts[#parts + 1] = buffer
      buffer = ''
    end
  end
  if buffer ~= '' then
    if #parts > 0 then
      parts[#parts] = parts[#parts] .. buffer
    else
      parts[1] = buffer
    end
  end
  if #parts < 2 then return nil end

  local out = pandoc.Inlines({})
  for i, part in ipairs(parts) do
    out:insert(pandoc.Code(part, el.attr))
    if i < #parts then out:insert(pandoc.RawInline('latex', '\\allowbreak')) end
  end
  return out
end

--- Collect the cells of every column (head, bodies, foot).
local function column_inlines(tbl, ncols)
  local columns = {}
  for i = 1, ncols do columns[i] = {} end

  local function add_rows(rows)
    for _, row in ipairs(rows or {}) do
      local i = 1
      for _, cell in ipairs(row.cells) do
        if i <= ncols then
          -- A cell holds a list of blocks; its text is enough for the measurement.
          local inlines = pandoc.utils.blocks_to_inlines(cell.contents or {})
          table.insert(columns[i], inlines)
        end
        i = i + (cell.col_span or 1)
      end
    end
  end

  add_rows(tbl.head.rows)
  for _, body in ipairs(tbl.bodies or {}) do
    add_rows(body.head)
    add_rows(body.body)
  end
  add_rows(tbl.foot.rows)
  return columns
end

function Table(tbl)
  local ncols = #tbl.colspecs
  if ncols == 0 then return nil end

  -- Header row in bold. pandoc sets it in the main font otherwise, and only the rule tells
  -- head and content apart.
  for _, row in ipairs(tbl.head.rows) do
    for _, cell in ipairs(row.cells) do
      cell.contents = cell.contents:walk({
        Plain = function(p) return pandoc.Plain { pandoc.Strong(p.content) } end,
        Para  = function(p) return pandoc.Para { pandoc.Strong(p.content) } end,
      })
    end
  end

  local columns = column_inlines(tbl, ncols)

  local min_width, weight = {}, {}
  for i = 1, ncols do
    local longest, widest, total, cells = MIN_COLUMN, MIN_COLUMN, 0, 0
    for _, inlines in ipairs(columns[i]) do
      local w = width(inlines)
      if w > widest then widest = w end
      total = total + w
      cells = cells + 1
      local word = longest_word(inlines)
      if word > longest then longest = word end
    end
    -- A single long word must not widen the column beyond the line; an overrun of a few
    -- millimetres is easier to bear than an unreadable table.
    -- Two characters extra: the estimate counts average character widths, and a bold word
    -- in capitals is wider than that.
    min_width[i] = math.min(longest + 2, LINE_CHARS / ncols * 1.25)

    -- The weight has two parts: the mean says how much text the column carries overall,
    -- the (capped) longest cell how tall a single row can get. The mean alone lets a column
    -- with one long cell starve; the longest cell alone treats a column with one outlier
    -- like a full one, so that a column of one-line entries takes nearly half the width
    -- while the column next to it wraps onto five lines.
    local mean = cells > 0 and total / cells or MIN_COLUMN
    weight[i] = math.max(min_width[i], 0.65 * mean + 0.35 * math.min(widest, MAX_COLUMN))
  end

  -- Distribute in proportion to the weight while keeping the minimum widths: columns below
  -- their minimum are fixed at it, and the rest is divided again among the others, until
  -- nothing changes.
  local w = {}
  local fixed, rest_width, rest_weight = {}, LINE_CHARS, 0
  for i = 1, ncols do rest_weight = rest_weight + weight[i] end

  for _ = 1, ncols do
    local changed = false
    for i = 1, ncols do
      if not fixed[i] then
        local b = rest_width * weight[i] / rest_weight
        if b < min_width[i] then
          fixed[i] = min_width[i]
          rest_width = rest_width - min_width[i]
          rest_weight = rest_weight - weight[i]
          changed = true
        end
      end
    end
    if not changed then break end
  end

  if rest_width <= 0 or rest_weight <= 0 then
    -- The minimum widths already fill the line: then in proportion to them, even if the
    -- table gets tight.
    local s = 0
    for i = 1, ncols do s = s + min_width[i] end
    for i = 1, ncols do w[i] = min_width[i] / s end
  else
    for i = 1, ncols do
      w[i] = (fixed[i] or (rest_width * weight[i] / rest_weight)) / LINE_CHARS
    end
  end

  -- Normalise: the sum must be 1, or the LaTeX writer sets a table narrower or wider than
  -- the text block.
  local s = 0
  for i = 1, ncols do s = s + w[i] end
  local specs = {}
  for i = 1, ncols do
    specs[i] = { tbl.colspecs[i][1], w[i] / s }
  end
  tbl.colspecs = specs
  return tbl
end

--[[ Short tables as one unbreakable box instead of a longtable.

Why: longtable leaves an orphaned table head at page breaks. At the top of a page stands the
header row between two rules, and below it the section begins with the same table, then
complete. The cause is the output routine: if the page overflows after the table has begun,
TeX chooses the break BEFORE the heading; longtable, however, puts its repeated head at the
top of the next page -- in front of the material that moved. The result looks like an empty
table.

Tried and without effect: more LaTeX runs (the error is stable from the second run on),
\needspace before the table, \filbreak, extra penalties before the table (makes it worse),
\flushbottom instead of \raggedbottom, switching off fancyhdr, mdframed, font size changes
and paragraph penalties.

So the other way: a table that fits on one page does not need longtable. As a `tabular` in a
`table[H]` it is one unbreakable box; when it no longer fits, the page builder moves it to
the next page whole. No repeated head, so no orphaned one.

Tables longer than a page stay longtables -- they MUST break. With them the error hardly
occurs, because the first page always holds content rows.

Tables inside a block quote stay longtables as well: header.tex redefines the quote as
mdframed, and a float inside mdframed is a source of errors not worth the conversion.
--]]

-- Rows a page holds (A4, text height 24.3 cm, table font \small with \arraystretch 1.18):
-- about 53. The threshold is deliberately two thirds of that -- the estimate below knows
-- neither hyphenation nor the real character widths and may be wrong without anything
-- running over.
local MAX_LINES = 34

--- Estimated number of lines of the typeset table.
local function line_count(tbl, widths)
  local n = 3  -- rules at the top, middle and bottom
  if #tbl.caption.long > 0 then n = n + 2 end

  local function rows(list)
    for _, row in ipairs(list or {}) do
      local height = 1
      local i = 1
      for _, cell in ipairs(row.cells) do
        if i <= #widths then
          local inlines = pandoc.utils.blocks_to_inlines(cell.contents or {})
          local room = math.max(widths[i] * LINE_CHARS, 1)
          local lines = math.ceil(width(inlines) / room)
          if lines > height then height = lines end
        end
        i = i + (cell.col_span or 1)
      end
      n = n + height
    end
  end

  rows(tbl.head.rows)
  for _, body in ipairs(tbl.bodies or {}) do
    rows(body.head); rows(body.body)
  end
  rows(tbl.foot.rows)
  return n
end

--- Rewrite longtable source into `table[H]` + `tabular`.
-- Returns nil when the source does not have the expected form. Then it stays a longtable --
-- better an orphaned head than a broken table, should a later pandoc write it differently.
local function as_box(ltx)
  if not ltx:find('\\begin{longtable}', 1, true) then return nil end

  -- Take out the caption; it is set above the table.
  local caption = ltx:match('\n\\caption(%b{})\\tabularnewline\n')
  if caption then
    ltx = ltx:gsub('\n\\caption%b{}\\tabularnewline\n', '\n', 1)
  end
  -- Remove the repeated head: everything from \endfirsthead to \endhead.
  if ltx:find('\\endfirsthead', 1, true) then
    local before = ltx
    ltx = ltx:gsub('\\endfirsthead.-\\endhead\n', '', 1)
    if ltx == before then return nil end
  else
    ltx = ltx:gsub('\\endhead\n', '', 1)
  end
  -- The closing rule. pandoc writes it with \endlastfoot BEFORE the data rows; it has to
  -- move to the end.
  if ltx:find('\\endlastfoot', 1, true) then
    local foot = ltx:match('(\\bottomrule\\noalign{}\n)\\endlastfoot\n')
    if not foot then return nil end
    ltx = ltx:gsub('\\bottomrule\\noalign{}\n\\endlastfoot\n', '', 1)
    ltx = ltx:gsub('\\end{longtable}', foot .. '\\end{tabular}', 1)
  else
    if not ltx:match('\\bottomrule[^\n]*\n\\end{longtable}') then return nil end
    ltx = ltx:gsub('\\end{longtable}', '\\end{tabular}', 1)
  end
  ltx = ltx:gsub('\\begin{longtable}%[%]', '\\begin{tabular}', 1)

  return table.concat {
    '\\begin{table}[H]\n\\centering\n',
    caption and ('\\caption' .. caption .. '\n') or '',
    ltx,
    '\n\\end{table}',
  }
end

-- Mark the tables inside block quotes beforehand; they stay longtables.
local function mark_quoted_tables(blocks)
  return blocks:walk({
    BlockQuote = function(bq)
      bq.content = bq.content:walk({
        Table = function(t)
          t.attr.classes:insert('keep-longtable')
          return t
        end,
      })
      return bq
    end,
  })
end

--- Does the table qualify as a box?
-- Excluded are tables in block quotes and tables longer than a page; both are explained
-- above.
local function is_box_candidate(tbl)
  if tbl.attr.classes:includes('keep-longtable') then return false end
  local widths = {}
  for i, cs in ipairs(tbl.colspecs) do widths[i] = cs[2] or (1 / #tbl.colspecs) end
  return line_count(tbl, widths) <= MAX_LINES
end

function Pandoc(doc)
  if FORMAT ~= 'latex' and FORMAT ~= 'beamer' then return nil end

  local blocks = mark_quoted_tables(doc.blocks)

  -- Two passes: first collect the tables that become boxes, then replace them in the same
  -- order. The traversal is deterministic, so the i-th LaTeX source belongs to the i-th
  -- table.
  local candidates = {}
  blocks:walk({
    Table = function(tbl)
      if is_box_candidate(tbl) then candidates[#candidates + 1] = tbl end
    end,
  })
  if #candidates == 0 then return nil end

  local ltx = {}
  for i, tbl in ipairs(candidates) do
    ltx[i] = pandoc.write(pandoc.Pandoc({ tbl }), 'latex')
  end

  local i = 0
  doc.blocks = blocks:walk({
    Table = function(tbl)
      if not is_box_candidate(tbl) then return nil end
      i = i + 1
      local box = as_box(ltx[i])
      if not box then return nil end
      return pandoc.RawBlock('latex', box)
    end,
  })
  return doc
end
