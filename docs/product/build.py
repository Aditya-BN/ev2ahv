#!/usr/bin/env python3
"""Build guide.html (and the PDF) from guide.md.

  python3 docs/product/build.py            # HTML only
  python3 docs/product/build.py --pdf      # HTML, then dist/ev2ahv-product-guide-<version>.pdf (headless Chrome)

Needs the python 'markdown' package. SVG diagrams are inlined; screenshots stay relative files.
"""
import html, os, re, subprocess, sys, tempfile
import markdown

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
VERSION = open(os.path.join(ROOT, "VERSION")).read().strip()
DATE = "28 November 2026"
CHROME = "/usr/bin/google-chrome"

CSS = r"""
@page { size: A4; margin: 16mm 17mm 18mm 17mm;
  @bottom-center { content: counter(page); font: 9pt 'Liberation Sans', Arial, sans-serif; color: #546e7a; }
}
@page :first { margin: 0; @bottom-center { content: none } }
:root { --teal:#0b6b73; --ink:#1c2b33; --mute:#546e7a; --line:#cfd8dc; }
html { font-family: 'Liberation Sans', Arial, sans-serif; font-size: 9.7pt; line-height: 1.47; color: var(--ink); }
body { margin: 0; }
.ver { string-set: ver "VERSION_PLACEHOLDER"; display:none }
.cover { page-break-after: always; height: 297mm; box-sizing: border-box; padding: 38mm 24mm 24mm 24mm; background: linear-gradient(160deg,#0b6b73 0%,#0b4f55 62%,#08373c 100%); color:#fff; position: relative; }
.cover .mark { font-size: 64pt; font-weight: 700; letter-spacing: -1px; margin: 0 0 6mm 0; }
.cover h1 { font-size: 25pt; line-height: 1.2; font-weight: 700; margin: 0 0 8mm 0; page-break-before: avoid; border: 0; color:#fff; padding:0 }
.cover .sub { font-size: 16pt; margin: 0 0 30mm 0; color:#b2dfe3 }
.cover .meta { font-size: 11.5pt; line-height: 1.8; border-top: 1px solid #5fa8ae; padding-top: 6mm; max-width: 110mm }
.cover .foot { position:absolute; bottom: 22mm; left: 24mm; right: 24mm; font-size: 9.5pt; color:#b2dfe3 }
h1, h2, h3, h4 { font-family: 'Liberation Sans', Arial, sans-serif; color: var(--ink); line-height: 1.25; }
h1 { font-size: 22pt; color: var(--teal); border-bottom: 2px solid var(--teal); padding-bottom: 3mm; margin: 0 0 7mm 0; page-break-before: always; break-after: avoid; }
h2 { font-size: 14pt; color: #0b4f55; margin: 8mm 0 3mm 0; break-after: avoid; }
h3 { font-size: 11.5pt; margin: 6mm 0 2mm 0; break-after: avoid; }
p { margin: 0 0 2.6mm 0; orphans: 3; widows: 3; }
a { color: var(--teal); text-decoration: none; }
ul, ol { margin: 0 0 3mm 0; padding-left: 6mm; }
li { margin-bottom: 0.9mm; }
code { font-family: 'DejaVu Sans Mono', monospace; font-size: 8.4pt; background: #eef3f4; padding: 0.3mm 1mm; border-radius: 2px; word-break: break-word; }
pre { background: #f3f6f7; border: 1px solid var(--line); border-left: 3px solid var(--teal); border-radius: 3px; padding: 3mm 4mm; margin: 0 0 4mm 0; white-space: pre-wrap; word-break: break-word; break-inside: avoid; }
pre code { background: none; padding: 0; font-size: 8.3pt; line-height: 1.4; }
blockquote { margin: 0 0 4mm 0; padding: 2mm 4mm; border-left: 3px solid #90a4ae; background: #f6f8f9; color: #263238; break-inside: avoid; }
blockquote p { margin: 0 }
table { border-collapse: collapse; width: 100%; margin: 0 0 5mm 0; font-size: 8.6pt; line-height: 1.38; }
th { background: #0b6b73; color: #fff; text-align: left; padding: 1.6mm 2mm; font-weight: 700; vertical-align: bottom; }
td:first-child { min-width: 20mm } td { padding: 1.4mm 2mm; border-bottom: 1px solid var(--line); vertical-align: top; overflow-wrap: anywhere; }
tr { break-inside: avoid; }
tbody tr:nth-child(even) td { background: #f6f9fa; }
td code { font-size: 7.8pt; } th code { background: rgba(255,255,255,.22); color: #fff; }
figure { margin: 0 0 5mm 0; text-align: center; break-inside: avoid; }
figure.diagram svg { box-sizing: border-box; width: 100%; height: auto; border: 1px solid var(--line); border-radius: 4px; }
figure img { max-width: 100%; max-height: 100mm; border: 1px solid var(--line); border-radius: 4px; }
figcaption { font-size: 8.6pt; color: var(--mute); margin-top: 1.5mm; }
.callout { border-left: 4px solid var(--teal); background: #eaf4f5; padding: 3mm 4mm; margin: 0 0 4mm 0; border-radius: 0 3px 3px 0; break-inside: avoid; font-size: 9.4pt; }
.callout p:last-child { margin-bottom: 0 }
.callout.warn { border-color: #c77700; background: #fff4e0; }
.callout.tip { border-color: #2e7d32; background: #eaf5ec; }
.toc { page-break-before: always; page-break-after: always; }
.toc h1 { page-break-before: avoid; }
.toc ol { list-style: none; padding: 0; margin: 0; }
.toc li { margin: 0; }
.toc .l1 { font-weight: 700; margin-top: 2.2mm; font-size: 10pt }
.toc .l2 { padding-left: 6mm; font-size: 9pt; color: #37474f }
.toc a { display: flex; align-items: baseline; color: inherit }
.toc a .t { flex: 0 1 auto; }
.toc a .dots { flex: 1 1 auto; border-bottom: 1px dotted #90a4ae; margin: 0 1.5mm; transform: translateY(-1mm); min-width: 6mm }
.toc a .p { flex: 0 0 auto; font-variant-numeric: tabular-nums }
strong { font-weight: 700 }
hr { border: 0; border-top: 1px solid var(--line); margin: 5mm 0 }
"""

