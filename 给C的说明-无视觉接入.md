# 给 C 的说明：用 Codex 跑真实扫描件（无视觉接入）

> 一句话结论：**你的 `extract_local.py` / `app.py` 没问题**，跑不动不是代码 bug。是线1 早期的架构隐含假设变了——它默认"执行抽取的 agent 能看图片"（当初为守隐私删掉了独立 OCR 层）。Codex + ChatGPT 是**纯文本**、看不到扫描件的像素，所以真实件卡住。现在补了一座**本地 OCR 桥**，你不需要视觉也能跑通。

---

## 一、先给你的一段话（可直接复制）

> C：之前那版线1 要"能看图的 agent"才能抽扫描件，你的 Codex 没视觉所以跑不动真实件。现在 A 这边加了个**本地 OCR 桥** `ocr_dump.py`（用 rapidocr，全程离线、真实单证不出电脑）。你只要三步：① 把扫描件 `ocr_dump` 成文本；② 让 Codex 拿着《抽取规程》从**文本**里抽 Contract-A；③ `validate.py` 过关。注意一点：**真实 CBP 7501 是编号格子表单，你那套 `标签: 值` 正则本来就读不出它**——真实件必须走"OCR 文本 + 规程 + 大模型"，不要用正则解析真实件；正则只适合你自造的规整文本模板。详见 `docs/无视觉执行模式.md`。

---

## 二、你这边的操作步骤

```bash
# 0) 装依赖（新增的本地 OCR：rapidocr-onnxruntime，首次运行离线下载一次模型文件）
pip install -r requirements.txt

# 1) 把扫描件转成文本（只跑含表单那一页，省算力）
python scripts/ocr_dump.py <你的文件.pdf> --pages 2 --dpi 200 --out page.txt
#    · 页面横放：加 --rotate 90
#    · 看不清/密集：--dpi 300（关键字段核对可到 400）
#    · 想要每行坐标+置信度：再加 --json page.json

# 2) 把 page.txt 作为“原文文本”交给 Codex，提示词要点：
#    「严格依照 抽取规程（prompts/抽取规程-v0.3.md）从下面这段 OCR 文本抽取 Contract-A JSON。
#      每个字段给 value + confidence + raw_span（raw_span 直接引用该 OCR 行原文）；
#      文本里没有 / OCR 读不清的就置 null，禁止猜测，禁止虚高 confidence；
#      近似字符(B/8,D/Q,O/0)和金额/编号看不清就 null 或标低置信。」

# 3) 用硬闸门校验 Codex 产出的 JSON
python 抽取结果JSON/工具/validate.py out.json     # 或 skill/customs-doc-extraction/scripts/validate.py，二者等价
#    [ERR] → 打回，让 Codex 对照原文重抽，直到 ERR 0。
```

产出格式和 A/B 完全一致：同一份 Contract-A、同一个 `validate.py`、同一个 `eval/run.py`。只是“把像素变成字”这一步，从“看”换成了“本地 OCR”。

---

## 三、你会踩的坑（提前说，省得你以为是坏了）

1. **正则解析真实件 = 读不出。** `extract_local.py` 的 `LABEL: value` 正则只吃规整文本版式。真实海关件是**编号格子**（"1. Filer Code / Entry No."），OCR 出来也没有 `标签:` 结构 → 会得到“可识别字段太少/未找到字段”。**这不是 OCR 失败**（OCR 其实把整张表读出来了），是解析器不适配。→ 真实件请用第二节的“OCR 文本 + 规程 + 模型”路径。
2. **OCR 有噪声。** 形近字符、千分位逗号/小数点、印章遮挡很常见。ID / 金额 / HS 这类关键字段，按规程用 `--dpi 400` 局部复核后再定值；定不准就 `null` + `confidence 0`，别硬填。
3. **可选：给 app.py 也接上。** 如果一定要走你那个网页 demo，我给了个可选补丁 `scripts/extract_local.ocr_fallback.py`，整文件替换仓库里的 `extract_local.py` 即可：遇到加密/无文字层/超页会**先本地 OCR**，不再直接抛错；但若 OCR 文本仍是格子表单（正则拼不出），它会抛一句**指向本模式**的提示，而不是给你一个看着成功实则错的 JSON。真实件的可靠路径仍是第二节。

---

## 四、隐私红线（务必）

- `ocr_dump.py` **完全本地**（PyMuPDF 渲染 + onnxruntime 推理，无网络调用、无云 OCR）。真实单证的图片、OCR 文本、抽取结果**都留在你自己电脑上**。
- 真实件与其结果**不要提交 git、不要放进那个 GitHub 仓库**（尤其若是公开仓库）。给队友传真实测试样本，用网盘/局域网等**私有**渠道，仓库里只放脚本 + 脱敏样例。
- few-shot / 演示样例一律脱敏（公司名/单号/金额换成占位值）。

---

## 五、队长 A 的待办（把桥交到 C 手上）

C 目前是从 GitHub 仓库拉代码，本地这套桥还没进那个共享库。转交前需要：
- 把 `scripts/ocr_dump.py`、`prompts/抽取规程-v0.3.md`、`schema/contract-A.schema.json`、`validate.py` 放进仓库对应位置（线1 目录，如 `modules/line1/`），`requirements.txt` 加 `rapidocr-onnxruntime`；
- 可选把 `extract_local.ocr_fallback.py` 作为 `extract_local.py` 的替代推上去；
- **只推脚本与脱敏件**；确认 `data/raw_pdfs/`、`out/`、`抽取结果JSON/`、`eval/gt_*` 在 `.gitignore` 内，别把真实件带进（公开）仓库；
- 再把本说明 + `docs/无视觉执行模式.md` 发给 C。
