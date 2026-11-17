"""断点续跑：从失败阶段继续，已落库的页面与原始行直接复用，不重抓。

采信阶段会把每条原始表格行写入 raw_rows 断点表，因此：
- 采信失败：页面不完整，整体重抓；
- 解析/质量失败：原始行已在库，从原始行续跑，页面不重抓、已采内容不重采；
- 链判定之后：复用已解析公告与质量结论。
重试不新增公告：续跑只在原批次上继续，幂等命中已完成批次时直接复用结果。
"""
from __future__ import annotations

from ... import config
from . import state

# 各失败阶段可复用的前置阶段；parse/quality 复用采信（页面 + 原始行断点）
REUSABLE_AFTER = {
    "ingest": (),
    "parse": ("ingest",),
    "quality": ("ingest", "parse"),
    "chain": ("ingest", "parse", "quality"),
    "list": ("ingest", "parse", "quality", "chain"),
    "export": ("ingest", "parse", "quality", "chain", "list"),
}


def plan(store, batch_id: str) -> dict:
    batch = store.get_batch(batch_id)
    if batch is None:
        return {"can_resume": False, "code": "E_NOT_FOUND", "from_stage": None,
                "reused_pages": 0, "reused_raw_rows": 0, "note": "批次不存在"}
    stages = store.load_stages(batch_id)
    failed = state.interrupted_stage(stages)
    if batch["status"] != "failed":
        return {"can_resume": False, "code": "B_RESUME_NO_STATE", "from_stage": None,
                "reused_pages": 0, "reused_raw_rows": 0,
                "note": f"批次状态为 {batch['status']}，只有失败批次可以续跑"}
    if failed is None:
        return {"can_resume": False, "code": "B_RESUME_NO_STATE", "from_stage": None,
                "reused_pages": 0, "reused_raw_rows": 0,
                "note": "没有记录到失败阶段，按全新批次重跑"}
    reused_pages = len([p for p in store.load_pages(batch_id) if p["status"] == "ok"])
    reused_raw_rows = len(store.load_raw_rows(batch_id))
    reusable_stages = REUSABLE_AFTER[failed]
    reuses_ingest = "ingest" in reusable_stages
    note = f"从「{config.STAGE_LABELS[failed]}」阶段续跑"
    if reuses_ingest:
        note += f"，复用已采信页面 {reused_pages} 个、原始行 {reused_raw_rows} 条，不重抓"
    return {
        "can_resume": True,
        "code": None,
        "from_stage": failed,
        "reusable_stages": list(reusable_stages),
        "reused_pages": reused_pages if reuses_ingest else 0,
        "reused_raw_rows": reused_raw_rows if reuses_ingest else 0,
        "note": note,
    }
