---
name: customs-doc-extraction
description: Extract structured fields from cross-border trade documents (customs declarations CBP 7501/3461, China customs forms, bills of lading) as PDFs or images, and output normalized Contract-A JSON with per-field value + confidence + raw_span. Use this skill whenever the user asks to extract, parse, digitize, or structure information from customs declarations, entry summaries, bills of lading, packing lists, or any cross-border logistics/trade document — even if they only say "read this customs form" or "get the fields out of this shipping document". Also use when asked to produce Contract-A JSON or validate such output. NOT for difference comparison or compliance judgment (that is a downstream module).
version: 1.0.0
---

# Customs Document Extraction (Contract-A)

Turn a trade document file (PDF/image) into **Contract-A structured JSON**.
You are an extraction engine: **extract only, never judge**. Difference comparison, risk
grading, and compliance verdicts belong to a downstream module — do not emit them.

## When to use

- Input: a customs declaration (US CBP 7501/3461, China 报关单, transit forms) or a
  bill of lading (ocean BL, port-to-port or combined transport), as PDF or image.
- Output: one JSON record per shipment ("entry"), all fields, normalized.

## Workflow (follow in order)

### 1. Render before reading (critical for scanned/court PDFs)

Most real-world files are scans; some are court exhibits that a PDF reader may
wrongly report as "password-protected". Do not fight the reader — render to PNG:

```bash
python <skill-dir>/scripts/render_pdf.py <file.pdf> --pages 2 --dpi 200 --out page.png
```

Rules of thumb (learned from live-fire testing):
- **≥150 dpi for field extraction** (200 dpi is the sweet spot).
- **≤50 dpi thumbnails only for page-level triage** ("which page is the 7501?") —
  never extract fields from them; at that resolution even case numbers get
  misread (B/D/Q and 8/B confusions observed in testing).
- Multi-page/long files: render a 50 dpi contact sheet first to locate target
  pages, then re-render those pages at 200 dpi.
- Rotated pages (90°/270°) can be read as-is by vision; no pre-rotation needed.
- GIF/JPG inputs are fine directly.

### 2. Classify the document

`doc_type` ∈ `customs_declaration` | `bill_of_lading`. If undecidable, emit all-null
fields and explain in `parse_meta.warnings`.

### 3. Extract every field per the contract

Read `references/contract-A.md` for the authoritative field tables (16 customs
fields, 9+2 BL fields), normalization rules, and enums. Every field in the table
MUST appear in your output — missing ones get `value: null`.

### 4. Apply the special rules

Read `references/protocol-rules.md` before finalizing. The rules that most often
catch errors in practice:
- **ID cross-verification**: entry/BL numbers usually appear in multiple places.
  When readings disagree, keep the highest-resolution reading, drop confidence to
  0.5, and list all candidates in warnings.
- **Lookalike characters** (B/8, D/Q, O/0, I/1): re-render that region at higher
  dpi before deciding.
- **Date bar selection**, **gross-weight aggregation**, **name-line-only**,
  **multi-document files**: all specified there.

### 5. Self-check, then run the deterministic gate

Your JSON must pass the bundled validator — this is non-negotiable, because a
validator is the only part of this pipeline that is 100% reproducible across
different agents:

```bash
python <skill-dir>/scripts/validate.py <your_output.json>
```

- `[ERR]` lines → fix and re-run until **ERR 0**. Common causes: value non-null
  but raw_span missing; value null but confidence > 0; wrong enum; packages as
  float; date not YYYY-MM-DD; hs_code not 10 digits (that one is a WARN).
- `[WARN]` lines are allowed (they flag items for human review) — but every WARN
  should also appear in `parse_meta.warnings` in plain words.

### 6. Deliver

Output JSON skeleton (all keys required):

```json
{
  "schema_version": "0.1-draft",
  "prompt_version": "v0.3",
  "doc_type": "...",
  "source_file": "path/to/file.pdf#p2 if multi-doc file",
  "engine": "<your agent/model identifier>",
  "fields": { "<field>": {"value": null, "confidence": 0, "raw_span": null, "normalized": true} },
  "parse_meta": {"ocr_used": false, "pages": 1, "warnings": []}
}
```

## Hard constraints (violating any one voids the output)

1. **Never guess.** Illegible, blank, redacted, or torn-off → `value: null`,
   `confidence: 0`. An empty field is a correct answer; a fabricated one is poison
   for the downstream comparison module.
2. **Every non-null field carries `raw_span`** (page + verbatim source snippet) —
   the downstream module uses it for explainability and UI display.
3. **You normalize** (dates → `YYYY-MM-DD`, money → float + ISO4217, weight → KG,
   HS code → 10 digits no dots, quantity → float + unit). Downstream receives
   normalized values only.
4. **Transcribe numbers exactly.** Never "fix" a digit; unit conversion happens
   only in the normalization layer, with the original text kept in raw_span.
5. **Extract only, no judgment.** Suspicious marks (alterations, suspicious
   parties) may be described objectively in `warnings`, never concluded upon.
6. **Confidential files** (marked CONFIDENTIAL / BUSINESS PROPRIETARY, or
   containing SWIFT codes, bank accounts): process locally only, never upload to
   any cloud OCR/API, outputs stay on this machine, mark `confidential_source`
   in warnings. See protocol-rules.md §6.

## Failure handling

- Validator keeps failing on a field you cannot see clearly → set it null and
  write why in warnings; do NOT bend the value to pass.
- A page is half invoice / half packing list → extract only the dominant document
  type; note the co-located document in warnings.
- File won't render at all → report the failure honestly with the error; do not
  emit a JSON full of guesses.

## Versioning

Protocol lineage v0.1 → v0.3 (v0.3 tightened 5 ambiguous wordings found by a
3-independent-executor consistency experiment; same-file accuracy 87.5% → 100%).
When modifying this skill, bump the version, log the change in the project's
decision log, and re-run the project's eval harness — accuracy must not regress.
