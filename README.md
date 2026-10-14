# 跨境物流单证核验 · 本地演示

面向企业单证核验人员的本地网页。演示链路是：浏览器上传同票 PDF → 优先读取 PDF 文字层、无文字层时本地 OCR，再调用 DeepSeek 生成契约 A JSON（失败时回退本地文本解析器）→ A 校验器检查字段结构 → B `compare.py` 生成差异与合规提示 → 网页核实并导出 HTML 报告。

## 启动

先确认 Windows 上已安装 Python 3.10 或更新版本（`python --version`）。在本目录安装依赖，并将 `modules/line1/.env.example` 复制为 `modules/line1/.env`。如需使用 DeepSeek，在 `.env` 填写 `DEEPSEEK_API_KEY`；该文件已被 Git 忽略。随后双击 `启动演示.cmd`，或手动运行：

```powershell
python -m pip install -r requirements-demo.txt
python app.py
```

没有有效密钥或 API 调用失败时，演示会回退到本地文本解析器；自造样例仍可完成上传、比对和报告导出。DeepSeek 模式会把本地提取的文字发送给 DeepSeek API，请只上传允许这样处理的单证。修改 `.env` 后，下一次分析会读取新配置。网页的“抽取来源”会显示本次实际使用了 DeepSeek 还是本地回退。

浏览器通常会自动打开 `http://127.0.0.1:8765/`，也可手动访问。**请通过这个地址访问，直接双击 HTML 文件不能调用本地 Python。** 服务仅绑定本机 `127.0.0.1`，按 `Ctrl+C` 停止。端口占用时可运行 `python app.py --port 8766`，再访问 `http://127.0.0.1:8766/`。

## 四份上传样例

在网页的“上传单证”页下载 `demo_docs/` 中的报关单 `customs_declaration.pdf`、提单 `bill_of_lading.pdf`、商业发票 `commercial_invoice.pdf`、装箱单 `packing_list.pdf`，分别选择对应类型，再点“开始抽取并比对”。四份文件属于同一票完全虚构的货物；商业发票的毛重和净重与其余单证不同，用于展示异常。每类选一份，至少选择两类；四类齐全最能展示完整流程。

这些 PDF 的文字可复制。若需要重新生成，安装 `reportlab` 后运行 `python scripts/generate_demo_pdfs.py`。上传文件在内存中读取，契约 A JSON 仅写入系统临时目录供 B 引擎处理，请求完成后自动清理；核实记录只在当前页面保留，需留档时导出 HTML 报告。

## 处理流程

```text
浏览器上传 PDF
  → 本地文字层/OCR + DeepSeek API 生成契约 A JSON；失败时 extract_local.py 解析文本
  → A 的 validate.py 检查字段结构与来源信息
  → B 的 compare.py 生成跨单证差异与提示
  → 网页展示、人工核实、导出报告
```

## 模块来源与边界

| 目录或文件 | 用途 |
| --- | --- |
| `modules/line1/` | A 交接包中的契约、规程、校验器和 skill。11 份真实件抽取结果与含实弹记录的交接文档不纳入本次推送。 |
| `modules/line2/` | B v0.2 代码包，包含 `compare.py`、`rules.py`、`verify.py`、契约 B 和自造 JSON 夹具。原代码未改动。 |
| `modules/line1/src/deepseek_extract.py` | 优先读取 PDF 文字层，无文字层时本地 OCR，然后调用 DeepSeek 输出契约 A。 |
| `extract_local.py` | DeepSeek 不可用时使用的确定性文本 PDF 提取器，输出契约 A。它不是 A 的 agent 视觉抽取流程。 |
| `app.py` | 本次新增的本地 HTTP 服务，上传、校验、调用 B 引擎并返回结果。 |
| `vendor/` | 本地运行所需的 pypdf 6.10.0 与 python-dotenv 1.2.2；各自许可证随目录保留。 |
| `C-页面设计稿.html` | 企业用户页面；可查看完整提取字段、分级差异、不可比项、合规提示，记录人工结论并导出报告。 |

首版只支持按四种单证类型选择 PDF。DeepSeek 路径在无文字层时使用本地 OCR，理论上可处理扫描件，但尚未用真实业务版式验证准确率；无密钥或 API 失败后的本地解析器只支持带可复制文字、使用演示标签版式的 PDF。A 的原 agent 视觉流程尚未接入网页。不要把自造样例效果解读为对真实业务单证的抽取准确率。

`raw_pdf/` 与 `赛题二测试资料（精简）/` 是仓库既有资料，不是本次自造上传样例。其中包含真实单证，`raw_pdf/` 还包含套 D；本地演示请使用 `demo_docs/`。现有仓库已跟踪部分真实 PDF，新增的 `.gitignore` 不会自动移除既有 Git 历史，后续对外发布前需单独处理资料范围。

## 自检

在 `modules/line1/` 目录运行 `python scripts/check_consistency.py`；在 `modules/line2/` 目录运行 `python verify.py`，预期为 `16/16 PASS`。网页端到端可用四份样例实际上传；返回结果应包含四份契约 A 字段、同一票的差异卡片和可导出的核验报告。A 的完整环境检查 `doctor.py` 面向其 agent 工作流，依赖另行安装的 PyMuPDF 等组件；本演示不需要这些组件。
