"""契约A 三件套一致性闸门。

交叉比对三处字段定义，任一不一致即非零退出：
  1. schema/contract-A.schema.json 的 field_specs（+ doc_type_enum）
  2. prompts/抽取规程-v*.md（取最新版）§4 字段表（+ 枚举）
  3. src/validate.py 的 REQUIRED / DOC_TYPES / 枚举集合
外加第 4 参照源：schema/字段口径表.md 的"必填/可空"列（契约的权威口径）。

检查项：
  C1 各 doc_type 下 schema 字段集 == 规程字段集（对称差为空）
  C2 validator REQUIRED == 口径表"必填"字段集
  C3 validator REQUIRED ⊆ schema 字段集
  C4 doc_type 枚举三处一致
  C5 transport_mode / transaction_mode 枚举三处一致

用法：python scripts/check_consistency.py
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

from src.validate import (  # noqa: E402
    DOC_TYPES, REQUIRED, TRANSACTION_ENUM, TRANSPORT_ENUM,
)

SCHEMA_PATH = ROOT / "schema" / "contract-A.schema.json"
SPEC_PATH = ROOT / "schema" / "字段口径表.md"
PROMPTS_DIR = ROOT / "prompts"

problems: list[str] = []


def problem(check: str, msg: str) -> None:
    problems.append(f"[{check}] {msg}")


# ---------------------------------------------------------------- 源1: schema.json

schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
schema_fields: dict[str, set[str]] = {
    dt: set(schema["field_specs"][dt].keys()) for dt in DOC_TYPES
}
schema_doc_types = set(schema.get("doc_type_enum", []))


def schema_enum(field: str) -> set[str] | None:
    """从 field_specs 描述文本提取枚举（'归一化为[枚举] A|B|C'）。"""
    for dt in DOC_TYPES:
        desc = schema["field_specs"][dt].get(field, "")
        m = re.search(r"归一化为(?:枚举)?\s*(.+?)\s*$", str(desc))
        if m:
            return {t.strip() for t in m.group(1).split("|") if t.strip()}
    return None


# ---------------------------------------------------------------- 源2: 抽取规程(最新版)

prompt_files = sorted(PROMPTS_DIR.glob("抽取规程-v*.md"))
if not prompt_files:
    print("[FATAL] prompts/ 下找不到 抽取规程-v*.md")
    sys.exit(2)
prompt_path = prompt_files[-1]
prompt_text = prompt_path.read_text(encoding="utf-8")

# doc_type 列表由 validator 的 DOC_TYPES 动态推导，新增类型无需再改正则
SECTION_RE = re.compile(
    r"^###\s+4\.\d\s+.*?(" + "|".join(DOC_TYPES) + r")\s*$", re.M)
ROW_RE = re.compile(r"^\|\s*([a-z_]+)\s*\|", re.M)

prompt_fields: dict[str, set[str]] = {dt: set() for dt in DOC_TYPES}
for sec in SECTION_RE.finditer(prompt_text):
    dt = sec.group(1)
    body = prompt_text[sec.end():]
    # 表格段到下一个标题为止
    nxt = re.search(r"^#{2,4}\s", body, re.M)
    if nxt:
        body = body[:nxt.start()]
    for row in ROW_RE.finditer(body):
        prompt_fields[dt].add(row.group(1))


def prompt_enum(field: str) -> set[str] | None:
    """从规程字段表提取枚举（'枚举：A / B / C'）。"""
    m = re.search(rf"^\|\s*{field}\s*\|.*?枚举[：:]\s*([^|]+)", prompt_text, re.M)
    if m:
        return {t.strip() for t in m.group(1).split("/") if t.strip()}
    return None


# ---------------------------------------------------------------- 源4: 字段口径表(必填列)

spec_text = SPEC_PATH.read_text(encoding="utf-8")
spec_required: dict[str, set[str]] = {dt: set() for dt in DOC_TYPES}
# 口径表段落标题形如 "## 报关单(customs_declaration)"
DOC_TYPE_LABELS = {
    "customs_declaration": "报关单",
    "bill_of_lading": "提单",
    "commercial_invoice": "商业发票",
    "packing_list": "装箱单",
}
missing_labels = [dt for dt in DOC_TYPES if dt not in DOC_TYPE_LABELS]
if missing_labels:
    problem("C0", f"check_consistency 缺 doc_type 中文标签映射，无法校验口径表: {missing_labels}")
SPEC_SECTION_RE = re.compile(
    r"^##\s+(" + "|".join(sorted((DOC_TYPE_LABELS[d] for d in DOC_TYPES if d in DOC_TYPE_LABELS),
                                 key=len, reverse=True)) + r")\("
    + "(" + "|".join(DOC_TYPES) + r")\)", re.M)
for m in SPEC_SECTION_RE.finditer(spec_text):
    dt = m.group(2)
    body = spec_text[m.end():]
    nxt = re.search(r"^##\s", body, re.M)
    if nxt:
        body = body[:nxt.start()]
    for line in body.splitlines():
        r = re.match(r"^\|\s*([a-z_]+)\s*\|[^|]+\|[^|]*\|\s*(否|是)\s*\|", line)
        if r and r.group(2) == "否":  # 可空列"否"=必填
            spec_required[dt].add(r.group(1))

# ---------------------------------------------------------------- 交叉比对

# C1: schema vs 规程 字段集
for dt in DOC_TYPES:
    only_schema = schema_fields[dt] - prompt_fields[dt]
    only_prompt = prompt_fields[dt] - schema_fields[dt]
    if only_schema:
        problem("C1", f"{dt}: 仅 schema 有字段 {sorted(only_schema)}（规程缺失）")
    if only_prompt:
        problem("C1", f"{dt}: 仅规程有字段 {sorted(only_prompt)}（schema 缺失）")

# C2: validator REQUIRED vs 口径表必填
for dt in DOC_TYPES:
    only_val = set(REQUIRED[dt]) - spec_required[dt]
    only_spec = spec_required[dt] - set(REQUIRED[dt])
    if only_val:
        problem("C2", f"{dt}: validator 必填但口径表标可空 {sorted(only_val)}")
    if only_spec:
        problem("C2", f"{dt}: 口径表必填但 validator 未要求 {sorted(only_spec)}")

# C3: REQUIRED ⊆ schema
for dt in DOC_TYPES:
    extra = set(REQUIRED[dt]) - schema_fields[dt]
    if extra:
        problem("C3", f"{dt}: validator REQUIRED 中 schema 不存在的字段 {sorted(extra)}")

# C4: doc_type 枚举
if schema_doc_types != set(DOC_TYPES):
    problem("C4", f"schema doc_type_enum {sorted(schema_doc_types)} != validator DOC_TYPES {sorted(DOC_TYPES)}")
prompt_doc_types = set(SECTION_RE.findall(prompt_text))
if prompt_doc_types != set(DOC_TYPES):
    problem("C4", f"规程 §4 小节 {sorted(prompt_doc_types)} != validator DOC_TYPES {sorted(DOC_TYPES)}")

# C5: 字段枚举三处一致
for field, val_enum in (("transport_mode", TRANSPORT_ENUM), ("transaction_mode", TRANSACTION_ENUM)):
    s_enum = schema_enum(field)
    p_enum = prompt_enum(field)
    if s_enum != val_enum:
        problem("C5", f"{field}: schema={sorted(s_enum or [])} != validator={sorted(val_enum)}")
    if p_enum != val_enum:
        problem("C5", f"{field}: 规程={sorted(p_enum or [])} != validator={sorted(val_enum)}")

# ---------------------------------------------------------------- 输出

print("=" * 62)
print(f"契约A 一致性闸门（参照规程: {prompt_path.name}）")
print("=" * 62)
print(f"{'doc_type':<22}{'schema':>8}{'规程':>6}{'validator必填':>12}{'口径表必填':>10}")
for dt in DOC_TYPES:
    print(f"{dt:<22}{len(schema_fields[dt]):>8}{len(prompt_fields[dt]):>6}"
          f"{len(REQUIRED[dt]):>12}{len(spec_required[dt]):>10}")
print(f"枚举 transport_mode : schema/规程/validator -> "
      f"{sorted(schema_enum('transport_mode') or [])}")
print(f"枚举 transaction_mode: schema/规程/validator -> "
      f"{sorted(schema_enum('transaction_mode') or [])}")
print("-" * 62)

if problems:
    for p in problems:
        print(p)
    print(f"\n一致性检查: FAIL（{len(problems)} 处不一致，先改 schema/字段口径表.md，再三处同步）")
    sys.exit(1)
print("一致性检查: PASS（schema / 规程 / validator / 口径表 四处一致）")
sys.exit(0)
