"""One-shot prep for the NO-VISION extraction loop (decision: local OCR bridge).

Purpose: let a TEXT-ONLY agent (e.g. Codex / a CLI LLM with no image input) extract
Contract-A from a scanned document without ever needing to "see" it. This script:

    1. renders the PDF locally and runs local RapidOCR (via ocr_dump.py, offline), and
    2. prints ONE paste-ready prompt that bundles: the Contract-A field list for the
       chosen doc_type, the normalization rules, the extraction discipline
       (value + confidence + raw_span, null-when-unsure, never guess), and the OCR text.

You then paste that whole block into your text agent; it answers with a Contract-A JSON;
you gate it with validate.py. No vision required anywhere.

Usage:
    python no_vision_prep.py <file.pdf> --pages 2 --dpi 200 --doc-type customs_declaration
    python no_vision_prep.py <scan.pdf> --rotate 90 --out prep.txt      # write prompt to a file
    python no_vision_prep.py <file.pdf> --pages 49-52 --doc-type bill_of_lading

Notes:
    - Fully local. No cloud OCR, no LLM call from this script (the LLM step is you pasting
      the prompt into your own agent). Real documents never leave the machine.
    - The OCR text is the agent's ONLY evidence; raw_span must quote a line from it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from ocr_dump import dump_pdf, load_ocr  # local import (same directory)


def _find_schema() -> dict:
    env = Path(__file__).resolve()
    candidates = [
        HERE.parent / "schema" / "contract-A.schema.json",           # modules/line1/scripts -> modules/line1/schema
        HERE.parent.parent / "schema" / "contract-A.schema.json",
        HERE.parent.parent / "modules" / "line1" / "schema" / "contract-A.schema.json",
    ]
    import os
    if os.getenv("CONTRACT_A_SCHEMA"):
        candidates.insert(0, Path(os.environ["CONTRACT_A_SCHEMA"]))
    for cand in candidates:
        if cand.is_file():
            return json.loads(cand.read_text(encoding="utf-8"))
    raise SystemExit("[ERR] 找不到 contract-A.schema.json；用 CONTRACT_A_SCHEMA 指定路径。")


PREAMBLE = """你是跨境单证「信息抽取引擎」。铁律：只抽取、不判定；不做差异比对/风险分级（那是下游模块）。
严格输出**一个 Contract-A JSON**，不要输出任何解释文字。

结构要求（每个字段都是对象）：
  "字段名": {"value": <归一化值或 null>, "confidence": <0~1>, "raw_span": "<引用下方 OCR 文本的原文行>", "normalized": true|false}

抽取纪律：
- 缺失、空白、被印章遮挡、OCR 读不清 → value=null、confidence=0、raw_span=null。宁可空，不可猜。
- 每个非空 value 必须有 raw_span（逐字引用下面 OCR 文本中的那一行/那一段）。
- confidence 宁低勿高：OCR 有噪声，形近字符 B/8、D/Q、O/0、I/1、千分位逗号 vs 小数点 易错。
  关键 ID / 金额 / HS 码若不确定，就 null 或把 confidence 压到 0.5 以下并在 parse_meta.warnings 说明。

归一化规则（value 必须是归一化后的形态，原文留在 raw_span）：
- 日期 → YYYY-MM-DD
- 金额 → {"amount": <float>, "currency": "<ISO4217 三位>"}
- 重量 → float，统一换算为 KG（LB×0.45359237，MT/TON×1000）
- HS 码 → 去点号、补齐 10 位数字；多个用数组
- 件数 packages → int
- 数量 quantity → [{"value": <float>, "unit": "<单位>"}]
- transport_mode ∈ sea|air|rail|road|multimodal（如 CBP Mode 码 11/12→sea, 20/21→air, 30→road, 40→rail）
- transaction_mode ∈ FOB|CIF|CFR|EXW|其他

顶层还要带：schema_version、prompt_version、doc_type、source_file、engine（写你的模型+“local-rapidocr-ocr-v1”）、
fields、parse_meta{ocr_used:true, pages:<int>, warnings:[...]}。

"""


def build_prompt(pdf: Path, doc_type: str, dpi: int, pages: str | None, rotate: int) -> tuple[str, dict]:
    schema = _find_schema()
    specs = schema["field_specs"].get(doc_type)
    if not specs:
        raise SystemExit(f"[ERR] doc_type={doc_type!r} 不在 schema.field_specs 里：{list(schema['field_specs'])}")
    ocr = load_ocr()
    res = dump_pdf(pdf, ocr, dpi, pages, rotate, annotate=False)
    if "error" in res:
        raise SystemExit(f"[ERR] {res['error']}")
    field_lines = "\n".join(f"  - {name}：{desc}" for name, desc in specs.items())
    prompt = (
        PREAMBLE
        + f"本次 doc_type = {doc_type}\n"
        + f"必须输出的字段清单（缺失也要显式给 null）：\n{field_lines}\n\n"
        + "==================== 以下为该扫描件本地 OCR 文本（唯一依据） ====================\n"
        + res["text"]
        + "\n==================== OCR 文本结束 ====================\n\n"
        + "现在，只输出 Contract-A JSON。\n"
    )
    return prompt, res


def main() -> int:
    ap = argparse.ArgumentParser(description="No-vision prep: OCR a PDF and emit a paste-ready Codex prompt.")
    ap.add_argument("pdf")
    ap.add_argument("--doc-type", default="customs_declaration",
                    help="customs_declaration | bill_of_lading | commercial_invoice | packing_list")
    ap.add_argument("--pages", help="e.g. 2 or 1-5,8 or 'all' (default all)")
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--rotate", type=int, default=0, choices=[0, 90, 180, 270])
    ap.add_argument("--out", help="write the prompt to this file instead of stdout")
    args = ap.parse_args()

    src = Path(args.pdf)
    if not src.exists():
        print(f"[ERR] not found: {src}", file=sys.stderr)
        return 2
    prompt, res = build_prompt(src, args.doc_type, args.dpi, args.pages, args.rotate)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(prompt, encoding="utf-8")
        print(f"[ok] prompt -> {args.out}  ({len(res['pages'])} pages OCR'd)")
        print("     把该文件内容整段贴给你的文本 agent，让它输出 Contract-A JSON，再 validate.py。")
    else:
        print(prompt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
