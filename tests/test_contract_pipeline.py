"""v2 门户端到端：快照绑定批次、原始行断点续跑、隔离、导出复现、契约切换不改历史。"""
from __future__ import annotations

import csv
import io
from decimal import Decimal

from app import contracts
from app.modules.m5_batches import resume as resume_mod
from app.modules.m5_batches import service as batch_service, state
from app.modules.m6_export import service as export_service
from app.contracts.snapshot import snapshots_digest


def _rows(body: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))


def test_portal_d_crawl_uses_v2_snapshot_and_quarantines_bad_rows(settings, store):
    """改版门户按 contract/2.0 采集；无单位/单位冲突 4 行进隔离，其余正常入链。"""
    result = batch_service.run(settings=settings, store=store, source="portal_d")
    assert result["status"] == "done"
    snap = result["snapshots"]
    assert len(snap) == 1 and snap[0]["portal"] == "portal_d"
    assert snap[0]["contract_version"] == contracts.CONTRACT_V2
    codes = sorted({q["code"] for q in result["quarantine_rows"]
                    if q["code"].startswith("Q_AMOUNT")})
    assert codes == ["Q_AMOUNT_UNIT_CONFLICT", "Q_AMOUNT_UNIT_MISSING"]
    # 9 行 = 解析成功 5 + 解析隔离 4
    parse_stage = next(s for s in result["stages"] if s["stage"] == "parse")
    assert parse_stage["in_count"] == 9
    assert parse_stage["quarantine_count"] == 4
    assert result["reconciliation"]["all_balanced"] is True


def test_batch_key_changes_when_contract_snapshot_changes(settings, store):
    """页面相同但契约快照不同 → 批次键不同，历史批次不被新契约改写。"""
    v2 = batch_service.run(settings=settings, store=store, source="portal_d")
    digests = state.page_digests(v2["pages"])
    key_v2 = state.batch_key(
        rule_version=settings.rule_version, source="portal_d", digests=digests,
        snapshot_digest_value=snapshots_digest(v2["snapshots"]))
    key_other = state.batch_key(
        rule_version=settings.rule_version, source="portal_d", digests=digests,
        snapshot_digest_value="")
    assert key_v2 == v2["batch"]["batch_key"]
    assert key_v2 != key_other


def test_snapshot_and_raw_rows_persisted_with_batch(settings, store):
    result = batch_service.run(settings=settings, store=store, source="portal_d")
    bid = result["batch"]["batch_id"]
    snaps = store.load_contract_snapshots(bid)
    assert {s["portal"]: s["contract_version"] for s in snaps} == {
        "portal_d": contracts.CONTRACT_V2}
    raw = store.load_raw_rows(bid)
    assert len(raw) == 9                      # 原始行全部落库
    assert all(r["fields"] for r in raw)
    manifest = store.load_manifest(bid)
    assert manifest["contract_snapshots"][0]["digest"] == snaps[0]["digest"]


def test_parse_interruption_resumes_from_saved_raw_rows(settings, store):
    """解析中断：失败批次已存原始行；续跑 ingest 标记 reused、不重抓、不新增公告。"""
    def boom():
        raise RuntimeError("注入的解析中断")

    failed = batch_service.run(settings=settings, store=store, source="portal_d",
                               hooks={"parse": boom})
    assert failed["status"] == "failed" and failed["failed_stage"] == "parse"
    bid = failed["batch"]["batch_id"]
    assert len(store.load_raw_rows(bid)) == 9          # 中断时原始行已落库
    plan = resume_mod.plan(store, bid)
    assert plan["can_resume"] is True
    assert plan["from_stage"] == "parse"
    assert plan["reused_raw_rows"] == 9 and plan["reused_pages"] == 1

    resumed = batch_service.run(settings=settings, store=store, source="portal_d",
                                resume_of=bid)
    assert resumed["status"] == "done"
    statuses = {s["stage"]: s["status"] for s in resumed["stages"]}
    assert statuses["ingest"] == "reused"             # 页面不重抓
    parse_note = next(s["note"] for s in resumed["stages"] if s["stage"] == "parse")
    assert "未重抓" in parse_note
    rid = resumed["batch"]["batch_id"]
    # 续跑只承接原 9 条原始行，不重抓、不新增
    assert store.count_rows(rid)["raw_rows"] == 9


