"""线1 配置加载：路径、LLM、OCR。

约定：
- 所有路径以 ROOT（本文件上级目录）为基准推导，勿硬编码绝对路径散落各处；
- MAT（测试资料）为只读绝对路径引用，任何代码不得向其写入。
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# ROOT = line1-information-extraction/
ROOT = Path(__file__).resolve().parent.parent

# .env 位于 ROOT；仅当前进程加载，不入库
load_dotenv(ROOT / ".env")

# --- 路径 ---
SRC_DIR = ROOT / "src"
DATA_DIR = ROOT / "data"            # 本地真实件，已 gitignore
RAW_PDFS_DIR = DATA_DIR / "raw_pdfs"
OUT_DIR = ROOT / "out"              # 抽取输出，已 gitignore
HANDOFF_B_DIR = ROOT / "HANDOFF-B"  # 交接 B 的包，已 gitignore
SCHEMA_PATH = ROOT / "schema" / "contract-A.schema.json"
EVAL_DIR = ROOT / "eval"
GT_DRAFT_DIR = EVAL_DIR / "gt_draft"      # 标准答案草稿（含真实件抽取结果，不入库）
GT_RAW_DIR = EVAL_DIR / "gt_raw"          # 原始标注（永不入库）

# 测试资料库（只读！）
MAT_DIR = Path(os.getenv("MAT_DIR", r"A:\个人文件\数联比赛项目\赛题二测试资料"))

# --- LLM（可选：仅脚本直调 API 批处理时使用；agent 模式下无需配置） ---
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")

# --- OCR（可选兜底：默认 agent 视觉直读，见 docs/决策记录.md #D003） ---
# agent_vision = agent 直接读文件（默认）；local = rapidocr；cloud = 云OCR（仅自造/脱敏件！）
OCR_ENGINE = os.getenv("OCR_ENGINE", "agent_vision")

# --- 调试 ---
DEBUG = os.getenv("DEBUG", "false").lower() in ("1", "true", "yes")


def ensure_dirs() -> None:
    """创建运行期目录（均已被 .gitignore 排除）。"""
    for d in (RAW_PDFS_DIR, OUT_DIR, GT_DRAFT_DIR, GT_RAW_DIR):
        d.mkdir(parents=True, exist_ok=True)
