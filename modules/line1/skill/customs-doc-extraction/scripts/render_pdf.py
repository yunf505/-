"""Render PDF pages to PNG for visual extraction (pymupdf-based).

Why this exists: real-world customs files are scans, often court exhibits whose
PDF text layer is only a stamp header (or absent entirely). A PDF reader may even
report them as password-protected (empty-password encryption). Rendering to PNG
and reading visually is the reliable path.

Usage:
    python render_pdf.py <file.pdf> --pages 2 --dpi 200 --out page.png
    python render_pdf.py <file.pdf> --pages 1-5,8 --dpi 200 --outDir renders/
    python render_pdf.py <file.pdf> --sheet all --dpi 50 --out sheet.png   # contact sheet for triage
    python render_pdf.py <file.pdf> --pages 2 --dpi 500 --clip 330,80,612,190 --out crop.png  # region zoom

Rules of thumb:
- dpi >= 150 for field extraction (200 recommended; 400-600 for zooming into
  ambiguous ID/code digits — lookalike chars B/8, D/Q, O/0 often need this).
- dpi <= 50 contact sheets are for page-level triage ONLY.
- Clip box is in PDF points (x0,y0,x1,y2), top-left origin of the unrotated page.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    import pymupdf
except ImportError:
    print("PyMuPDF is required: pip install PyMuPDF")
    sys.exit(2)


def parse_pages(spec: str, max_page: int) -> list[int]:
    """'2' -> [2]; '1-5,8' -> [1..5,8]; pages are 1-based."""
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if m := re.match(r"^(\d+)-(\d+)$", part):
            out.update(range(int(m.group(1)), int(m.group(2)) + 1))
        elif part.isdigit():
            out.add(int(part))
        else:
            raise ValueError(f"bad page spec: {part!r}")
    bad = [p for p in out if p < 1 or p > max_page]
    if bad:
        raise ValueError(f"pages out of range 1..{max_page}: {sorted(bad)}")
    return sorted(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--pages", help="e.g. 2 or 1-5,8 (1-based)")
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--out", help="output PNG (single page)")
    ap.add_argument("--outDir", help="output dir for multiple pages")
    ap.add_argument("--sheet", help="'all' or page spec: render contact sheet with page badges")
    ap.add_argument("--clip", help="x0,y0,x1,y1 in PDF points; zoom a region (use with --pages)")
    args = ap.parse_args()

    src = Path(args.pdf)
    if not src.exists():
        print(f"[ERR] not found: {src}"); return 2
    doc = pymupdf.open(src)
    if doc.needs_pass:
        if not doc.authenticate(""):
            print("[ERR] password-protected with non-empty password"); return 2
    n = len(doc)
    print(f"{src.name}: {n} pages")

    clip = None
    if args.clip:
        x0, y0, x1, y1 = (float(v) for v in args.clip.split(","))
        clip = pymupdf.Rect(x0, y0, x1, y1)

    def render(i: int, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        pix = doc[i - 1].get_pixmap(dpi=args.dpi, clip=clip)
        pix.save(path)
        print(f"  p{i} -> {path} ({pix.width}x{pix.height})")

    if args.sheet:
        pages = parse_pages("1-%d" % n if args.sheet == "all" else args.sheet, n)
        from PIL import Image, ImageDraw
        import io
        ims = []
        for i in pages:
            pix = doc[i - 1].get_pixmap(dpi=args.dpi)
            ims.append(Image.open(io.BytesIO(pix.tobytes("png"))))
        w, h = ims[0].size
        cols = 10
        rows = (len(ims) + cols - 1) // cols
        sheet = Image.new("RGB", (w * cols, h * rows), "white")
        dr = ImageDraw.Draw(sheet)
        for idx, im in enumerate(ims):
            x, y = (idx % cols) * w, (idx // cols) * h
            sheet.paste(im, (x, y))
            dr.rectangle([x + 1, y + 1, x + 34, y + 20], fill="red")
            dr.text((x + 5, y + 3), str(pages[idx]), fill="white")
        out = Path(args.out) if args.out else Path("contact_sheet.png")
        sheet.save(out)
        print(f"  sheet ({len(ims)} pages) -> {out} ({sheet.size})")
        return 0

    if not args.pages:
        if n == 1 and args.out:
            args.pages = "1"       # single-page file: default to page 1
        else:
            print(f"[ERR] need --pages ({n} pages) or --sheet"); return 2
    pages = parse_pages(args.pages, n)
    if len(pages) == 1 and args.out:
        render(pages[0], Path(args.out))
    elif args.outDir:
        outdir = Path(args.outDir); outdir.mkdir(parents=True, exist_ok=True)
        for i in pages:
            render(i, outdir / f"{src.stem}_p{i}.png")
    else:
        print("[ERR] need --out (single page) or --outDir (multiple) or --sheet")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
