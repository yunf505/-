"""Pure-local extraction engine (decision #D008): OCR words (with coordinates) ->
template/anchor-column field mapping -> normalized Contract-A JSON. NO agent, NO LLM,
NO cloud. This is the runtime extractor for the web deliverable.

Design (why anchor + column, not absolute coordinates):
    Standardized customs forms (CBP 7501, China 报关单, ocean B/L) have FIXED box
    geometry, and the printed box LABELS/NUMBERS survive OCR reliably. So we anchor on
    the label/box-number token, derive that field's column x-window from the distance to
    the next label on the same row, and read the value words that fall in that column on
    the label row and the 1-2 rows below it. This is robust to page scale/shift (relative
    to detected anchors) unlike hard-coded absolute rectangles.

Reuse: ocr_dump.py is the "eyes" (render + local RapidOCR). This module is the "reader".

CLI (proof of life):
    python src/extract_engine.py <file.pdf> --doc-type customs_declaration --pages 2 --out out.json
Then: python src/validate.py out.json   # must be ERR 0
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE.parent / "scripts"))  # for ocr_dump import

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import ocr_dump  # local OCR bridge (eyes layer)


# ------------------------------------------------------------------ OCR words

def ocr_words(pdf_path: Path, pages: str | None, dpi: int, rotate: int):
    """Return list of word dicts {text,x0,y0,x1,y1,cx,cy,conf,page} via local RapidOCR."""
    import pymupdf
    engine = ocr_dump.load_ocr()
    doc = pymupdf.open(str(pdf_path))
    try:
        if doc.needs_pass and not doc.authenticate(""):
            raise SystemExit("[ERR] 加密 PDF：空口令解不开。")
        total = len(doc)
        if pages:
            try:
                page_nos = [p for p in ocr_dump.parse_pages(pages, total) if 1 <= p <= total]
            except ValueError:
                page_nos = list(range(1, total + 1))  # requested beyond range -> just do all
        else:
            page_nos = list(range(1, total + 1))
        if not page_nos:
            page_nos = [1]
        words = []
        for pno in page_nos:
            img, _w, _h = ocr_dump.page_to_bgr(doc, pno - 1, dpi, rotate)
            result, _ = engine(img)
            for box, text, conf in (result or []):
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                words.append({
                    "page": pno, "text": text, "conf": float(conf),
                    "x0": min(xs), "x1": max(xs), "y0": min(ys), "y1": max(ys),
                    "cx": sum(xs) / len(xs), "cy": sum(ys) / len(ys),
                })
    finally:
        doc.close()
    return words, total


def group_rows(words, tol_ratio=0.6):
    """Group words into rows by y; return list of rows (each a list of words sorted by x)."""
    if not words:
        return []
    heights = sorted(w["y1"] - w["y0"] for w in words)
    med_h = heights[len(heights) // 2] or 10.0
    tol = med_h * tol_ratio
    ws = sorted(words, key=lambda w: w["cy"])
    rows, cur, ref = [], [], None
    for w in ws:
        if ref is None or abs(w["cy"] - ref) <= tol:
            cur.append(w)
            ref = w["cy"] if ref is None else (ref + w["cy"]) / 2
        else:
            rows.append(sorted(cur, key=lambda x: x["x0"]))
            cur, ref = [w], w["cy"]
    if cur:
        rows.append(sorted(cur, key=lambda x: x["x0"]))
    return rows


# ------------------------------------------------------------------ normalization

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
US_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$")


def norm_date(s: str):
    s = s.strip()
    m = US_DATE_RE.match(s)
    if m:
        mo, d, y = int(m[1]), int(m[2]), int(m[3])
        if y < 100:
            y += 2000
        return f"{y:04d}-{mo:02d}-{d:02d}"
    if DATE_RE.match(s):
        return s
    return None


def norm_money(s: str, default_ccy="USD"):
    s = s.upper().replace(" ", "")
    m = re.match(r"^([A-Z]{3})?\$?(-?[0-9][0-9,]*(?:\.[0-9]+)?)", s)
    if not m:
        return None
    ccy = m[1] or default_ccy
    amt = float(m[2].replace(",", ""))
    if not re.match(r"^[A-Z]{3}$", ccy):
        ccy = default_ccy
    return {"amount": amt, "currency": ccy}


def norm_weight_kg(s: str):
    m = re.match(r"^([0-9][0-9,]*(?:\.[0-9]+)?)\s*(KGS?|LB?S?|MT|TON|)$", s.strip().upper())
    if not m:
        return None
    v = float(m[1].replace(",", ""))
    u = m[2]
    if u in ("LB", "LBS"):
        return round(v * 0.45359237, 4)
    if u in ("MT", "TON"):
        return v * 1000
    return v


def norm_int(s: str):
    m = re.match(r"^([0-9][0-9,]*)", s.strip())
    return int(m[1].replace(",", "")) if m else None


def norm_hs_list(s: str):
    codes = re.findall(r"\d[\d.]{4,}", s)
    out = []
    for c in codes:
        d = re.sub(r"\D", "", c)
        if len(d) >= 6:
            out.append(d[:10].ljust(10, "0") if len(d) < 10 else d[:10])
    return out or None


def norm_quantity(s: str):
    m = re.match(r"^([0-9][0-9,]*(?:\.[0-9]+)?)\s*([A-Za-z]{1,6})$", s.strip())
    if not m:
        return None
    return [{"value": float(m[1].replace(",", "")), "unit": m[2].upper()}]


CBP_MODE = {"1": "rail", "2": "air", "3": "truck", "4": "air", "5": "mail",
            "6": "pipeline", "7": "truck", "8": "truck", "9": "multimodal",
            "10": "sea", "11": "sea", "12": "sea", "20": "air", "21": "air",
            "22": "air", "30": "road", "31": "road", "32": "road", "40": "rail"}


def norm_transport(s: str):
    t = s.strip().lower()
    if t in {"sea", "air", "rail", "road", "multimodal"}:
        return t
    m = re.match(r"^(\d{1,2})", t)
    if m and m[1] in CBP_MODE:
        mapped = CBP_MODE[m[1]]
        return "road" if mapped == "truck" else mapped
    return None


# ------------------------------------------------------------------ field helpers

def fv(value, conf, raw, normalized=True):
    if value is None:
        return {"value": None, "confidence": 0.0, "raw_span": None, "normalized": True}
    return {"value": value, "confidence": round(max(0.0, min(1.0, conf)), 2),
            "raw_span": raw, "normalized": bool(normalized)}


def row_text(row):
    return "  ".join(w["text"] for w in row)


# ------------------------------------------------------------------ CBP 7501 template

BOX_RE = re.compile(r"^(\d{1,2})\.\s*([A-Za-z].*)$")


def _box_columns(rows):
    """Map box-number -> (row_index, x0, x_next) using the row where the 'N. Label' appears."""
    cols = {}
    for ri, row in enumerate(rows):
        marks = []
        for wi, w in enumerate(row):
            m = BOX_RE.match(w["text"].strip())
            if m:
                marks.append((int(m[1]), w["x0"]))
        for k, (num, x0) in enumerate(marks):
            x_next = marks[k + 1][1] if k + 1 < len(marks) else None
            cols.setdefault(num, []).append((ri, x0, x_next))
    return cols


def _value_in_column(rows, ri, x0, x_next, down=2):
    """Collect words in column [x0, x_next) on rows ri..ri+down (label row + below)."""
    hi = x_next if x_next is not None else 10 ** 9
    picked = []
    for r in range(ri, min(len(rows), ri + down + 1)):
        for w in rows[r]:
            # word center within the column window
            if x0 - 6 <= w["cx"] < hi - 6:
                # skip the label token itself (starts with 'N.')
                if r == ri and BOX_RE.match(w["text"].strip()):
                    continue
                picked.append(w)
    picked.sort(key=lambda w: (w["y0"], w["x0"]))
    if not picked:
        return None, 0.0, ""
    text = " ".join(w["text"] for w in picked)
    conf = sum(w["conf"] for w in picked) / len(picked)
    return text, conf, text


def extract_7501(pdf_path, pages, dpi, rotate, source_name):
    words, total = ocr_words(pdf_path, pages, dpi, rotate)
    rows = group_rows(words)
    cols = _box_columns(rows)

    def grab(box):
        occ = cols.get(box)
        if not occ:
            return None, 0.0, ""
        ri, x0, x_next = occ[0]
        return _value_in_column(rows, ri, x0, x_next)

    fields = {}

    # box1 Filer Code / Entry No.  -> declaration_no
    t, c, raw = grab(1)
    entry = None
    if t:
        m = re.search(r"([A-Z0-9]{2,4})\s*[- ]?\s*(\d{5,})[- ]?(\d)", t.replace(" ", " "))
        if m:
            entry = f"{m[1]}-{m[2]}-{m[3]}"
        else:
            m2 = re.search(r"\d{5,}-\d", t)
            entry = m2.group(0) if m2 else None
    fields["declaration_no"] = fv(entry, c, raw if entry else None)

    # box6 Port Code -> port_of_destination ; box19 Foreign Port of Lading -> port_of_dispatch
    t, c, raw = grab(6)
    port = re.search(r"\b(\d{4})\b", t or "")
    fields["port_of_destination"] = fv(port.group(1) if port else None, c, raw if port else None)

    t, c, raw = grab(19)
    fp = re.search(r"\b(\d{4,5})\b", t or "")
    fields["port_of_dispatch"] = fv(fp.group(1) if fp else None, c, raw if fp else None)

    # box9 Mode of Transport -> transport_mode
    t, c, raw = grab(9)
    mode = re.search(r"\b(\d{1,2})\b", t or "")
    tm = norm_transport(mode.group(1)) if mode else None
    fields["transport_mode"] = fv(tm, c, raw if tm else None)

    # box10 Country of Origin -> trading_country
    t, c, raw = grab(10)
    cc = re.search(r"\b([A-Z]{2})\b", (t or "").upper())
    fields["trading_country"] = fv(cc.group(1) if cc else None, c, raw if cc else None)

    # box12 B/L or AWB No.
    t, c, raw = grab(12)
    bl = re.search(r"\b([A-Z]{3,4}\d{9,12})\b", (t or "").replace(" ", ""))
    fields["bl_no_aux"] = fv(bl.group(1) if bl else None, c, raw if bl else None)  # aux, stripped later

    # box14 Exporting Country
    t, c, raw = grab(14)
    ec = re.search(r"\b([A-Z]{2})\b", (t or "").upper())
    fields["overseas_consignee"] = fv(None, 0, None)  # US import: overseas_consignee null per v1.1 rule

    # box35 Total Entered Value -> total_value (USD)
    t, c, raw = grab(35)
    val = re.search(r"([0-9][0-9,]*\.[0-9]{2}|[0-9][0-9,]{2,})", t or "")
    tv = norm_money(val.group(1)) if val else None
    fields["total_value"] = fv(tv, c, raw if tv else None)

    # goods name: only accept a merchandise-style line in the goods block; reject the
    # legal declaration paragraph and any form-label/header band. If unsure -> null.
    goods = None
    page_h = max((w["y1"] for w in words), default=1)
    label_markers = ("FILER CODE", "ENTRY NO", "DEPARTMENT OF", "ENTRY SUMMARY", "SURETY",
                     "MODE OF TRANSPORT", "COUNTRY OF", "PORT OF", "B / L", "B/L", "TOTAL ENTERED")
    legal_markers = ("DECLARE", "PURCHASER", "CONSIGNEE FOR CBP", "INVOICES ARE TRUE",
                     "BEST OF MY", "KNOWLEDGE AND BELIEF", "OR AUTHORIZED AGENT",
                     "IMPORTER OF RECORD AND", "AGREEMENT TO PURCHASE", "FULLY DISCLOSE")
    for row in rows:
        tt = row_text(row)
        up = tt.upper()
        if row and row[0]["y0"] < 0.35 * page_h:
            continue  # skip the top header/box-label band
        if any(mk in up for mk in label_markers) or any(mk in up for mk in legal_markers):
            continue
        if re.search(r"[A-Za-z]{3,}[^,]*,[^,]*", tt) and not re.search(r"^\s*\d", tt) and len(tt) < 80:
            goods = tt.strip()
            break
    fields["goods_name"] = fv(goods, 0.5, goods)

    # packages: find "#### CTNS"/"#### PKGS"/"#### PACK"
    pkgs, praw, pconf = None, None, 0.0
    for row in rows:
        tt = row_text(row)
        m = re.search(r"([0-9][0-9,]*)\s*(CTNS?|CARTONS?|PALLETS?|PKGS?|PACKAGES?|BALES?)", tt.upper())
        if m:
            pkgs = norm_int(m[1])
            praw, pconf = tt, max(w["conf"] for w in row)
            break
    fields["packages"] = fv(pkgs, pconf, praw if pkgs else None)

    # hs_code: collect HTS patterns across ALL rows (e.g. 9903.88.09 / 2202.99.9000), dedupe
    hs_rows, hraw_parts, hconf = [], [], 0.0
    for row in rows:
        tt = row_text(row)
        codes = re.findall(r"\b\d{4}\.\d{2}\.\d{2,4}\b", tt)
        if codes:
            hs_rows.extend(codes)
            hraw_parts.append(tt)
            hconf = max(hconf, max(w["conf"] for w in row))
    # dedupe preserving order
    seen, uniq = set(), []
    for c in hs_rows:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    hs = norm_hs_list(" ".join(uniq)) if uniq else None
    fields["hs_code"] = fv(hs, hconf, " | ".join(hraw_parts) if hs else None)

    # ---- fill remaining required fields explicitly as null (never guess) ----
    required = ["declaration_no", "domestic_consignor", "trading_country", "transport_mode",
                "transaction_mode", "hs_code", "goods_name", "quantity", "total_value",
                "gross_weight_kg", "packages", "declaration_date"]
    for name in required:
        fields.setdefault(name, fv(None, 0, None))
    # quantity: try a "<num> <UNIT>" near merchandise; else null
    if fields["quantity"]["value"] is None:
        q = None
        for row in rows:
            m = re.search(r"\b([0-9][0-9,]*(?:\.[0-9]+)?)\s*(L|KG|MTR|PCS|SET|DOZ|PR)\b", row_text(row).upper())
            if m:
                q = norm_quantity(m.group(0))
                fields["quantity"] = fv(q, max(w["conf"] for w in row), row_text(row))
                break
        fields.setdefault("quantity", fv(None, 0, None))
    fields.pop("bl_no_aux", None)  # aux only; not a customs_declaration field

    warnings = [
        "产品运行时纯代码抽取（#D008）：ocr_dump 出词+坐标 → 7501 编号锚点/列映射 → 归一化；无 agent/LLM/云",
        "未能可靠定位的必填字段按契约置 null（宁空勿猜）；关键 ID/金额建议高 DPI 复核",
    ]
    return {
        "schema_version": "0.1-draft",
        "prompt_version": "template-7501-v1",
        "doc_type": "customs_declaration",
        "source_file": Path(source_name).name,
        "engine": "local-template-extractor-v1 (rapidocr+anchor-column)",
        "fields": fields,
        "parse_meta": {"ocr_used": True, "pages": total, "warnings": warnings},
    }


# ------------------------------------------------------------------ dispatch

def extract(pdf_path, doc_type, pages, dpi, rotate, source_name, engine="template"):
    """Dispatch by engine. template = pure local code; deepseek = local OCR + DeepSeek API."""
    if engine == "deepseek":
        import deepseek_extract  # same dir
        return deepseek_extract.extract_deepseek(pdf_path, doc_type, source_name, dpi=dpi)
    if doc_type == "customs_declaration":
        return extract_7501(pdf_path, pages, dpi, rotate, source_name)
    raise SystemExit(f"[ERR] 模板未实现：doc_type={doc_type}（下一步补 中国报关单 / 提单；或用 --engine deepseek）")


def main() -> int:
    ap = argparse.ArgumentParser(description="Local extraction engine (template or deepseek).")
    ap.add_argument("pdf")
    ap.add_argument("--doc-type", default="customs_declaration")
    ap.add_argument("--engine", default="template", choices=["template", "deepseek"],
                    help="template=纯本地代码(离线); deepseek=本地OCR+DeepSeek API(需 .env 填 key)")
    ap.add_argument("--pages", default=None)
    ap.add_argument("--dpi", type=int, default=250)
    ap.add_argument("--rotate", type=int, default=0, choices=[0, 90, 180, 270])
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    doc = extract(Path(args.pdf), args.doc_type, args.pages, args.dpi, args.rotate,
                  args.pdf, engine=args.engine)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    pop = sum(1 for f in doc["fields"].values() if f["value"] is not None)
    print(f"[ok] {args.out}: {len(doc['fields'])} fields, {pop} populated -> validate it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
