"""Parses XML and HTML with lxml, runs XPath and an XSLT transformation."""

from lxml import etree, html


def main() -> None:
    root = etree.fromstring(b"<r><i n='2'>b</i><i n='1'>a</i></r>")
    print([e.text for e in root.xpath("//i[@n > 1]")])
    style = etree.XML(b"""<xsl:stylesheet version="1.0"
        xmlns:xsl="http://www.w3.org/1999/XSL/Transform">
      <xsl:output method="text"/>
      <xsl:template match="/"><xsl:for-each select="r/i"><xsl:sort select="@n"/>
        <xsl:value-of select="."/></xsl:for-each></xsl:template></xsl:stylesheet>""")
    print(str(etree.XSLT(style)(root)).split())
    page = html.fromstring("<p>one <b>two</b></p>")
    print(page.text_content(), etree.tostring(page).decode())
