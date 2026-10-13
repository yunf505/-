# 契约B v0.2 · 给 C（线3 前端）的接口说明

**发件方**：B（线2 比对引擎）
**版本**：v0.2（含 P0 分票降级链补丁）
**冻结基线**：契约B v0.1（9-25 出初稿、当日冻结、"只加不改"）

---

## 0. TL;DR（30 秒读完）

- **6 个冻结字段原样保留**：`diffs[]` 里 `field / left / right / level / message / rule_id` 一个都没动
- **level 只有三个值**：`red` / `yellow` / `orange`（分工表硬约束，映射汉字由你侧做，见 §3.0）
- **顶层新增两个数组**：`not_comparable[]` 和 `notes[]`，都是"只加不改"范围，你不读也不影响运行
- **多票输出顶层结构**：`{contract_version, generated_by, generated_at, ticket_count, tickets: [...]}`；单票输出直接是 ticket dict
- **样例文件**：`sample-output-票01.json`（单票）+ `differences.json`（8 张 ticket 完整数据，可直接拿来做前端 mock）
- **JSON Schema**：`contract-B.schema.json`（draft-07，可用 `ajv` / `jsonschema` 等库校验）
- **有问题直接找我（B）**，不要自己兼容 —— 分工表 §4.2 明说契约B 由我维护

---

## 1. 相对 9-25 冻结版的变更点

按"只加不改"原则，全部是**新增**，没有删改任何冻结字段：

| 位置 | 新增内容 | 是否必读 |
|---|---|---|
| 顶层 | `not_comparable[]` 数组（港口一码一名等"不可比"场景） | ✅ 建议渲染 |
| 顶层 | `notes[]` 数组（金额容差内差异、OCR 模糊匹配命中） | 可选渲染 |
| 顶层（多票） | `ticket_count` / `generated_at` / `tickets[]` 包装 | ✅ 遍历用 |
| `groups[0].group_key_type` | 枚举扩展：新增 `bl_no` / `weak_invoice_triple` 两个值（原有 4 值保留） | ✅ 徽章展示 |
| `diffs[]` 每条 | `id / category / semantic_key / verdict / deviation / suggest_action / downgrade_reasons` | 可选 |
| `not_comparable[]` 每条 | 与 diffs 类似结构 + `reason` 字段（`code-vs-name` 等） | 建议渲染 |
| `notes[]` 每条 | 与 diffs 类似结构，`verdict ∈ {within_tolerance, fuzzy_match, match}` | 可选 |
| `alerts[]` 每条 | `id / category / trigger / url` | 建议渲染 |
| `skipped[]` 每条 | `id / source` | 可选 |
| `side`（left/right） | `doc_type / extracted` 两个字段 | 可选 |

**没变的**（冻结契约，动它就是违规）：
- `diffs[].field / left / right / level / message / rule_id` 6 个字段
- `level` 三值枚举 `red / yellow / orange`
- `alerts[]` 与 `diffs[]` 严格分开（合规提示不是差异）

---

## 2. 顶层结构

### 2.1 单票输出（例如 `sample-output-票01.json`）

```json
{
  "contract_version": "B-0.1",
  "ticket_id": "票01-不误报-容差与OCR噪声",
  "generated_by": "compare-engine-v0.1",
  "groups": [ { "group_key": "...", "group_key_type": "...", "docs": [...] } ],
  "diffs": [ ... ],
  "not_comparable": [ ... ],
  "notes": [ ... ],
  "alerts": [ ... ],
  "skipped": [ ... ],
  "summary": { ... }
}
```

### 2.2 多票输出（例如 `differences.json`）

```json
{
  "contract_version": "B-0.1",
  "generated_by": "compare-engine-v0.1",
  "generated_at": "2026-10-07T20:15:30",
  "ticket_count": 8,
  "tickets": [ {单票结构}, {单票结构}, ... ]
}
```

**判别方法**：有 `tickets` 字段就是多票包装，否则是单票。前端可以直接：
```js
const tickets = payload.tickets || [payload];
```

---

## 3. 五个数组的语义与渲染建议

