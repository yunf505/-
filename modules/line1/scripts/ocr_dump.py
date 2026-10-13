"""Local OCR bridge: turn a PDF (or a single page image) into plain text WITHOUT any cloud.

Why this exists (decision: local OCR bridge / "no-vision execution mode"; see docs/无视觉执行模式.md):
    The line-1 extraction 规程 was designed so the *executing agent's vision* reads
    rendered PNGs (see #D003). That path only works on agents with image understanding.
    A text-only agent (e.g. Codex / a CLI LLM with no image input) cannot "see" a
    scanned customs form at all, so real scans fail before extraction begins.

    This bridge decouples "getting the pixels into characters" from "who reads them".
    It renders each page with PyMuPDF and runs *local* RapidOCR (onnxruntime, offline)
    to emit a text dump. Any text-capable agent then follows prompts/抽取规程 on that
    TEXT to produce Contract-A JSON, and validate.py gates it — exactly the same 规程
    and validator, just with OCR standing in for the agent's eyes.

Hard rules honoured here:
    - Fully local. No network, no cloud OCR, no LLM call. Real documents never leave the box.
    - Encrypted PDFs: tries the empty password first (court/WPS exports are often empty-password).
    - No artificial page limit. Use --pages for huge files to bound cost.
    - Rotated scans are NOT auto-corrected: pass --rotate 90|180|270 when a page reads sideways.

Usage:
    python scripts/ocr_dump.py <file.pdf>                      # all pages, 200 dpi, text to stdout
    python scripts/ocr_dump.py <file.pdf> --out dump.txt
    python scripts/ocr_dump.py <file.pdf> --pages 49-52 --dpi 300 --json dump.json
    python scripts/ocr_dump.py <scan.pdf> --rotate 90 --out dump.txt   # 90 deg CW correction

Notes for the downstream extraction agent:
    - RapidOCR returns per-segment confidence; low-confidence runs are normal on stamps,
      handwriting and dot-matrix digits. Do NOT trust a single OCR line for IDs or amounts;
      the 规程 still requires raw_span + confidence, and lookalike chars (B/8, D/Q, O/0,
      5/S, 8/B) must be double-checked against a high-dpi re-render or a targeted crop.
    - OCR text order is reconstructed top-to-bottom / left-to-right by y-banding; complex
      multi-column boxes may interleave. When a field looks garbled, re-render that region
      with render_pdf.py --clip and read it visually (if you have vision) or crop-OCR it.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    import pymupdf
except ImportError:
    print("[ERR] PyMuPDF is required: pip install PyMuPDF", file=sys.stderr)
    sys.exit(2)


def parse_pages(spec: str | None, max_page: int) -> list[int]:
    """'2' -> [2]; '1-5,8' -> [1..5,8]; None/'all' -> every page. Pages are 1-based."""
    if spec is None or spec.strip().lower() == "all":
        return list(range(1, max_page + 1))
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(\d+)-(\d+)$", part)
        if m:
            out.update(range(int(m.group(1)), int(m.group(2)) + 1))
        elif part.isdigit():
            out.add(int(part))
        else:
            raise ValueError(f"bad page spec: {part!r}")
    bad = [p for p in out if p < 1 or p > max_page]
    if bad:
        raise ValueError(f"pages out of range 1..{max_page}: {sorted(bad)}")
    return sorted(out)


def load_ocr():
    """Instantiate RapidOCR lazily so this file still imports when OCR is not installed."""
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        print(
            "[ERR] rapidocr-onnxruntime not installed.\n"
            "      pip install rapidocr-onnxruntime   (first run downloads ONNX models locally once)",
            file=sys.stderr,
        )
        raise
    return RapidOCR()


def png_to_bgr(png_bytes: bytes):
    """Decode PNG bytes to an OpenCV BGR ndarray without touching the filesystem."""
    import numpy as np
    import cv2
    arr = np.frombuffer(png_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("cv2 failed to decode rendered page")
    return img


def page_to_bgr(doc, page_index: int, dpi: int, rotate: int):
    """Render one page to a BGR ndarray, applying optional CW rotation."""
    page = doc[page_index]
    if rotate:
        page.set_rotation((page.rotation + rotate) % 360)
    pix = page.get_pixmap(dpi=dpi)
    return png_to_bgr(pix.tobytes("png")), pix.width, pix.height


def to_lines(ocr_result):
    """RapidOCR result -> list of dicts {text,conf,x,y,w,h,bx,by}, then reading-order lines.

    Each returned line merges segments that share a horizontal band, sorted left->right.
    """
    segs = []
    for item in ocr_result:
        box, text, score = item[0], item[1], float(item[2])
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        segs.append(
            {
                "text": text,
                "conf": round(score, 4),
                "x0": min(xs), "x1": max(xs),
                "y0": min(ys), "y1": max(ys),
                "bx": sum(xs) / len(xs), "by": sum(ys) / len(ys),
                "h": (max(ys) - min(ys)) or 1.0,
            }
        )
    if not segs:
        return [], []
    # Band tolerance: 60% of the median glyph height.
    heights = sorted(s["h"] for s in segs)
    med_h = heights[len(heights) // 2]
    tol = med_h * 0.6
    segs.sort(key=lambda s: s["by"])
    bands = []
    current = []
    ref_y = None
    for s in segs:
        if ref_y is None or abs(s["by"] - ref_y) <= tol:
            current.append(s)
            ref_y = s["by"] if ref_y is None else (ref_y + s["by"]) / 2
        else:
            bands.append(current)
            current = [s]
            ref_y = s["by"]
    if current:
        bands.append(current)
    lines = []
    for band in bands:
        band.sort(key=lambda s: s["x0"])
        text = "  ".join(s["text"] for s in band)
        conf = sum(s["conf"] for s in band) / len(band)
        lines.append(
            {
                "text": text,
                "conf": round(conf, 4),
                "x": round(min(s["x0"] for s in band), 1),
                "y": round(min(s["y0"] for s in band), 1),
                "w": round(max(s["x1"] for s in band) - min(s["x0"] for s in band), 1),
                "h": round(max(s["y1"] for s in band) - min(s["y0"] for s in band), 1),
            }
        )
    return segs, lines


def dump_pdf(pdf_path: Path, ocr, dpi: int, pages_spec: str | None, rotate: int, annotate: bool):
    doc = pymupdf.open(pdf_path)
    if doc.needs_pass:
        if not doc.authenticate(""):
            return {"error": "password-protected with non-empty password; supply it out-of-band", "path": str(pdf_path)}
    n = len(doc)
    pages = parse_pages(pages_spec, n)
    out_lines = []
    json_pages = []
    for idx in pages:
        try:
            img, w, h = page_to_bgr(doc, idx - 1, dpi, rotate)
        except Exception as exc:  # noqa: BLE001
            out_lines.append(f"=== PAGE {idx} === [render error: {exc}]")
            json_pages.append({"page": idx, "error": str(exc), "lines": []})
            continue
        result, _elapse = ocr(img)
        segs, lines = to_lines(result or [])
        out_lines.append(f"=== PAGE {idx} ({w}x{h} @{dpi}dpi) ===")
        for ln in lines:
            if annotate and ln["conf"] < 0.85:
                out_lines.append(f"{ln['text']}   [conf:{ln['conf']:.2f}]")
            else:
                out_lines.append(ln["text"])
        if not lines:
            out_lines.append("(no text recognized)")
        json_pages.append(
            {
                "page": idx,
                "size": {"w": w, "h": h},
                "dpi": dpi,
                "rotate": rotate,
                "line_count": len(lines),
                "mean_conf": round(sum(l["conf"] for l in lines) / len(lines), 4) if lines else 0.0,
                "lines": lines,
            }
        )
    doc.close()
    return {"pages": json_pages, "text": "\n".join(out_lines)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Local OCR bridge: PDF -> text (offline, RapidOCR).")
    ap.add_argument("pdf", help="input PDF")
    ap.add_argument("--out", help="write text dump here (default: stdout)")
    ap.add_argument("--json", help="also write structured JSON (per-line boxes + confidence) here")
    ap.add_argument("--dpi", type=int, default=200, help="render dpi (default 200; 300+ for dense/blur)")
    ap.add_argument("--pages", help="e.g. 2 or 1-5,8 or 'all' (default all)")
    ap.add_argument("--rotate", type=int, default=0, choices=[0, 90, 180, 270], help="clockwise correction for sideways scans")
    ap.add_argument("--annotate", action="store_true", help="append [conf:x.xx] to low-confidence lines in the text")
    args = ap.parse_args()

    src = Path(args.pdf)
    if not src.exists():
        print(f"[ERR] not found: {src}", file=sys.stderr)
        return 2
    ocr = load_ocr()
    res = dump_pdf(src, ocr, args.dpi, args.pages, args.rotate, args.annotate)
    if "error" in res:
        print(f"[ERR] {res['error']}: {res['path']}", file=sys.stderr)
        return 3

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(res["text"], encoding="utf-8")
        print(f"[ok] text -> {args.out}  ({sum(p['line_count'] for p in res['pages'])} lines, {len(res['pages'])} pages)")
    else:
        print(res["text"])

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "source_file": src.name,
            "engine": "local-rapidocr-onnxruntime",
            "ocr_used": True,
            "dpi": args.dpi,
            "rotate": args.rotate,
            "pages": res["pages"],
        }
        Path(args.json).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[ok] json -> {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
