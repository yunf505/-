"""生成线2 比对引擎的自造测试数据（全部虚构，不含任何真实单证信息）。

用法：
    python gen_fixtures.py            # 生成到 ./票XX/
契约：契约A v1.1（4 个 doc_type）
"""

import json, os, sys

BASE = os.path.dirname(os.path.abspath(__file__))


def fv(value, conf=0.95, page=1, text=None, normalized=True):
    """构造一个 FieldValue。缺失字段请用 null_field()。"""
    return {
        "value": value,
        "confidence": conf,
        "raw_span": {"page": page, "text": text} if text else None,
        "normalized": normalized,
    }


def null_field(note=None):
    """缺失 = value null + confidence 0 + raw_span null（契约铁律）"""
    return {"value": None, "confidence": 0, "raw_span": None, "normalized": True}


def doc(doc_type, source, fields, warnings=None, pages=1):
    return {
        "schema_version": "0.1-draft",
        "prompt_version": "v0.4",
        "doc_type": doc_type,
        "source_file": source,
        "engine": "self-made-fixture",
        "fields": fields,
        "parse_meta": {"ocr_used": False, "pages": pages, "warnings": warnings or []},
    }


# ---------------------------------------------------------------- 报关单 16 字段
def cd(no, consignor, consignor_span, country, pod, podest, mode, deal,
       hs, goods, qty, amount, gw, nw, pkgs, date, warnings=None, bl_hint=None):
    fields = {
        "declaration_no": fv(no, 1.0, text=f"1. Filer Code/Entry No.: {no}"),
        "domestic_consignor": fv(consignor, 0.95, text=consignor_span),
        "overseas_consignee": null_field(),          # 美国件一律 null（官方口径）
        "trading_country": fv(country, 1.0, text=f"14. Exporting Country: {country}"),
        "port_of_dispatch": fv(pod, 0.5, text=f"19. Foreign Port of Lading: {pod}", normalized=False),
        "port_of_destination": fv(podest, 0.5, text=f"20. U.S. Port of Unlading: {podest}", normalized=False),
        "transport_mode": fv(mode, 0.7, text=f"9. Mode of Transport: {'11' if mode == 'sea' else '40'}"),
        "transaction_mode": fv(deal, 1.0, text=f"Incoterm: {deal}"),
        "hs_code": fv(hs, 0.95, text="33.A. HTSUS No.: " + "; ".join(hs)),
        "goods_name": fv(goods, 0.95, text=f"32. Description: {goods}"),
        "quantity": fv(qty, 0.95, text="35. Net Quantity in HTSUS Units"),
        "total_value": fv({"amount": amount, "currency": "USD"}, 0.95,
                          text=f"39. Total Entered Value: ${amount:,.2f}"),
        "gross_weight_kg": fv(gw, 0.95, text=f"34.A. Gross Weight: {gw} KG"),
        "net_weight_kg": fv(nw, 0.95, text=f"Net Weight: {nw} KG"),
        "packages": (fv(pkgs, 0.95, text=f"34.B. Manifest Qty: {pkgs} CTNS")
                     if pkgs is not None else null_field()),
        "declaration_date": fv(date, 1.0, text=f"7. Entry Date: {date}"),
    }
    w = list(warnings or [])
    if bl_hint:
        w.append(f"栏12 B/L or AWB Number: {bl_hint}（跨证主键，契约A 无承载字段，暂记 warnings）")
    return doc("customs_declaration", f"fixtures-synthetic/{no}.json", fields, w)