def test_resume_does_not_duplicate_announcements(settings, store):
    """不变量：续跑成功后公告数与一次成功运行一致，不产生重复公告。"""
    once = batch_service.run(settings=settings, store=store, source="portal_d")
    once_count = store.count_rows(once["batch"]["batch_id"])["announcements"]

    def boom():
        raise RuntimeError("boom")

    failed = batch_service.run(settings=settings, store=store, source="portal_d",
                               hooks={"quality": boom})
    resumed = batch_service.run(settings=settings, store=store, source="portal_d",
                                resume_of=failed["batch"]["batch_id"])
    rid = resumed["batch"]["batch_id"]
    assert store.count_rows(rid)["announcements"] == once_count


def test_rerun_is_idempotent_with_same_snapshot(settings, store):
    """同输入+同契约快照重跑命中批次键，不新增任何行。"""
    first = batch_service.run(settings=settings, store=store, source="portal_d")
    before = store.count_rows(first["batch"]["batch_id"])
    second = batch_service.run(settings=settings, store=store, source="portal_d")
    assert second["idempotent_replay"] is True
    assert second["batch"]["batch_id"] == first["batch"]["batch_id"]
    assert store.count_rows(first["batch"]["batch_id"]) == before
    assert len([b for b in store.list_batches() if b["status"] == "done"]) == 1


def test_v2_export_reproduces_amount_and_basis(settings, store):
    result = batch_service.run(settings=settings, store=store, source="portal_d")
    bid = result["batch"]["batch_id"]
    chains = _rows(export_service.chains_csv(store, bid))
    header = chains[0]
    i_cv, i_mode, i_raw, i_basis = (header.index("契约版本"), header.index("额度模式"),
                                   header.index("额度原文"), header.index("采用依据"))
    data = chains[1:]
    assert all(r[i_cv] == contracts.CONTRACT_V2 for r in data)
    assert all(r[i_mode] == "split" for r in data)
    assert any("未做旧文本换算" in r[i_basis] for r in data)
    assert any(r[i_raw] == "320" for r in data)

    policies = _rows(export_service.policies_csv(store, bid, settings))
    p_header = policies[0]
    assert p_header[:6] == ["公文号", "标题", "状态", "当前截止", "截止说明", "资助额度(万元)"]
    # 清单金额合计与对账一致
    total = sum(Decimal(r[5]) for r in policies[1:])
    assert total == Decimal(result["reconciliation"]["amounts"]["parts"]["清单合计"])


def test_historical_v1_batch_reproduces_original_amount_after_registry_changes(
        settings, store):
    """门户后来切到 v2，已完成的 v1 批次导出仍复现原金额与 v1 采用依据。"""
    old = batch_service.run(settings=settings, store=store, source="portal_a")
    old_bid = old["batch"]["batch_id"]
    before_switch = _rows(export_service.policies_csv(store, old_bid, settings))

    # 门户 A 切换到 v2（模拟后续改版）
    contracts.set_portal_version("portal_a", contracts.CONTRACT_V2)
    try:
        after_switch = _rows(export_service.policies_csv(store, old_bid, settings))
        manifest = store.load_manifest(old_bid)
        assert manifest["contract_snapshots"][0]["contract_version"] == contracts.CONTRACT_V1
    finally:
        contracts.set_portal_version("portal_a", contracts.CONTRACT_V1)

    assert after_switch == before_switch                 # 历史导出逐字节一致
    flat = "\n".join(",".join(r) for r in after_switch)
    assert "contract/1.4" in flat and "v1 文本契约" in flat


