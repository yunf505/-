"""DeepSeek extraction engine: local OCR text -> DeepSeek chat API -> Contract-A JSON.

Runtime chain for the web app (C's design):
    upload(bytes) -> [THIS MODULE] -> Contract-A dict -> validate.py -> line2 compare

    1. Render + local RapidOCR (via scripts/ocr_dump.py) turns the PDF into TEXT offline.
    2. The text + Contract-A field list + extraction discipline go to DeepSeek
       (OpenAI-compatible /chat/completions, JSON mode, temperature 0).
    3. The reply is parsed, required fields are filled with explicit nulls, and a light
       structural check runs; on failure we retry ONCE with the error list appended.

Config (env / .env — NO secrets in code, NO secrets in git):
    DEEPSEEK_API_KEY    required at runtime; fill at the demo site.
    DEEPSEEK_BASE_URL   default https://api.deepseek.com
    DEEPSEEK_MODEL      default deepseek-flash   (marketing name "V4.1-Flash";
                        official API id is `deepseek-flash`; edit here if console differs)

CLI (offline self-test without network):
    python src/deepseek_extract.py <file.pdf> --doc-type customs_declaration --out out.json
    python src/deepseek_extract.py <file.pdf> --mock --out out.json   # no API call
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "scripts"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import ocr_dump                      # local OCR bridge (eyes layer)
from no_vision_prep import PREAMBLE, _find_schema   # prompt discipline + field list


class DeepSeekError(RuntimeError):
    """User-facing error (missing key, HTTP failure, unparsable reply...)."""


# ------------------------------------------------------------------ config

def _cfg():
    # Read the file on each request so a key entered after server startup takes effect.
    env_file = ROOT / ".env"
    file_cfg = {}
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                file_cfg[k.strip()] = v.strip()
    return {
        "api_key": os.getenv("DEEPSEEK_API_KEY") or file_cfg.get("DEEPSEEK_API_KEY", ""),
        "base_url": os.getenv("DEEPSEEK_BASE_URL") or file_cfg.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        "model": os.getenv("DEEPSEEK_MODEL") or file_cfg.get("DEEPSEEK_MODEL", "deepseek-flash"),
    }


# ------------------------------------------------------------------ local OCR (bytes-friendly)

def ocr_text(pdf_source, dpi: int = 200) -> tuple[str, int, bool]:
    """Read usable PDF text layers; OCR image-only pages locally."""
    import pymupdf
    engine = None
    ocr_used = False
    if isinstance(pdf_source, (bytes, bytearray)):
        doc = pymupdf.open(stream=bytes(pdf_source), filetype="pdf")
    else:
        doc = pymupdf.open(str(pdf_source))
    try:
        if doc.needs_pass and not doc.authenticate(""):
            raise DeepSeekError("加密 PDF：空口令解不开，请先解密或提供口令。")
        pages = len(doc)
        parts: list[str] = []
        for i in range(pages):
            embedded = doc[i].get_text("text", sort=True).strip()
            if sum(char.isalnum() for char in embedded) >= 100:
                parts.append(f"=== PAGE {i + 1} (PDF text layer) ===")
                parts.append(embedded)
                continue
            if engine is None:
                engine = ocr_dump.load_ocr()
            ocr_used = True
            img, w, h = ocr_dump.page_to_bgr(doc, i, dpi, 0)
            result, _ = engine(img)
            _segs, lines = ocr_dump.to_lines(result or [])
            parts.append(f"=== PAGE {i + 1} ({w}x{h} @{dpi}dpi) ===")
            if lines:
                parts.extend(ln["text"] for ln in lines)
            else:
                parts.append("(no text recognized)")
        return "\n".join(parts), pages, ocr_used
    finally:
        doc.close()


# ------------------------------------------------------------------ prompt

def build_prompt(text: str, doc_type: str) -> str:
    schema = _find_schema()
    specs = schema["field_specs"].get(doc_type)
    if not specs:
        raise DeepSeekError(f"doc_type={doc_type!r} 不在 schema：{list(schema['field_specs'])}")
    field_lines = "\n".join(f"  - {name}：{desc}" for name, desc in specs.items())
    return (
        PREAMBLE
        + f"本次 doc_type = {doc_type}\n"
        + f"必须输出的字段清单（缺失也要显式给 null）：\n{field_lines}\n\n"
        + "==================== 以下为该扫描件本地 OCR 文本（唯一依据） ====================\n"
        + text
        + "\n==================== OCR 文本结束 ====================\n\n"
        + "现在，只输出 Contract-A JSON。\n"
    )


# ------------------------------------------------------------------ API call

def call_deepseek(prompt: str, api_key: str, base_url: str, model: str, timeout: int = 120) -> str:
    import requests
    url = base_url.rstrip("/") + "/chat/completions"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": (
                '只输出合法 JSON 对象，不要解释。格式示例：'
                '{"fields":{"example":{"value":null,"confidence":0,'
                '"raw_span":null,"normalized":true}},"parse_meta":{"warnings":[]}}'
            )},
            {"role": "user", "content": prompt},
        ],
        "thinking": {"type": "disabled"},
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "max_tokens": 8192,
    }
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body, timeout=timeout,
    )
    if resp.status_code != 200:
        if resp.status_code in (401, 403):
            raise DeepSeekError(f"DeepSeek HTTP {resp.status_code}: API 密钥未通过认证")
        raise DeepSeekError(f"DeepSeek HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        choice = resp.json()["choices"][0]
        content = choice["message"]["content"]
    except Exception as exc:  # noqa: BLE001
        raise DeepSeekError(f"DeepSeek 响应结构异常: {exc}") from exc
    if choice.get("finish_reason") == "length":
        raise DeepSeekError("DeepSeek 输出达到 token 上限，JSON 可能被截断")
    if not content:
        raise DeepSeekError("DeepSeek 返回空内容")
    return content


def parse_json(content: str) -> dict:
    """Robustly pull the JSON object out of an LLM reply (fences/prose tolerant)."""
    s = content.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.S)
    if fence:
        s = fence.group(1)
    else:
        a, b = s.find("{"), s.rfind("}")
        if a >= 0 and b > a:
            s = s[a:b + 1]
    try:
        data = json.loads(s)
    except json.JSONDecodeError as exc:
        raise DeepSeekError(f"模型输出不是合法 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise DeepSeekError("模型输出不是 JSON 对象")
    return data


# ------------------------------------------------------------------ post-process + light gate

def postprocess(data: dict, doc_type: str, source_name: str, pages: int, ocr_used: bool) -> dict:
    schema = _find_schema()
    specs = schema["field_specs"][doc_type]
    fields_in = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    fields: dict[str, dict] = {}
    for name in specs:  # keep ONLY contract fields, in contract order
        fv = fields_in.get(name) if isinstance(fields_in.get(name), dict) else {}
        value = fv.get("value")
        conf = fv.get("confidence", 0 if value is None else 0.6)
        raw = fv.get("raw_span")
        fields[name] = {
            "value": value,
            "confidence": round(float(conf) if isinstance(conf, (int, float)) else 0.0, 2),
            "raw_span": raw if value is not None else None,
            "normalized": bool(fv.get("normalized", True)),
        }
    return {
        "schema_version": schema.get("schema_version", "0.1-draft"),
        "prompt_version": "deepseek-v1",
        "doc_type": doc_type,
        "source_file": Path(source_name).name,
        "engine": f"deepseek({_cfg()['model']}) + " + (
            "local-rapidocr-ocr-v1" if ocr_used else "local-pdf-text-v1"
        ),
        "fields": fields,
        "parse_meta": {
            "ocr_used": ocr_used,
            "pages": pages,
            "warnings": [str(w) for w in (data.get("parse_meta") or {}).get("warnings", [])]
            or ["DeepSeek 引擎：LLM 自报置信仅供参考；关键字段建议对照原文复核"],
        },
    }


def light_check(doc: dict) -> list[str]:
    """Same spirit as validate.py ERR rules (structure only; final gate is still validate.py)."""
    errs: list[str] = []
    fields = doc.get("fields", {})
    for name, fv in fields.items():
        if not isinstance(fv, dict):
            errs.append(f"fields.{name}: 不是对象")
            continue
        for key in ("value", "confidence", "raw_span", "normalized"):
            if key not in fv:
                errs.append(f"fields.{name}: 缺 {key}")
        v, c, r = fv.get("value"), fv.get("confidence"), fv.get("raw_span")
        if v is None and c not in (0, 0.0):
            errs.append(f"fields.{name}: value=null 但 confidence={c} 应为 0")
        if v is None and r not in (None, ""):
            errs.append(f"fields.{name}: value=null 但 raw_span 非空")
        if v is not None and not r:
            errs.append(f"fields.{name}: value 非空但 raw_span 缺失")
        if v is not None and c == 0:
            errs.append(f"fields.{name}: value 非空但 confidence=0")
        if not (isinstance(c, (int, float)) and 0 <= c <= 1):
            errs.append(f"fields.{name}: confidence={c!r} 不在 [0,1]")
    return errs


# ------------------------------------------------------------------ orchestration

def extract_deepseek(pdf_source, doc_type: str, source_name: str = "upload.pdf",
                     dpi: int = 200, mock_reply: str | None = None) -> dict:
    """Main entry for the web backend. pdf_source: bytes or path. Returns Contract-A dict."""
    cfg = _cfg()
    if mock_reply is None and not cfg["api_key"]:
        raise DeepSeekError("缺 DEEPSEEK_API_KEY：请在 .env 填写（现场临时填即可）。")
    text, pages, ocr_used = ocr_text(pdf_source, dpi=dpi)
    if not text.strip() or text.count("(no text recognized)") >= pages:
        raise DeepSeekError("本地 OCR 未识别到任何文字，无法抽取。")
    prompt = build_prompt(text, doc_type)

    if mock_reply is not None:
        content = mock_reply
    else:
        content = call_deepseek(prompt, cfg["api_key"], cfg["base_url"], cfg["model"])

    def checked_document(reply: str) -> tuple[dict, list[str]]:
        result = postprocess(parse_json(reply), doc_type, source_name, pages, ocr_used)
        return result, light_check(result)

    try:
        doc, errs = checked_document(content)
    except DeepSeekError as exc:
        if mock_reply is not None:
            raise
        doc, errs = {}, [str(exc)]
    if errs and mock_reply is None:  # one repair round-trip against the real API
        retry_prompt = (
            prompt
            + "\n\n你上一次输出存在以下结构错误，请修正后重新只输出修正后的 JSON：\n- "
            + "\n- ".join(errs[:12])
        )
        content = call_deepseek(retry_prompt, cfg["api_key"], cfg["base_url"], cfg["model"])
        doc, errs = checked_document(content)
    if errs:
        raise DeepSeekError("模型输出未通过结构检查：" + "; ".join(errs[:12]))
    return doc


# ------------------------------------------------------------------ CLI

def main() -> int:
    ap = argparse.ArgumentParser(description="DeepSeek extraction engine (OCR text -> Contract-A).")
    ap.add_argument("pdf")
    ap.add_argument("--doc-type", default="customs_declaration")
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--out", required=True)
    ap.add_argument("--mock", action="store_true",
                    help="offline test: skip API, use a canned reply to exercise parse/postprocess")
    args = ap.parse_args()

    mock = None
    if args.mock:
        mock = json.dumps({
            "fields": {
                "declaration_no": {"value": "TZ6-6002483-5", "confidence": 0.9,
                                   "raw_span": "1. Filer Code / Entry No.  TZ6  6002483-5", "normalized": True},
                "trading_country": {"value": "CN", "confidence": 0.85, "raw_span": "10. Country of Origln  CN", "normalized": True},
                "transport_mode": {"value": "sea", "confidence": 0.8, "raw_span": "9. Mode of Transport  11", "normalized": True},
            },
            "parse_meta": {"warnings": ["mock 输出：仅验证解析/后处理链路"]},
        }, ensure_ascii=False)
    try:
        doc = extract_deepseek(args.pdf, args.doc_type, args.pdf, dpi=args.dpi, mock_reply=mock)
    except DeepSeekError as exc:
        print(f"[ERR] {exc}")
        return 2
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    pop = sum(1 for f in doc["fields"].values() if f["value"] is not None)
    print(f"[ok] {args.out}: {len(doc['fields'])} fields, {pop} populated (engine={doc['engine']})")
    print("     最终闸门: python skill/customs-doc-extraction/scripts/validate.py " + args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