def slug(s):
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s.lower()).strip("-")
    return s[:60] or "x"

def inline_figs(text):
    def rep(m):
        alt, src = m.group(1), m.group(2)
        if src.endswith(".svg"):
            svg = open(os.path.join(HERE, src)).read()
            svg = re.sub(r"<\?xml[^>]*\?>", "", svg).strip()
            return f'\n<figure class="diagram">{svg}<figcaption>{html.escape(alt)}</figcaption></figure>\n'
        return f'\n<figure><img src="{src}" alt="{html.escape(alt)}"><figcaption>{html.escape(alt)}</figcaption></figure>\n'
    return re.sub(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$", rep, text, flags=re.M)

def build(pages=None):
    src = open(os.path.join(HERE, "guide.md")).read()
    src = re.sub(r"\A# [^\n]*\n+\*\*Product Guide[^\n]*\n+", "", src)   # cover carries these
    src = inline_figs(src)
    md = markdown.Markdown(extensions=["tables", "fenced_code", "md_in_html", "attr_list", "sane_lists"])
    body = md.convert(src)
    # ids for h1/h2 and TOC
    heads = []
    def idr(m):
        lvl, inner = m.group(1), m.group(2)
        i = f"s-{slug(inner)}-{len(heads)}"
        heads.append((int(lvl), re.sub(r"<[^>]+>", "", html.unescape(inner)), i))
        return f'<h{lvl} id="{i}">{inner}</h{lvl}>'
    body = re.sub(r"<h([12])>(.*?)</h\1>", idr, body)
    items = []
    for lvl, text, i in heads:
        if text == "About this guide":
            pass
        pg = (pages or {}).get(i, "")
        items.append(f'<li class="l{lvl}"><a href="#{i}"><span class="t">{html.escape(text)}</span><span class="dots"></span><span class="p">{pg}</span></a></li>')
    toc = '<section class="toc"><h1>Contents</h1><ol>' + "".join(items) + "</ol></section>"
    cover = f'''<section class="cover"><div class="mark">ev2ahv</div>
<h1>Migrating Stratus everRun VMs to Nutanix AHV</h1><div class="sub">Product Guide</div>
<div class="meta">Version {VERSION}<br>{DATE}<br>Appliance image ev2ahv-{VERSION}.qcow2<br>Tested with everRun 8.1.1</div>
<div class="foot">Documentation addresses use the reserved range 192.0.2.0/24. Replace them with your own.</div></section>'''
    css = CSS.replace("VERSION_PLACEHOLDER", VERSION)
    doc = f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>ev2ahv: Migrating Stratus everRun VMs to Nutanix AHV. Product Guide {VERSION}</title><style>{css}</style></head><body><span class="ver">{VERSION}</span>{cover}{toc}{body}</body></html>'
    open(os.path.join(HERE, "guide.html"), "w").write(doc)
    return heads

def pdf(out):
    subprocess.run([CHROME, "--headless", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={out}", "file://" + os.path.join(HERE, "guide.html")],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def page_texts(out):
    n = int(re.search(r"Pages:\s+(\d+)", subprocess.run(["pdfinfo", out], capture_output=True, text=True).stdout).group(1))
    return [subprocess.run(["pdftotext", "-f", str(i), "-l", str(i), "-layout", out, "-"], capture_output=True, text=True).stdout for i in range(1, n + 1)]

def main():
    heads = build()
    if "--pdf" not in sys.argv:
        return
    out = os.path.join(ROOT, "dist", f"ev2ahv-product-guide-{VERSION}.pdf")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    pdf(out)
    for _ in range(2):   # fill in page numbers, render again until stable
        texts = page_texts(out)
        pages, start = {}, 0
        # skip cover and the contents pages
        toc_end = next(i for i, t in enumerate(texts) if "This guide is for the people who plan" in t)
        for lvl, text, i in heads:
            for pi in range(max(start, toc_end), len(texts)):
                if any(l.strip() == text for l in texts[pi].splitlines()) or re.search(r"^\s*" + re.escape(text[:50]), texts[pi], re.M):
                    pages[i] = pi + 1; start = pi; break
        build(pages)
        pdf(out)
    print(out, len(page_texts(out)), "pages")

main()
