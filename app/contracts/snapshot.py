"""采集快照绑定：把本次采集各门户的契约定义冻结成快照，并算出可复算的摘要。

快照与批次一对一：
- `bind_snapshots` 在采信阶段按实际参与采集的门户生成快照；
- 批次键、运行清单都带上快照 digest，历史批次永远按当时的契约解释，
  门户后续改版/切契约不会改变历史金额与采用依据。
"""
from __future__ import annotations

import hashlib
import json

from . import registry


class SnapshotError(RuntimeError):
    """快照绑定异常（如某门户没有可用契约）。"""


def snapshot_digest(contract_body: dict) -> str:
    payload = json.dumps(contract_body, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def bind_snapshots(portal_ids: list[str]) -> list[dict]:
    """按门户清单冻结契约快照；同一门户在一次采集中只出现一份。"""
    snapshots: list[dict] = []
    seen: set[str] = set()
    for portal_id in portal_ids:
        if portal_id in seen:
            continue
        seen.add(portal_id)
        try:
            snapshots.append(registry.portal_snapshot(portal_id))
        except KeyError as exc:
            raise SnapshotError(f"门户 {portal_id} 没有可用契约：{exc}") from exc
    return snapshots


def snapshots_digest(snapshots: list[dict]) -> str:
    """快照集合摘要：顺序无关（按门户排序后再哈希），供批次键使用。"""
    h = hashlib.sha256()
    for snap in sorted(snapshots, key=lambda s: s["portal"]):
        h.update(snap["portal"].encode("utf-8"))
        h.update(b"\x00")
        h.update(snap["digest"].encode("utf-8"))
        h.update(b"\x1e")
    return h.hexdigest()


def snapshot_by_portal(snapshots: list[dict]) -> dict[str, dict]:
    return {s["portal"]: s for s in snapshots}


def version_of(snapshots: list[dict], portal_id: str) -> str:
    for snap in snapshots:
        if snap["portal"] == portal_id:
            return snap["contract_version"]
    raise SnapshotError(f"门户 {portal_id} 不在本次采集快照内")
