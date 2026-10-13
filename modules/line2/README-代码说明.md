# 线2 比对引擎 · 代码包使用说明

**给 C（线3 前端）** · 版本 v0.2

这个包是**可运行的完整代码**，不只是接口文档。解压后按下面三步就能跑起来看效果。

---

## 0. TL;DR（30 秒上手）

```bash
# 解压后进入目录
cd 契约B-代码包-v0.2

# 1. 跑全部 7 票 fixtures（六票 + 票07 混票验证），输出 8 张 ticket 到 differences.json
python compare.py 03-输入/fixtures-synthetic -o differences.json

# 2. 跑单票看细节（例如票03 的身份冒用案例）
python compare.py 03-输入/fixtures-synthetic/票03-主体不符-同址不同名

# 3. 跑自动化验收（16 项检查，PASS/FAIL 汇总）
python verify.py
```

**零第三方依赖**，Python 3.8+ 标准库即可。`jsonschema` 是可选的（只有你想自己校验 schema 时才装）。

---

## 1. 目录结构

```
契约B-代码包-v0.2/
├── README-代码说明.md          ← 本文件，先读这个
├── README-给C.md               ← 接口说明（字段字典、渲染建议、五个数组语义）
│
├── compare.py                  ← 主引擎（约 1600 行）
├── rules.py                    ← 10 条美国合规规则（约 260 行）
├── verify.py                   ← 自动化验收脚本（约 320 行）
│
├── contract-B.schema.json      ← 契约B JSON Schema (draft-07)
├── differences.json            ← 已跑好的 8 张 ticket 结果（可直接做前端 mock）
├── sample-output-票01.json     ← 单票样例（票01，0 红 0 黄）
├── 线2完成情况.md              ← 完整实现文档（阈值表、已知限制、决策记录）
│
└── 03-输入/
    └── fixtures-synthetic/
        ├── README.md                          ← 六票验收基准（硬指标在 §1）
        ├── gen_fixtures.py                    ← 数据生成脚本（改数据改这里，别手改 JSON）
        ├── 票01-不误报-容差与OCR噪声/         ← 3 份 JSON
        ├── 票02-金额超容差/                   ← 2 份 JSON
        ├── 票03-主体不符-同址不同名/          ← 3 份 JSON（A 点名的负样本都在这票）
        ├── 票04-HS跨证不一致/                 ← 2 份 JSON
        ├── 票05-件数缺失-日期矛盾-缺发票/     ← 2 份 JSON
        ├── 票06-港口码对地名-不可比/          ← 2 份 JSON
        └── 票07-混票归组验证/                 ← 4 份 JSON（两票混放，验证分票）
```

---

## 2. 三个 Python 文件的职责

| 文件 | 职责 | 行数 |
|---|---|---|
| `compare.py` | 主引擎：加载 JSON → 分票（4 档降级链）→ 两两比对（数值/字符/编号/港口/HS/日期）→ 组装契约B 输出 | ~1600 |
| `rules.py` | 10 条美国合规规则（R-001 到 R-010），纯 if-then，输出进 `alerts[]` | ~260 |
| `verify.py` | 自动化验收：期望值对照 + 契约B 结构校验 + 确定性校验 + 硬约束自检 | ~320 |

**依赖关系**：`compare.py` import `rules.py`；`verify.py` 用 subprocess 调 `compare.py`。三个文件必须在同一目录。

---

## 3. CLI 用法

### 3.1 `compare.py`

```bash
python compare.py <path> [-o OUTPUT] [--pretty]
```

| 参数 | 说明 |
|---|---|
| `<path>` | 任意目录（递归加载全部 JSON）或单个 JSON 文件。支持平铺目录（无子目录）和多票混放目录 |
| `-o, --output` | 输出 JSON 文件路径；不给则打印到 stdout |
| `--pretty` | 缩进 2 空格输出（默认开启） |

**三种典型用法**：

```bash
# A. 跑全部 fixtures（递归加载 18 份 JSON → 分成 8 张 ticket）
python compare.py 03-输入/fixtures-synthetic -o differences.json

# B. 跑单票目录（只看一票的细节）
python compare.py 03-输入/fixtures-synthetic/票03-主体不符-同址不同名

# C. 跑混票目录（验证分票降级链）
python compare.py 03-输入/fixtures-synthetic/票07-混票归组验证
# 输出会是 2 tickets（票 X 和票 Y），不会串票
```

