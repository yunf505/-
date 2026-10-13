# few-shot 配对（自造脱敏样例 · 规程 v0.3）

> 用途：给执行 agent 看的"输入→输出"示范。**全部为自造数据**，与任何真实企业无关。
> 配对形式：输入描述（真实输入是渲染后的 PNG 图，此处用文字描述版面）→ 契约A 输出 JSON。

## 样例 1：报关单（customs_declaration）

**输入**（一张美国 CBP 7501 风格的报关单扫描图，版面要点）：
- Box 1 Filer Code/Entry No.: `XQ7-1234567-8`（表头与页眉两处均为此读数）
- Box 3 Summary Date: `07/09/25`；Box 7 Entry Date: `06/20/25`
- Box 10/14 Country: `CN`
- Box 9 Mode of Transport: `11`；承运人栏印有船名
- Box 19 Foreign Port of Lading: `57078`（CBP 港口代码）；Box 20 Port of Unlading: `4601`
- Box 25/26 Importer of Record: `自造测试进出口有限公司, 1 DEMO STREET, SHANGHAI`
- 行001：HTS `8471.30.0000`，货名 `便携式计算机`，数量 `100台`，申报价值 `USD 50,000.00`，毛重栏整票值 `350.5`
- 全表无成交方式（Incoterm）栏；净重栏印的是法定计量单位 `120 L`（非重量）

**输出**（节选，完整 16 字段见下）：

```json
{
  "schema_version": "0.1-draft",
  "prompt_version": "v0.3",
  "doc_type": "customs_declaration",
  "source_file": "demo/sample-customs-declaration.pdf#p2",
  "engine": "<执行 agent 自报>",
  "fields": {
    "declaration_no": {"value": "XQ7-1234567-8", "confidence": 0.95, "raw_span": {"page": 2, "text": "1. Filer Code/Entry No.: XQ7-1234567-8（表头与页眉互证一致）"}, "normalized": true},
    "domestic_consignor": {"value": "自造测试进出口有限公司", "confidence": 0.7, "raw_span": {"page": 2, "text": "26. Importer of Record: 自造测试进出口有限公司（仅取名称行，不含地址）"}, "normalized": true},
    "overseas_consignee": {"value": null, "confidence": 0, "raw_span": null, "normalized": true},
    "trading_country": {"value": "CN / 中国", "confidence": 0.95, "raw_span": {"page": 2, "text": "10. Country of Origin: CN"}, "normalized": true},
    "port_of_dispatch": {"value": "57078", "confidence": 0.7, "raw_span": {"page": 2, "text": "19. Foreign Port of Lading: 57078"}, "normalized": false},
    "port_of_destination": {"value": "4601", "confidence": 0.7, "raw_span": {"page": 2, "text": "20. U.S. Port of Unlading: 4601"}, "normalized": false},
    "transport_mode": {"value": "sea", "confidence": 0.7, "raw_span": {"page": 2, "text": "9. Mode of Transport: 11（CBP代码11=水路，船名佐证）"}, "normalized": true},
    "transaction_mode": {"value": null, "confidence": 0, "raw_span": null, "normalized": true},
    "hs_code": {"value": ["8471300000"], "confidence": 0.95, "raw_span": {"page": 2, "text": "HTSUS No.: 8471.30.0000"}, "normalized": true},
    "goods_name": {"value": "便携式计算机", "confidence": 0.7, "raw_span": {"page": 2, "text": "28. Description: 便携式计算机"}, "normalized": true},
    "quantity": {"value": [{"value": 100.0, "unit": "台"}], "confidence": 0.7, "raw_span": {"page": 2, "text": "31. Net Quantity: 100台"}, "normalized": true},
    "total_value": {"value": {"amount": 50000.0, "currency": "USD"}, "confidence": 0.95, "raw_span": {"page": 2, "text": "35. Total Entered Value USD 50,000.00"}, "normalized": true},
    "gross_weight_kg": {"value": 350.5, "confidence": 0.95, "raw_span": {"page": 2, "text": "30. Grossweight: 350.5 KG（整票栏直取）"}, "normalized": true},
    "net_weight_kg": {"value": null, "confidence": 0, "raw_span": null, "normalized": true},
    "packages": {"value": 20, "confidence": 0.95, "raw_span": {"page": 2, "text": "Manifest Quantity: 20 CTNS"}, "normalized": true},
    "declaration_date": {"value": "2025-07-09", "confidence": 0.95, "raw_span": {"page": 2, "text": "3. Summary Date: 07/09/25（§5.9 优先取 Summary Date）"}, "normalized": true}
  },
  "parse_meta": {
    "ocr_used": false,
    "pages": 1,
    "warnings": [
      "net_weight_kg 置null原因：净重栏法定计量单位为 L 非重量，禁止换算猜测",
      "port_of_dispatch/port_of_destination 为 CBP 港口代码非地名，normalized=false 保留原码"
    ]
  }
}
```