### 3.0 五个数组的关系总览（先看这张表）

引擎的输出**分成五个互不重叠的数组**，各自承载不同语义。前端渲染时**必须分开**，混在一起会误导用户。

| 数组 | 语义 | 颜色建议 | 是否与"不一致"有关 | 渲染优先级 |
|---|---|---|---|---|
| `diffs[]` | 真实不一致（字段级差异） | 红/黄/橙三色 | ✅ 是 | ⭐⭐⭐ 核心 |
| `not_comparable[]` | 不可比（两侧存的不是同一类值） | 橙色边框 + "不可比"标签 | ❌ **不是不一致！** | ⭐⭐ 建议 |
| `notes[]` | 已识别但不告警（容差内 / OCR 模糊匹配） | 绿色或灰色 | ❌ 不是差异 | ⭐⭐ 建议（路演必展示） |
| `alerts[]` | 合规提示（10 条美国进口规则命中） | 红/黄/橙三色 | ❌ 与 diffs 无关 | ⭐⭐⭐ 单独区块 |
| `skipped[]` | 主动放弃比对的字段 | 灰显折叠 | ❌ 不是差异 | ⭐ 折叠区 |

**level 三值的中英映射**（引擎永远输出英文，映射由你侧做）：

| 引擎输出 | 分工表汉字 | 含义 | 建议色号 |
|---|---|---|---|
| `red` | 红 | 阻断，必须改单 | `#DC2626` 或类似 |
| `yellow` | 黄 | 需人工复核 | `#F59E0B` 或类似 |
| `orange` | 橙 | 存疑（缺失/低置信/疑似拼写） | `#FB923C` 或类似 |

**关键区分**：
- `diffs[]` 与 `alerts[]` 都是三色，但**语义完全不同**：前者是"两份单证写得不一样"，后者是"这批货可能触发某条美国进口法规"。渲染时必须分成两个区块，不能混
- `not_comparable[]` 虽然 level 也是 orange，但**不是差异**，是"没法比"。建议加一个明显的"不可比 ≠ 不一致"标签，避免用户误读
- `notes[]` 是"系统有业务判断力"的证明（识别到了但按规则放过），路演时评委问"你怎么保证不误报"就靠这个区块回答

### 3.1 `diffs[]` — 真实不一致（红/黄/橙）

**语义**：字段级差异，判定为 `different`。**这是核心业务输出**。

**渲染**：
- 按 `level` 三色：`red` = 阻断（红底白字）、`yellow` = 需人工复核（黄底黑字）、`orange` = 存疑（橙底黑字）
- 每条展示 `field` / `semantic_key` 作标题，`message` 作正文
- `left` / `right` 两栏并排展示 `value`，鼠标悬停 `raw_span.text` 显示"原文依据：第 N 页 …"
- `deviation.rel` 存在时在数字旁小字标注"偏差 0.74%"
- `suggest_action` 存在时作为一行灰字建议
- `downgrade_reasons` 存在时可加一个"⚠ 已降级"角标（说明该条原本更严重，因低置信被降级）

**示例**（票03 的身份冒用）：
```json
{
  "id": "D001",
  "category": "field_diff",
  "field": "买方",
  "semantic_key": "买方",
  "level": "red",
  "left":  { "source": "commercial_invoice.json", "doc_type": "commercial_invoice", "path": "fields.buyer", "value": "深港电子器件有限公司", "confidence": 1.0, "raw_span": {"page": 1, "text": "To: 深港电子器件有限公司 / Add: 深圳市南山区科技园路 88 号 3 栋"} },
  "right": { "source": "customs_declaration.json", "doc_type": "customs_declaration", "path": "fields.domestic_consignor", "value": "东岸国际贸易（深圳）有限公司", "confidence": 0.95, "raw_span": {"page": 1, "text": "30. Importer of Record: 东岸国际贸易（深圳）有限公司 / Add: 深圳市南山区科技园路88号3栋"} },
  "message": "买方疑似身份冒用：地址相同（归一化后「深圳市南山区科技园路88号3栋」）而主体名不同（...）",
  "rule_id": "CMP-IDENTITY",
  "verdict": "different",
  "suggest_action": "疑似身份冒用：地址相同而主体名不同，须核实实际进口方"
}
```