# ---------------------------------------------------------------- 发票 17 字段
def inv(no, date, seller, buyer, buyer_span, goods, qty, unit, total,
        pkgs, gw, nw, ctnr, pol, pod, origin="CN", warnings=None):
    fields = {
        "invoice_no": fv(no, 1.0, text=f"INVOICE NO. {no}"),
        "invoice_date": fv(date, 1.0, text=f"Invoice Date: {date}"),
        "seller": fv(seller, 1.0, text=f"From: {seller}"),
        "buyer": fv(buyer, 1.0, text=buyer_span),
        "order_no": fv("PO-FAKE-0001", 0.9, text="PO No.: PO-FAKE-0001"),
        "goods_name": fv(goods, 0.95, text=f"Description: {goods}"),
        "quantity": fv(qty, 0.95, text="Q'ty"),
        "unit_price": fv({"amount": unit, "currency": "USD"}, 0.9, text=f"Unit price: US${unit}"),
        "total_value": fv({"amount": total, "currency": "USD"}, 0.95,
                          text=f"Total Amount: US${total:,.2f}"),
        "packages": fv(pkgs, 0.95, text=f"Cartons: {pkgs} CTNS"),
        "gross_weight_kg": fv(gw, 0.95, text=f"G.W.: {gw} KGS"),
        "net_weight_kg": fv(nw, 0.95, text=f"N.W.: {nw} KGS"),
        "marks": fv("N/M", 0.9, text="Marks & Nos: N/M"),
        "container_no": fv(ctnr, 1.0, text=f"Ctnr No.: {ctnr}"),
        "country_of_origin": fv(origin, 0.95, text=f"COUNTRY OF ORIGIN: {origin}"),
        "port_of_loading": fv(pol, 0.95, text=f"Loading Port: {pol}"),
        "port_of_discharge": fv(pod, 0.95, text=f"Discharge Port: {pod}"),
    }
    return doc("commercial_invoice", f"fixtures-synthetic/{no}.json", fields, warnings)


# ---------------------------------------------------------------- 装箱单 16 字段
def pl(no, inv_no, date, seller, buyer, buyer_span, goods, qty, pkgs,
       gw, nw, ctnr, pol, pod, warnings=None):
    fields = {
        "packing_list_no": fv(no, 1.0, text=f"PACKING LIST No.: {no}"),
        "invoice_no": fv(inv_no, 1.0, text=f"Invoice No.: {inv_no}"),
        "packing_date": fv(date, 1.0, text=f"Packing Date: {date}") if date else null_field(),
        "seller": fv(seller, 1.0, text=f"Seller: {seller}"),
        "buyer": fv(buyer, 1.0, text=buyer_span),
        "goods_name": fv(goods, 0.95, text=f"Description: {goods}"),
        "quantity": fv(qty, 0.95, text="QUANTITY"),
        "packages": fv(pkgs, 0.95, text=f"TOTAL: {pkgs} CARTONS"),
        "kind_of_packages": fv("CARTONS", 0.95, text=f"{pkgs} CARTONS"),
        "gross_weight_kg": fv(gw, 0.95, text=f"G.W.: {gw} KGS"),
        "net_weight_kg": fv(nw, 0.95, text=f"N.W.: {nw} KGS"),
        "measurement_cbm": fv(28.6, 0.85, text="MEASUREMENT: 28.6 CBM"),
        "marks": fv("N/M", 0.9, text="Marks & Nos: N/M"),
        "container_no": fv(ctnr, 1.0, text=f"Ctnr No.: {ctnr}"),
        "port_of_loading": fv(pol, 0.95, text=f"Loading Port: {pol}"),
        "port_of_discharge": fv(pod, 0.95, text=f"Discharge Port: {pod}"),
    }
    return doc("packing_list", f"fixtures-synthetic/{no}.json", fields, warnings)


