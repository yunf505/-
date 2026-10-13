"""Extract Contract A fields — with an OPTIONAL local-OCR fallback (no-vision bridge).

This is C's deterministic `extract_local.py`, minimally patched so that the three
conditions it used to hard-reject (encrypted / no text layer / >20 pages) now first
try a *local, offline* OCR pass instead of immediately raising:

    1. still no usable characters -> raise with a pointer to 无视觉执行模式
    2. characters obtained but too few `LABEL: value` matches (real CBP box-forms)
       -> raise the SAME 字段太少 guard, but the message now tells you to extract via
          `scripts/ocr_dump.py` + 抽取规程 with a text agent, not this regex parser.

Nothing here calls a cloud API or an LLM. If rapidocr / pymupdf are not installed,
OCR is simply skipped and behaviour reverts to the original (raise on scan).

Real-world note: CBP 7501s are numbered-box forms; their OCR text does NOT contain
`LABEL: value` lines, so this regex parser cannot rebuild Contract-A from them.
That is expected — this fallback only helps text-layout docs saved as scans. For
genuine form scans use the 无视觉执行模式 (ocr_dump -> 抽取规程 -> validate).
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "vendor"))

try:
    from pypdf import PdfReader
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("缺少 pypdf。请先运行 python -m pip install -r requirements.txt") from exc


def _resolve_schema() -> dict:
    """Find contract-A.schema.json without hardcoding anyone's absolute path."""
    env = os.getenv("CONTRACT_A_SCHEMA")
    candidates = []
    if env:
        candidates.append(Path(env))
    candidates += [
        ROOT / "modules" / "line1" / "schema" / "contract-A.schema.json",
        ROOT / "schema" / "contract-A.schema.json",
        ROOT.parent / "schema" / "contract-A.schema.json",
    ]
    for cand in candidates:
        if cand.is_file():
            return json.loads(cand.read_text(encoding="utf-8"))
    raise RuntimeError("找不到 contract-A.schema.json，请设 CONTRACT_A_SCHEMA 环境变量指向它。")


SCHEMA = _resolve_schema()
DOC_TYPES = tuple(SCHEMA["doc_type_enum"])

LABELS = {
    "customs_declaration": {
        "declaration_no": "DECLARATION NO", "domestic_consignor": "DOMESTIC CONSIGNOR",
        "overseas_consignee": "OVERSEAS CONSIGNEE", "trading_country": "TRADING COUNTRY",
        "port_of_dispatch": "PORT OF DISPATCH", "port_of_destination": "PORT OF DESTINATION",
        "transport_mode": "TRANSPORT MODE", "transaction_mode": "TRANSACTION MODE",
        "hs_code": "HS CODE", "goods_name": "GOODS NAME", "quantity": "QUANTITY",
        "total_value": "TOTAL VALUE", "gross_weight_kg": "GROSS WEIGHT",
        "net_weight_kg": "NET WEIGHT", "packages": "PACKAGES",
        "declaration_date": "DECLARATION DATE",
    },
    "bill_of_lading": {
        "bl_no": "B/L NO", "shipper": "SHIPPER", "consignee": "CONSIGNEE",
        "notify_party": "NOTIFY PARTY", "port_of_loading": "PORT OF LOADING",
        "port_of_discharge": "PORT OF DISCHARGE", "goods_description": "GOODS DESCRIPTION",
    },
    "commercial_invoice": {
        "invoice_no": "INVOICE NO", "invoice_date": "INVOICE DATE", "seller": "SELLER",
        "buyer": "BUYER", "order_no": "ORDER NO", "goods_name": "GOODS NAME",
        "quantity": "QUANTITY", "unit_price": "UNIT PRICE", "total_value": "TOTAL VALUE",
        "packages": "PACKAGES", "gross_weight_kg": "GROSS WEIGHT",
        "net_weight_kg": "NET WEIGHT", "marks": "MARKS", "container_no": "CONTAINER NO",
        "country_of_origin": "COUNTRY OF ORIGIN", "port_of_loading": "PORT OF LOADING",
        "port_of_discharge": "PORT OF DISCHARGE",
    },
    "packing_list": {
        "packing_list_no": "PACKING LIST NO", "invoice_no": "INVOICE NO",
        "packing_date": "PACKING DATE", "seller": "SELLER", "buyer": "BUYER",
        "goods_name": "GOODS NAME", "quantity": "QUANTITY", "packages": "PACKAGES",
        "kind_of_packages": "KIND OF PACKAGES", "gross_weight_kg": "GROSS WEIGHT",
        "net_weight_kg": "NET WEIGHT", "measurement_cbm": "MEASUREMENT",
        "marks": "MARKS", "container_no": "CONTAINER NO",
        "port_of_loading": "PORT OF LOADING", "port_of_discharge": "PORT OF DISCHARGE",
    },
}
SIGNATURES = {
    "customs_declaration": "DECLARATION NO",
    "bill_of_lading": "B/L NO",
    "commercial_invoice": "INVOICE NO",
    "packing_list": "PACKING LIST NO",
}
LINE_RE = re.compile(r"^([A-Z0-9 /&()._-]+):\s*(.*?)\s*$")
NUMBER_RE = re.compile(r"^([0-9][0-9,]*(?:\.[0-9]+)?)\s*([A-Z]*)$", re.I)
MONEY_RE = re.compile(r"^([A-Z]{3})\s+([0-9][0-9,]*(?:\.[0-9]+)?)$")

