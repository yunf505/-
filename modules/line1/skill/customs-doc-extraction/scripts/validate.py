"""Contract-A validator (self-contained, no project dependencies).

Usage:
    python validate.py <candidate.json> [--quiet]

Exit code 1 = rejected (fix and re-extract). 0 = passed (WARNs allowed).
Checks: structure, required fields, enums, normalization compliance, and the
two anti-patterns: hallucination-style output (value w/o raw_span) and
false confidence (null value with confidence > 0).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DOC_TYPES = ("customs_declaration", "bill_of_lading")
TRANSPORT_ENUM = {"sea", "air", "rail", "road", "multimodal"}
TRANSACTION_ENUM = {"FOB", "CIF", "CFR", "EXW", "其他"}
REQUIRED = {
    "customs_declaration": [
        "declaration_no", "domestic_consignor", "trading_country", "transport_mode",
        "transaction_mode", "hs_code", "goods_name", "quantity", "total_value",
        "gross_weight_kg", "packages", "declaration_date",
    ],
    "bill_of_lading": [
        "bl_no", "shipper", "consignee", "port_of_loading", "port_of_discharge",
        "packages_and_weight", "goods_description",
    ],
}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HS10_RE = re.compile(r"^\d{10}$")
ISO4217_RE = re.compile(r"^[A-Z]{3}$")

errors: list[str] = []
warnings: list[str] = []


def err(m): errors.append(m)


def warn(m): warnings.append(m)


def check_field(name, fv):
    if not isinstance(fv, dict):
        err(f"fields.{name}: not an object"); return
    for key in ("value", "confidence", "raw_span", "normalized"):
        if key not in fv:
            err(f"fields.{name}: missing key '{key}'")
    value, conf = fv.get("value"), fv.get("confidence")
    raw_span, normalized = fv.get("raw_span"), fv.get("normalized")
    if not isinstance(conf, (int, float)) or not (0 <= conf <= 1):
        err(f"fields.{name}: confidence={conf!r} not in [0,1]")
    if value is None:
        if conf not in (0, 0.0):
            err(f"fields.{name}: value=null but confidence={conf} (missing must be 0)")
        if raw_span not in (None, ""):
            err(f"fields.{name}: value=null but raw_span non-empty (fabricated evidence)")
        return
    if not raw_span:
        err(f"fields.{name}: non-null value without raw_span")
    elif isinstance(raw_span, dict) and not raw_span.get("text"):
        err(f"fields.{name}: raw_span.text empty")
    if conf == 0:
        err(f"fields.{name}: non-null value with confidence=0 (contradiction)")
    if normalized is False:
        warn(f"fields.{name}: normalized=false (kept raw string)"); return
    if normalized is not True:
        return
    if name == "declaration_date" and not DATE_RE.match(str(value)):
        err(f"fields.{name}: {value!r} not YYYY-MM-DD")
    if name in ("gross_weight_kg", "net_weight_kg") and (not isinstance(value, (int, float)) or value < 0):
        err(f"fields.{name}: {value!r} not a non-negative number (KG)")
    if name == "packages" and (not isinstance(value, int) or isinstance(value, bool)):
        err(f"fields.{name}: {value!r} not int")
    if name == "transport_mode" and value not in TRANSPORT_ENUM:
        err(f"fields.{name}: {value!r} not in {sorted(TRANSPORT_ENUM)}")
    if name == "transaction_mode" and value not in TRANSACTION_ENUM:
        err(f"fields.{name}: {value!r} not in {sorted(TRANSACTION_ENUM)}")
    if name == "hs_code":
        for c in (value if isinstance(value, list) else [value]):
            if not HS10_RE.match(str(c)):
                warn(f"fields.{name}: {c!r} not 10 digits (kept as-is, review)")
    if name == "total_value":
        if not (isinstance(value, dict) and isinstance(value.get("amount"), (int, float))
                and ISO4217_RE.match(str(value.get("currency", "")))):
            err(f"fields.{name}: {value!r} not {{amount: float, currency: ISO4217}}")
    if name == "quantity":
        ok = isinstance(value, list) and all(
            isinstance(q, dict) and isinstance(q.get("value"), (int, float))
            and isinstance(q.get("unit"), str) and q["unit"] for q in value)
        if not ok:
            err(f"fields.{name}: {value!r} not [{{value: float, unit: str}}]")
    if name == "packages_and_weight":
        ok = (isinstance(value, dict) and isinstance(value.get("packages"), int)
              and isinstance(value.get("gross_weight_kg"), (int, float)))
        if not ok:
            err(f"fields.{name}: {value!r} not {{packages: int, gross_weight_kg: float, unit: str}}")


def validate(candidate: dict) -> int:
    doc_type = candidate.get("doc_type")
    if doc_type not in DOC_TYPES:
        err(f"doc_type={doc_type!r} not in {DOC_TYPES}")
        for e in errors: print(f"[ERR] {e}")
        print(f"Result: REJECTED (doc_type invalid)")
        return 1
    if not candidate.get("source_file"):
        err("source_file missing")
    if not candidate.get("prompt_version"):
        err("prompt_version missing (top-level, required)")
    if not candidate.get("engine"):
        err("engine missing (top-level, required)")
    fields = candidate.get("fields")
    if not isinstance(fields, dict):
        err("fields missing or not an object"); return 1
    for name in REQUIRED[doc_type]:
        if name not in fields:
            err(f"fields.{name}: required field entry missing (emit null explicitly)")
    for name, fv in fields.items():
        check_field(name, fv)
    meta = candidate.get("parse_meta")
    if not isinstance(meta, dict):
        err("parse_meta missing")
    else:
        if not isinstance(meta.get("ocr_used"), bool):
            err("parse_meta.ocr_used must be bool")
        if not isinstance(meta.get("pages"), int) or meta.get("pages", 0) < 1:
            err("parse_meta.pages must be int >= 1")
        if not isinstance(meta.get("warnings"), list):
            err("parse_meta.warnings must be a string array")
        elif meta["warnings"]:
            warn(f"parse_meta.warnings non-empty: {meta['warnings']}")
    for e in errors: print(f"[ERR] {e}")
    for w in warnings: print(f"[WARN] {w}")
    print(f"Result: {len(fields)} fields | ERR {len(errors)} | WARN {len(warnings)}"
          f" -> {'REJECTED' if errors else 'PASS'}")
    return 1 if errors else 0


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__); return 2
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"[ERR] file not found: {path}"); return 2
    try:
        candidate = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"[ERR] JSON parse error: {e}"); return 2
    return validate(candidate)


if __name__ == "__main__":
    sys.exit(main())
