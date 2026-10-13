# SKILL.md · 单证抽取技能接口说明

> **接口**：输入单证文件（PDF/图片）→ 输出契约A JSON。
> 本文件是面向"人"的接口说明；面向 agent 的可执行版本在 `skill/customs-doc-extraction/`（英文），两者同步维护。

## 适用场景
- 报关单（美国 CBP 7501/3461、中国报关单、过境报关单）与提单（英文/中文，扫描或文本层）；
- 任何"把单证变成结构化字段"的请求，包括"读一下这份报关单""提取这份提单的字段"；
- **不适用**：差异比对/风险分级（线2 的事）、票据/发票单独抽取（当前契约未覆盖，v0.4 待议）。

## 执行步骤（agent 视角）
1. **渲染**：`scripts/render_pdf.py` ≥150dpi（推荐 200）渲染 PNG 再视觉读取；≤50dpi 缩略图只做页级定位；多页长卷先出 contact sheet 定位；
2. **分类**：判 `doc_type`（customs_declaration / bill_of_lading）；
3. **抽取**：按 `references/contract-A.md` 字段表逐字段抽取，全字段列出，缺失置 null；
4. **规则**：执行 `references/protocol-rules.md`（编号互证/形近字符复核/日期取栏/毛重聚合/名称仅名称行/多单证拆分/保密件须知）；
5. **验证**：`scripts/validate.py` 必须过（ERR 0），FAIL 打回重做；
6. **交付**：JSON 含顶层必填 `prompt_version` 与 `engine`；真实件输出只落 `out/`。

## 硬约束（同全局约束，浓缩）
只提取不判定 ｜ 禁止猜测填充（null + confidence 0）｜ 每字段 value+confidence+raw_span ｜ 归一化在抽取侧完成 ｜ 真实件与产物不入 git 不上平台 ｜ 保密件只本地处理。

## 验证方式
- 交付判据 = `scripts/validate.py` ERR 0（确定性闸门，不依赖执行者）；
- 质量判据 = `eval/run.py` 对 gt_draft 的字段准确率（报关单 ≥90%、提单 ≥85%）；
- 一致性判据 = `scripts/check_consistency.py` 四处一致（schema/规程/validator/口径表）。

## 失败处置
| 情形 | 处置 |
|---|---|
| validate 反复不过某字段 | 该字段置 null + warnings 说明原因，**不得**为过闸门改值 |
| 字形存疑且放大后仍不能定论 | 保留最可能读数 + confidence 0.5 + warnings 列全部候选 |
| 文件渲染失败 | 如实报错，不出"全猜测"JSON |
| 同页合排多单证 | 只抽主单证类型，warnings 注明共存单证 |

## 版本沿革（详见 VERSIONS.md）
v0.1（2026-10-05 初版）→ v0.3（2026-10-06 收紧 5 条歧义 + 保密件须知 + 读图工序）：同件实测 87.5% → 100%。
