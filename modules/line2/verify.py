"""
线2 比对引擎自动化验收脚本
============================

零第三方依赖，只用标准库。一条命令跑完：

    python verify.py

四项检查：
  1. 逐票比对期望值（硬编码自 fixtures/README §1 + P0 票07 混票验证）
  2. 契约B 结构校验（6 必填字段 + level ∈ {red, yellow, orange}）
  3. 确定性校验（跑两次比对输出，tickets 部分逐字节相等，排除 generated_at）
  4. 硬约束自检（扫代码不许出现大模型调用与真实件路径）

最后打印 PASS/FAIL 汇总和退出码（0 = 全过，非 0 = 有 FAIL）。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

BASE = Path(__file__).parent
COMPARE_PY = BASE / "compare.py"
RULES_PY = BASE / "rules.py"
FIXTURES = BASE / "03-输入" / "fixtures-synthetic"

# 期望值硬编码（来源：fixtures/README §1 六票硬指标 + P0 票07 混票验证要求）
# 用户 P1 明确："票03 6红"（不是 3 红，同根因多处报出属正常，见 D5 §4）
EXPECTED = {
    "票01-不误报-容差与OCR噪声": {
        "red": 0, "yellow": 0, "orange": 0,
        "not_comparable": 4, "notes": 2, "alerts": 1, "skipped": 1,
    },
    "票02-金额超容差": {
        "red": 1, "yellow": 0, "orange": 0,
        "not_comparable": 2, "alerts": 2,
    },
    "票03-主体不符-同址不同名": {
        "red": 6, "yellow": 0, "orange": 0,
        "not_comparable": 4, "alerts": 3,
    },
    "票04-HS跨证不一致": {
        "red": 1, "yellow": 0, "orange": 0,
        "not_comparable": 2, "alerts": 1, "skipped": 1,
    },
    "票05-件数缺失-日期矛盾-缺发票": {
        "red": 1, "yellow": 2, "orange": 0,
        "not_comparable": 2, "alerts": 1,
    },
    "票06-港口码对地名-不可比": {
        "red": 0, "yellow": 0, "orange": 0,
        "not_comparable": 2, "alerts": 1, "skipped": 1,
    },
    # 票07：混票归组验证（P0），期望分成 2 票，各自 0 红 0 黄
    "票07-混票归组验证#SYNU7777001": {
        "red": 0, "yellow": 0, "orange": 0,
        "not_comparable": 2, "alerts": 1,
    },
    "票07-混票归组验证#SYNU7777002": {
        "red": 0, "yellow": 0, "orange": 0,
        "not_comparable": 2, "alerts": 3,
    },
}

REQUIRED_DIFF_FIELDS = {"field", "left", "right", "level", "message", "rule_id"}
LEVEL_ENUM = {"red", "yellow", "orange"}

# 大模型调用模式（硬约束①：LLM 不得参与判定）
LLM_PATTERNS = [
    (r"\bopenai\b", "openai 库调用"),
    (r"\banthropic\b", "anthropic 库调用"),
    (r"\blangchain\b", "langchain 库调用"),
    (r"\bapi_key\b", "api_key 出现"),
    (r"\bmodel\s*=\s*[\"']", "model= 参数（大模型调用常见形式）"),
    (r"\bchat\.completions\b", "chat.completions 调用"),
    (r"\bllm\s*\.", "llm. 调用"),
    (r"\bgpt-\d", "gpt-N 模型名"),
    (r"\bclaude-\d", "claude-N 模型名"),
]

# 真实件路径模式（硬约束②：只能用自造件）
REAL_DOC_PATTERNS = [
    (r"赛题二测试资料", "真实件目录：赛题二测试资料"),
    (r"包1-核心单证", "真实件目录：包1-核心单证"),
    (r"包2-规则与溯源", "真实件目录：包2-规则与溯源"),
    (r"同票套[A-D]", "真实件：同票套 A/B/C/D"),
    (r"法院保密", "L3 法院保密标记件"),
    (r"交接包-线1信息抽取", "线1 真实件抽取结果"),
    (r"抽取结果JSON", "线1 真实件输出目录"),
]


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

class Reporter:
    """收集 PASS/FAIL 并最后汇总。"""

    def __init__(self):
        self.checks: list = []  # [(section, name, ok, detail)]

    def record(self, section: str, name: str, ok: bool, detail: str = ""):
        self.checks.append((section, name, ok, detail))
        mark = "✅" if ok else "❌"
        print(f"  {mark} {name}" + (f" — {detail}" if detail else ""))

    def section(self, title: str):
        print(f"\n=== {title} ===")

    def summary(self) -> int:
        total = len(self.checks)
        passed = sum(1 for _, _, ok, _ in self.checks if ok)
        failed = total - passed
        print("\n" + "=" * 60)
        print(f"汇总：{passed}/{total} PASS，{failed} FAIL")
        if failed:
            print("\nFAIL 明细：")
            for sec, name, ok, detail in self.checks:
                if not ok:
                    print(f"  [{sec}] {name}: {detail}")
        print("=" * 60)
        print("结果：PASS ✅" if failed == 0 else "结果：FAIL ❌")
        return 0 if failed == 0 else 1


def run_compare(path: Path, out_file: Path) -> tuple:
    """跑 compare.py，返回 (returncode, stdout+stderr, payload_dict)。"""
    proc = subprocess.run(
        [sys.executable, str(COMPARE_PY), str(path), "-o", str(out_file)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(BASE),
    )
    if proc.returncode != 0:
        return proc.returncode, proc.stdout + proc.stderr, None
    try:
        with open(out_file, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as e:
        return 1, f"输出文件读取失败: {e}", None
    return 0, proc.stdout + proc.stderr, payload


def iter_tickets(payload):
    """统一遍历：单票 dict 或多票 {tickets: [...]}。"""
    if isinstance(payload, dict) and "tickets" in payload:
        return payload["tickets"]
    return [payload]


# ---------------------------------------------------------------------------
# 检查 1：逐票比对期望值
# ---------------------------------------------------------------------------

def check_expectations(rep: Reporter, payload):
    rep.section("检查 1：逐票比对期望值（fixtures/README §1 + P0 票07）")
    tickets = list(iter_tickets(payload))
    tickets_by_id = {t.get("ticket_id"): t for t in tickets}

    # 先检查票数
    rep.record(
        "期望值", "tickets 数量",
        len(tickets) == len(EXPECTED),
        f"期望 {len(EXPECTED)} 票，实际 {len(tickets)} 票",
    )

    for tid, exp in EXPECTED.items():
        t = tickets_by_id.get(tid)
        if not t:
            rep.record("期望值", tid, False, "未在输出中找到")
            continue
        s = t.get("summary", {})
        d = s.get("diffs", {})
        actual = {
            "red": d.get("red", 0),
            "yellow": d.get("yellow", 0),
            "orange": d.get("orange", 0),
            "not_comparable": s.get("not_comparable", 0),
            "notes": s.get("notes", 0),
            "alerts": s.get("alerts", 0),
            "skipped": s.get("skipped", 0),
        }
        mismatches = []
        for k, v in exp.items():
            if actual.get(k, 0) != v:
                mismatches.append(f"{k}: 期望 {v}, 实际 {actual.get(k, 0)}")
        detail = "; ".join(mismatches) if mismatches else (
            f"red={actual['red']} yellow={actual['yellow']} orange={actual['orange']} "
            f"nc={actual['not_comparable']} alerts={actual['alerts']}"
        )
        rep.record("期望值", tid, not mismatches, detail)


# ---------------------------------------------------------------------------
# 检查 2：契约B 结构校验
# ---------------------------------------------------------------------------

def check_contract_b(rep: Reporter, payload):
    rep.section("检查 2：契约B 结构校验（6 必填字段 + level 三值枚举）")
    tickets = list(iter_tickets(payload))
    total_diffs = 0
    level_counter = Counter()
    struct_errors = []

    for t in tickets:
        tid = t.get("ticket_id", "?")
        # 顶层必备
        for k in ("contract_version", "ticket_id", "generated_by",
                  "groups", "diffs", "alerts", "skipped", "summary"):
            if k not in t:
                struct_errors.append(f"{tid}: 缺顶层字段 {k}")
        # diffs 6 必填
        for i, d in enumerate(t.get("diffs", [])):
            total_diffs += 1
            miss = REQUIRED_DIFF_FIELDS - set(d.keys())
            if miss:
                struct_errors.append(f"{tid}.diffs[{i}] 缺字段 {sorted(miss)}")
            lv = d.get("level")
            if lv not in LEVEL_ENUM:
                struct_errors.append(f"{tid}.diffs[{i}].level={lv!r} 非法")
            level_counter[lv] += 1
        # not_comparable 与 alerts 的 level 也要检查
        for i, x in enumerate(t.get("not_comparable", [])):
            if x.get("level") not in LEVEL_ENUM:
                struct_errors.append(f"{tid}.not_comparable[{i}].level={x.get('level')!r} 非法")
            level_counter[x.get("level")] += 1
        for i, a in enumerate(t.get("alerts", [])):
            if a.get("level") not in LEVEL_ENUM:
                struct_errors.append(f"{tid}.alerts[{i}].level={a.get('level')!r} 非法")
            level_counter[a.get("level")] += 1

    rep.record(
        "契约B", "6 必填字段 + level 三值枚举",
        not struct_errors,
        f"{len(struct_errors)} 处违规" if struct_errors else f"{total_diffs} 条 diffs 全部合规",
    )
    for e in struct_errors[:5]:
        print(f"      ! {e}")

    # 打印实测的 level 分布（用户 P1 要求）
    print(f"      level 分布：red={level_counter['red']} "
          f"yellow={level_counter['yellow']} orange={level_counter['orange']}")

    # summary 计数一致性
    summary_errors = []
    for t in tickets:
        s = t.get("summary", {})
        c = Counter(d["level"] for d in t.get("diffs", []))
        if s.get("diffs", {}).get("red") != c.get("red", 0):
            summary_errors.append(f"{t.get('ticket_id')}: summary.diffs.red 与实际不一致")
        if s.get("diffs", {}).get("yellow") != c.get("yellow", 0):
            summary_errors.append(f"{t.get('ticket_id')}: summary.diffs.yellow 与实际不一致")
        if s.get("alerts") != len(t.get("alerts", [])):
            summary_errors.append(f"{t.get('ticket_id')}: summary.alerts 与实际不一致")
        if s.get("skipped") != len(t.get("skipped", [])):
            summary_errors.append(f"{t.get('ticket_id')}: summary.skipped 与实际不一致")
    rep.record("契约B", "summary 计数与实际数组一致", not summary_errors,
               f"{len(summary_errors)} 处不一致" if summary_errors else "全部一致")


# ---------------------------------------------------------------------------
# 检查 3：确定性校验
# ---------------------------------------------------------------------------

def check_determinism(rep: Reporter):
    rep.section("检查 3：确定性校验（跑两次输出必须逐字节相等，排除 generated_at）")
    with tempfile.TemporaryDirectory() as tmp:
        f1 = Path(tmp) / "run1.json"
        f2 = Path(tmp) / "run2.json"
        rc1, _, p1 = run_compare(FIXTURES, f1)
        rc2, _, p2 = run_compare(FIXTURES, f2)
        if rc1 != 0 or rc2 != 0 or p1 is None or p2 is None:
            rep.record("确定性", "两次运行都成功", False,
                       f"run1 rc={rc1}, run2 rc={rc2}")
            return
        # 去掉 generated_at
        p1.pop("generated_at", None)
        p2.pop("generated_at", None)
        s1 = json.dumps(p1, ensure_ascii=False, sort_keys=True)
        s2 = json.dumps(p2, ensure_ascii=False, sort_keys=True)
        equal = s1 == s2
        detail = "两次输出完全一致" if equal else (
            f"两次输出不一致，长度 {len(s1)} vs {len(s2)}"
        )
        rep.record("确定性", "tickets 逐字节相等（排除 generated_at）", equal, detail)


# ---------------------------------------------------------------------------
# 检查 4：硬约束自检
# ---------------------------------------------------------------------------

def check_hard_constraints(rep: Reporter):
    rep.section("检查 4：硬约束自检（扫代码文本）")
    sources = {
        "compare.py": COMPARE_PY.read_text(encoding="utf-8"),
        "rules.py": RULES_PY.read_text(encoding="utf-8"),
    }

    # 4a: LLM 调用
    llm_hits = []
    for fname, text in sources.items():
        # 去掉文档字符串和注释里的"提及"（避免误伤），只查真实代码
        # 简化：直接扫全文，因为规格文档提到 openai 等词是极小概率
        for pat, label in LLM_PATTERNS:
            for m in re.finditer(pat, text, re.IGNORECASE):
                # 取上下文 60 字符
                start = max(0, m.start() - 30)
                end = min(len(text), m.end() + 30)
                ctx = text[start:end].replace("\n", " ")
                llm_hits.append(f"{fname}:{label}: ...{ctx}...")
    rep.record(
        "硬约束①", "compare.py / rules.py 无大模型调用",
        not llm_hits,
        f"{len(llm_hits)} 处命中" if llm_hits else "全文扫描无命中",
    )
    for h in llm_hits[:5]:
        print(f"      ! {h}")

    # 4b: 真实件路径
    path_hits = []
    for fname, text in sources.items():
        for pat, label in REAL_DOC_PATTERNS:
            for m in re.finditer(pat, text):
                start = max(0, m.start() - 30)
                end = min(len(text), m.end() + 30)
                ctx = text[start:end].replace("\n", " ")
                path_hits.append(f"{fname}:{label}: ...{ctx}...")
    rep.record(
        "硬约束②", "compare.py / rules.py 不含真实件路径",
        not path_hits,
        f"{len(path_hits)} 处命中" if path_hits else "全文扫描无命中",
    )
    for h in path_hits[:5]:
        print(f"      ! {h}")

    # 4c: level 枚举硬约束（在 schema 文件里）
    schema_path = BASE / "contract-B.schema.json"
    if schema_path.exists():
        schema_text = schema_path.read_text(encoding="utf-8")
        has_enum = '"enum": ["red", "yellow", "orange"]' in schema_text
        rep.record("硬约束③", "contract-B.schema.json 强制 level 三值枚举", has_enum,
                   "enum 已定义" if has_enum else "未找到 enum 定义")
    else:
        rep.record("硬约束③", "contract-B.schema.json 存在", False, "文件不存在")

    # 4d: not_comparable 单独承载（硬约束④）
    has_nc_array = "not_comparable" in sources["compare.py"] and \
                   '"not_comparable":' in sources["compare.py"]
    rep.record("硬约束④", "not_comparable 顶层单独数组（不混入 diffs[]）",
               has_nc_array,
               "已实现" if has_nc_array else "未找到 not_comparable 数组")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    print("=" * 60)
    print("线2 比对引擎自动化验收")
    print("=" * 60)

    rep = Reporter()

    # 跑一次 fixtures 根目录，拿到 payload 供检查 1/2 使用
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "differences.json"
        rc, output, payload = run_compare(FIXTURES, out)
        if rc != 0 or payload is None:
            print(f"\n❌ compare.py 跑失败：\n{output}")
            return 2
        check_expectations(rep, payload)
        check_contract_b(rep, payload)

    check_determinism(rep)
    check_hard_constraints(rep)

    return rep.summary()


if __name__ == "__main__":
    sys.exit(main())
