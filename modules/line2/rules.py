"""
美国合规规则库 · 10 条
========================

对应 `02-规则库/美国合规规则-10条-可执行.md`。所有触发条件均为纯 if-then，
LLM 不参与判定（分工表硬约束①）。输出进 `alerts[]`，不进 `diffs[]`。

字段依赖（规则库开头「通用约定」原文）：
    hs_all   = set(报关单.fields.hs_code.value)          # 已是去点号 10 位字符串数组
    text_all = " ".join([报关单.goods_name, 提单.goods_description]).lower()
"""
from __future__ import annotations

from typing import Any, Iterable


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def hs_prefix(hs_all: set, p: str) -> bool:
    return any(h.startswith(p) for h in hs_all)


def kw(text_all: str, *words: str) -> bool:
    return any(w in text_all for w in words)


def _alert(rule_id: str, level: str, message: str, basis: str, url: str,
           trigger: dict | None = None) -> dict:
    """按契约B alerts[] 结构组装单条 alert。level 只用 red/yellow/orange。"""
    return {
        "category": "compliance_alert",
        "rule_id": rule_id,
        "level": level,
        "message": message,
        "basis": basis,
        "url": url,
        "trigger": trigger or {},
    }


# ---------------------------------------------------------------------------
# 10 条规则
# ---------------------------------------------------------------------------

def r001_hs_form(hs_all: set) -> list:
    """R-001 HS 编码形式校验（orange）。9903 附加税目不算错归，跳过。"""
    out = []
    for h in sorted(hs_all):
        if h.startswith("9903"):
            continue
        if len(h) != 10 or not h.isdigit():
            out.append(_alert(
                "R-001", "orange",
                f"HS 编码应为去点号后的 10 位数字，当前为 {h}，"
                f"请核对是否缺位或含第 99 章附加税目",
                "schema/contract-A.schema.json normalization.hs_code + 给B的说明.md 第四节",
                "",
                {"hs_code": h},
            ))
    return out


def r002_gri(hs_all: set) -> list:
    """R-002 归类须依 GRI 与类/章注（orange，无条件命中）。"""
    return [_alert(
        "R-002", "orange",
        "归类须依《归类总规则》(GRI) 及相应类注、章注确定，"
        "建议复核本票所涉章节是否存在排除条款",
        "C-USITC-GRI-归类总规则.pdf",
        "https://hts.usitc.gov/reststop/file?release=currentRelease"
        "&filename=General%20Rules%20of%20Interpretation",
        {"hs_code": sorted(hs_all), "matched_scope": "全部 HS"},
    )]


def r003_motor_vehicle_parts(hs_all: set) -> list:
    """R-003 HS 8708 机动车辆零部件（yellow）。"""
    if not hs_prefix(hs_all, "8708"):
        return []
    return [_alert(
        "R-003", "yellow",
        "该批为机动车辆零部件，须按适用 FMVSS 制造并由原厂自我认证方可进口；"
        "不合规者禁止进口。请向供货方索取制造商符合性声明",
        "C-NHTSA-零部件FMVSS符合性FAQ.pdf",
        "https://www.nhtsa.gov/importing-vehicle/importation-and-certification-faqs-2",
        {"hs_prefix": "8708"},
    )]


def r004_whole_vehicle(hs_all: set) -> list:
    """R-004 整车 HS 8702/8703/8704/8711（red）。"""
    for p in ("8702", "8703", "8704", "8711"):
        if hs_prefix(hs_all, p):
            return [_alert(
                "R-004", "red",
                "整车进口须提交 HS-7 Declaration Form；"
                "非合格车辆须经 NHTSA 注册进口商(RI)办理，否则无法清关",
                "C-NHTSA-车辆进口与HS-7申报.pdf",
                "https://www.nhtsa.gov/importing-vehicle",
                {"hs_prefix": p},
            )]
    return []


FCC_KEYWORDS = (
    "wireless", "bluetooth", "wifi", "wi-fi", "rf ", "radio",
    "transmitter", "antenna", "zigbee", "lora",
    "gps module", "cellular module",
)


def r005_fcc_rf(text_all: str) -> list:
    """R-005 射频设备 FCC 设备授权（yellow，基于品名关键词而非 HS）。"""
    matched = [w.strip() for w in FCC_KEYWORDS if w in text_all]
    if not matched:
        return []
    return [_alert(
        "R-005", "yellow",
        "货物疑似含射频功能，须在进口前依 47 CFR Part 2 取得 FCC 设备授权"
        "(Certification 或 SDoC)，并满足 §2.1204 十一项进口条件之一；"
        "最终收货人须留存授权文件备查",
        "C-FCC-设备授权EquipmentAuthorization.pdf + C-FCC-受管设备进口条件.pdf",
        "https://www.fcc.gov/oet/ea/importation",
        {"matched_keywords": matched},
    )]