# ---------------------------------------------------------------- 提单 9 字段
def bl(no, shipper, consignee, notify, vessel, voyage, pol, pod, pkgs, gw,
       desc, warnings=None):
    fields = {
        "bl_no": fv(no, 1.0, text=f"B/L NO. {no}"),
        "shipper": fv(shipper, 1.0, text=f"SHIPPER: {shipper}"),
        "consignee": fv(consignee, 1.0, text=f"CONSIGNEE: {consignee}"),
        "notify_party": fv(notify, 0.95, text=f"NOTIFY PARTY: {notify}"),
        "vessel_voyage": fv({"vessel": vessel, "voyage": voyage}, 0.95,
                            text=f"VESSEL / VOYAGE: {vessel} / {voyage}"),
        "port_of_loading": fv(pol, 1.0, text=f"PORT OF LOADING: {pol}"),
        "port_of_discharge": fv(pod, 1.0, text=f"PORT OF DISCHARGE: {pod}"),
        "packages_and_weight": fv({"packages": pkgs, "gross_weight_kg": gw, "unit": "CTN"},
                                  0.95, text=f"{pkgs} CTNS / {gw} KGS"),
        "goods_description": fv(desc, 0.95, text=f"DESCRIPTION OF GOODS: {desc}"),
    }
    w = list(warnings or [])
    if consignee.upper() == "TO ORDER":
        w.append("consignee 为指示抬头 TO ORDER，比对收货人时改用 notify_party 旁证，不得判为缺失或不一致")
    return doc("bill_of_lading", f"fixtures-synthetic/{no}.json", fields, w)


TICKETS = {}

# ============================================================ 票01：不误报（E1 + E6 + 归组）
TICKETS["票01-不误报-容差与OCR噪声"] = [
    cd("SYN2026-000101",
       "华宇文具（上海）进出口有限公司",
       "30. Importer of Record: 华宇文具（上海）进出口有限公司 / 上海市闵行区申长路 1234 号",
       "CN", "57078", "2704", "sea", "FOB",
       ["4820100000"], "A5 笔记本 NOTEBOOK",
       [{"value": 127440.0, "unit": "PCS"}],
       26762.0, 16920.0, 15240.0, 540, "2026-03-18",
       bl_hint="MAEU SZHVJM535"),
    inv("INV-2026-0101", "2026-03-10",
        "PACIFIC GIFTS & STATIONERY (HK) LTD", "华宇文具（上海）进出口有限公司",
        "To: 华宇文具（上海）进出口有限公司 / Add: 上海市闵行区申长路 1234 号",
        "A5 NOTEBOOK", [{"value": 127440.0, "unit": "PCS"}], 0.21,
        26762.4, 540, 16920.0, 15240.0, "SYNU1234567", "SHANGHAI", "LOS ANGELES"),
    bl("MAEU92ZKVJM35",
       "PACIFIC GIFTS & STATIONERY (HK) LTD", "TO ORDER",
       "华宇文具（上海）进出口有限公司", "NORDIC VOYAGER", "0FM3W1",
       "SHANGHAI", "LOS ANGELES", 540, 16920.0,
       "A5 NOTEBOOK 127440 PCS; HS 4820.10; Ctnr SYNU1234567"),
]

# ============================================================ 票02：T1 金额超容差 → 红
TICKETS["票02-金额超容差"] = [
    cd("SYN2026-000202",
       "东岸国际贸易（深圳）有限公司",
       "30. Importer of Record: 东岸国际贸易（深圳）有限公司",
       "CN", "57078", "2704", "sea", "CIF",
       ["8708100000"], "汽车保险杠 BUMPER",
       [{"value": 320.0, "unit": "SET"}],
       84200.0, 16920.0, 15240.0, 320, "2026-04-02"),
    inv("INV-2026-0202", "2026-03-20",
        "NORDIC AUTO PARTS AB", "东岸国际贸易（深圳）有限公司",
        "To: 东岸国际贸易（深圳）有限公司",
        "AUTOMOTIVE BUMPER", [{"value": 320.0, "unit": "SET"}], 262.5,
        89000.0, 320, 16920.0, 15240.0, "SYNU7654321", "GOTHENBURG", "SHENZHEN",
        origin="SE"),
]