**stderr 会打印每票摘要**，方便快速看结果：
```
票01-不误报-容差与OCR噪声: red=0 yellow=0 orange=0 not_comparable=4 notes=2 alerts=1 skipped=1 | group_key=SYNU1234567 type=container_no
票03-主体不符-同址不同名: red=6 yellow=0 orange=0 not_comparable=4 notes=0 alerts=3 skipped=0 | group_key=SYNU3344556 type=container_no
...
```

### 3.2 `verify.py`

```bash
python verify.py
```

无参数。跑 4 项检查共 16 个断言：
1. **逐票比对期望值**（9 项）：硬编码自 fixtures/README §1 + 票07 混票验证
2. **契约B 结构校验**（2 项）：6 必填字段 + level 三值枚举 + summary 一致性
3. **确定性校验**（1 项）：连跑两次输出逐字节相等（排除 `generated_at`）
4. **硬约束自检**（4 项）：扫代码无 LLM 调用 + 无真实件路径 + schema enum + not_comparable 独立数组

**退出码**：0 = 全过，1 = 有 FAIL。可以接 CI。

### 3.3 `gen_fixtures.py`（在 fixtures-synthetic/ 目录里）

```bash
cd 03-输入/fixtures-synthetic
python gen_fixtures.py
```

重新生成全部 7 票的 JSON 文件。**改数据请改这个脚本，不要手改 JSON**（fixtures/README §0 明确要求）。脚本无随机、无时间戳，重跑输出字节级一致。

---

## 4. 前端集成建议

### 4.1 直接用已跑好的 `differences.json`

包里已经有一份跑好的 `differences.json`（8 张 ticket），**你不需要跑 Python 就能做前端 mock**：

```js
import data from './differences.json';
const tickets = data.tickets || [data];  // 兼容单票/多票
tickets.forEach(renderTicketCard);
```

### 4.2 如果想动态跑引擎

两种方案：

**方案 A：前端调后端 API**（推荐）
- 后端起一个 Flask/FastAPI 服务，包装 `compare.py` 的 `run_pipeline()` 函数
- 前端上传 JSON → 后端跑引擎 → 返回契约B JSON
- 优点：引擎逻辑集中在后端，前端只管渲染

**方案 B：前端用 Pyodide 跑 Python**（不推荐）
- 把 compare.py + rules.py 编译成 WASM 在浏览器跑
- 缺点：包体积大、性能差、调试难

### 4.3 渲染优先级

按 `README-给C.md` §3.0 的总览表：
1. **先做 `diffs[]`**（核心业务输出，红/黄/橙三色）
2. **再做 `alerts[]`**（合规提示，单独区块）
3. **然后 `not_comparable[]`**（不可比，橙色边框 + "≠ 不一致"标签）
4. **最后 `notes[]` 和 `skipped[]`**（路演加分项，证明系统有判断力）

### 4.4 三张必做的 demo 卡

| ticket_id | 演示价值 |
|---|---|
| 票01-不误报-容差与OCR噪声 | **不误报**：金额容差内 + OCR 模糊匹配 + TO ORDER 跳过 |
| 票03-主体不符-同址不同名 | **身份冒用**：CMP-IDENTITY，raw_span 里有地址证据 |
| 票06-港口码对地名-不可比 | **不可比 ≠ 不一致**：not_comparable 单独区块 |

---

## 5. 关键设计点（走查时会被问到）

### 5.1 判定全是确定性规则，无 LLM

- `compare.py` / `rules.py` 全文无一处调用大模型
- 所有 `message` 都是 f-string 模板化
- `suggest_action` 是硬编字典
- `verify.py` 检查 4 会用 9 条正则扫全文确认（openai/anthropic/langchain/api_key/model=/chat.completions/llm./gpt-N/claude-N）

**为什么**：分工表硬约束①"比对用确定性规则引擎，LLM 只做解释，不参与判定——判定可复现，演示才敢现场点"。

### 5.2 level 只有三个值

