"""门户契约版本化：v1 文本额度 / v2 数值+单位双列、快照绑定、切换回退、隔离原因码。

覆盖需求：
- 门户改版后额度拆两列，新采集按 v2 字段契约校验；
- 改版前批次按 v1 原契约恢复，不把旧额度换算成新金额；
- 无单位 / 单位冲突（含数值列内嵌单位冲突）的新记录进隔离；
- 契约按门户管理并绑定采集快照；切换预检失败保留旧版。
"""
from __future__ import annotations

from decimal import Decimal

from app import contracts
from app.modules.m2_parse import coerce, service


V1 = contracts.CONTRACT_V1
V2 = contracts.CONTRACT_V2
BASE_V2 = {"公文号": "澄政办〔2026〕01号", "公告序号": "1", "公告类型": "首次发布",
           "标题": "新区专项奖励", "发布时间": "2026-11-03"}


# ---------- v2 双列额度归一 ----------

def test_v2_split_amount_normalizes_value_and_unit():
    amount, unit, error, meta = coerce.norm_amount_split("320", "万元")
    assert error is None
    assert amount == Decimal("320.00") and unit == "万元"
    assert meta["factor"] == "1"


def test_v2_split_amount_yuan_uses_unit_table_not_guess():
    amount, unit, error, _ = coerce.norm_amount_split("80000", "元")
    assert error is None and unit == "元"
    assert amount == Decimal("8.00")          # 80000 元 = 8 万元，按单位表折算


def test_v2_split_amount_keeps_thousands_and_fullwidth():
    amount, _u, error, _ = coerce.norm_amount_split("１，５００", "万元")
    assert error is None and amount == Decimal("1500.00")


def test_v2_missing_unit_is_quarantined_not_defaulted():
    """无单位的新记录进隔离，禁止用默认单位「万元」顶替。"""
    announcement, errors = service.parse_one(
        {**BASE_V2, "额度数值": "260", "额度单位": ""},
        portal="portal_d", contract_version=V2)
    assert announcement is None
    assert [e["code"] for e in errors] == ["Q_AMOUNT_UNIT_MISSING"]


def test_v2_sentinel_unit_is_quarantined():
    _a, errors = service.parse_one(
        {**BASE_V2, "额度数值": "410", "额度单位": "-"},
        portal="portal_d", contract_version=V2)
    assert any(e["code"] == "Q_AMOUNT_UNIT_MISSING" for e in errors)


def test_v2_unknown_unit_value_is_conflict():
    _a, errors = service.parse_one(
        {**BASE_V2, "额度数值": "600", "额度单位": "美元"},
        portal="portal_d", contract_version=V2)
    assert any(e["code"] == "Q_AMOUNT_UNIT_CONFLICT" for e in errors)


def test_v2_embedded_unit_conflict_is_quarantined():
    """数值列内嵌单位与单位列冲突：不猜以哪个为准，整行隔离。"""
    _a, errors = service.parse_one(
        {**BASE_V2, "额度数值": "120万元", "额度单位": "元"},
        portal="portal_d", contract_version=V2)
    codes = [e["code"] for e in errors]
    assert "Q_AMOUNT_UNIT_CONFLICT" in codes


def test_v2_embedded_unit_matching_unit_column_is_accepted():
    announcement, errors = service.parse_one(
        {**BASE_V2, "额度数值": "120万元", "额度单位": "万元"},
        portal="portal_d", contract_version=V2)
    assert errors == [] and announcement is not None
    assert announcement["amount_wan"] == Decimal("120.00")


def test_v2_both_columns_sentinel_is_unregistered_three_state():
    announcement, errors = service.parse_one(
        {**BASE_V2, "额度数值": "-", "额度单位": "-"},
        portal="portal_d", contract_version=V2)
    assert errors == []
    assert announcement["amount_wan"] is None       # 哨兵三态，不是 0
    assert announcement["amount_unit"] == ""


def test_v2_bad_number_still_gives_bad_amount():
    _a, errors = service.parse_one(
        {**BASE_V2, "额度数值": "约一千万", "额度单位": "万元"},
        portal="portal_d", contract_version=V2)
    assert any(e["code"] == "Q_BAD_AMOUNT" for e in errors)


def test_v2_announcement_carries_contract_and_basis():
    announcement, _ = service.parse_one(
        {**BASE_V2, "额度数值": "320", "额度单位": "万元"},
        portal="portal_d", contract_version=V2)
    assert announcement["contract_version"] == V2
    assert announcement["amount_mode"] == "split"
    assert announcement["raw_amount_text"] == "320"
    assert "v2 双列" in announcement["amount_basis"]["basis"]