# ============================================================ 票03：T2 同址不同名 → 红
TICKETS["票03-主体不符-同址不同名"] = [
    cd("SYN2026-000303",
       "东岸国际贸易（深圳）有限公司",
       "30. Importer of Record: 东岸国际贸易（深圳）有限公司 / Add: 深圳市南山区科技园路88号3栋",
       "CN", "57078", "2704", "sea", "FOB",
       ["8517620000"], "蓝牙音箱 BLUETOOTH SPEAKER",
       [{"value": 2400.0, "unit": "PCS"}],
       52800.0, 9600.0, 8640.0, 240, "2026-04-12"),
    inv("INV-2026-0303", "2026-04-02",
        "PACIFIC GIFTS & STATIONERY (HK) LTD", "深港电子器件有限公司",
        "To: 深港电子器件有限公司 / Add: 深圳市南山区科技园路 88 号 3 栋",
        "BLUETOOTH SPEAKER", [{"value": 2400.0, "unit": "PCS"}], 22.0,
        52800.0, 240, 11000.0, 9900.0, "SYNU3344556", "SHANGHAI", "LOS ANGELES"),
    pl("PL-2026-0303", "INV-2026-0303", "2026-04-03",
       "PACIFIC GIFTS & STATIONERY (HK) LTD", "东岸国际贸易（深圳）有限公司",
       "Buyer: 东岸国际贸易（深圳）有限公司 / Add: 深圳市南山区科技园路88号3栋",
       "BLUETOOTH SPEAKER", [{"value": 2400.0, "unit": "PCS"}],
       240, 9600.0, 8640.0, "SYNU3344556", "SHANGHAI", "LOS ANGELES"),
]

# ============================================================ 票04：T4 跨证 HS 不一致 → 红
TICKETS["票04-HS跨证不一致"] = [
    cd("SYN2026-000404",
       "华宇文具（上海）进出口有限公司",
       "30. Importer of Record: 华宇文具（上海）进出口有限公司",
       "CN", "57078", "2704", "sea", "FOB",
       ["4820100000"], "A5 笔记本 NOTEBOOK",
       [{"value": 60000.0, "unit": "PCS"}],
       12600.0, 7200.0, 6480.0, 200, "2026-05-06"),
    bl("SZPLA2400404",
       "PACIFIC GIFTS & STATIONERY (HK) LTD", "TO ORDER",
       "华宇文具（上海）进出口有限公司", "NORDIC VOYAGER", "0FM4W2",
       "SHANGHAI", "LOS ANGELES", 200, 7200.0,
       "AUTOMOTIVE BUMPER PARTS 200 SETS; HS 8708.10; Ctnr SYNU9988776"),
]

# ============================================================ 票05：T3 件数缺失黄 + T5 日期矛盾红 + T6 缺发票黄
TICKETS["票05-件数缺失-日期矛盾-缺发票"] = [
    cd("SYN2026-000505",
       "东岸国际贸易（深圳）有限公司",
       "30. Importer of Record: 东岸国际贸易（深圳）有限公司",
       "CN", "57078", "2704", "sea", "FOB",
       ["8471300000"], "笔记本电脑 LAPTOP",
       [{"value": 150.0, "unit": "SET"}],
       132000.0, 2400.0, 2160.0, None, "2026-05-18",
       warnings=["34.B Manifest Qty 栏空白，按铁律置 null 且 confidence=0，不得按 0 处理",
                 "栏18 Missing Docs: COMMERCIAL INVOICE（CBP 已登记缺件）"]),
    pl("PL-2026-0505", "INV-2026-0505", "2026-05-25",
       "NORDIC AUTO PARTS AB", "东岸国际贸易（深圳）有限公司",
       "Buyer: 东岸国际贸易（深圳）有限公司",
       "LAPTOP", [{"value": 150.0, "unit": "SET"}],
       320, 2400.0, 2160.0, "SYNU5566778", "GOTHENBURG", "SHENZHEN"),
]

# ============================================================ 票06：T7 代码↔地名 → 不可比
TICKETS["票06-港口码对地名-不可比"] = [
    cd("SYN2026-000606",
       "华宇文具（上海）进出口有限公司",
       "30. Importer of Record: 华宇文具（上海）进出口有限公司",
       "CN", "57078", "2704", "sea", "FOB",
       ["4820100000"], "A5 笔记本 NOTEBOOK",
       [{"value": 90000.0, "unit": "PCS"}],
       18900.0, 10800.0, 9720.0, 300, "2026-06-08"),
    bl("SZPLA2400606",
       "PACIFIC GIFTS & STATIONERY (HK) LTD", "TO ORDER",
       "华宇文具（上海）进出口有限公司", "NORDIC VOYAGER", "0FM6W3",
       "YANTIAN", "LOS ANGELES", 300, 10800.0,
       "A5 NOTEBOOK 90000 PCS; HS 4820.10"),
]