- `red` / `yellow` / `orange`，枚举强制
- 中英映射（红/黄/橙）由前端做，引擎永远输出英文
- `contract-B.schema.json` 里 `definitions.level.enum` 锁死

### 5.3 not_comparable 单独数组

- 港口一码一名（如 `2704` vs `LOS ANGELES`）→ 进 `not_comparable[]`，不进 `diffs[]`
- 语义："没法比" ≠ "比出来不一样"
- 前端必须单独区块渲染，明确标注"不可比 ≠ 不一致"

### 5.4 分票 4 档降级链（P0 补丁）

- 第 1 档：`container_no` 严格相等
- 第 2 档：`bl_no` 严格相等（**故意不用模糊匹配**，防票04/06 串票）
- 第 3 档：`invoice_no` + 三元组；一方无 invoice_no 时退化为"金额宽容差 + 件数相等"
- 第 4 档：同目录兜底

**票07 验证**：两票货的四份文档混放同一目录，引擎正确分成 2 tickets，无跨票假差异。

### 5.5 票03 输出 6 红而非 3 红

- 同一根因（发票重量整体虚增 + 发票买方冒用）在两对组合里各报一次
- 符合"同根因多处报出属正常"（`README-接手先看.md §5-B` 原话）
- 前端渲染时可考虑按 `rule_id + semantic_key` 折叠展示"根因组"

---

## 6. 已知限制（走查时如实说）

1. **港口只比代码串相等**，未做 CBP 代码→地名映射（官方 HTS Annex C 电子件未取到，反推证据每条 n=1 不足以作硬判据）
2. **完整日期链未实现**：契约A v1.1 里 `bill_of_lading` 没有装船日字段，只做两段近似检查
3. **品名不跨证比对**：映射表 §3 未列品名，且相似度阈值难定
4. **报关单 `bl_no` 从 warnings 正则抽**：契约A 报关单无该字段，若上游修改 warnings 文案会失效
5. **只在自造样例上验证**：真实扫描件的抽取噪声通过 `confidence` 与 `warnings` 降级处理，未在真件上全量回归

完整列表见 `线2完成情况.md` §5。

---

## 7. 三条硬约束（走查会看）

| # | 约束 | 落实位置 |
|---|---|---|
| ① | LLM 不得参与判定 | 全文无模型调用；verify.py 检查 4 自动扫描 |
| ② | 只用 fixtures-synthetic/ 自造数据 | 代码里无任何真实件路径；verify.py 检查 4 自动扫描 |
| ③ | level 只用 red/yellow/orange | schema enum 强制；verify.py 检查 2 验证 |
| ④ | not_comparable 单独承载 | 顶层独立数组；verify.py 检查 4 验证 |

---

## 8. 有问题找谁

- **契约B 结构问题** → 找 B（线2 主责），不要自己兼容（分工表 §4.2）
- **需要更多样例数据** → 告诉 B，可以现场造 fixtures 跑给你
- **引擎 bug** → 先跑 `python verify.py` 看是哪项 FAIL，把输出贴给 B
- **想改阈值/规则** → 阈值在 `compare.py` 顶部常量区，规则在 `rules.py`，但**改前先找 B 确认**，避免双向漂移

---

## 附录：快速自检清单

拿到包后按顺序跑一遍，确认环境 OK：

```bash
# 1. Python 版本 ≥ 3.8
python --version

# 2. 跑单票，看 stdout 是否输出合法 JSON
python compare.py 03-输入/fixtures-synthetic/票01-不误报-容差与OCR噪声

# 3. 跑全部 fixtures，看 stderr 摘要是否 8 票
python compare.py 03-输入/fixtures-synthetic -o /tmp/test.json

# 4. 跑验收，看是否 16/16 PASS
python verify.py

# 5.（可选）装 jsonschema 自己校验输出
pip install jsonschema
python -c "
import json
from jsonschema import Draft7Validator
schema = json.load(open('contract-B.schema.json', encoding='utf-8'))
data = json.load(open('differences.json', encoding='utf-8'))
v = Draft7Validator(schema)
for t in data.get('tickets', [data]):
    errs = list(v.iter_errors(t))
    print(t['ticket_id'], '✅' if not errs else f'❌ {len(errs)} errors')
"
```

全过就说明环境没问题，可以开始做前端了。