def r006_fcc_no_hs_mapping(r005_hit: bool) -> list:
    """R-006 反规则：FCC 与 HS 无映射关系（orange，随 R-005 一同出现）。"""
    if not r005_hit:
        return []
    return [_alert(
        "R-006", "orange",
        "FCC 要求与 HS 编码之间不存在映射关系，本系统的 FCC 判定基于商品功能描述"
        "而非 HS 段；另：FCC Form 740 已于 2017-11-02 废止，单证清单不应再列",
        "C-FCC-受管设备进口条件.pdf",
        "https://www.fcc.gov/oet/ea/importation",
        {"note": "反规则，随 R-005 出现"},
    )]


def r007_fda_prior_notice(hs_all: set) -> list:
    """R-007 FDA 食品 Prior Notice（HS 第 16–23 章，red）。"""
    food_chapters = {f"{i:02d}" for i in range(16, 24)}
    for h in sorted(hs_all):
        if h[:2] in food_chapters:
            return [_alert(
                "R-007", "red",
                "该批属食品/动物饲料，进口或申报进口前须通过 PNSI 系统提交 FDA "
                "Prior Notice，并申报该食品曾被拒绝入境的国别；漏报视为申报不完整",
                "C-FDA-食品进口提前申报PriorNotice.pdf",
                "https://www.fda.gov/industry/fda-import-process/"
                "prior-notice-imported-foods",
                {"hs_code": h, "chapter": h[:2]},
            )]
    return []


def r008_medical_device(hs_all: set) -> list:
    """R-008 医疗器械注册与列名（HS 9018/9001/3005，yellow）。"""
    for p in ("9018", "9001", "3005"):
        if hs_prefix(hs_all, p):
            return [_alert(
                "R-008", "yellow",
                "该批属医疗器械，境外企业须完成 FDA 年度注册(21 CFR Part 807)"
                "与器械列名；属上市前授权范围者须提供 510(k) / De Novo / PMA / "
                "PDP / HDE 编号",
                "C-FDA-医疗器械注册与列名.pdf",
                "https://www.fda.gov/medical-devices/how-study-and-market-your-device/"
                "device-registration-and-listing",
                {"hs_prefix": p},
            )]
    return []


def r009_radiation_electronics(hs_all: set) -> list:
    """R-009 辐射类电子产品双重监管（HS 9022/8516/8528/8541/9013，yellow）。"""
    for p in ("9022", "8516", "8528", "8541", "9013"):
        if hs_prefix(hs_all, p):
            return [_alert(
                "R-009", "yellow",
                "该批属辐射类电子产品，同时受 FDA 辐射性能标准(CDRH) 与 FCC "
                "设备授权双重监管，需分别确认两套合规文件",
                "C-FDA-受监管产品进口总览.pdf",
                "https://www.fda.gov/industry/import-program/"
                "importing-fda-regulated-products",
                {"hs_prefix": p},
            )]
    return []


def r010_beef_quota(hs_all: set) -> list:
    """R-010 牛肉关税配额（HS 0201/0202，yellow）。"""
    for p in ("0201", "0202"):
        if hs_prefix(hs_all, p):
            return [_alert(
                "R-010", "yellow",
                "该批属牛肉及其食用杂碎，受年度关税配额限量约束，"
                "须确认配额内/外税率与相应签证要求",
                "C-USITC-Chapter02-肉及食用杂碎.pdf",
                "https://hts.usitc.gov/reststop/file?release=currentRelease"
                "&filename=Chapter%202",
                {"hs_prefix": p},
            )]
    return []


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def _collect_hs_all(customs_dec: dict | None) -> set:
    if not customs_dec:
        return set()
    hs_field = customs_dec.get("fields", {}).get("hs_code") or {}
    v = hs_field.get("value")
    if isinstance(v, list):
        return {h for h in v if isinstance(h, str) and h}
    if isinstance(v, str) and v:
        return {v}
    return set()


def _collect_text_all(customs_dec: dict | None, other_docs: Iterable[dict]) -> str:
    texts: list = []
    if customs_dec:
        gn = (customs_dec.get("fields", {}).get("goods_name") or {}).get("value")
        if isinstance(gn, str):
            texts.append(gn)
    for d in other_docs:
        if d.get("doc_type") == "bill_of_lading":
            gd = (d.get("fields", {}).get("goods_description") or {}).get("value")
            if isinstance(gd, str):
                texts.append(gd)
    return " ".join(texts).lower()


def evaluate(customs_dec: dict | None, all_docs: list) -> list:
    """跑 10 条规则，返回 alerts[]（已带 id）。"""
    hs_all = _collect_hs_all(customs_dec)
    text_all = _collect_text_all(customs_dec, all_docs)

    alerts: list = []
    alerts += r001_hs_form(hs_all)
    alerts += r002_gri(hs_all)
    alerts += r003_motor_vehicle_parts(hs_all)
    alerts += r004_whole_vehicle(hs_all)
    r005 = r005_fcc_rf(text_all)
    alerts += r005
    alerts += r006_fcc_no_hs_mapping(bool(r005))
    alerts += r007_fda_prior_notice(hs_all)
    alerts += r008_medical_device(hs_all)
    alerts += r009_radiation_electronics(hs_all)
    alerts += r010_beef_quota(hs_all)

    for i, a in enumerate(alerts, 1):
        a["id"] = f"A{i:03d}"
    return alerts
