# Protocol Rules (v0.3)

These rules were each added because a real failure mode was observed in
live-fire tests or in a 3-independent-executor consistency experiment. The
"why" matters: knowing it helps you apply the rule in unseen situations.

## §1 ID cross-verification (declaration_no / bl_no / container nos.)

Entry and BL numbers usually appear in several places: the 7501 header, the
3461 header, fax headers, packing list/invoice references. After reading one:
find a second occurrence and compare.
- Readings agree → normal confidence (0.95 / 0.70).
- Readings disagree → keep the **highest-resolution rendering's** reading,
  drop confidence to 0.5, and list **all candidates with their sources** in
  warnings for human review. Never silently pick a favorite.

*Why: in one test, a single entry number came back as four different strings
across four executions — the letters differed exactly at B/D/Q and 8/B
positions. The only stable defense is cross-verification.*

## §2 Date bar selection (declaration_date)

- CBP 7501: **Summary Date first**; if blank, Entry Date; the raw_span must
  name which bar was used. Signature date is NOT taken (note a warning only if
  it differs substantially).
- Bill of lading: Date of Issue (no contract field yet — record in warnings).
- China customs form: 申报日期 bar; if absent, import date + note.

*Why: three different dates coexist on a 7501; without a fixed order,
different agents pick different bars and downstream comparison breaks.*

## §3 Gross weight aggregation (gross_weight_kg)

- Whole-shipment gross weight bar present → take it directly.
- Only per-line values → sum them, mark raw_span as "sum of line values (…)",
  and add a warning.
- Other documents' totals (e.g. packing list) may **corroborate** but never
  **replace** the document's own figure; disagreement → warning.

## §4 Lookalike characters

B/8, D/Q, O/0, I/1/l, S/5, Z/2 are the classic scan confusions. Whenever an
ID/code/amount contains one and the glyph is doubtful: re-render that region
at higher dpi (400–600, use render_pdf.py --clip) before deciding. Still
undecided → treat as §1 disagreement (best reading + confidence 0.5 + warning
listing candidates).

## §5 Multi-document files / co-located documents

- One PDF containing several documents or shipments → **one record per
  shipment**; `source_file` gets a `#p<page>` suffix; sibling documents of the
  same shipment are noted in warnings ("extracted separately").
- One page co-locating two documents (e.g. invoice with embedded packing
  columns) → extract only the page's **dominant** document type; never mix
  fields across documents on the same page; each document's own bars win.

## §6 Confidential files (hard red line)

Files marked CONFIDENTIAL / BUSINESS PROPRIETARY, or containing SWIFT codes,
bank accounts, or personal contact details:
1. Process **locally only** — never upload their content to any cloud
   OCR/API/service.
2. Output JSON stays on this machine (never into git or any platform).
3. Transcribing sensitive strings into the local JSON is allowed (local
   intermediate), but ANY demo/report/screenshot/upload requires redaction
   first (owner: the data-quality teammate).
4. When batch-processing alongside normal files, add `"confidential_source"`
   to parse_meta.warnings.

## §7 Reading pipeline requirements

- PDFs: render to PNG at ≥150 dpi (200 recommended) and read visually. The
  PDF text layer of court exhibits usually contains only the stamp header —
  do not trust it for fields.
- Thumbnails ≤50 dpi: page-level triage only, never field extraction.
- Empty-password-encrypted PDFs: readers may falsely report "password
  protected" — rendering sidesteps this entirely.
