"""Local-only web demo: upload PDF -> Contract A -> B comparison -> browser.

Run: python app.py
Open: http://127.0.0.1:8765/
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import webbrowser
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from extract_local import DOC_TYPES, ExtractionError, extract_pdf

# --- 线1 DeepSeek 引擎接线（本地OCR→deepseek-flash API→契约A；失败自动退回本地解析器） ---
_LINE1 = Path(__file__).resolve().parent / "modules" / "line1"
if str(_LINE1 / "src") not in sys.path:
    sys.path.insert(0, str(_LINE1 / "src"))
try:
    from deepseek_extract import extract_deepseek, DeepSeekError
    DEEPSEEK_READY = True
except Exception as _exc:  # 缺依赖/缺文件时不阻断启动，退回纯本地
    extract_deepseek, DeepSeekError, DEEPSEEK_READY = None, None, False
    print(f"[WARN] DeepSeek 引擎不可用（{ _exc }），将使用本地文本解析器。", file=sys.stderr)


ROOT = Path(__file__).resolve().parent
VENDOR = ROOT / "vendor"
HTML = ROOT / "C-页面设计稿.html"
DEMO_DIR = ROOT / "demo_docs"
LINE1 = ROOT / "modules" / "line1"
LINE2 = ROOT / "modules" / "line2"
MAX_REQUEST_BYTES = 30 * 1024 * 1024
MAX_FILE_BYTES = 12 * 1024 * 1024


def extract_document(pdf_bytes: bytes, doc_type: str, filename: str) -> dict:
    """DeepSeek 引擎优先；不可用/失败时退回本地文本解析器（离线演示路径）。"""
    if DEEPSEEK_READY:
        try:
            return extract_deepseek(pdf_bytes, doc_type, filename)
        except DeepSeekError as exc:
            print(f"[WARN] DeepSeek 引擎失败（{exc}），退回本地解析器：{filename}", file=sys.stderr)
        except Exception as exc:  # 网络/解析等意外，不让演示直接崩
            print(f"[WARN] DeepSeek 引擎异常（{exc}），退回本地解析器：{filename}", file=sys.stderr)
    document = extract_pdf(pdf_bytes, doc_type, filename)
    if DEEPSEEK_READY:
        document.setdefault("parse_meta", {}).setdefault("warnings", []).append(
            "DeepSeek 引擎不可用，本结果由本地文本解析器生成"
        )
    return document


def analyze(files: dict[str, tuple[str, bytes]]) -> dict:
    """Use the existing A validator and B engine without changing their code."""
    if not 2 <= len(files) <= 4:
        raise ExtractionError("请至少选择两类、最多四类同票单证。")
    documents = []
    with tempfile.TemporaryDirectory(prefix="shulian-demo-") as work:
        workdir = Path(work)
        for doc_type in DOC_TYPES:
            if doc_type not in files:
                continue
            filename, pdf_bytes = files[doc_type]
            if len(pdf_bytes) > MAX_FILE_BYTES:
                raise ExtractionError(f"{filename} 超过单文件 12 MB 限制。")
            document = extract_document(pdf_bytes, doc_type, filename)
            json_path = workdir / f"{doc_type}.json"
            json_path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
            check = subprocess.run(
                [sys.executable, str(LINE1 / "src" / "validate.py"), str(json_path), "--quiet"],
                cwd=LINE1, env={**os.environ, "PYTHONPATH": str(VENDOR) + os.pathsep + os.environ.get("PYTHONPATH", "")},
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
            )
            if check.returncode != 0:
                raise ExtractionError(f"{filename} 的字段结构未通过契约 A 校验：{check.stdout.strip() or check.stderr.strip()}")
            documents.append(document)

        comparison = subprocess.run(
            [sys.executable, str(LINE2 / "compare.py"), str(workdir)],
            cwd=LINE2, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        if comparison.returncode != 0:
            raise RuntimeError("比对引擎运行失败：" + (comparison.stderr.strip() or comparison.stdout.strip()))
        try:
            payload = json.loads(comparison.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("比对引擎没有返回有效 JSON。") from exc

    tickets = payload.get("tickets") if isinstance(payload, dict) else None
    if tickets is None:
        tickets = [payload]
    if not tickets or any("summary" not in ticket for ticket in tickets):
        raise RuntimeError("比对引擎未返回有效的单票结果。")
    return {"mode": "live", "ticket_count": len(tickets), "tickets": tickets, "documents": documents}


class Handler(BaseHTTPRequestHandler):
    server_version = "ShulianLocalDemo/1.0"

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        path = unquote(urlsplit(self.path).path)
        if path in {"/", "/C-页面设计稿.html"}:
            self._send(200, HTML.read_bytes(), "text/html; charset=utf-8")
            return
        if path == "/api/health":
            self._json(200, {"status": "ready", "mode": "local"})
            return
        if path.startswith("/demo_docs/"):
            name = path.removeprefix("/demo_docs/")
            allowed = {f"{doc_type}.pdf" for doc_type in DOC_TYPES}
            if name in allowed and (DEMO_DIR / name).is_file():
                self._send(200, (DEMO_DIR / name).read_bytes(), "application/pdf")
                return
        if path == "/favicon.ico":
            self._send(204, b"", "image/x-icon")
            return
        self._json(404, {"error": "页面不存在。"})

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/api/analyze":
            self._json(404, {"error": "接口不存在。"})
            return
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("multipart/form-data;"):
            self._json(415, {"error": "请以 multipart/form-data 上传 PDF。"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = 0
        if not 0 < size <= MAX_REQUEST_BYTES:
            self._json(413, {"error": "上传内容为空或超过 30 MB。"})
            return
        try:
            body = self.rfile.read(size)
            header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii")
            message = BytesParser(policy=default).parsebytes(header + body)
            if not message.is_multipart():
                raise ExtractionError("上传格式不正确。")
            files: dict[str, tuple[str, bytes]] = {}
            for part in message.iter_parts():
                doc_type = part.get_param("name", header="content-disposition")
                filename = part.get_filename()
                if doc_type not in DOC_TYPES or not filename:
                    continue
                if doc_type in files:
                    raise ExtractionError("同一单证类型请只选择一份 PDF。")
                if not filename.lower().endswith(".pdf"):
                    raise ExtractionError(f"{filename} 不是 PDF 文件。")
                content = part.get_payload(decode=True) or b""
                files[doc_type] = (Path(filename).name, content)
            self._json(200, analyze(files))
        except ExtractionError as exc:
            self._json(422, {"error": str(exc)})
        except subprocess.TimeoutExpired:
            self._json(504, {"error": "本地分析超时，请减少文件页数后重试。"})
        except Exception as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            self._json(500, {"error": "本地分析未完成，请查看启动窗口中的错误信息。"})


def main() -> None:
    parser = argparse.ArgumentParser(description="跨境物流单证核验本地演示")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"本地演示已启动：{url}")
    print("按 Ctrl+C 停止；上传文件只在内存和系统临时目录中处理。")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open_new_tab(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