### 3.2 `not_comparable[]` — 不可比（不是不一致！）

**语义**：两侧存的**不是同一类值**（例如一侧是 CBP 港口代码 `2704`、另一侧是地名 `LOS ANGELES`），无法直接比对。**绝不能渲染成"不一致"**。

**渲染**：
- 橙色边框（`level=orange`），但**图标/文案要明确"不可比 ≠ 不一致"**
- 展示 `reason` 字段（`code-vs-name` / `code-vs-code` / `name-vs-name` / `missing-column`）
- 建议文案："两侧存储形式不同（一码一名），无法直接比对；如需硬判请查 HTS Annex C"

**为什么单独一个数组**：如果混进 `diffs[]` 会污染票01 的"0 红 0 黄"期望，也会让用户误以为"数据有问题"，实际是"数据形式不同"。

### 3.3 `notes[]` — 已识别但不告警

**语义**：引擎识别到了差异，但按规则**判定为可接受**。这是"系统有业务判断力"的证明。

**两种 `verdict`**：
- `within_tolerance`：金额容差内差异（例如票01 的 26762.00 vs 26762.40，差 0.40 美元，在 CBP 允许的整数舍入范围内）
- `fuzzy_match`：编号 OCR 模糊匹配命中（例如票01 的 `MAEU SZHVJM535` vs `MAEU92ZKVJM35`，ratio=0.769 ≥ 0.70，判为传真件二次扫描噪声而非造假）

**渲染**：
- 绿色或灰色，"已识别但不告警"区块
- **路演必展示** —— 评委问"你怎么保证不误报"时，就是这个区块的答案

### 3.4 `alerts[]` — 合规提示（不是差异）

**语义**：10 条美国进口合规规则的命中（例如 R-002 归类须依 GRI、R-005 射频设备 FCC 授权）。**与 diffs 完全无关**，是独立的合规提示通道。

**渲染**：
- **单独一个"合规提示"区块**，绝不与 `diffs` 混在一起
- 按 `level` 三色（red/yellow/orange）
- 展示 `message` + `basis`（依据文件名）+ `url`（官方链接，可点击打开原文）
- `trigger` 字段展示触发条件的实际取值（如 `{hs_prefix: "8708"}` 或 `{matched_keywords: ["bluetooth"]}`）

### 3.5 `skipped[]` — 主动放弃比对的字段

**语义**：引擎**故意不比**的字段及理由。**这是"系统不是瞎比"的证明**。

**当前两种 `policy`**：
- `POL-TO-ORDER`：提单 consignee 为指示抬头 `TO ORDER`，属国际贸易标准写法，改用 `notify_party` 旁证比对
- `POL-BOTH-NULL`：两侧字段均为 null，跳过

**渲染**：
- 灰显折叠区
- 展示 `field` + `reason` + `policy`
- 评委问"TO ORDER 为什么不报错"时，就是这个区块的答案

---

## 4. `groups[0].group_key_type` 枚举（P0 新增两值）

**语义**：这票是靠什么键归出来的。前端可直接展示成"归组依据"徽章，让走查方一眼看到归组可信度。

| 值 | 含义 | 强度 |
|---|---|---|
| `container_no` | 用箱号直接字段归组（发票/装箱单的 `container_no`） | 最强 |
| `container_no(from_bl_desc)` | 用箱号归组，但从提单 `goods_description` 正则抽 | 强 |
| `bl_no` | 用提单号归组（含从报关单 `parse_meta.warnings` 抽） | 中 |
| `weak_invoice_triple` | 用发票号 + 金额 + 件数三元组归组 | 弱 |
| `declaration_no` | 用报关单自号归组（弱键） | 弱 |
| `directory_name` | 兜底：同目录 = 同票 | 最弱，需人工确认 |

**渲染建议**：强度越弱，徽章颜色越接近橙色，提示"这票归组可信度较低"。

---

## 5. `summary` 结构