**教学点**：null 的三种正确姿势（栏位不存在→transaction_mode；单位不可比→net_weight_kg；表面未印→overseas_consignee）；代码字段的 normalized=false；日期取 Summary Date 并在 raw_span 注明。

## 样例 2：提单（bill_of_lading）

**输入**（一张港口至港口提单扫描图，版面要点）：
- B/L No.: `DEMO000000001`（正页与续页页眉两处一致）
- Shipper: `DEMO TRADING CO., LTD`（仅名称行）
- Consignee: `TO ORDER`；Notify: `DEMO LOGISTICS INC`
- 正页 Vessel/Voy. 栏印一串 `DEMO VESSEL 2 005W`；**续页页眉分栏标注 Vessel: `DEMO VESSEL 2` / Voyage: `005W`**
- Port of Loading `SHANGHAI, CHINA` / Port of Discharge `LOS ANGELES, CA`
- 表体行：`500 CARTONS / DEMO GOODS (HTS #9403.60.0000) / 1234.567KGS`
- Date of Issue: `15 JUN 2025`

**输出**（关键字段节选）：

```json
{
  "prompt_version": "v0.3",
  "doc_type": "bill_of_lading",
  "source_file": "demo/sample-bill-of-lading.pdf#p2",
  "engine": "<执行 agent 自报>",
  "fields": {
    "bl_no": {"value": "DEMO000000001", "confidence": 0.95, "raw_span": {"page": 2, "text": "B/L No.: DEMO000000001（正页与续页互证一致）"}, "normalized": true},
    "consignee": {"value": "TO ORDER", "confidence": 0.95, "raw_span": {"page": 2, "text": "2. Consignee: TO ORDER"}, "normalized": true},
    "vessel_voyage": {"value": {"vessel": "DEMO VESSEL 2", "voyage": "005W"}, "confidence": 0.7, "raw_span": {"page": 3, "text": "续页表头标注 Vessel: DEMO VESSEL 2 / Voyage: 005W（正页为一整串，按续页分栏标注拆分）"}, "normalized": true},
    "packages_and_weight": {"value": {"packages": 500, "gross_weight_kg": 1234.567, "unit": "CARTONS"}, "confidence": 0.95, "raw_span": {"page": 2, "text": "500 CARTONS ... 1234.567KGS"}, "normalized": true},
    "goods_description": {"value": "DEMO GOODS (HTS #9403.60.0000)", "confidence": 0.7, "raw_span": {"page": 2, "text": "Description of Goods cell: DEMO GOODS (HTS #9403.60.0000)"}, "normalized": true},
    "hs_code": {"value": ["9403600000"], "confidence": 0.7, "raw_span": {"page": 2, "text": "(HTS #9403.60.0000)"}, "normalized": true}
  },
  "parse_meta": {"ocr_used": false, "pages": 2, "warnings": ["Date of Issue 2025-06-15 契约提单侧暂无字段，记于warnings"]}
}
```

**教学点**：vessel/voyage 拆分优先找续页分栏标注；goods_description 逐字保留（HTS 内联不裁剪，另抽 hs_code 恒为数组）；`unit`=包装单位；TO ORDER 照抄；签发日期暂记 warnings。

---
> 真实件的图文 few-shot（脱敏截图+对应 JSON）列入 v0.4，入库前必须完成企业名/税号/货值替换。
