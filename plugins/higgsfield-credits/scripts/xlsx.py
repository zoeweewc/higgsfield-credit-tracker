"""Minimal .xlsx writer - standard library only, so nobody has to install anything.

write(path, [("Tab name", [[cell, cell, ...], ...]), ...])
Strings are written inline; ints/floats as numbers.
"""
import re, zipfile
from xml.sax.saxutils import escape

CT = ('<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
      '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
      '<Default Extension="xml" ContentType="application/xml"/>'
      '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
      '{sheets}</Types>')
RELS = ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>')


def col_name(i):
    name = ""
    while i >= 0:
        name = chr(ord("A") + i % 26) + name
        i = i // 26 - 1
    return name


def safe_tab(name, used):
    name = re.sub(r"[\[\]:*?/\\]", "-", str(name))[:31] or "Sheet"
    base, n = name, 2
    while name.lower() in used:
        name = f"{base[:28]}-{n}"
        n += 1
    used.add(name.lower())
    return name


def sheet_xml(rows):
    out = ['<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>']
    for r, row in enumerate(rows, start=1):
        out.append(f'<row r="{r}">')
        for c, val in enumerate(row):
            ref = f"{col_name(c)}{r}"
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                out.append(f'<c r="{ref}"><v>{val}</v></c>')
            elif val not in (None, ""):
                out.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(str(val))}</t></is></c>')
        out.append("</row>")
    out.append("</sheetData></worksheet>")
    return "".join(out)


def write(path, sheets):
    used = set()
    names = [safe_tab(n, used) for n, _ in sheets]
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
        f'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(1, len(sheets) + 1))
    wb = ('<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
          + "".join(f'<sheet name="{escape(n)}" sheetId="{i}" r:id="rId{i}"/>' for i, n in enumerate(names, 1))
          + "</sheets></workbook>")
    wb_rels = ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                         f'Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(sheets) + 1))
               + "</Relationships>")
    tmp = path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CT.format(sheets=overrides))
        z.writestr("_rels/.rels", RELS)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        for i, (_, rows) in enumerate(sheets, 1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", sheet_xml(rows))
    import os
    os.replace(tmp, path)