```json
{
  "checked_fields": 42,
  "diffs": { "red": 0, "yellow": 0, "orange": 0 },
  "not_comparable": 4,
  "notes": 2,
  "alerts": 1,
  "skipped": 1
}
```

**渲染**：顶部一排计数徽章。`checked_fields` 是"引擎跑过的字段级比对总次数"，可展示成"已检查 42 项"。

---

## 6. 判定阈值总表（前端可选展示，让用户明白为什么某条是 red/yellow/orange）

| 类别 | 绿区（不告警） | yellow | red | Rule ID |
|---|---|---|---|---|
| 金额 | \|a−b\| ≤ max(1.00, 0.005×max\|a,b\|) | 超绿区且 rel ≤ 0.05 | rel > 0.05 | `CMP-NUM-AMOUNT` |
| 毛重/净重 | rel ≤ 0.02 | 0.02 < rel ≤ 0.05 | rel > 0.05 | `CMP-NUM-WEIGHT` |
| 件数 | 完全相等 | 0 < rel ≤ 0.05 | rel > 0.05 | `CMP-NUM-PIECES` |
| 数量 | rel ≤ 0.02 | 0.02 < rel ≤ 0.05 | rel > 0.05 | `CMP-NUM-QTY` |
| 币种 | 精确相等 | — | 不等 | `CMP-CURRENCY` |
| 主体名 | 归一化后相等 | — | sim ≥ 0.85 → orange；< 0.85 → red | `CMP-STR-PARTY` |
| 身份冒用 | — | — | 地址同 + 名字不同 → red（不降级） | `CMP-IDENTITY` |
| 编号（bl_no / 箱号） | 归一化相等 或 ratio ≥ 0.70 | — | ratio < 0.70 → red | `CMP-ID-BL` / `CMP-ID-CONTAINER` |
| HS 编码 | 归一化 10 位后集合有交集 | — | 无交集 → red | `CMP-HS` |
| 港口（同类） | 码码全等 或 名名归一化全等 | — | 同类不等 → red | `CMP-PORT` |
| 港口（一码一名） | — | — | **not_comparable**（进 `not_comparable[]`，不进 diffs） | `CMP-PORT-NC` |
| 日期链 | packing_date ≤ declaration_date && invoice_date ≤ declaration_date | — | 违反 → red | `CMP-DATE` |
| 字段缺失 | 两侧均 null → skipped | 一侧 null → yellow | — | `CMP-MISSING` |
| 单证缺失 | — | 报关单栏18 非空 → yellow | — | `CMP-DOC-MISSING` |

**降级规则**（内嵌于所有比较器）：任一侧 `confidence < 0.7` / `normalized == false` / `warnings` 提及此字段 → red→yellow、yellow→orange。`CMP-IDENTITY` 不降级（红就是红）。

---

## 7. 已知限制（走查时会被问到）

1. **港口只比代码串相等**，未做 CBP 代码→地名映射（官方 HTS Annex C 电子件未取到，反推证据每条 n=1 不足以作硬判据）
2. **完整日期链未实现**：契约A v1.1 里 `bill_of_lading` 没有装船日字段，本引擎只做「`packing_date` / `invoice_date` ≤ `declaration_date`」两段近似检查
3. **品名不跨证比对**：映射表 §3 未列品名，且相似度阈值难定；品名只用于合规规则的关键词匹配
4. **`trading_country` vs `country_of_origin` 不比**：语义近似但非等同
5. **报关单 `bl_no` 从 warnings 正则抽**：契约A 报关单无该字段，若上游修改 warnings 文案会失效
6. **只在自造样例上验证**：真实扫描件的抽取噪声通过 `confidence` 与 `warnings` 降级处理，未在真件上全量回归
7. **票03 输出 6 红而非 3 红**：同一根因（发票重量整体虚增 + 发票买方冒用）在两对组合里各报一次，符合"同根因多处报出属正常"（`README-接手先看.md §5-B` 原话）。前端渲染时可考虑按 `rule_id + semantic_key` 折叠展示"根因组"

---

## 8. 交付物清单（本 zip 包内）

