"""
线2 跨单证比对引擎
====================

按 fixtures-synthetic/README.md 第 1 节的六票验收硬指标实现。

**判定原则（分工表硬约束①）**：全部走确定性规则，LLM 不参与任何判定。
本文件里没有一处调用大模型，message 全部 f-string 模板化。

**规格来源**（优先级从高到低）：
    1. `01-规格/v1.1修订必读-…md`（A 于 2026-10-07 发布，覆盖交接方 v1.0 的失效部分）
    2. `01-规格/美国单证映射表.md`（跨证同一实体比对矩阵、港口码规则、金额容差公式）
    3. `01-规格/三级分色与判定阈值.md`（归一化六步、相似度阈值、降级规则）
    4. `01-规格/契约B-schema.md`（输出结构，6 必填字段冻结）

**Level 只用三个值**：red / yellow / orange（分工表硬约束③）。
`not_comparable` 单独放在顶层 `not_comparable[]` 数组，不污染 diffs[]。

**CLI**:
    python compare.py <fixtures-root>            # 跑六票合并 → differences.json
    python compare.py <ticket-dir>               # 跑单票 → 打印到 stdout
    python compare.py <path> -o out.json         # 指定输出文件
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable

import rules as compliance_rules


# Windows 控制台默认 GBK，强制 stdout/stderr 用 UTF-8 以便输出 ↔ / 中文
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ===========================================================================
# 常量
# ===========================================================================

CONTRACT_VERSION = "B-0.1"
ENGINE_VERSION = "compare-engine-v0.1"

# 归一化常量（阈值文档第二节 + v1.1 §3.3）
PARTY_LEGAL_SUFFIXES = r"\b(co|corp|ltd|limited|inc|company|llc)\b"
ID_SIMILARITY_THRESHOLD = 0.70     # fixtures/README §2 实测 E6 ratio=0.769，取 0.70 留余量
PARTY_SIMILARITY_THRESHOLD = 0.85  # 阈值文档第二节
CONFIDENCE_FLOOR = 0.70            # 阈值文档第三节

# 金额容差（v1.1 §3.1 官方公式）
def amount_tol(a: float, b: float) -> float:
    return max(1.00, 0.005 * max(abs(a), abs(b)))

# 数值分档阈值（阈值文档第一节）
WEIGHT_GREEN_REL = 0.02
NUM_YELLOW_REL = 0.05

# ISO 6346 集装箱号：4 位大写字母 + 7 位数字
CONTAINER_NO_RE = re.compile(r"\b[A-Z]{4}\d{7}\b")

# 提单 goods_description 内嵌 HS：形如 "HS 4820.10" / "HS: 8708.10"
HS_INLINE_RE = re.compile(r"HS\s*[:：]?\s*(\d[\d.]*)", re.IGNORECASE)

# 报关单 warnings 里的 B/L 编号：形如 "栏12 B/L or AWB Number: MAEU SZHVJM535（...）"
BL_NO_IN_WARNINGS_RE = re.compile(
    r"B/L\s*or\s*AWB\s*Number\s*[:：]\s*([A-Z0-9][A-Z0-9\s\-]*?)(?:（|\(|$)",
    re.IGNORECASE,
)

# 地址抽取（v1.1 §3.2）：形如 "Add: XXX" / "Address: XXX" / "Addr：XXX"
ADDR_IN_SPAN_RE = re.compile(
    r"(?:add|address|addr)\s*[:：]\s*(.+?)(?:\||$)",
    re.IGNORECASE,
)

# 7501 栏18 Missing Docs（Q4 决策：单条件缺件判定）
MISSING_DOCS_RE = re.compile(r"栏\s*18\s*Missing Docs\s*[:：]\s*(.+?)(?:（|$)", re.IGNORECASE)

# 指示抬头
TO_ORDER_VALUES = {"TO ORDER", "TO THE ORDER", "TO ORDER OF SHIPPER", "ORDER"}

# 建议文案（模板化，非 LLM）
SUGGEST_ACTIONS = {
    "CMP-NUM-AMOUNT":   "核对发票与报关单金额，确认是否存在漏报或错报；容差内差异不阻断",
    "CMP-NUM-WEIGHT":   "核对实际装柜毛重/净重，必要时向货代索取磅单",
    "CMP-NUM-PIECES":   "向报关行确认实际装柜件数，少报属申报不实",
    "CMP-NUM-QTY":      "核对合同数量与申报数量",
    "CMP-STR-PARTY":    "核对进出口主体资格，确认是否存在关联关系或代理关系",
    "CMP-IDENTITY":     "疑似身份冒用：地址相同而主体名不同，须核实实际进口方",
    "CMP-CURRENCY":     "币种不一致属重大差异，立即停止申报并核对合同",
    "CMP-HS":           "跨证 HS 编码不一致，须核对归类依据（GRI 及类/章注）",
    "CMP-PORT":         "港口不一致，须核对提单与报关单填报口径",
    "CMP-PORT-NC":      "港口一码一名，无法直接比对；如需硬判请查 HTS Annex C",
    "CMP-ID-BL":        "提单号不一致，须核对是否为 OCR 噪声或换单",
    "CMP-ID-CONTAINER": "箱号不一致，须核对实际装柜箱号",
    "CMP-DATE":         "日期链矛盾，须核对制单顺序与实际装运日",
    "CMP-MISSING":      "字段缺失，向报关行/货代索取完整单证；缺失≠0",
    "CMP-DOC-MISSING":  "CBP 已登记缺件（7501 栏18），须补齐后重新申报",
}


# ===========================================================================
# 跨证字段矩阵（映射表 §3）
# ===========================================================================
# 每一项：(semantic_key, kind, {doc_type: path_spec}, rule_id)
# path_spec 可以是：
#   - list[str]     → 逐层进入 fields.<key>.value 的子路径
#   - str           → 特殊提取标记（"__extract_xxx__"）
#
# 装货港/卸货港：报关单用 port_of_dispatch/port_of_destination，
# 其余三方用 port_of_loading/port_of_discharge（映射表 §1）。

FIELD_MATRIX: list = [
    # ---- 买方链（映射表 §3：7501 栏30 Importer of Record）----
    ("买方", "party_buyer", {
        "customs_declaration": ["domestic_consignor"],
        "commercial_invoice":  ["buyer"],
        "packing_list":        ["buyer"],
        "bill_of_lading":      ["consignee"],   # TO ORDER 时自动改用 notify_party
    }, "CMP-STR-PARTY"),

    # ---- 卖方链（映射表 §3：不与 7501 比）----
    ("卖方", "party_seller", {
        "commercial_invoice": ["seller"],
        "packing_list":       ["seller"],
        "bill_of_lading":     ["shipper"],
    }, "CMP-STR-PARTY"),

    # ---- 件数 ----
    ("件数", "pieces", {
        "customs_declaration": ["packages"],
        "commercial_invoice":  ["packages"],
        "packing_list":        ["packages"],
        "bill_of_lading":      ["packages_and_weight", "packages"],
    }, "CMP-NUM-PIECES"),

    # ---- 毛重 ----
    ("毛重", "weight", {
        "customs_declaration": ["gross_weight_kg"],
        "commercial_invoice":  ["gross_weight_kg"],
        "packing_list":        ["gross_weight_kg"],
        "bill_of_lading":      ["packages_and_weight", "gross_weight_kg"],
    }, "CMP-NUM-WEIGHT"),

    # ---- 净重（提单没有单独净重）----
    ("净重", "weight", {
        "customs_declaration": ["net_weight_kg"],
        "commercial_invoice":  ["net_weight_kg"],
        "packing_list":        ["net_weight_kg"],
    }, "CMP-NUM-WEIGHT"),

    # ---- 金额（映射表 §3：只在报关单 ↔ 发票之间比）----
    ("金额", "amount", {
        "customs_declaration": ["total_value", "amount"],
        "commercial_invoice":  ["total_value", "amount"],
    }, "CMP-NUM-AMOUNT"),

    # ---- 币种（阈值文档第一节：单独精确比）----
    ("币种", "currency", {
        "customs_declaration": ["total_value", "currency"],
        "commercial_invoice":  ["total_value", "currency"],
    }, "CMP-CURRENCY"),

    # ---- 装货港 ----
    ("装货港", "port", {
        "customs_declaration": ["port_of_dispatch"],
        "commercial_invoice":  ["port_of_loading"],
        "packing_list":        ["port_of_loading"],
        "bill_of_lading":      ["port_of_loading"],
    }, "CMP-PORT"),

    # ---- 卸货港 ----
    ("卸货港", "port", {
        "customs_declaration": ["port_of_destination"],
        "commercial_invoice":  ["port_of_discharge"],
        "packing_list":        ["port_of_discharge"],
        "bill_of_lading":      ["port_of_discharge"],
    }, "CMP-PORT"),

    # ---- 数量（[{value, unit}] 结构，只比 value 之和）----
    ("数量", "quantity", {
        "customs_declaration": ["quantity"],
        "commercial_invoice":  ["quantity"],
        "packing_list":        ["quantity"],
    }, "CMP-NUM-QTY"),

    # ---- 箱号（提单无字段，从 goods_description 抽 ISO 6346）----
    ("箱号", "container_no", {
        "commercial_invoice": ["container_no"],
        "packing_list":       ["container_no"],
        "bill_of_lading":     "__extract_container__",
    }, "CMP-ID-CONTAINER"),

    # ---- 提单号（报关单无字段，从 warnings 抽；Q3 决策）----
    ("提单号", "bl_no", {
        "customs_declaration": "__extract_bl_no_from_warnings__",
        "bill_of_lading":      ["bl_no"],
    }, "CMP-ID-BL"),

    # ---- HS 编码（发票/装箱单没有 hs_code 字段，只走 报关单 ↔ 提单）----
    ("HS 编码", "hs", {
        "customs_declaration": ["hs_code"],
        "bill_of_lading":      "__extract_hs__",
    }, "CMP-HS"),
]


# ===========================================================================
# 工具函数：归一化 & 抽取
# ===========================================================================

def span_text(raw: Any) -> str:
    """兼容 raw_span 的 str / dict / None 三种形式（fixtures/README §3-①）。"""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        return raw.get("text", "") or ""
    return str(raw)


def norm_party(s: str) -> str:
    """主体名归一化六步（阈值文档第二节，顺序不能乱）。"""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))                 # 1 全角→半角
    s = s.lower()                                              # 2 统一小写
    s = s.replace("&", " and ")                                # 3 & → and
    s = re.sub(r"[.,;:'\"`/\-\\]", " ", s)                     # 4 标点转空格
    s = re.sub(PARTY_LEGAL_SUFFIXES, " ", s)                   # 5 去法律后缀
    s = re.sub(r"\s+", " ", s).strip()                         # 6 空白折叠
    return s


def norm_addr(s: str) -> str:
    """地址归一化（阈值文档第二节：不能去后缀、不能去标点里的语义部分）。"""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    s = s.lower()
    s = re.sub(r"\s+", "", s)                                  # 去全部空白（88 号 vs 88号）
    s = re.sub(r"[.,;:'\"`/\-]", "", s)
    return s


def norm_id(s: str) -> str:
    """编号归一化：去空格、去分隔符、大写（v1.1 §3.3）。"""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s)).upper()
    s = re.sub(r"[\s\-_/.,]", "", s)
    return s


def norm_port_name(s: str) -> str:
    """港口地名归一化：大写、去标点、折叠空白（映射表 §5.1 规则3）。"""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s)).upper()
    s = re.sub(r"[.,;:'\"`/\-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def hs_norm(s: str) -> str:
    """HS 编码归一化：去点、右侧补 0 到 10 位。"""
    if s is None:
        return ""
    digits = re.sub(r"\D", "", str(s))
    return digits.ljust(10, "0")[:10]


def sim(a: str, b: str) -> float:
    """字符串相似度（difflib.SequenceMatcher.ratio）。"""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def addr_from_span(raw_span_text: str) -> str | None:
    """从 raw_span.text 正则抽地址（v1.1 §3.2），返回归一化后的地址。"""
    if not raw_span_text:
        return None
    m = ADDR_IN_SPAN_RE.search(raw_span_text)
    if not m:
        return None
    return norm_addr(m.group(1))


def extract_container_from_bl(doc: dict) -> str | None:
    """从提单 goods_description 正则捞 ISO 6346 箱号（v1.1 §3.5）。"""
    gd = doc.get("fields", {}).get("goods_description") or {}
    text = str(gd.get("value") or "") + " " + span_text(gd.get("raw_span"))
    m = CONTAINER_NO_RE.search(text)
    return m.group(0) if m else None


def extract_hs_from_bl(doc: dict) -> list | None:
    """从提单 goods_description 抽内嵌 HS 并归一化到 10 位数组。"""
    gd = doc.get("fields", {}).get("goods_description") or {}
    text = str(gd.get("value") or "") + " " + span_text(gd.get("raw_span"))
    hits = HS_INLINE_RE.findall(text)
    if not hits:
        return None
    return [hs_norm(h) for h in hits]


def extract_bl_no_from_warnings(doc: dict) -> str | None:
    """从报关单 parse_meta.warnings 抽提单号（Q3 决策）。"""
    warnings = doc.get("parse_meta", {}).get("warnings") or []
    for w in warnings:
        m = BL_NO_IN_WARNINGS_RE.search(str(w))
        if m:
            return m.group(1).strip()
    return None


# ===========================================================================
# 字段元数据访问
# ===========================================================================

class FieldMeta:
    """封装一个字段在某份 doc 里的取值 + 元数据。"""
    __slots__ = ("value", "confidence", "raw_span", "normalized",
                 "path_str", "source_file", "doc_warnings", "doc_type",
                 "field_key", "extracted")

    def __init__(self, value: Any, confidence: float, raw_span: Any,
                 normalized: bool, path_str: str, source_file: str,
                 doc_warnings: list, doc_type: str, field_key: str,
                 extracted: bool = False):
        self.value = value
        self.confidence = confidence
        self.raw_span = raw_span
        self.normalized = normalized
        self.path_str = path_str
        self.source_file = source_file
        self.doc_warnings = doc_warnings or []
        self.doc_type = doc_type
        self.field_key = field_key
        self.extracted = extracted

    def side_dict(self) -> dict:
        """按契约B left/right 结构组装。"""
        return {
            "source": self.source_file,
            "doc_type": self.doc_type,
            "path": self.path_str,
            "value": self.value,
            "confidence": self.confidence,
            "raw_span": self.raw_span,
            "normalized": self.normalized,
            "extracted": self.extracted,
        }


def _get_field_raw(doc: dict, path: list) -> tuple:
    """按路径从 fields.<key>.value 逐层取值。返回 (value, field_dict, path_str)。"""
    fields = doc.get("fields", {}) or {}
    if not path:
        return None, {}, ""
    top_key = path[0]
    field_dict = fields.get(top_key) or {}
    v = field_dict.get("value")
    # 逐层进入子路径（针对 packages_and_weight.packages 之类的嵌套）
    for k in path[1:]:
        if isinstance(v, dict):
            v = v.get(k)
        else:
            v = None
            break
    path_str = "fields." + ".".join(path)
    return v, field_dict, path_str


def get_field_meta(doc: dict, path_spec: Any, semantic_kind: str = "") -> FieldMeta | None:
    """
    通用字段取值。path_spec 是 list 或 "__extract_xxx__" 标记。
    返回 None 表示这份 doc 没有该字段（不是"字段值为 null"）。
    """
    doc_type = doc.get("doc_type", "")
    source_file = doc.get("source_file") or doc.get("_source_file") or ""
    doc_warnings = doc.get("parse_meta", {}).get("warnings") or []

    # ---- 特殊提取通道 ----
    if isinstance(path_spec, str) and path_spec.startswith("__extract_"):
        if path_spec == "__extract_container__":
            v = extract_container_from_bl(doc)
            if v is None:
                return None
            gd = doc.get("fields", {}).get("goods_description") or {}
            return FieldMeta(
                value=v,
                confidence=gd.get("confidence", 0.9),
                raw_span=gd.get("raw_span"),
                normalized=True,
                path_str="fields.goods_description[regex:container_no]",
                source_file=source_file,
                doc_warnings=doc_warnings,
                doc_type=doc_type,
                field_key="goods_description",
                extracted=True,
            )
        if path_spec == "__extract_hs__":
            v = extract_hs_from_bl(doc)
            if v is None:
                return None
            gd = doc.get("fields", {}).get("goods_description") or {}
            return FieldMeta(
                value=v,
                confidence=gd.get("confidence", 0.9),
                raw_span=gd.get("raw_span"),
                normalized=True,
                path_str="fields.goods_description[regex:hs]",
                source_file=source_file,
                doc_warnings=doc_warnings,
                doc_type=doc_type,
                field_key="goods_description",
                extracted=True,
            )
        if path_spec == "__extract_bl_no_from_warnings__":
            v = extract_bl_no_from_warnings(doc)
            if v is None:
                return None
            return FieldMeta(
                value=v,
                confidence=0.6,       # warnings 里抽的，天生低置信
                raw_span={"page": 1, "text": v},
                normalized=False,
                path_str="parse_meta.warnings[regex:bl_no]",
                source_file=source_file,
                doc_warnings=doc_warnings,
                doc_type=doc_type,
                field_key="warnings",
                extracted=True,
            )
        return None

    # ---- TO ORDER 特殊处理：提单 consignee → 改用 notify_party ----
    if (doc_type == "bill_of_lading" and semantic_kind == "party_buyer"
            and path_spec == ["consignee"]):
        consignee_field = doc.get("fields", {}).get("consignee") or {}
        cval = consignee_field.get("value")
        if isinstance(cval, str) and cval.strip().upper() in TO_ORDER_VALUES:
            # 改用 notify_party
            np_field = doc.get("fields", {}).get("notify_party") or {}
            if np_field.get("value") is not None:
                return FieldMeta(
                    value=np_field.get("value"),
                    confidence=np_field.get("confidence", 1.0),
                    raw_span=np_field.get("raw_span"),
                    normalized=np_field.get("normalized", True),
                    path_str="fields.notify_party (consignee 为 TO ORDER，旁证)",
                    source_file=source_file,
                    doc_warnings=doc_warnings,
                    doc_type=doc_type,
                    field_key="notify_party",
                )
            return None  # 没有 notify_party 就跳过

    # ---- 常规路径 ----
    v, field_dict, path_str = _get_field_raw(doc, path_spec)
    if not field_dict:
        return None  # 该 doc_type 完全没这个字段
    return FieldMeta(
        value=v,
        confidence=field_dict.get("confidence", 1.0),
        raw_span=field_dict.get("raw_span"),
        normalized=field_dict.get("normalized", True),
        path_str=path_str,
        source_file=source_file,
        doc_warnings=doc_warnings,
        doc_type=doc_type,
        field_key=path_spec[0] if path_spec else "",
    )


# ===========================================================================
# 判定层（纯确定性）
# ===========================================================================

def _downgrade(level: str, ma: FieldMeta, mb: FieldMeta) -> tuple:
    """
    阈值文档第三节：任一侧 confidence<0.7 / normalized=false / warnings 提及此字段 → 降一级。
    返回 (new_level, reasons[])。CMP-IDENTITY 不调用本函数。
    """
    reasons = []

    def check(m: FieldMeta, tag: str):
        if m.confidence < CONFIDENCE_FLOOR:
            reasons.append(f"{tag}.confidence={m.confidence:.2f}<{CONFIDENCE_FLOOR}")
        if m.normalized is False:
            reasons.append(f"{tag}.normalized=false")
        # warnings 提及字段名（只对非抽取字段生效；抽取字段本身就是低置信通道）
        if not m.extracted:
            for w in m.doc_warnings:
                if m.field_key and m.field_key.lower() in str(w).lower():
                    reasons.append(f"{tag}.warnings 提及 {m.field_key}")
                    break

    check(ma, "left")
    check(mb, "right")

    if not reasons:
        return level, []
    downgraded = {"red": "yellow", "yellow": "orange", "orange": "orange"}.get(level, level)
    return downgraded, reasons


def amount_verdict(a: float, b: float) -> tuple:
    """
    v1.1 §3.1 官方公式 + 阈值文档 rel 分档拼接（Q3 决策）。
    返回 (verdict, abs_diff, rel_diff)。
    verdict ∈ {'match', 'within_tolerance', 'yellow', 'red'}
    """
    if a is None or b is None:
        return "missing", None, None
    abs_d = abs(a - b)
    denom = max(abs(a), abs(b), 1e-9)
    rel_d = abs_d / denom
    tol = amount_tol(a, b)
    if abs_d == 0:
        return "match", abs_d, rel_d
    if abs_d <= tol:
        return "within_tolerance", abs_d, rel_d
    if rel_d <= NUM_YELLOW_REL:
        return "yellow", abs_d, rel_d
    return "red", abs_d, rel_d


def weight_verdict(a: float, b: float) -> tuple:
    """阈值文档第一节：rel ≤ 0.02 绿 / ≤ 0.05 yellow / > 0.05 red。"""
    if a is None or b is None:
        return "missing", None, None
    abs_d = abs(a - b)
    denom = max(abs(a), abs(b), 1e-9)
    rel_d = abs_d / denom
    if abs_d == 0:
        return "match", abs_d, rel_d
    if rel_d <= WEIGHT_GREEN_REL:
        return "within_tolerance", abs_d, rel_d
    if rel_d <= NUM_YELLOW_REL:
        return "yellow", abs_d, rel_d
    return "red", abs_d, rel_d


def pieces_verdict(a, b) -> tuple:
    """阈值文档第一节：件数无容忍，必须完全相等；不等按 rel 分档。"""
    if a is None or b is None:
        return "missing", None, None
    abs_d = abs(a - b)
    denom = max(abs(a), abs(b), 1e-9)
    rel_d = abs_d / denom
    if a == b:
        return "match", 0, 0.0
    if rel_d <= NUM_YELLOW_REL:
        return "yellow", abs_d, rel_d
    return "red", abs_d, rel_d


def quantity_verdict(a_list, b_list) -> tuple:
    """
    数量：[{value, unit}] 结构。只比 value 之和（unit 不同也不管，因为单位换算超纲）。
    """
    if a_list is None or b_list is None:
        return "missing", None, None
    def total(lst):
        if not isinstance(lst, list):
            return None
        s = 0.0
        for item in lst:
            if isinstance(item, dict) and isinstance(item.get("value"), (int, float)):
                s += item["value"]
        return s
    ta, tb = total(a_list), total(b_list)
    if ta is None or tb is None:
        return "missing", None, None
    return weight_verdict(ta, tb)  # 数量走"衡器误差"档，2% 绿区


def party_verdict(a: str, b: str) -> tuple:
    """阈值文档第二节：相等 match / sim ≥ 0.85 orange / else red。"""
    if a is None or b is None:
        return "missing", None
    an, bn = norm_party(a), norm_party(b)
    if not an and not bn:
        return "match", 1.0
    if an == bn:
        return "match", 1.0
    r = sim(an, bn)
    if r >= PARTY_SIMILARITY_THRESHOLD:
        return "orange", r
    return "red", r


def id_verdict(a: str, b: str) -> tuple:
    """v1.1 §3.3：全等 match；否则 ratio ≥ 0.70 → 模糊命中（绿）；else red。"""
    if a is None or b is None:
        return "missing", None
    an, bn = norm_id(a), norm_id(b)
    if not an and not bn:
        return "match", 1.0
    if an == bn:
        return "match", 1.0
    r = sim(an, bn)
    if r >= ID_SIMILARITY_THRESHOLD:
        return "fuzzy_match", r
    return "red", r


def is_port_code(s: str) -> bool:
    """判断港口值是否为纯数字代码（映射表 §5.1）。"""
    if s is None:
        return False
    s = str(s).strip()
    return bool(s) and s.isdigit()


def port_verdict(a: str, b: str) -> tuple:
    """
    映射表 §5.1 比对规则：
      1. 两侧都是代码 → 代码串全等（去空格）
      2. 一侧代码一侧地名 → not_comparable
      3. 两侧都是地名 → 归一化后全等
    返回 (verdict, note)。verdict ∈ {'match', 'different', 'not_comparable'}
    """
    if a is None or b is None:
        return "missing", None
    a_str, b_str = str(a).strip(), str(b).strip()
    a_is_code, b_is_code = is_port_code(a_str), is_port_code(b_str)
    if a_is_code and b_is_code:
        return ("match" if a_str == b_str else "different"), "code-vs-code"
    if a_is_code != b_is_code:
        return "not_comparable", "code-vs-name"
    # 两侧都是地名
    an, bn = norm_port_name(a_str), norm_port_name(b_str)
    return ("match" if an == bn else "different"), "name-vs-name"


def currency_verdict(a: str, b: str) -> str:
    """币种精确比（阈值文档第一节）。"""
    if a is None or b is None:
        return "missing"
    return "match" if str(a).strip().upper() == str(b).strip().upper() else "red"


def hs_verdict(a_list, b_list) -> str:
    """HS 编码集合比对。任一侧空集合视为 missing。交集非空即 match。"""
    if not a_list or not b_list:
        return "missing"
    sa = {hs_norm(x) for x in a_list if isinstance(x, str)}
    sb = {hs_norm(x) for x in b_list if isinstance(x, str)}
    if not sa or not sb:
        return "missing"
    return "match" if (sa & sb) else "different"


def identity_check(ma: FieldMeta, mb: FieldMeta) -> str | None:
    """
    CMP-IDENTITY（阈值文档第二节 + v1.1 §3.2）：
    两侧 raw_span 都能抽到地址 && norm_addr 相等 && norm_party 不等 → 'red'
    否则返回 None（交给普通 party_verdict 处理）。
    """
    addr_a = addr_from_span(span_text(ma.raw_span))
    addr_b = addr_from_span(span_text(mb.raw_span))
    if not addr_a or not addr_b:
        return None
    if addr_a != addr_b:
        return None
    if norm_party(str(ma.value or "")) == norm_party(str(mb.value or "")):
        return None
    return "red"


# ===========================================================================
# 输出收集器
# ===========================================================================

class Bucket:
    """收集一票内所有输出条目。"""

    def __init__(self):
        self.diffs: list = []
        self.notes: list = []
        self.skipped: list = []
        self.not_comparable: list = []
        self.checked_fields: int = 0

    def _next_id(self, lst: list, prefix: str) -> str:
        return f"{prefix}{len(lst) + 1:03d}"

    def add_diff(self, *, field: str, semantic_key: str, level: str,
                 left: FieldMeta, right: FieldMeta, message: str, rule_id: str,
                 deviation: dict | None = None, verdict: str = "different",
                 downgrade_reasons: list | None = None,
                 category: str = "field_diff"):
        entry = {
            "id": self._next_id(self.diffs, "D"),
            "category": category,
            "field": field,
            "semantic_key": semantic_key,
            "level": level,
            "left": left.side_dict() if isinstance(left, FieldMeta) else left,
            "right": right.side_dict() if isinstance(right, FieldMeta) else right,
            "message": message,
            "rule_id": rule_id,
            "verdict": verdict,
            "suggest_action": SUGGEST_ACTIONS.get(rule_id, ""),
        }
        if deviation is not None:
            entry["deviation"] = deviation
        if downgrade_reasons:
            entry["downgrade_reasons"] = downgrade_reasons
        self.diffs.append(entry)

    def add_note(self, *, field: str, semantic_key: str, verdict: str,
                 left: FieldMeta, right: FieldMeta, message: str, rule_id: str,
                 deviation: dict | None = None):
        entry = {
            "id": self._next_id(self.notes, "N"),
            "category": "note",
            "field": field,
            "semantic_key": semantic_key,
            "verdict": verdict,
            "left": left.side_dict() if isinstance(left, FieldMeta) else left,
            "right": right.side_dict() if isinstance(right, FieldMeta) else right,
            "message": message,
            "rule_id": rule_id,
        }
        if deviation is not None:
            entry["deviation"] = deviation
        self.notes.append(entry)

    def add_not_comparable(self, *, field: str, semantic_key: str,
                           left: FieldMeta, right: FieldMeta, message: str,
                           rule_id: str, reason: str):
        self.not_comparable.append({
            "id": self._next_id(self.not_comparable, "X"),
            "category": "not_comparable",
            "field": field,
            "semantic_key": semantic_key,
            "level": "orange",      # v1.1 §3.4：not_comparable → orange
            "verdict": "not_comparable",
            "left": left.side_dict() if isinstance(left, FieldMeta) else left,
            "right": right.side_dict() if isinstance(right, FieldMeta) else right,
            "message": message,
            "rule_id": rule_id,
            "reason": reason,
            "suggest_action": SUGGEST_ACTIONS.get(rule_id, ""),
        })

    def add_skipped(self, *, field: str, reason: str, policy: str,
                    source: str = ""):
        self.skipped.append({
            "id": self._next_id(self.skipped, "S"),
            "field": field,
            "source": source,
            "reason": reason,
            "policy": policy,
        })


# ===========================================================================
# 字段级比较（一对 doc）
# ===========================================================================

def _fmt_num(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def cmp_numeric_pair(sem_key: str, kind: str, rule_id: str,
                     ma: FieldMeta, mb: FieldMeta, bucket: Bucket):
    """金额/毛重/净重/件数/数量 通用比较。"""
    bucket.checked_fields += 1

    # ---- 缺失处理（Q1 决策：一侧 null → yellow）----
    if ma.value is None and mb.value is None:
        bucket.add_skipped(
            field=f"{sem_key}({ma.field_key}↔{mb.field_key})",
            reason="两侧均为 null，跳过",
            policy="POL-BOTH-NULL",
            source=f"{ma.source_file} / {mb.source_file}",
        )
        return
    if ma.value is None or mb.value is None:
        null_side = ma if ma.value is None else mb
        other = mb if ma.value is None else ma
        bucket.add_diff(
            field=f"{sem_key}",
            semantic_key=sem_key,
            level="yellow",
            left=ma, right=mb,
            message=(f"{sem_key}缺失：{null_side.doc_type} 未载明，"
                     f"{other.doc_type}={_fmt_num(other.value)}；缺失≠0，需人工复核"),
            rule_id="CMP-MISSING",
            verdict="different",
            category="missing_field",
        )
        return

    # ---- 数值判定 ----
    if kind == "amount":
        verdict, abs_d, rel_d = amount_verdict(float(ma.value), float(mb.value))
    elif kind == "pieces":
        verdict, abs_d, rel_d = pieces_verdict(ma.value, mb.value)
    elif kind == "quantity":
        verdict, abs_d, rel_d = quantity_verdict(ma.value, mb.value)
    else:  # weight
        verdict, abs_d, rel_d = weight_verdict(float(ma.value), float(mb.value))

    deviation = {"abs": abs_d, "rel": round(rel_d, 6)} if rel_d is not None else None

    if verdict == "match":
        return  # 完全相等不输出

    if verdict == "within_tolerance":
        bucket.add_note(
            field=sem_key, semantic_key=sem_key, verdict="within_tolerance",
            left=ma, right=mb,
            message=(f"{sem_key}容差内差异：{ma.doc_type}={_fmt_num(ma.value)} ↔ "
                     f"{mb.doc_type}={_fmt_num(mb.value)}（|Δ|={abs_d:g}, "
                     f"rel={rel_d:.4%}，在阈值内不告警）"),
            rule_id=rule_id, deviation=deviation,
        )
        return

    if verdict == "fuzzy_match":
        # 数值型不会有 fuzzy_match，但保留兼容
        return

    # yellow / red
    level = verdict
    level, reasons = _downgrade(level, ma, mb)
    bucket.add_diff(
        field=sem_key, semantic_key=sem_key, level=level,
        left=ma, right=mb,
        message=(f"{sem_key}不一致：{ma.doc_type}={_fmt_num(ma.value)} ↔ "
                 f"{mb.doc_type}={_fmt_num(mb.value)}（|Δ|={abs_d:g}, "
                 f"rel={rel_d:.2%}）"),
        rule_id=rule_id, deviation=deviation,
        downgrade_reasons=reasons or None,
    )


def cmp_currency_pair(ma: FieldMeta, mb: FieldMeta, bucket: Bucket):
    bucket.checked_fields += 1
    if ma.value is None or mb.value is None:
        return
    v = currency_verdict(ma.value, mb.value)
    if v == "match":
        return
    # 币种不参与容差、不降级（阈值文档第一节：直接 red）
    bucket.add_diff(
        field="币种", semantic_key="币种", level="red",
        left=ma, right=mb,
        message=f"币种不一致：{ma.doc_type}={ma.value} ↔ {mb.doc_type}={mb.value}",
        rule_id="CMP-CURRENCY",
    )


def cmp_party_pair(sem_key: str, ma: FieldMeta, mb: FieldMeta, bucket: Bucket,
                   rule_id: str = "CMP-STR-PARTY"):
    """主体名比对（含 CMP-IDENTITY 独立通道）。"""
    bucket.checked_fields += 1

    if ma.value is None and mb.value is None:
        return
    if ma.value is None or mb.value is None:
        null_side = ma if ma.value is None else mb
        other = mb if ma.value is None else ma
        bucket.add_diff(
            field=sem_key, semantic_key=sem_key, level="yellow",
            left=ma, right=mb,
            message=(f"{sem_key}缺失：{null_side.doc_type} 未载明，"
                     f"{other.doc_type}=「{other.value}」"),
            rule_id="CMP-MISSING", category="missing_field",
        )
        return

    # ---- CMP-IDENTITY 独立通道（不降级）----
    if identity_check(ma, mb) == "red":
        addr_a = addr_from_span(span_text(ma.raw_span))
        bucket.add_diff(
            field=sem_key, semantic_key=sem_key, level="red",
            left=ma, right=mb,
            message=(f"{sem_key}疑似身份冒用：地址相同（归一化后 「{addr_a}」）"
                     f"而主体名不同（{ma.doc_type}=「{ma.value}」 ↔ "
                     f"{mb.doc_type}=「{mb.value}」）"),
            rule_id="CMP-IDENTITY",
        )
        return

    # ---- 普通字符比对 ----
    verdict, ratio = party_verdict(str(ma.value), str(mb.value))
    if verdict == "match":
        return

    if verdict == "orange":
        level = "orange"
        msg = (f"{sem_key}疑似拼写/简称差异：{ma.doc_type}=「{ma.value}」 ↔ "
               f"{mb.doc_type}=「{mb.value}」（相似度 {ratio:.3f} ≥ "
               f"{PARTY_SIMILARITY_THRESHOLD}），建议人工复核")
    else:  # red
        level = "red"
        msg = (f"{sem_key}不一致：{ma.doc_type}=「{ma.value}」 ↔ "
               f"{mb.doc_type}=「{mb.value}」（相似度 {ratio:.3f} < "
               f"{PARTY_SIMILARITY_THRESHOLD}）")

    level, reasons = _downgrade(level, ma, mb)
    bucket.add_diff(
        field=sem_key, semantic_key=sem_key, level=level,
        left=ma, right=mb, message=msg, rule_id=rule_id,
        downgrade_reasons=reasons or None,
    )


def cmp_id_pair(sem_key: str, rule_id: str,
                ma: FieldMeta, mb: FieldMeta, bucket: Bucket):
    """编号比对（bl_no / container_no），模糊匹配（v1.1 §3.3）。"""
    bucket.checked_fields += 1
    if ma.value is None or mb.value is None:
        return
    verdict, ratio = id_verdict(str(ma.value), str(mb.value))
    if verdict == "match":
        return
    if verdict == "fuzzy_match":
        bucket.add_note(
            field=sem_key, semantic_key=sem_key, verdict="fuzzy_match",
            left=ma, right=mb,
            message=(f"{sem_key}模糊匹配命中：{ma.doc_type}=「{ma.value}」 ↔ "
                     f"{mb.doc_type}=「{mb.value}」（ratio={ratio:.3f} ≥ "
                     f"{ID_SIMILARITY_THRESHOLD}），判为 OCR 噪声不告警"),
            rule_id=rule_id,
        )
        return
    # red
    level = "red"
    level, reasons = _downgrade(level, ma, mb)
    bucket.add_diff(
        field=sem_key, semantic_key=sem_key, level=level,
        left=ma, right=mb,
        message=(f"{sem_key}不一致：{ma.doc_type}=「{ma.value}」 ↔ "
                 f"{mb.doc_type}=「{mb.value}」（ratio={ratio:.3f} < "
                 f"{ID_SIMILARITY_THRESHOLD}）"),
        rule_id=rule_id,
        downgrade_reasons=reasons or None,
    )


def cmp_port_pair(sem_key: str, ma: FieldMeta, mb: FieldMeta, bucket: Bucket):
    """港口比对（映射表 §5.1）。码 vs 名 → not_comparable（Q2 决策：单独数组）。"""
    bucket.checked_fields += 1
    if ma.value is None or mb.value is None:
        return
    verdict, note = port_verdict(str(ma.value), str(mb.value))
    if verdict == "match":
        return
    if verdict == "not_comparable":
        bucket.add_not_comparable(
            field=sem_key, semantic_key=sem_key,
            left=ma, right=mb,
            message=(f"{sem_key}不可比：{ma.doc_type} 存"
                     f"{'代码' if is_port_code(str(ma.value)) else '地名'}「{ma.value}」，"
                     f"{mb.doc_type} 存"
                     f"{'代码' if is_port_code(str(mb.value)) else '地名'}「{mb.value}」；"
                     f"按映射表 §5.1 规则 2 判不可比，不判不一致"),
            rule_id="CMP-PORT-NC", reason=note,
        )
        return
    # different：码码不等 或 名名不等
    level = "red"
    level, reasons = _downgrade(level, ma, mb)
    bucket.add_diff(
        field=sem_key, semantic_key=sem_key, level=level,
        left=ma, right=mb,
        message=(f"{sem_key}不一致：{ma.doc_type}=「{ma.value}」 ↔ "
                 f"{mb.doc_type}=「{mb.value}」（同类值直接比对不等）"),
        rule_id="CMP-PORT",
        downgrade_reasons=reasons or None,
    )


def cmp_hs_pair(ma: FieldMeta, mb: FieldMeta, bucket: Bucket):
    """HS 编码跨证比对（只在 报关单 ↔ 提单 之间跑）。"""
    bucket.checked_fields += 1
    if ma.value is None or mb.value is None:
        return
    a_list = ma.value if isinstance(ma.value, list) else [ma.value]
    b_list = mb.value if isinstance(mb.value, list) else [mb.value]
    v = hs_verdict(a_list, b_list)
    if v == "match" or v == "missing":
        return
    bucket.add_diff(
        field="HS 编码", semantic_key="HS 编码", level="red",
        left=ma, right=mb,
        message=(f"HS 编码跨证不一致：{ma.doc_type}={a_list} ↔ "
                 f"{mb.doc_type}={b_list}（归一化到 10 位后无交集）"),
        rule_id="CMP-HS",
    )


# ===========================================================================
# 单对 doc 比较调度
# ===========================================================================

def compare_pair(doc_a: dict, doc_b: dict, bucket: Bucket):
    """按 FIELD_MATRIX 对两份 doc 跑所有适用的字段级比较。"""
    for sem_key, kind, mapping, rule_id in FIELD_MATRIX:
        ta = doc_a.get("doc_type")
        tb = doc_b.get("doc_type")
        if ta not in mapping or tb not in mapping:
            continue  # 这一对 doc 里至少一方没这个字段

        ma = get_field_meta(doc_a, mapping[ta], semantic_kind=kind)
        mb = get_field_meta(doc_b, mapping[tb], semantic_kind=kind)
        if ma is None or mb is None:
            continue

        if kind in ("amount", "weight", "pieces", "quantity"):
            cmp_numeric_pair(sem_key, kind, rule_id, ma, mb, bucket)
        elif kind == "currency":
            cmp_currency_pair(ma, mb, bucket)
        elif kind in ("party_buyer", "party_seller"):
            cmp_party_pair(sem_key, ma, mb, bucket, rule_id=rule_id)
        elif kind in ("container_no", "bl_no"):
            cmp_id_pair(sem_key, rule_id, ma, mb, bucket)
        elif kind == "port":
            cmp_port_pair(sem_key, ma, mb, bucket)
        elif kind == "hs":
            cmp_hs_pair(ma, mb, bucket)


# ===========================================================================
# 组级检查
# ===========================================================================

def check_to_order(docs: list, bucket: Bucket):
    """提单 consignee = TO ORDER → skipped，POL-TO-ORDER。"""
    for d in docs:
        if d.get("doc_type") != "bill_of_lading":
            continue
        c = (d.get("fields", {}).get("consignee") or {}).get("value")
        if isinstance(c, str) and c.strip().upper() in TO_ORDER_VALUES:
            bucket.add_skipped(
                field="consignee",
                reason=(f"提单为指示抬头「{c}」，属国际贸易标准写法；"
                        f"已改用 notify_party 旁证比对买方链"),
                policy="POL-TO-ORDER",
                source=d.get("source_file") or d.get("_source_file") or "",
            )


def check_missing_docs(docs: list, bucket: Bucket):
    """
    Q4 决策：单条件规则——报关单 warnings 里出现「栏18 Missing Docs」记录即报黄。
    比 EXPECTED 差集更权威（映射表 §1：CBP 已登记缺件优先采信），
    且避免票01/02/03/04/06 因单证不齐而误报。
    """
    for d in docs:
        if d.get("doc_type") != "customs_declaration":
            continue
        warnings = d.get("parse_meta", {}).get("warnings") or []
        for w in warnings:
            m = MISSING_DOCS_RE.search(str(w))
            if m:
                missing_list = m.group(1).strip()
                bucket.add_diff(
                    field="单证完整性", semantic_key="单证缺失", level="yellow",
                    left={
                        "source": d.get("source_file") or d.get("_source_file") or "",
                        "doc_type": "customs_declaration",
                        "path": "parse_meta.warnings[栏18 Missing Docs]",
                        "value": missing_list,
                        "confidence": 1.0,
                        "raw_span": {"page": 1, "text": str(w)},
                        "normalized": True,
                    },
                    right={
                        "source": "(group)", "doc_type": "group",
                        "path": "-", "value": None,
                        "confidence": 1.0, "raw_span": None, "normalized": True,
                    },
                    message=(f"CBP 已在 7501 栏18 登记缺件：{missing_list}；"
                             f"该票单证不齐，须补齐后重新申报"),
                    rule_id="CMP-DOC-MISSING",
                    category="missing_doc",
                )


def check_date_chain(docs: list, bucket: Bucket):
    """
    日期链近似检查（fixtures/README §3-②）：
      - packing_date > declaration_date → red（制单日晚于申报日 = 矛盾）
      - invoice_date  > declaration_date → red
    完整日期链（出口日≤装船日≤进口日≤申报日≤汇总日）需要 bill_of_lading 装船日字段，
    契约A v1.1 未提供，暂不做。已在 D5 已知限制里写明。
    """
    def get_date(doc, field_name):
        f = doc.get("fields", {}).get(field_name) or {}
        v = f.get("value")
        return (v, f) if isinstance(v, str) and v else (None, None)

    customs = next((d for d in docs if d.get("doc_type") == "customs_declaration"), None)
    if not customs:
        return
    dec_date, dec_f = get_date(customs, "declaration_date")
    if not dec_date:
        return

    for d in docs:
        dt = d.get("doc_type")
        if dt == "packing_list":
            pd, pf = get_date(d, "packing_date")
            if pd and pd > dec_date:
                _emit_date_conflict(bucket, customs, dec_date, dec_f,
                                    d, pd, pf, "装箱单制单日", "报关申报日")
        elif dt == "commercial_invoice":
            ivd, ivf = get_date(d, "invoice_date")
            if ivd and ivd > dec_date:
                _emit_date_conflict(bucket, customs, dec_date, dec_f,
                                    d, ivd, ivf, "发票日", "报关申报日")


def _emit_date_conflict(bucket, doc_a, date_a, field_a,
                        doc_b, date_b, field_b, label_b, label_a):
    ma = FieldMeta(
        value=date_a, confidence=field_a.get("confidence", 1.0),
        raw_span=field_a.get("raw_span"), normalized=field_a.get("normalized", True),
        path_str="fields.declaration_date",
        source_file=doc_a.get("source_file") or doc_a.get("_source_file") or "",
        doc_warnings=doc_a.get("parse_meta", {}).get("warnings") or [],
        doc_type=doc_a.get("doc_type", ""), field_key="declaration_date",
    )
    mb = FieldMeta(
        value=date_b, confidence=field_b.get("confidence", 1.0),
        raw_span=field_b.get("raw_span"), normalized=field_b.get("normalized", True),
        path_str=f"fields.{list(field_b.keys())[0] if field_b else ''}",
        source_file=doc_b.get("source_file") or doc_b.get("_source_file") or "",
        doc_warnings=doc_b.get("parse_meta", {}).get("warnings") or [],
        doc_type=doc_b.get("doc_type", ""), field_key="date",
    )
    # 修正 mb 的 path_str
    if doc_b.get("doc_type") == "packing_list":
        mb.path_str = "fields.packing_date"
        mb.field_key = "packing_date"
    elif doc_b.get("doc_type") == "commercial_invoice":
        mb.path_str = "fields.invoice_date"
        mb.field_key = "invoice_date"

    bucket.checked_fields += 1
    level = "red"
    level, reasons = _downgrade(level, ma, mb)
    bucket.add_diff(
        field="日期链", semantic_key="日期矛盾", level=level,
        left=ma, right=mb,
        message=(f"日期链矛盾：{label_b}（{doc_b.get('doc_type')}={date_b}）"
                 f"晚于{label_a}（{doc_a.get('doc_type')}={date_a}）；"
                 f"制单顺序不合理"),
        rule_id="CMP-DATE",
        downgrade_reasons=reasons or None,
    )


# ===========================================================================
# 归组（Step 1，P0 补丁：4 档降级链，不再要求"同目录 = 同票"）
# ===========================================================================
#
# 判定"同一票"的规则：**同一票号 = 同一票**。文档物理上在不在同一目录不再重要。
#
# 4 档降级链（上一级取不到就退到下一级）：
#   1. container_no 严格相等（含从提单 goods_description 正则抽的 ISO 6346）
#   2. bl_no 严格相等（含从报关单 parse_meta.warnings 正则抽）
#      —— 故意不用模糊匹配：票04 提单 SZPLA2400404 与票06 提单 SZPLA2400606
#         的 ratio ≈ 0.92，模糊归组会串票。模糊匹配只用于比对阶段判 OCR 噪声。
#   3. invoice_no 严格相等 && 金额在容差内 && 件数相等；
#      一方无 invoice_no 时（如报关单）退化为"金额宽容差 rel ≤ 0.20 + 件数相等"
#   4. 同目录兜底：仅当该目录内只有一个组时，把孤立文档加入
#
# 归不进的文档自成一组，group_key_type 如实反映用了哪一档。

# 第 3 档宽松版的金额相对容差（比 CMP-NUM-AMOUNT 的 0.005 宽，用于分票而非判差异）
GROUP_AMOUNT_REL_TOL = 0.20


def _amount_close(a, b, rel_tol=GROUP_AMOUNT_REL_TOL) -> bool:
    """分票用的金额宽松匹配：任一方为 None 视为不匹配；否则相对偏差 ≤ rel_tol。"""
    if a is None or b is None:
        return False
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return False
    denom = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / denom <= rel_tol


def extract_ticket_features(doc: dict) -> dict:
    """
    抽一份文档的分票特征。所有字段都可能是 None。
    返回 {container_no, bl_no, invoice_no, amount, packages, source_dir}
    """
    doc_type = doc.get("doc_type", "")
    fields = doc.get("fields", {}) or {}

    # ---- container_no ----
    cn = None
    if doc_type in ("commercial_invoice", "packing_list"):
        v = (fields.get("container_no") or {}).get("value")
        if isinstance(v, str) and v:
            cn = v
    elif doc_type == "bill_of_lading":
        cn = extract_container_from_bl(doc)

    # ---- bl_no ----
    bl = None
    if doc_type == "bill_of_lading":
        v = (fields.get("bl_no") or {}).get("value")
        if isinstance(v, str) and v:
            bl = v
    elif doc_type == "customs_declaration":
        bl = extract_bl_no_from_warnings(doc)

    # ---- invoice_no ----
    inv = None
    if doc_type in ("commercial_invoice", "packing_list"):
        v = (fields.get("invoice_no") or {}).get("value")
        if isinstance(v, str) and v:
            inv = v

    # ---- amount ----
    amt = None
    if doc_type in ("customs_declaration", "commercial_invoice"):
        v = (fields.get("total_value") or {}).get("value")
        if isinstance(v, dict):
            amt = v.get("amount")

    # ---- packages ----
    pkg = None
    if doc_type == "bill_of_lading":
        v = (fields.get("packages_and_weight") or {}).get("value")
        if isinstance(v, dict):
            pkg = v.get("packages")
    else:
        pkg = (fields.get("packages") or {}).get("value")

    return {
        "container_no": cn,
        "container_no_from_bl_desc": (doc_type == "bill_of_lading" and cn is not None),
        "bl_no": bl,
        "invoice_no": inv,
        "amount": amt,
        "packages": pkg,
        "source_dir": doc.get("_source_dir", ""),
        "declaration_no": (fields.get("declaration_no") or {}).get("value")
                          if doc_type == "customs_declaration" else None,
    }


def partition_into_tickets(docs: list) -> list:
    """
    把一批文档分成多票。返回 [{"ticket_label", "docs", "group_key", "group_key_type"}]。
    使用 Union-Find + 4 档降级链。
    """
    n = len(docs)
    if n == 0:
        return []

    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    feats = [extract_ticket_features(d) for d in docs]

    # ---- 第 1 档：container_no 严格相等 ----
    by_container: dict = {}
    for i, f in enumerate(feats):
        if f["container_no"]:
            by_container.setdefault(f["container_no"], []).append(i)
    for idxs in by_container.values():
        for j in idxs[1:]:
            union(idxs[0], j)

    # ---- 第 2 档：bl_no 严格相等（不用模糊匹配，防串票）----
    by_bl: dict = {}
    for i, f in enumerate(feats):
        if f["bl_no"]:
            by_bl.setdefault(norm_id(f["bl_no"]), []).append(i)
    for idxs in by_bl.values():
        for j in idxs[1:]:
            union(idxs[0], j)

    # ---- 第 3 档：invoice_no + 三元组 ----
    # 严格版：invoice_no 相同 && 金额在容差内（或一方 null）&& 件数相等（或一方 null）
    by_inv: dict = {}
    for i, f in enumerate(feats):
        if f["invoice_no"]:
            by_inv.setdefault(f["invoice_no"], []).append(i)
    for inv_no, idxs in by_inv.items():
        for i, j in combinations(idxs, 2):
            if find(i) == find(j):
                continue
            fi, fj = feats[i], feats[j]
            amt_ok = (fi["amount"] is None or fj["amount"] is None
                      or _amount_close(fi["amount"], fj["amount"],
                                       rel_tol=GROUP_AMOUNT_REL_TOL))
            pkg_ok = (fi["packages"] is None or fj["packages"] is None
                      or fi["packages"] == fj["packages"])
            if amt_ok and pkg_ok:
                union(i, j)

    # 宽松版：一方没 invoice_no（如报关单），靠金额+件数模糊匹配已有组
    # 只在"该文档还没被归入任何多元素组"时才尝试，避免过度合并
    for i, fi in enumerate(feats):
        if fi["invoice_no"]:
            continue
        if fi["amount"] is None or fi["packages"] is None:
            continue
        for j, fj in enumerate(feats):
            if i == j or find(i) == find(j):
                continue
            if not fj["invoice_no"] and fj["container_no"] is None and fj["bl_no"] is None:
                continue  # 对方也是"三无"文档，跳过（避免报关单之间乱合并）
            if _amount_close(fi["amount"], fj["amount"]) and fi["packages"] == fj["packages"]:
                union(i, j)
                break  # 一份文档只归一次

    # ---- 第 4 档：同目录兜底 ----
    # 只对"未被归入任何多元素组"的孤立文档，且"该目录内只有一个多元素组"时加入
    by_dir: dict = {}
    for i, f in enumerate(feats):
        by_dir.setdefault(f["source_dir"], []).append(i)

    for dir_path, idxs in by_dir.items():
        if not dir_path:
            continue
        # 统计该目录下的组
        groups_in_dir: dict = {}
        for i in idxs:
            groups_in_dir.setdefault(find(i), []).append(i)
        multi_element_groups = [g for g in groups_in_dir.values() if len(g) > 1]
        single_element_groups = [g[0] for g in groups_in_dir.values() if len(g) == 1]
        # 如果只有一个多元素组，把所有孤立文档归进去
        if len(multi_element_groups) == 1 and single_element_groups:
            target = multi_element_groups[0][0]
            for i in single_element_groups:
                union(i, target)
        # 如果全是孤立文档（该目录只有 1 份文档，或多份文档都没强键）
        # → 全部合并到一组（同目录 = 同票的经典兜底）
        elif not multi_element_groups and len(single_element_groups) > 1:
            for i in single_element_groups[1:]:
                union(single_element_groups[0], i)

    # ---- 收集结果 ----
    groups: dict = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    result = []
    for member_idxs in groups.values():
        member_docs = [docs[i] for i in member_idxs]
        member_feats = [feats[i] for i in member_idxs]
        gk, gkt = decide_group_key(member_docs, member_feats)
        # ticket_label：所有成员同目录 → 用目录名；否则用 group_key
        dirs = {f["source_dir"] for f in member_feats if f["source_dir"]}
        if len(dirs) == 1:
            label = Path(list(dirs)[0]).name
        else:
            label = gk or "ticket"
        result.append({
            "ticket_label": label,
            "docs": member_docs,
            "group_key": gk,
            "group_key_type": gkt,
        })

    # 按 (source_dir, group_key) 排序保证确定性
    result.sort(key=lambda t: (
        min((f["source_dir"] for f in [extract_ticket_features(d) for d in t["docs"]]
             if f["source_dir"]), default=""),
        t["group_key"] or "",
        t["ticket_label"],
    ))

    # 同目录多组时给 label 加 group_key 后缀，避免输出无法区分（票07 场景）
    label_counts = Counter(t["ticket_label"] for t in result)
    for t in result:
        if label_counts[t["ticket_label"]] > 1:
            t["ticket_label"] = f"{t['ticket_label']}#{t['group_key']}"

    return result


def decide_group_key(docs: list, feats: list) -> tuple:
    """决定一组的 group_key 与 group_key_type，反映实际用了哪一档。"""
    # 1a) container_no 直接字段（最强证据）
    for f in feats:
        if f["container_no"] and not f["container_no_from_bl_desc"]:
            return f["container_no"], "container_no"
    # 1b) container_no 从提单描述抽（次强）
    for f in feats:
        if f["container_no"] and f["container_no_from_bl_desc"]:
            return f["container_no"], "container_no(from_bl_desc)"
    # 2) bl_no
    for f in feats:
        if f["bl_no"]:
            return f["bl_no"], "bl_no"
    # 3) invoice_no（说明是靠三元组归的）
    for f in feats:
        if f["invoice_no"]:
            return f["invoice_no"], "weak_invoice_triple"
    # 3.5) declaration_no（报关单自号，弱键）
    for f in feats:
        if f["declaration_no"]:
            return f["declaration_no"], "declaration_no"
    # 4) 目录名兜底
    dirs = {f["source_dir"] for f in feats if f["source_dir"]}
    if len(dirs) == 1:
        return Path(list(dirs)[0]).name, "directory_name"
    return "unknown", "directory_name"


# ===========================================================================
# 单票主流程
# ===========================================================================

def load_docs_recursive(root: Path) -> list:
    """
    递归加载 root 下所有 JSON 文档（含子目录）。
    每份文档打上 _source_file（文件名）和 _source_dir（所在目录绝对路径）。
    """
    docs = []
    if root.is_file() and root.suffix.lower() == ".json":
        paths = [root]
    elif root.is_dir():
        paths = sorted(root.rglob("*.json"))
    else:
        return docs
    for p in paths:
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception as e:
            print(f"[WARN] 跳过 {p}: {e}", file=sys.stderr)
            continue
        if not isinstance(d, dict) or "doc_type" not in d:
            continue
        d["_source_file"] = p.name
        d["_source_dir"] = str(p.parent)
        if not d.get("source_file") or "fixtures-synthetic" in str(d.get("source_file")):
            d["source_file"] = p.name
        docs.append(d)
    return docs


def process_ticket(ticket: dict) -> dict:
    """跑一票（ticket 来自 partition_into_tickets 的输出），返回契约B dict。"""
    docs = ticket["docs"]
    label = ticket["ticket_label"]
    if not docs:
        return {"ticket_id": label, "error": "no docs"}

    group = {
        "group_key": ticket["group_key"],
        "group_key_type": ticket["group_key_type"],
        "docs": [
            {
                "file": d.get("source_file") or d.get("_source_file") or "",
                "doc_type": d.get("doc_type", ""),
            }
            for d in docs
        ],
    }
    bucket = Bucket()

    # Step 2：两两比对
    for a, b in combinations(docs, 2):
        compare_pair(a, b, bucket)

    # 组级检查
    check_to_order(docs, bucket)
    check_missing_docs(docs, bucket)
    check_date_chain(docs, bucket)

    # Step 3：合规规则
    customs = next((d for d in docs if d.get("doc_type") == "customs_declaration"), None)
    alerts = compliance_rules.evaluate(customs, docs)

    # Step 4：组装契约B
    return assemble_contract_b(label, group, bucket, alerts)


def assemble_contract_b(ticket_id: str, group: dict, bucket: Bucket,
                        alerts: list) -> dict:
    level_counter = Counter(d["level"] for d in bucket.diffs)
    return {
        "contract_version": CONTRACT_VERSION,
        "ticket_id": ticket_id,
        "generated_by": ENGINE_VERSION,
        "groups": [group],
        "diffs": bucket.diffs,
        "not_comparable": bucket.not_comparable,
        "notes": bucket.notes,
        "alerts": alerts,
        "skipped": bucket.skipped,
        "summary": {
            "checked_fields": bucket.checked_fields,
            "diffs": {
                "red": level_counter.get("red", 0),
                "yellow": level_counter.get("yellow", 0),
                "orange": level_counter.get("orange", 0),
            },
            "not_comparable": len(bucket.not_comparable),
            "notes": len(bucket.notes),
            "alerts": len(alerts),
            "skipped": len(bucket.skipped),
        },
    }


# ===========================================================================
# CLI
# ===========================================================================

def run_pipeline(root: Path) -> list:
    """加载 → 分票 → 逐票比对 → 返回结果列表。"""
    docs = load_docs_recursive(root)
    if not docs:
        return []
    tickets = partition_into_tickets(docs)
    return [process_ticket(t) for t in tickets]


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="线2 跨单证比对引擎（契约B v0.1，含 P0 分票降级链）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("path", help="任意目录（递归加载全部 JSON）或单个 JSON 文件；"
                                 "支持平铺目录（无子目录）和多票混放目录")
    ap.add_argument("-o", "--output", help="输出 JSON 文件路径；不给则打印到 stdout")
    ap.add_argument("--pretty", action="store_true", default=True,
                    help="缩进 2 空格输出（默认开启）")
    args = ap.parse_args(argv)

    root = Path(args.path)
    if not root.exists():
        print(f"[ERROR] 路径不存在：{root}", file=sys.stderr)
        return 2

    results = run_pipeline(root)
    if not results:
        print(f"[ERROR] 未在 {root} 下找到任何合规A JSON 文档", file=sys.stderr)
        return 2

    # 单票 → 直接输出 dict；多票 → 顶层是 tickets 数组
    if len(results) == 1:
        payload = results[0]
    else:
        payload = {
            "contract_version": CONTRACT_VERSION,
            "generated_by": ENGINE_VERSION,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "ticket_count": len(results),
            "tickets": results,
        }

    text = json.dumps(payload, ensure_ascii=False,
                      indent=2 if args.pretty else None)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"[OK] 已写入 {out_path}（{len(results)} 票）", file=sys.stderr)
    else:
        print(text)

    # 打印每票摘要到 stderr（方便人工快速看）
    for r in results:
        s = r.get("summary", {})
        d = s.get("diffs", {})
        g = (r.get("groups") or [{}])[0]
        print(f"  {r.get('ticket_id')}: "
              f"red={d.get('red', 0)} yellow={d.get('yellow', 0)} "
              f"orange={d.get('orange', 0)} "
              f"not_comparable={s.get('not_comparable', 0)} "
              f"notes={s.get('notes', 0)} alerts={s.get('alerts', 0)} "
              f"skipped={s.get('skipped', 0)} "
              f"| group_key={g.get('group_key')} type={g.get('group_key_type')}",
              file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
