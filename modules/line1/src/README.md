# src/
解析管线。建议最小结构:
```
src/
  extract.py          # CLI 入口:python extract.py <file> -o out/xx.json
  pdf_text.py         # 文本层抽取(PyMuPDF/pdfplumber)
  ocr.py              # 扫描件→文本(渲染 200dpi + OCR)
  llm_extract.py      # LLM 结构化(JSON mode + schema 约束)
  normalize.py        # 归一化与校验(日期/金额/单位/HS)
  schema_check.py     # 输出契约校验
```
原则:文本层件先跑通,再攻扫描件;两条路径在 llm_extract 之后合流。
