--[[ Assemble one guide of the wiki into a single document.

pandoc reads _Sidebar.md (tools/wiki/Makefile passes it as the input); this filter replaces
it with the pages of one guide. The sidebar is the table of contents of the wiki, so the PDF
has the same pages in the same order, and a page added to the wiki but not to the sidebar is
an error, not a silent omission.

Metadata, set by the Makefile:
  guide   the sidebar heading of the guide ("User Guide", "Developer Guide")
  prefix  the file name prefix of its pages ("User-", "Dev-")

Each page is read on its own with pandoc's GitHub reader, as GitHub renders it: reference
links and footnotes stay within their page, and heading identifiers are GitHub's anchors.

What changes for the PDF:
  - The page's H1 becomes a section (a new page, see header.tex) with the page name as its
    identifier; every other heading identifier gets the page name as a prefix, because the
    same heading ("Examples") occurs on several pages.
  - Wiki links into the guide become links inside the PDF; wiki links to pages outside it
    (the other guide, Home) become links to the wiki on GitHub. A link to a page or anchor
    that does not exist is an error.
  - An image alone in a paragraph becomes a numbered figure. Its alt text is the caption;
    the list of figures gets the alt text up to the first ": ", "; " or ". ". A drawn
    diagram whose graph has class="landscape" (graph [class="landscape"] in its .dot source)
    stands on a landscape page of its own (header.tex's landscapefigure).
--]]

local stringify = pandoc.utils.stringify

local WIKI = 'https://github.com/devitops-com/fsbpyr/wiki/'

local READER = 'gfm'

local errors = {}

local function fail(message)
  errors[#errors + 1] = message
end

--- The page names of the guide, in sidebar order.
local function sidebar_pages(blocks, guide)
  for i, block in ipairs(blocks) do
    if block.t == 'Para' and stringify(block) == guide then
      local list = blocks[i + 1]
      if not list or list.t ~= 'BulletList' then
        error('_Sidebar.md: no list of pages after "' .. guide .. '"')
      end
      local pages = {}
      for _, item in ipairs(list.content) do
        local link
        pandoc.walk_block(pandoc.Div(item), {
          Link = function(l) link = link or l.target end,
        })
        if not link then error('_Sidebar.md: an entry of "' .. guide .. '" has no link') end
        pages[#pages + 1] = link
      end
      return pages
    end
  end
  error('_Sidebar.md: no heading "' .. guide .. '"')
end

--- Every page file with the prefix is in the sidebar, and every sidebar entry is a file.
local function check_complete(pages, prefix)
  local listed = {}
  for _, page in ipairs(pages) do listed[page] = true end
  local present = {}
  for _, name in ipairs(pandoc.system.list_directory('.')) do
    local page = name:match('^(.+)%.md$')
    if page then present[page] = true end
    if page and page:sub(1, #prefix) == prefix and not listed[page] then
      fail(name .. ' is not in _Sidebar.md')
    end
  end
  for _, page in ipairs(pages) do
    if not present[page] then fail('_Sidebar.md names ' .. page .. ', which does not exist') end
  end
  return present
end

local function read_page(page)
  local handle = assert(io.open(page .. '.md', 'r'))
  local text = handle:read('a')
  handle:close()
  return pandoc.read(text, READER)
end

--- Give the page's headings document-wide identifiers; returns the page's anchor table
--- (GitHub anchor -> identifier in the PDF).
--- The page's first heading must be its only H1: it becomes the page's section, and a
--- heading before it or a second H1 would put text under the wrong page.
local function rename_headings(page, doc)
  local anchors = {}
  local first = true
  doc.blocks = doc.blocks:walk({
    Header = function(h)
      local anchor = h.identifier
      if first then
        if h.level ~= 1 then fail('the first heading of ' .. page .. '.md is not an H1') end
        h.identifier = page
        first = false
      else
        if h.level == 1 then
          fail(page .. '.md has a second H1 heading: ' .. stringify(h))
        end
        h.identifier = page .. '--' .. anchor
      end
      anchors[anchor] = h.identifier
      return h
    end,
  })
  if first then fail(page .. '.md has no heading') end
  return anchors
end

--- The link target in the PDF for a wiki link, or nil when it is not one. On the wiki a
--- relative target without a slash or a dot is a page name (`Page`, `Page#anchor`).
local function wiki_target(page, target, in_guide, present, anchors)
  local name, anchor
  if target:match('^#') then
    name, anchor = page, target:sub(2)
  else
    name, anchor = target:match('^([%w%-]+)#(.+)$')
    name = name or target:match('^([%w%-]+)$')
    if not name then return nil end
    if not present[name] then
      fail(page .. '.md links to the wiki page ' .. name .. ', which does not exist')
      return nil
    end
  end
  if not in_guide[name] then
    return WIKI .. name .. (anchor and ('#' .. anchor) or '')
  end
  if not anchor then return '#' .. name end
  local id = anchors[name][anchor]
  if not id then
    fail(page .. '.md links to ' .. target .. ', which has no such anchor')
    return '#' .. name
  end
  return '#' .. id
end

--- The caption for the list of figures: the alt text up to its first clause boundary.
local function short_caption(alt)
  local cut = #alt + 1
  for _, boundary in ipairs({ ': ', '; ', '. ' }) do
    local at = alt:find(boundary, 1, true)
    if at and at < cut then cut = at end
  end
  return alt:sub(1, cut - 1)
end

--- Whether a drawn diagram asks for a landscape page: Graphviz writes graph [class=...] into
--- the class of the SVG's top group, `<g id="graph0" class="graph landscape" ...>`. The
--- images are in the built wiki, the directory pandoc runs in.
local function landscape(src)
  if not src:match('%.svg$') then return false end
  local file = io.open(src, 'r')
  if not file then return false end
  local head = file:read(4096) or ''
  file:close()
  local classes = head:match('<g id="graph0" class="([^"]*)"')
  return classes ~= nil and (' ' .. classes .. ' '):find(' landscape ', 1, true) ~= nil
end

local function figures(blocks)
  local count = 0
  local result = blocks:walk({
    Para = function(p)
      if #p.content ~= 1 or p.content[1].t ~= 'Image' then return nil end
      local image = p.content[1]
      local alt = stringify(image.caption)
      count = count + 1
      local figure = pandoc.Figure(
        { pandoc.Plain { image } },
        { long = { pandoc.Plain(image.caption) },
          short = { pandoc.Str(short_caption(alt)) } })
      if not landscape(image.src) then return figure end
      return { pandoc.RawBlock('latex', '\\begin{landscapefigure}'), figure,
               pandoc.RawBlock('latex', '\\end{landscapefigure}') }
    end,
  })
  return result, count
end

function Pandoc(sidebar)
  local guide = stringify(sidebar.meta.guide or '')
  local prefix = stringify(sidebar.meta.prefix or '')
  if guide == '' or prefix == '' then error('wiki.lua needs the metadata guide and prefix') end

  local pages = sidebar_pages(sidebar.blocks, guide)
  local present = check_complete(pages, prefix)
  local in_guide = {}
  for _, page in ipairs(pages) do in_guide[page] = true end

  local docs, anchors = {}, {}
  for _, page in ipairs(pages) do
    if present[page] then
      docs[page] = read_page(page)
      anchors[page] = rename_headings(page, docs[page])
    end
  end

  local blocks = pandoc.Blocks({})
  local figure_count = 0
  for _, page in ipairs(pages) do
    local doc = docs[page]
    if doc then
      -- In alt text GitHub keeps what looks like a tag as text (`runtimes/<id>`); the alt
      -- text is the figure caption here, so the same.
      doc.blocks = doc.blocks:walk({
        Image = function(image)
          image.caption = image.caption:walk({
            RawInline = function(r) return pandoc.Str(r.text) end,
          })
          return image
        end,
      })
      doc.blocks = doc.blocks:walk({
        Link = function(l)
          local target = wiki_target(page, l.target, in_guide, present, anchors)
          if target then l.target = target end
          return l
        end,
        -- The LaTeX writer drops raw HTML. An error, so that a page relying on it is
        -- noticed; the pages have none.
        RawInline = function(r)
          if r.format == 'html' then fail(page .. '.md has raw HTML: ' .. r.text) end
        end,
        RawBlock = function(r)
          if r.format == 'html' then fail(page .. '.md has raw HTML: ' .. r.text) end
        end,
      })
      local page_blocks, count = figures(doc.blocks)
      figure_count = figure_count + count
      blocks:extend(page_blocks)
    end
  end

  if #errors > 0 then
    for _, message in ipairs(errors) do io.stderr:write('wiki.lua: ' .. message .. '\n') end
    error(#errors .. ' problem(s) in the wiki pages of the ' .. guide)
  end

  -- The list of figures goes at the end: figures are looked up while reading, not
  -- browsed beforehand. The wiki's tables have no captions, so there is no list of tables.
  if figure_count > 0 then
    blocks:insert(pandoc.RawBlock('latex',
      '\\clearpage\n\\KOMAoptions{listof=totoc}\n\\listoffigures'))
  end

  sidebar.blocks = blocks
  -- The PDF's document title; the visible title is on the title page (title-<guide>.tex).
  sidebar.meta['title-meta'] = 'fsbpyr ' .. guide
  return sidebar
end
