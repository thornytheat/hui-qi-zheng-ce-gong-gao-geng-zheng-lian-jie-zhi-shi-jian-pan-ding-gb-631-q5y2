"""门户管理契约版本化：v1（额度为带单位文本）与 v2（额度拆成数值/单位两列）。

契约是「门户采集快照」的一部分：
- 每个门户在登记表里绑定一个当前生效的契约版本；
- 采集时把当次契约定义冻结成快照（含 digest），随批次与运行清单一起落库；
- 解析、导出都按批次绑定的快照走，门户后来再改版也不影响历史批次复现。
"""
from .registry import (
    CONTRACT_V1,
    CONTRACT_V2,
    PORTAL_CONTRACT_REGISTRY,
    SUPPORTED_VERSIONS,
    active_version,
    canonicalize,
    catalog,
    contract_for,
    effective_contract,
    normalize_v2_unit,
    portal_snapshot,
    set_portal_version,
    switch_with_preflight,
    validate_headers,
)
from .snapshot import SnapshotError, bind_snapshots, snapshot_digest, snapshots_digest

__all__ = [
    "CONTRACT_V1", "CONTRACT_V2", "SUPPORTED_VERSIONS", "PORTAL_CONTRACT_REGISTRY",
    "active_version", "canonicalize", "catalog", "contract_for",
    "effective_contract", "normalize_v2_unit", "portal_snapshot",
    "set_portal_version", "switch_with_preflight", "validate_headers",
    "SnapshotError", "bind_snapshots", "snapshot_digest", "snapshots_digest",
]