def test_v2_quarantine_export_contains_new_reason_codes(settings, store):
    result = batch_service.run(settings=settings, store=store, source="portal_d")
    bid = result["batch"]["batch_id"]
    body = export_service.quarantine_csv(store, bid).decode("utf-8-sig")
    assert "Q_AMOUNT_UNIT_MISSING" in body
    assert "Q_AMOUNT_UNIT_CONFLICT" in body


def test_legacy_failed_batch_without_snapshot_gets_rebound(settings, store):
    """兼容：更早的失败批次没有 contract_snapshots 行时，续跑按门户当前登记补绑。"""
    def boom():
        raise RuntimeError("注入链判定中断")

    failed = batch_service.run(settings=settings, store=store, source="portal_d",
                               hooks={"chain": boom})
    bid = failed["batch"]["batch_id"]
    # 模拟改版前遗留：删除快照行，保留页面与原始行
    with store.connect() as conn:
        conn.execute("DELETE FROM contract_snapshots WHERE batch_id=?", (bid,))
    assert store.load_contract_snapshots(bid) == []
    resumed = batch_service.run(settings=settings, store=store, source="portal_d",
                                resume_of=bid)
    assert resumed["status"] == "done"
    rid = resumed["batch"]["batch_id"]
    snaps = store.load_contract_snapshots(rid)
    assert snaps and snaps[0]["contract_version"] == contracts.CONTRACT_V2
    # 补绑的快照仍驱动 v2 解析，违约行依旧隔离
    codes = {q["code"] for q in resumed["quarantine_rows"]}
    assert "Q_AMOUNT_UNIT_MISSING" in codes


def test_contract_switch_api_preflight_and_fallback(client):
    """契约切换接口：预检失败 409 且保留旧版；成功才切换。"""
    bad = client.post("/api/contract/switch",
                      json={"portal": "portal_a", "contract_version": contracts.CONTRACT_V2,
                            "headers": ["文号", "资助额度"]})
    assert bad.status_code == 409
    assert bad.json()["error"] == "Q_CONTRACT_PREFLIGHT_FAILED"
    assert client.get("/api/contract/portals").json()["portal_registry"]["portal_a"] == \
        contracts.CONTRACT_V1

    ok = client.post("/api/contract/switch",
                     json={"portal": "portal_c", "contract_version": contracts.CONTRACT_V2,
                           "headers": ["公文号", "公告序号", "公告类型", "标题",
                                       "发布时间", "额度数值", "额度单位"]})
    assert ok.status_code == 200 and ok.json()["active"] == contracts.CONTRACT_V2
    contracts.set_portal_version("portal_c", contracts.CONTRACT_V1)


def test_preview_endpoint_supports_v2_contract(client):
    resp = client.post("/api/parse/preview",
                       json={"contract_version": contracts.CONTRACT_V2,
                             "fields": {**{
                                 "公文号": "澄政办〔2026〕09号", "公告序号": "1",
                                 "公告类型": "首次发布", "标题": "预览项",
                                 "发布时间": "2026-11-03"},
                                 "额度数值": "500", "额度单位": "万元"}})
    body = resp.json()
    assert body["ok"] is True
    assert body["announcement"]["contract_version"] == contracts.CONTRACT_V2
    assert body["announcement"]["amount_wan"] == "500.00"

    bad = client.post("/api/parse/preview",
                      json={"contract_version": contracts.CONTRACT_V2,
                            "fields": {**{
                                "公文号": "澄政办〔2026〕10号", "公告序号": "1",
                                "公告类型": "首次发布", "标题": "预览缺单位",
                                "发布时间": "2026-11-03"},
                                "额度数值": "500", "额度单位": ""}})
    assert bad.json()["quarantine"][0]["code"] == "Q_AMOUNT_UNIT_MISSING"