# ---------- v1 旧契约不被改写 ----------

def test_v1_text_amount_path_unchanged():
    """旧契约：带单位文本仍走文本折算，原文保留，模式标记为 text。"""
    announcement, errors = service.parse_one(
        {**BASE_V2, "资助额度": "1,200万元"},
        portal="portal_a", contract_version=V1)
    assert errors == []
    assert announcement["amount_wan"] == Decimal("1200.00")
    assert announcement["amount_mode"] == "text"
    assert announcement["raw_amount_text"] == "1,200万元"
    assert "v1 文本契约" in announcement["amount_basis"]["basis"]


def test_v1_yuan_text_uses_original_conversion():
    """旧额度按原契约单位表换算，不套用 v2 双列规则。"""
    announcement, _ = service.parse_one(
        {**BASE_V2, "资助额度": "12,000,000元"},
        portal="portal_b", contract_version=V1)
    assert announcement["amount_wan"] == Decimal("1200.0000")


def test_two_generations_keep_distinct_basis_for_same_number():
    v1_row, _ = service.parse_one(
        {**BASE_V2, "资助额度": "320万元"}, portal="portal_a", contract_version=V1)
    v2_row, _ = service.parse_one(
        {**BASE_V2, "额度数值": "320", "额度单位": "万元"},
        portal="portal_d", contract_version=V2)
    assert v1_row["amount_wan"] == v2_row["amount_wan"] == Decimal("320.00")
    assert v1_row["amount_mode"] == "text" and v2_row["amount_mode"] == "split"
    assert v1_row["amount_basis"]["basis"] != v2_row["amount_basis"]["basis"]


# ---------- 门户登记、快照与切换回退 ----------

def test_portal_registry_defaults_and_v2_portal():
    assert contracts.active_version("portal_a") == V1
    assert contracts.active_version("portal_b") == V1
    assert contracts.active_version("portal_c") == V1
    assert contracts.active_version("portal_d") == V2


def test_snapshot_is_frozen_and_digest_stable():
    snap = contracts.portal_snapshot("portal_d")
    assert snap["contract_version"] == V2
    assert snap["amount_mode"] == "split"
    assert len(snap["digest"]) == 64
    again = contracts.portal_snapshot("portal_d")
    assert again["digest"] == snap["digest"]


def test_switch_preflight_failure_keeps_old_contract():
    before = contracts.active_version("portal_a")
    result = contracts.switch_with_preflight(
        "portal_a", V2, sample_headers=["文号", "标题", "资助额度"])  # 缺两列
    assert result["ok"] is False
    assert result["code"] == "Q_CONTRACT_PREFLIGHT_FAILED"
    assert contracts.active_version("portal_a") == before       # 保留旧版
    assert result["missing_amount"] == ["额度数值", "额度单位"]


def test_switch_preflight_success_activates_v2():
    previous = contracts.active_version("portal_c")
    result = contracts.switch_with_preflight(
        "portal_c", V2,
        sample_headers=["公文号", "公告序号", "公告类型", "标题", "发布时间",
                        "额度数值", "额度单位"])
    try:
        assert result["ok"] is True and result["previous"] == previous
        assert contracts.active_version("portal_c") == V2
    finally:
        contracts.set_portal_version("portal_c", previous)       # 还原全局登记


def test_switch_unknown_version_rejected():
    result = contracts.switch_with_preflight("portal_a", "contract/9.9")
    assert result["ok"] is False and result["code"] == "Q_CONTRACT_UNKNOWN_VERSION"
    assert contracts.active_version("portal_a") == V1


def test_validate_headers_detects_split_columns():
    ok = contracts.validate_headers(V2, ["公文号", "公告序号", "公告类型", "标题",
                                         "发布时间", "额度数值", "额度单位"])
    assert ok["ok"] is True and ok["missing_amount"] == []
    bad = contracts.validate_headers(V2, ["公文号", "资助额度"])
    assert bad["ok"] is False and "额度单位" in bad["missing_amount"]


def test_contract_catalog_exposes_both_generations():
    catalog = contracts.catalog()
    assert set(catalog["supported_versions"]) == {V1, V2}
    v2_cols = {c["name"] for c in catalog["contracts"][V2]["columns"]}
    assert {"amount_value", "amount_unit"} <= v2_cols
    assert "amount" not in v2_cols
    v1_cols = {c["name"] for c in catalog["contracts"][V1]["columns"]}
    assert "amount" in v1_cols
