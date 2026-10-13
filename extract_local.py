"""Extract Contract A fields from the four text-based PDF layouts in demo_docs.

This is a deterministic local adapter for the first demo. It reads PDF text; it
does not run OCR or the agent described in line 1's extraction prompt.
"""

from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "vendor"))

try:
    from pypdf import PdfReader
except ImportError as exc:
    raise RuntimeError("缺少 pypdf。请先运行 python -m pip install -r requirements-demo.txt") from exc


SCHEMA = json.loads(
    (ROOT / "modules" / "line1" / "schema" / "contract-A.schema.json").read_text(encoding="utf-8")
)
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


class ExtractionError(ValueError):
    """The selected file cannot be extracted within this demo's scope."""


def _pdf_lines(pdf_bytes: bytes) -> tuple[list[tuple[int, str]], int]:
    if not pdf_bytes.startswith(b"%PDF-"):
        raise ExtractionError("文件不是有效 PDF。")
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes), strict=False)
        if reader.is_encrypted:
            raise ExtractionError("首版演示不支持加密 PDF。")
        pages = len(reader.pages)
        if not 1 <= pages <= 20:
            raise ExtractionError("首版演示仅支持 1 到 20 页的 PDF。")
        lines = []
        for page_no, page in enumerate(reader.pages, 1):
            for raw in (page.extract_text() or "").splitlines():
                line = " ".join(raw.strip().split())
                if line:
                    lines.append((page_no, line))
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError("PDF 无法读取，请确认文件未损坏。") from exc
    if not lines:
        raise ExtractionError("PDF 没有可复制文字；首版演示不支持扫描件 OCR。")
    return lines, pages


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
    """Return a full Contract A document based only on the uploaded PDF's text."""
    if doc_type not in DOC_TYPES:
        raise ExtractionError("不支持的单证类型。")
    lines, pages = _pdf_lines(pdf_bytes)
    entries: dict[str, tuple[str, dict]] = {}
    for page_no, line in lines:
        match = LINE_RE.match(line)
        if match:
            label = match[1].strip().upper()
            entries.setdefault(label, (match[2], {"page": page_no, "text": line}))
    signature = SIGNATURES[doc_type]
    if signature not in entries:
        raise ExtractionError(f"未找到“{signature}”字段。请检查所选单证类型，或使用演示文件的文本版式。")

    warnings: list[str] = []
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
        raise ExtractionError("可识别字段太少，无法可靠比对。请使用配套演示 PDF。")
    return {
        "schema_version": SCHEMA["schema_version"],
        "prompt_version": "local-text-v1",
        "doc_type": doc_type,
        "source_file": Path(filename).name,
        "engine": "local-deterministic-pdf-text-parser-v1",
        "fields": fields,
        "parse_meta": {"ocr_used": False, "pages": pages, "warnings": warnings},
    }