# OCR is best-effort; expose whether it was actually used for the last parse.
_LAST_OCR_USED = False


class ExtractionError(ValueError):
    """The selected file cannot be extracted within this demo's scope."""


def _ocr_available() -> bool:
    try:
        import pymupdf  # noqa: F401
        import rapidocr_onnxruntime  # noqa: F401
        import numpy  # noqa: F401
        import cv2  # noqa: F401
        return True
    except Exception:
        return False


def _ocr_lines(pdf_bytes: bytes, dpi: int = 250) -> list[tuple[int, str]]:
    """Local, offline OCR: render each page and read characters. Returns (page_no, line)."""
    import pymupdf
    import numpy as np
    import cv2
    from rapidocr_onnxruntime import RapidOCR

    engine = RapidOCR()
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        if doc.needs_pass and not doc.authenticate(""):
            raise ExtractionError("加密 PDF：空口令解不开，请提供口令。")
        pages = len(doc)
        out: list[tuple[int, str]] = []
        for idx in range(pages):
            pix = doc[idx].get_pixmap(dpi=dpi)
            img = cv2.imdecode(np.frombuffer(pix.tobytes("png"), np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                continue
            result, _ = engine(img)
            for item in (result or []):
                line = " ".join(str(item[1]).split())
                if line:
                    out.append((idx + 1, line))
    finally:
        doc.close()
    return out


def _has_signature(lines: list[tuple[int, str]], signature: str | None) -> bool:
    """Does any line look like the required `SIGNATURE: value` label of this doc type?"""
    if not signature:
        return bool(lines)
    for _, line in lines:
        match = LINE_RE.match(line)
        if match and match[1].strip().upper() == signature:
            return True
    return False


def _pdf_lines(pdf_bytes: bytes, signature: str | None = None,
               ocr_fallback: bool = True) -> tuple[list[tuple[int, str]], int]:
    """Return (lines, page_count). Falls back to local OCR when the text layer is unusable.

    "Unusable" now means any of: no text layer at all, a text layer that is clearly just
    noise (e.g. court-stamp headers with no `LABEL: value` for this doc type), or >20 pages.
    That is exactly what real scanned customs documents look like, so the fallback actually
    fires for them instead of silently returning a header-only dump.
    """
    global _LAST_OCR_USED
    _LAST_OCR_USED = False
    if not pdf_bytes.startswith(b"%PDF-"):
        raise ExtractionError("文件不是有效 PDF。")

    reason: str | None = None
    pages = 0
    lines: list[tuple[int, str]] = []
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes), strict=False)
        if reader.is_encrypted:
            reason = "首版仅支持未加密 PDF"
        else:
            pages = len(reader.pages)
            for page_no, page in enumerate(reader.pages, 1):
                for raw in (page.extract_text() or "").splitlines():
                    line = " ".join(raw.strip().split())
                    if line:
                        lines.append((page_no, line))
            if not lines:
                reason = "PDF 无可用文字层（扫描件）"
            elif not _has_signature(lines, signature):
                reason = "文字层里没有本单证类型的 `标签: 值`（疑似扫描件/编号格子表单）"
            elif pages > 20:
                reason = f"页数 {pages} 超过 20 页上限"
    except ExtractionError:
        raise
    except Exception as exc:
        reason = f"PDF 读取失败：{exc}"

    if reason is None:
        return lines, pages

    # ---- OCR fallback (no-vision bridge): turn the scan into local text instead of bailing out. ----
    if ocr_fallback and _ocr_available():
        try:
            ocr = _ocr_lines(pdf_bytes)
        except ExtractionError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ExtractionError(f"本地 OCR 兜底失败：{exc}") from exc
        if ocr:
            _LAST_OCR_USED = True
            total = max((p for p, _ in ocr), default=1)
            return ocr, total
        raise ExtractionError(
            "扫描件本地 OCR 未读到任何字符。请调高 dpi 或检查图片质量。"
        )

    # OCR not available / disabled -> original hard-reject, but point at the bridge.
    raise ExtractionError(
        f"{reason}；且本机未安装本地 OCR 兜底。请 `pip install rapidocr-onnxruntime` 后重试，"
        f"或改用无视觉执行模式：scripts/ocr_dump.py 出文本 -> 按《抽取规程》抽取 -> validate.py。"
        f"（详见 docs/无视觉执行模式.md）"
    )


