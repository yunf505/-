# Contract-A Field Specification (v0.1-draft)

Authoritative field tables and normalization rules. Every field below MUST appear
in your output JSON (missing → `value: null`, `confidence: 0`).
Each field value object: `{"value", "confidence", "raw_span": {"page", "text"}, "normalized"}`.

## 1. customs_declaration (16 fields)

| field | means (source bar) | required | normalization |
|---|---|---|---|
| declaration_no | customs/entry number (e.g. CBP 7501 box 1 Filer Code/Entry No.; China 海关编号/预录入编号) | yes | strip spaces; cross-verify across pages (see protocol-rules §1) |
| domestic_consignor | domestic consignor/importer of record — **name line only, no street address/zip** | yes | collapse extra whitespace |
| overseas_consignee | overseas consignor/shipper — name line only | optional | same |
| trading_country | country of trade/origin | yes | ISO 2-letter + Chinese name, e.g. `"US / 美国"`, `"CN / 中国"` |
| port_of_dispatch | port of lading/dispatch | optional | keep verbatim; if it is a **CBP port code** (e.g. 57078), copy the code as-is and set `normalized: false` |
| port_of_destination | port of unlading/destination | optional | same |
| transport_mode | mode of transport | yes | enum: `sea` / `air` / `rail` / `road` / `multimodal` (CBP code 11 = sea) |
| transaction_mode | terms of sale (Incoterm) | yes | enum: `FOB` / `CIF` / `CFR` / `EXW` / `其他`; absent on US 7501 → null |
| hs_code | HTS/HS commodity code | yes | strip dots/spaces, 10 digits; **multiple line items → array of all codes**; sub-10-digit → keep as-is + warning. Chapter 99 special-duty codes (e.g. 9903.88.09) are NOT commodity classifications — exclude from the array |
| goods_name | description of merchandise | yes | **string, never an array**; multiple lines joined with "; " in the same order as the hs_code array |
| quantity | quantity and unit | yes | array of `{"value": float, "unit": str}` — legal first/second quantities as separate entries, verbatim units (e.g. CARTO, NO, L) |
| total_value | total entered value + currency | yes | `{"amount": float, "currency": "ISO4217"}` (strip thousands separators) |
| gross_weight_kg | gross weight | yes | float, KG (LB × 0.45359237, MT × 1000); aggregation rule in protocol-rules §3 |
| net_weight_kg | net weight | optional | float, KG; if the form's legal unit of measure is not weight (L/CARTO/NO) → **null** (never convert-guess) |
| packages | package count | yes | int |
| declaration_date | declaration/summary date | yes | YYYY-MM-DD; bar-selection rule in protocol-rules §2 |

## 2. bill_of_lading (9 + 2 fields)

| field | means | required | normalization |
|---|---|---|---|
| bl_no | bill of lading number | yes | strip whitespace; cross-verify (protocol-rules §1) |
| shipper | shipper — name line only | yes | verbatim case |
| consignee | consignee — name line only | yes | "TO ORDER" style clauses copied verbatim |
| notify_party | notify party — name line only | optional | same |
| vessel_voyage | vessel + voyage | optional | split into `{"vessel", "voyage"}`; if the form prints one string, look for labeled Vessel/Voyage fields on continuation pages first — if none, put the last voyage-looking token(s) in voyage and the rest in vessel, confidence 0.5 + warning listing the alternative split |
| port_of_loading | port of loading | yes | verbatim |
| port_of_discharge | port of discharge | yes | verbatim |
| packages_and_weight | packages & gross weight | yes | `{"packages": int, "gross_weight_kg": float, "unit": str}` — `unit` is the **package unit** (e.g. "CARTONS"), not the weight unit |
| goods_description | description of goods | yes | **description cell text, transcribed verbatim and fully** — an HTS reference inside the cell stays inline (hs_code is additionally extracted, not cut out); exclude row-mate packages/FCL/container-type text (whether columnar or typed over column borders, as typewriter forms often do) |
| hs_code | HTS if stated in goods description | optional | **always an array of strings** (same as customs_declaration), even when only one code is present |
| marks | marks & numbers: container no. + seal no. + the marks&nos column, verbatim and **complete** (transcribe the whole column, don't summarize) | optional | do not merge description-column text into marks |

## 3. Normalization quick table

| type | rule | on failure |
|---|---|---|
| date | `YYYY-MM-DD` | keep raw string, `normalized: false`, warning |
| money | float + ISO4217 currency | same |
| weight | KG (LB × 0.45359237, MT × 1000) | same |
| quantity | float value + unit string, never mixed | same |
| HS code | 10 digits, no dots/spaces | keep as-is + warning |
| names | collapse whitespace, keep case, **name line only** | tolerance is downstream's business |

## 4. Confidence tiers

| situation | confidence |
|---|---|
| clean print / text layer, direct read | 0.95 |
| clear scan, light noise | 0.70 |
| cross-field inference / complex layout / blurred / ID readings disagree | 0.50 |
| missing | 0 |