# ============================================================ 票07：P0 混票归组验证
# 两票货（X / Y）的 4 份文档混放在同一目录，验证分票降级链能否正确分开。
# 全部虚构，与现有 fixtures 一样不掺真东西。
#
# 期望：引擎输出 tickets 长度 = 2；两票各自 0 红 0 黄；不出现跨票假差异。
#
# 分票路径：
#   - 发票 X / 发票 Y 有各自 container_no（SYNU7777001 vs SYNU7777002）→ 第 1 档分开
#   - 报关单 X / Y 无 container_no、无 bl_no、无 invoice_no → 走第 3 档宽松版
#     (金额 + 件数 匹配对应发票) → 分别归入 X / Y 组

_cd_x = cd("SYN2026-000707X",
           "华宇文具（上海）进出口有限公司",
           "30. Importer of Record: 华宇文具（上海）进出口有限公司",
           "CN", "57078", "2704", "sea", "FOB",
           ["4820100000"], "A5 笔记本 NOTEBOOK",
           [{"value": 5000.0, "unit": "PCS"}],
           15000.0, 3000.0, 2700.0, 100, "2026-07-10")
_cd_x["_filename"] = "customs_declaration_X.json"

_inv_x = inv("INV-2026-0707X", "2026-07-05",
             "PACIFIC GIFTS & STATIONERY (HK) LTD", "华宇文具（上海）进出口有限公司",
             "To: 华宇文具（上海）进出口有限公司",
             "A5 NOTEBOOK", [{"value": 5000.0, "unit": "PCS"}], 3.0,
             15000.0, 100, 3000.0, 2700.0, "SYNU7777001", "SHANGHAI", "LOS ANGELES")
_inv_x["_filename"] = "commercial_invoice_X.json"

_cd_y = cd("SYN2026-000707Y",
           "东岸国际贸易（深圳）有限公司",
           "30. Importer of Record: 东岸国际贸易（深圳）有限公司",
           "CN", "57078", "2704", "sea", "CIF",
           ["8517620000"], "蓝牙音箱 BLUETOOTH SPEAKER",
           [{"value": 2000.0, "unit": "PCS"}],
           42000.0, 8000.0, 7200.0, 200, "2026-07-12")
_cd_y["_filename"] = "customs_declaration_Y.json"

_inv_y = inv("INV-2026-0707Y", "2026-07-06",
             "NORDIC ELECTRONICS AB", "东岸国际贸易（深圳）有限公司",
             "To: 东岸国际贸易（深圳）有限公司",
             "BLUETOOTH SPEAKER", [{"value": 2000.0, "unit": "PCS"}], 21.0,
             42000.0, 200, 8000.0, 7200.0, "SYNU7777002", "GOTHENBURG", "SHENZHEN",
             origin="SE")
_inv_y["_filename"] = "commercial_invoice_Y.json"

TICKETS["票07-混票归组验证"] = [_cd_x, _inv_x, _cd_y, _inv_y]


def main():
    for name, docs in TICKETS.items():
        d = os.path.join(BASE, name)
        os.makedirs(d, exist_ok=True)
        for doc_json in docs:
            # 支持自定义文件名（票07 混放场景需要）；否则用 doc_type.json
            fn = doc_json.get("_filename") or (doc_json["doc_type"] + ".json")
            payload = {k: v for k, v in doc_json.items() if not k.startswith("_")}
            with open(os.path.join(d, fn), "w", encoding="utf-8") as fp:
                json.dump(payload, fp, ensure_ascii=False, indent=2)
        print(f"[ok] {name}  ({len(docs)} 份)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