def _field(value: Any = None, raw: dict | None = None, normalized: bool = True) -> dict:
    return {"value": value, "confidence": 0.98 if value is not None else 0.0,
            "raw_span": raw if value is not None else None, "normalized": normalized}


def _number(value: str) -> float:
    return float(value.replace(",", ""))


def _normalize(field: str, text: str) -> tuple[Any, bool]:
    value = text.strip()
    if not value or value.upper() in {"N/A", "NOT PROVIDED", "-"}:
        return None, True
    if field == "marks" and value.upper() in {"N/M", "NO MARKS"}:
        return None, True
    if field in {"declaration_date", "invoice_date", "packing_date"}:
        return value, bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))
    if field in {"packages"}:
        return (int(value.replace(",", "")), True) if re.fullmatch(r"[0-9][0-9,]*", value) else (value, False)
    if field in {"gross_weight_kg", "net_weight_kg"}:
        match = NUMBER_RE.fullmatch(value)
        if not match:
            return value, False
        amount, unit = _number(match[1]), match[2].upper()
        if unit in {"KG", "KGS", ""}:
            return amount, True
        if unit in {"LB", "LBS"}:
            return round(amount * 0.45359237, 4), True
        if unit in {"MT", "TON"}:
            return amount * 1000, True
        return value, False
    if field == "measurement_cbm":
        match = NUMBER_RE.fullmatch(value)
        return (_number(match[1]), True) if match and match[2].upper() in {"CBM", ""} else (value, False)
    if field in {"total_value", "unit_price"}:
        match = MONEY_RE.fullmatch(value.upper())
        return ({"amount": _number(match[2]), "currency": match[1]}, True) if match else (value, False)
    if field == "quantity":
        match = NUMBER_RE.fullmatch(value)
        return ([{"value": _number(match[1]), "unit": match[2].upper()}], True) if match and match[2] else (value, False)
    if field == "hs_code":
        codes = [re.sub(r"\D", "", item) for item in re.split(r"[,;]", value)]
        codes = [item for item in codes if item]
        return (codes, True) if codes else (value, False)
    if field == "container_no":
        return re.sub(r"[\s-]", "", value).upper(), True
    if field == "transport_mode":
        mode = value.lower()
        return mode, mode in {"sea", "air", "rail", "road", "multimodal"}
    if field == "transaction_mode":
        mode = value.upper()
        return mode, mode in {"FOB", "CIF", "CFR", "EXW"}
    return value, True