| 文件 | 用途 |
|---|---|
| `README-给C.md`（本文件） | 接口说明，读这个就够 |
| `contract-B.schema.json` | JSON Schema draft-07，可用 `ajv` / `jsonschema` 校验你的 mock 数据 |
| `sample-output-票01.json` | 单票样例（票01，0 红 0 黄，含 not_comparable / notes / skipped / alerts 全部数组） |
| `differences.json` | 8 张 ticket 完整数据（六票 fixtures + 票07 混票验证 X/Y），可直接拿来做前端 mock |
| `线2完成情况.md` | 完整实现文档（阈值表 + 已知限制 + 演示案例 + 决策记录），可选读 |

---

## 9. 8 张 ticket 的 mock 数据速览（`differences.json`）

| ticket_id | red | yellow | orange | not_comparable | notes | alerts | skipped | 演示价值 |
|---|---|---|---|---|---|---|---|---|
| 票01-不误报-容差与OCR噪声 | 0 | 0 | 0 | 4 | 2 | 1 | 1 | **不误报**（金额容差 + OCR 模糊匹配 + TO ORDER） |
| 票02-金额超容差 | 1 | 0 | 0 | 2 | 0 | 2 | 0 | 金额超容差判红 |
| 票03-主体不符-同址不同名 | 6 | 0 | 0 | 4 | 0 | 3 | 0 | **身份冒用**（CMP-IDENTITY）+ 重量差 |
| 票04-HS跨证不一致 | 1 | 0 | 0 | 2 | 0 | 1 | 1 | HS 跨证红 |
| 票05-件数缺失-日期矛盾-缺发票 | 1 | 2 | 0 | 2 | 0 | 1 | 0 | 日期矛盾红 + 件数缺失黄 + 单证缺失黄 |
| 票06-港口码对地名-不可比 | 0 | 0 | 0 | 2 | 0 | 1 | 1 | **不可比 ≠ 不一致** |
| 票07-混票归组验证#SYNU7777001 | 0 | 0 | 0 | 2 | 0 | 1 | 0 | 混票分票验证 X |
| 票07-混票归组验证#SYNU7777002 | 0 | 0 | 0 | 2 | 0 | 3 | 0 | 混票分票验证 Y |

**建议 C 侧优先做的三张 demo 卡**：
- 票01（不误报，展示 notes 和 skipped）
- 票03（身份冒用，展示 CMP-IDENTITY 和 raw_span 证据）
- 票06（不可比，展示 not_comparable 单独区块）

---

## 10. 反馈渠道

**如果你那版结构与本包不一致**：
1. 先告诉我哪里不一致
2. 我判断是"你侧兼容"还是"我侧调整"
3. **不要自己兼容** —— 分工表 §4.2 明说契约B 由我维护，避免双向漂移

**如果需要更多样例数据**（例如某类差异你没见过）：告诉我，我可以现场造 fixtures 跑给你。

---

## 附录：字段字典（`side` 结构）

```json
{
  "source": "commercial_invoice.json",         // 文件名
  "doc_type": "commercial_invoice",            // 契约A v1.1 的 4 种 doc_type 之一
  "path": "fields.total_value.amount",         // 字段路径
  "value": 26762.40,                           // 值（可以是标量/数组/对象/null）
  "confidence": 0.95,                          // 抽取置信度 0-1
  "raw_span": { "page": 1, "text": "..." },    // 原文位置（也可以是字符串或 null）
  "normalized": true,                          // 是否已归一化
  "extracted": false                           // 【P0 新增】true 表示从其他文本正则抽的（如提单箱号从 goods_description 抽）
}
```

**`raw_span` 兼容性**：可能是字符串（A 的示例格式）或对象 `{page, text}`（A 的实产格式）。前端渲染时：
```js
const spanText = typeof raw_span === 'string' ? raw_span : (raw_span?.text || '');
const spanPage = typeof raw_span === 'object' ? raw_span?.page : null;
```

---

**联系人**：B（线2 主责）
**契约B 维护权**：B（分工表 §4.2）
**本包版本**：v0.2
