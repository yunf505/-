"""契约A 校验器（agent 模式硬闸门）。

任何 agent 产出的候选 JSON，必须通过本校验器才算有效交付。

用法：
    python src/validate.py <candidate.json>
    python src/validate.py <candidate.json> --quiet   # 只输出结果行

判定：
    [ERR]  违反契约（缺失必填字段/结构错误/归一化不达标/猜测填充嫌疑）→ 退出码 1，打回重抽
    [WARN] 不阻塞但需复核（HS 不足位/normalized=false/warnings 非空）→ 退出码 0
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from src.config import SCHEMA_PATH  # noqa: E402

# ---------------------------------------------------------------- 常量

DOC_TYPES = ("customs_declaration", "bill_of_lading", "commercial_invoice", "packing_list")

TRANSPORT_ENUM = {"sea", "air", "rail", "road", "multimodal"}
TRANSACTION_ENUM = {"FOB", "CIF", "CFR", "EXW", "其他"}

# 必填字段（来自 schema/字段口径表.md；口径表为准，改这里须先改口径表）
REQUIRED = {
    "customs_declaration": [
        "declaration_no", "domestic_consignor", "trading_country",
        "transport_mode", "transaction_mode", "hs_code", "goods_name",
        "quantity", "total_value", "gross_weight_kg", "packages",
        "declaration_date",
    ],
    "bill_of_lading": [
        "bl_no", "shipper", "consignee", "port_of_loading",
        "port_of_discharge", "packages_and_weight", "goods_description",
    ],
    # v1.1 新增（只加不改）。必填集刻意收紧：让"单证上确实没有该栏"能合法输出
    # null 而不被闸门卡死；invoice_no / container_no 属跨证强键但"尽力抽"，不列必填。
    "commercial_invoice": [
        "invoice_no", "invoice_date", "seller", "buyer",
        "goods_name", "total_value",
    ],
    "packing_list": [
        "goods_name", "packages", "gross_weight_kg",
    ],
}

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HS10_RE = re.compile(r"^\d{10}$")
ISO4217_RE = re.compile(r"^[A-Z]{3}$")
CONTAINER_RE = re.compile(r"^[A-Z]{4}\d{7}$")

errors: list[str] = []
warnings: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


def warn(msg: str) -> None:
    warnings.append(msg)


# ---------------------------------------------------------------- 字段级检查

def check_field(name: str, fv: dict) -> None:
    """检查单个字段的 FieldValue 结构与归一化达标情况。"""
    if not isinstance(fv, dict):
        err(f"fields.{name}: 不是对象（应为 {{value, confidence, raw_span, normalized}}）")
        return
    for key in ("value", "confidence", "raw_span", "normalized"):
        if key not in fv:
            err(f"fields.{name}: 缺 key '{key}'")

    value, conf = fv.get("value"), fv.get("confidence")
    raw_span, normalized = fv.get("raw_span"), fv.get("normalized")

    # --- confidence 结构 ---
    if not isinstance(conf, (int, float)) or not (0 <= conf <= 1):
        err(f"fields.{name}: confidence={conf!r} 不在 [0,1]")

    # --- 缺失字段的铁律 ---
    if value is None:
        if conf not in (0, 0.0):
            err(f"fields.{name}: value=null 但 confidence={conf}（缺失必须 confidence=0）")
        if raw_span not in (None, ""):
            err(f"fields.{name}: value=null 但 raw_span 非空（缺失不得编造原文依据）")
        return

    # --- 非空字段必须有原文依据 ---
    if not raw_span:
        err(f"fields.{name}: value 非空但 raw_span 缺失（禁止无依据输出）")
    elif isinstance(raw_span, dict) and not raw_span.get("text"):
        err(f"fields.{name}: raw_span.text 为空")
    if isinstance(conf, (int, float)) and conf == 0:
        err(f"fields.{name}: value 非空但 confidence=0（自相矛盾，疑似猜测）")

    # --- 归一化达标检查（只在声明 normalized=true 时强制） ---
    if normalized is False:
        warn(f"fields.{name}: normalized=false（保留原串，需人工复核）")
        return
    if normalized is not True:
        return  # 结构错误已在上面报过

    if name in ("declaration_date", "invoice_date", "packing_date") and not DATE_RE.match(str(value)):
        err(f"fields.{name}: {value!r} 不符合 YYYY-MM-DD")
    if name in ("gross_weight_kg", "net_weight_kg"):
        if not isinstance(value, (int, float)) or value < 0:
            err(f"fields.{name}: {value!r} 不是非负数值(KG)")
    if name == "packages":
        if not isinstance(value, int) or isinstance(value, bool):
            err(f"fields.{name}: {value!r} 不是 int")
    if name == "transport_mode" and value not in TRANSPORT_ENUM:
        err(f"fields.{name}: {value!r} 不在枚举 {sorted(TRANSPORT_ENUM)}")
    if name == "transaction_mode" and value not in TRANSACTION_ENUM:
        err(f"fields.{name}: {value!r} 不在枚举 {sorted(TRANSACTION_ENUM)}")
    if name in ("hs_code",):
        codes = value if isinstance(value, list) else [value]
        for c in codes:
            if not HS10_RE.match(str(c)):
                warn(f"fields.{name}: {c!r} 不是 10 位数字（不足位保留原样，需复核）")
    if name in ("total_value", "unit_price"):
        if not (isinstance(value, dict) and isinstance(value.get("amount"), (int, float))
                and ISO4217_RE.match(str(value.get("currency", "")))):
            err(f"fields.{name}: {value!r} 不符合 {{amount: float, currency: ISO4217}}")
    if name == "quantity":
        ok = isinstance(value, list) and all(
            isinstance(q, dict) and isinstance(q.get("value"), (int, float))
            and isinstance(q.get("unit"), str) and q["unit"]
            for q in value)
        if not ok:
            err(f"fields.{name}: {value!r} 不符合 [{{value: float, unit: str}}]")
    if name == "packages_and_weight":
        ok = (isinstance(value, dict) and isinstance(value.get("packages"), int)
              and isinstance(value.get("gross_weight_kg"), (int, float)))
        if not ok:
            err(f"fields.{name}: {value!r} 不符合 {{packages: int, gross_weight_kg: float, unit: str}}")
    if name == "measurement_cbm":
        if not isinstance(value, (int, float)) or value < 0:
            err(f"fields.{name}: {value!r} 不是非负数值(CBM)")
    if name == "container_no":
        # ISO 6346：4 个大写字母 + 7 位数字。跨证最强键，格式不对必须人工复核
        stripped = re.sub(r"[\s\-]", "", str(value)).upper()
        if not CONTAINER_RE.match(stripped):
            warn(f"fields.{name}: {value!r} 不符合 ISO 6346(4字母+7数字)，"
                 f"去空格后为 {stripped!r}；作为跨证主键前需人工复核")


# ---------------------------------------------------------------- 主流程

def validate(candidate: dict, quiet: bool = False) -> int:
    # --- 顶层结构 ---
    doc_type = candidate.get("doc_type")
    if doc_type not in DOC_TYPES:
        err(f"doc_type={doc_type!r} 不在 {DOC_TYPES}")
        for e in errors: print(f"[ERR] {e}")
        print("校验结果: REJECTED (doc_type 无效)")
        return 1
    if not candidate.get("source_file"):
        err("source_file 缺失")
    if not candidate.get("prompt_version"):
        err("prompt_version 缺失（顶层必填：无法追溯所用的抽取规程版本）")
    if not candidate.get("engine"):
        err("engine 缺失（顶层必填：无法记录执行引擎，影响可复现性）")

    fields = candidate.get("fields")
    if not isinstance(fields, dict):
        err("fields 缺失或不是对象")
        return 1

    # --- 必填字段齐全（缺失条目本身必须存在且 value=null） ---
    for name in REQUIRED[doc_type]:
        if name not in fields:
            err(f"fields.{name}: 必填字段条目缺失（即使 value=null 也须显式给出）")

    # --- 逐字段检查 ---
    for name, fv in fields.items():
        check_field(name, fv)

    # --- parse_meta ---
    meta = candidate.get("parse_meta")
    if not isinstance(meta, dict):
        err("parse_meta 缺失或不是对象")
    else:
        if not isinstance(meta.get("ocr_used"), bool):
            err("parse_meta.ocr_used 必须为 bool")
        if not isinstance(meta.get("pages"), int) or meta.get("pages", 0) < 1:
            err("parse_meta.pages 必须为 >=1 的 int")
        if not isinstance(meta.get("warnings"), list):
            err("parse_meta.warnings 必须为 string 数组")
        elif meta["warnings"]:
            warn(f"parse_meta.warnings 非空: {meta['warnings']}")

    # --- 输出 ---
    if not quiet:
        for e in errors:
            print(f"[ERR] {e}")
        for w in warnings:
            print(f"[WARN] {w}")
    n_fields = len(fields)
    print(f"校验结果: {n_fields} 字段 | ERR {len(errors)} | WARN {len(warnings)}"
          f" -> {'打回重抽' if errors else '通过'}")
    return 1 if errors else 0


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return 2
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"[ERR] 文件不存在: {path}")
        return 2
    try:
        candidate = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"[ERR] JSON 解析失败: {e}")
        return 2
    quiet = "--quiet" in sys.argv
    return validate(candidate, quiet=quiet)


if __name__ == "__main__":
    sys.exit(main())
