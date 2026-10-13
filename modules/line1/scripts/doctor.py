"""环境自检：Python 版本 / 依赖 / OCR / LLM API / 隐私 ignore 规则。

用法：python scripts/doctor.py
判定：PASS(绿) 全部通过；WARN(黄) 不阻塞但需留意；FAIL(红) 必须修复。
退出码：有任何 FAIL 则为 1，否则 0。
"""

from __future__ import annotations

import importlib
import importlib.metadata
import sys
from pathlib import Path

# Windows 控制台 gbk 乱码兜底
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

RESULTS: list[tuple[str, str, str]] = []  # (status, item, detail)

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
SYMBOL = {PASS: "[OK] ", WARN: "[WARN]", FAIL: "[FAIL]"}


def record(status: str, item: str, detail: str = "") -> None:
    RESULTS.append((status, item, detail))


# ---------- 1. Python 版本 ----------
v = sys.version_info
if v >= (3, 11):
    record(PASS, f"Python {v.major}.{v.minor}.{v.micro}", ">= 3.11 满足")
else:
    record(FAIL, f"Python {v.major}.{v.minor}.{v.micro}", "需 >= 3.11")

# ---------- 2. 依赖 ----------
DEPS = {
    "pymupdf": "PyMuPDF",
    "pdfplumber": "pdfplumber",
    "PIL": "Pillow",
    "cv2": "opencv-python",
    "pydantic": "pydantic",
    "jsonschema": "jsonschema",
    "requests": "requests",
    "dotenv": "python-dotenv",
    "pytest": "pytest",
}
for module, pkg in DEPS.items():
    try:
        m = importlib.import_module(module)
        try:
            ver = importlib.metadata.version(pkg.lower().replace("python-", "-"))
        except Exception:
            ver = ""
        record(PASS, pkg, f"import OK {ver}".strip())
    except ImportError as e:
        record(FAIL, pkg, f"未安装/导入失败: {e}")

# ---------- 3. 核心三件套（agent 模式：prompt + schema + validator） ----------
try:
    from src import config  # noqa: E402

    prompt_files = sorted((config.ROOT / "prompts").glob("抽取规程-v*.md"))
    if prompt_files:
        record(PASS, "prompt: 抽取规程", f"{prompt_files[-1].name}（取最新版）")
    else:
        record(FAIL, "prompt: 抽取规程", "prompts/ 下无 抽取规程-v*.md")
    if config.SCHEMA_PATH.exists():
        record(PASS, "schema: contract-A", str(config.SCHEMA_PATH.name))
    else:
        record(FAIL, "schema: contract-A", f"缺失: {config.SCHEMA_PATH}")
    try:
        from src import validate  # noqa: F401

        record(PASS, "validator: src/validate.py", "可导入")
    except Exception as e:
        record(FAIL, "validator: src/validate.py", f"导入失败: {e}")
    # 契约A 三件套一致性闸门（必检）
    import subprocess

    r = subprocess.run([sys.executable, str(config.ROOT / "scripts" / "check_consistency.py")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode == 0:
        record(PASS, "一致性闸门: check_consistency", "schema/规程/validator/口径表 四处一致")
    else:
        tail = (r.stdout or r.stderr or "").strip().splitlines()
        detail = tail[-1] if tail else "无输出"
        record(FAIL, "一致性闸门: check_consistency", f"不一致: {detail}")
    if (config.ROOT / ".env").exists():
        record(PASS, ".env", "存在（可选配置；agent 模式默认不需要）")
    else:
        record(PASS, ".env", "未创建（agent 模式无需配置，正常）")
    for d in (config.RAW_PDFS_DIR, config.GT_DRAFT_DIR, config.OUT_DIR):
        if d.exists():
            record(PASS, f"目录 {d.relative_to(config.ROOT)}", "存在")
        else:
            record(FAIL, f"目录 {d.relative_to(config.ROOT)}", "缺失")
    if config.MAT_DIR.exists():
        record(PASS, "MAT 测试资料目录", str(config.MAT_DIR))
    else:
        record(WARN, "MAT 测试资料目录", f"不存在（默认路径未找到）: {config.MAT_DIR}")
except Exception as e:  # pragma: no cover
    record(FAIL, "src/config.py", f"加载失败: {e}")

# ---------- 4. OCR（可选兜底：默认 agent 视觉直读，见 #D003） ----------
if config.OCR_ENGINE == "agent_vision":
    record(PASS, "OCR 引擎", "agent_vision（视觉直读，无需任何配置）")
else:
    record(WARN, "OCR 引擎", f"OCR_ENGINE={config.OCR_ENGINE}（兜底模式，确认真实件不走 cloud）")
try:
    from rapidocr_onnxruntime import RapidOCR  # noqa: F401

    record(PASS, "OCR 兜底: rapidocr-onnxruntime", "已安装（真实件唯一允许的 OCR 兜底）")
except ImportError:
    record(PASS, "OCR 兜底: rapidocr-onnxruntime", "未安装（可选；agent_vision 模式用不到）")

# ---------- 5. LLM API（可选：仅脚本批处理模式） ----------
try:
    if not (config.LLM_BASE_URL and config.LLM_API_KEY):
        record(PASS, "LLM API", "未配置（agent 模式不需要；仅脚本批处理才配）")
    else:
        import requests as _rq

        base = config.LLM_BASE_URL.rstrip("/")
        url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
        r = _rq.get(url, headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
                    timeout=15)
        if r.status_code == 200:
            record(PASS, "LLM API", f"{url} 连通（model 列表可访问）")
        elif r.status_code in (401, 403):
            record(FAIL, "LLM API", f"{url} 认证失败({r.status_code})：检查 API Key")
        else:
            record(WARN, "LLM API", f"{url} 返回 {r.status_code}：端点可达但响应异常")
except Exception as e:
    record(FAIL, "LLM API", f"请求异常: {e}")

# ---------- 6. 隐私红线：.gitignore 覆盖检查 ----------
REQUIRED_IGNORES = ["out/", "HANDOFF-B/", "data/", "*.pdf", "*.png", "*.jpg",
                    "eval/gt_raw/", ".env"]
gi = (config.ROOT / ".gitignore")
if gi.exists():
    content = gi.read_text(encoding="utf-8")
    missing = [p for p in REQUIRED_IGNORES if p not in content]
    if missing:
        record(FAIL, ".gitignore 隐私覆盖", f"缺规则: {missing}")
    else:
        record(PASS, ".gitignore 隐私覆盖", "out/ HANDOFF-B/ data/ 图片PDF gt_raw/ .env 全部排除")
else:
    record(FAIL, ".gitignore", "文件不存在")

# ---------- 输出 ----------
print("=" * 62)
print("线1 单证信息抽取 · 环境自检")
print("=" * 62)
for status, item, detail in RESULTS:
    print(f"{SYMBOL[status]} {item:<28} {detail}")
n_fail = sum(1 for s, _, _ in RESULTS if s == FAIL)
n_warn = sum(1 for s, _, _ in RESULTS if s == WARN)
print("-" * 62)
print(f"结果: {len(RESULTS)} 项 | FAIL {n_fail} | WARN {n_warn}")
if n_fail:
    print("结论: 存在必须修复项（红），修复后重跑。")
else:
    print("结论: 全绿" + ("（黄项为提示，不阻塞）" if n_warn else "。"))
sys.exit(1 if n_fail else 0)