def extract_pdf(pdf_bytes: bytes, doc_type: str, filename: str) -> dict:
    """Return a full Contract A document based only on the uploaded PDF's text (or local OCR)."""
    if doc_type not in DOC_TYPES:
        raise ExtractionError("不支持的单证类型。")
    lines, pages = _pdf_lines(pdf_bytes, signature=SIGNATURES[doc_type])
    ocr_used = _LAST_OCR_USED
    entries: dict[str, tuple[str, dict]] = {}
    for page_no, line in lines:
        match = LINE_RE.match(line)
        if match:
            label = match[1].strip().upper()
            entries.setdefault(label, (match[2], {"page": page_no, "text": line}))
    signature = SIGNATURES[doc_type]
    if signature not in entries:
        hint = (
            "（本件疑似编号格子表单；正则解析器读不出。真实件请走无视觉执行模式："
            "ocr_dump.py 出文本 -> 按《抽取规程》抽取。详见 docs/无视觉执行模式.md）"
            if ocr_used else "。请检查所选单证类型，或使用演示文件的文本版式。"
        )
        raise ExtractionError(f"未找到“{signature}”字段{hint}")

    warnings: list[str] = []
    if ocr_used:
        warnings.append("本结果含本地 OCR 文本，近似字符(B/8,D/Q,O/0)与金额/编号建议高DPI复核")
    fields: dict[str, dict] = {}
    for field in SCHEMA["field_specs"][doc_type]:
        if field in {"vessel_voyage", "packages_and_weight"}:
            continue
        label = LABELS[doc_type].get(field)
        entry = entries.get(label) if label else None
        if entry is None:
            fields[field] = _field()
            continue
        value, normalized = _normalize(field, entry[0])
        fields[field] = _field(value, entry[1], normalized)
        if not normalized:
            warnings.append(f"{field}: 未能归一化，保留原文")

    if doc_type == "bill_of_lading":
        vessel, voyage = entries.get("VESSEL"), entries.get("VOYAGE")
        if vessel and voyage:
            fields["vessel_voyage"] = _field(
                {"vessel": vessel[0], "voyage": voyage[0]},
                {"page": vessel[1]["page"], "text": vessel[1]["text"] + " | " + voyage[1]["text"]},
            )
        else:
            fields["vessel_voyage"] = _field()
        packages, weight = entries.get("PACKAGES"), entries.get("GROSS WEIGHT")
        if packages and weight:
            pkg, pkg_ok = _normalize("packages", packages[0])
            kg, kg_ok = _normalize("gross_weight_kg", weight[0])
            if pkg_ok and kg_ok:
                fields["packages_and_weight"] = _field(
                    {"packages": pkg, "gross_weight_kg": kg, "unit": "CTN"},
                    {"page": packages[1]["page"], "text": packages[1]["text"] + " | " + weight[1]["text"]},
                )
            else:
                fields["packages_and_weight"] = _field()
                warnings.append("packages_and_weight: 件数或毛重未能归一化")
        else:
            fields["packages_and_weight"] = _field()

    if doc_type == "customs_declaration" and "B/L NO" in entries:
        warnings.append(f"栏12 B/L or AWB Number: {entries['B/L NO'][0]}（从原文辅助提取）")
    if doc_type == "customs_declaration" and "MISSING DOCS" in entries:
        warnings.append(f"栏18 Missing Docs: {entries['MISSING DOCS'][0]}")

    populated = sum(item["value"] is not None for item in fields.values())
    if populated < 3:
        hint = (
            "（扫描件经 OCR 后仍无 `标签: 值` 版式——真实编号格子表单请用无视觉执行模式："
            "ocr_dump.py -> 抽取规程 -> validate。详见 docs/无视觉执行模式.md）"
            if ocr_used else "。请使用配套演示 PDF。"
        )
        raise ExtractionError(f"可识别字段太少（{populated}<3），无法可靠比对{hint}")
    return {
        "schema_version": SCHEMA["schema_version"],
        "prompt_version": "local-ocr-fallback-v1" if ocr_used else "local-text-v1",
        "doc_type": doc_type,
        "source_file": Path(filename).name,
        "engine": "local-deterministic-pdf-text-parser-v1",
        "fields": fields,
        "parse_meta": {"ocr_used": ocr_used, "pages": pages, "warnings": warnings},
    }
