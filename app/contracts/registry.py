"""门户契约登记表：两代契约定义、按门户的当前版本登记、切换预检与回退。

- v1（`contract/1.4`）：额度是带单位的单列文本（如 `420万元`、`12,000,000元`），
  解析时按单位表折算成万元，原文随公告保留；
- v2（`contract/2.0`）：门户改版后额度拆成「额度数值」「额度单位」两列，
  单位必须显式给出且与数值列内嵌单位（若有）一致，否则整行进隔离，不做猜测换算。

切换是显式动作：先预检（样例表头能否命中新契约的必需列），预检失败保留旧版。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from decimal import Decimal

from .. import config
from ..modules.m2_parse import fields as m2_fields

CONTRACT_V1 = "contract/1.4"
CONTRACT_V2 = "contract/2.0"
SUPPORTED_VERSIONS = (CONTRACT_V1, CONTRACT_V2)

# v2 额度两列的列名别名（数值列 / 单位列）
AMOUNT_VALUE_ALIASES = ("额度数值", "资助额度数值", "资助金额(数值)", "金额(数值)",
                        "支持金额(数值)", "额度(数值)")
AMOUNT_UNIT_ALIASES = ("额度单位", "资助额度单位", "资助金额(单位)", "金额单位",
                       "金额(单位)", "支持金额(单位)", "额度(单位)")
# v2 允许的单位取值（与 config.UNIT_TO_WAN 的键对齐，去重保序）
V2_UNIT_CHOICES = ("万元", "元", "亿元")
V2_UNIT_ALIASES = {
    "万": "万元", "万元": "万元", "人民币万元": "万元",
    "元": "元", "人民币元": "元", "圆": "元",
    "亿元": "亿元", "亿": "亿元",
}

# 原因码：v2 新记录额度列违约
Q_AMOUNT_UNIT_MISSING = "Q_AMOUNT_UNIT_MISSING"
Q_AMOUNT_UNIT_CONFLICT = "Q_AMOUNT_UNIT_CONFLICT"


@dataclass(frozen=True)
class FieldSpec:
    name: str
    zh: str
    aliases: tuple[str, ...]
    kind: str
    required: bool
    note: str


def _v1_fields() -> tuple[FieldSpec, ...]:
    return tuple(
        FieldSpec(f.name, f.zh, f.aliases, f.kind, f.required, f.note)
        for f in m2_fields.FIELDS
    )


def _v2_fields() -> tuple[FieldSpec, ...]:
    out: list[FieldSpec] = []
    for f in m2_fields.FIELDS:
        if f.name == "amount":
            out.append(FieldSpec(
                "amount_value", "额度数值", AMOUNT_VALUE_ALIASES, "amount_value",
                False, "纯数值（允许千分位/全角），不再内嵌单位；哨兵按未登记"))
            out.append(FieldSpec(
                "amount_unit", "额度单位", AMOUNT_UNIT_ALIASES, "amount_unit",
                False, f"显式单位，取值 { '/'.join(V2_UNIT_CHOICES) }；"
                       f"缺失或与数值列内嵌单位冲突整行隔离"))
        else:
            out.append(FieldSpec(f.name, f.zh, f.aliases, f.kind, f.required, f.note))
    return tuple(out)


@dataclass(frozen=True)
class ContractSpec:
    version: str
    label: str
    amount_mode: str                      # text（v1 带单位文本） | split（v2 数值+单位）
    fields: tuple[FieldSpec, ...]
    note: str

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields)

    @property
    def required_names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields if f.required)

    def field(self, name: str) -> FieldSpec | None:
        for f in self.fields:
            if f.name == name:
                return f
        return None

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "label": self.label,
            "amount_mode": self.amount_mode,
            "note": self.note,
            "amount": {
                "mode": self.amount_mode,
                "text_column": "资助额度（数值与单位写在同一格，按单位表折算万元）"
                if self.amount_mode == "text" else None,
                "value_column": "额度数值" if self.amount_mode == "split" else None,
                "unit_column": "额度单位" if self.amount_mode == "split" else None,
                "unit_choices": list(V2_UNIT_CHOICES) if self.amount_mode == "split" else [],
                "quantize": str(config.DECIMAL_QUANT),
                "ledger_unit": config.MONEY_UNIT,
            },
            "columns": [
                {"name": f.name, "zh": f.zh, "aliases": list(f.aliases),
                 "kind": f.kind, "required": f.required, "note": f.note}
                for f in self.fields
            ],
        }


CONTRACTS: dict[str, ContractSpec] = {
    CONTRACT_V1: ContractSpec(
        version=CONTRACT_V1,
        label="门户改版前契约（额度为带单位单列文本）",
        amount_mode="text",
        fields=_v1_fields(),
        note="兼容改版前已采集批次：额度原文保留，按采集时单位表折算，不重算不换算成新金额"),
    CONTRACT_V2: ContractSpec(
        version=CONTRACT_V2,
        label="门户改版后契约（额度拆成数值与单位两列）",
        amount_mode="split",
        fields=_v2_fields(),
        note="新采集按本契约校验：单位缺失或单位冲突的记录进隔离，不用默认单位顶替"),
}

# 门户 → 当前生效契约版本（可通过切换接口变更；历史批次仍绑定各自快照，不受影响）
PORTAL_CONTRACT_REGISTRY: dict[str, str] = {
    "portal_a": CONTRACT_V1,
    "portal_b": CONTRACT_V1,
    "portal_c": CONTRACT_V1,
    # 改版后的新门户（样例站 portal_d）一上线即按 v2 采集
    "portal_d": CONTRACT_V2,
}


def contract_for(version: str) -> ContractSpec:
    if version not in CONTRACTS:
        raise KeyError(f"未登记的契约版本 {version!r}；支持 {SUPPORTED_VERSIONS}")
    return CONTRACTS[version]


def active_version(portal_id: str) -> str:
    return PORTAL_CONTRACT_REGISTRY.get(portal_id, CONTRACT_V1)


def effective_contract(portal_id: str) -> ContractSpec:
    return contract_for(active_version(portal_id))


def set_portal_version(portal_id: str, version: str) -> str:
    """直接登记门户契约版本（不做预检的内部路径，测试与装配用）。"""
    if version not in CONTRACTS:
        raise KeyError(f"未登记的契约版本 {version!r}")
    previous = PORTAL_CONTRACT_REGISTRY.get(portal_id, CONTRACT_V1)
    PORTAL_CONTRACT_REGISTRY[portal_id] = version
    return previous


def _alias_index(spec: ContractSpec) -> dict[str, str]:
    """归一后表头 → 规范字段（中文名给 3 分，别名给 2 分，取最高分）。"""
    index: dict[str, tuple[str, int]] = {}
    for f in spec.fields:
        key = m2_fields.norm_header(f.zh)
        if key not in index or index[key][1] < 3:
            index[key] = (f.name, 3)
    for f in spec.fields:
        for i, alias in enumerate(f.aliases):
            key = m2_fields.norm_header(alias)
            score = 2 - min(i, 1)
            if key not in index or index[key][1] < score:
                index[key] = (f.name, score)
    return {k: v[0] for k, v in index.items()}


def validate_headers(version: str, headers: list[str]) -> dict:
    """预检：样例/实际表头能否命中契约的全部必需列与额度列。

    返回 {ok, missing_required, missing_amount, resolved, unknown}。
    """
    spec = contract_for(version)
    alias_index = _alias_index(spec)
    resolved: dict[str, str] = {}
    unknown: list[str] = []
    for header in headers:
        name = alias_index.get(m2_fields.norm_header(header))
        if name is None:
            unknown.append(header)
            continue
        resolved.setdefault(name, header)
    missing_required = [f.zh for f in spec.fields
                        if f.required and f.name not in resolved]
    missing_amount: list[str] = []
    if spec.amount_mode == "split":
        for needed in ("amount_value", "amount_unit"):
            if needed not in resolved:
                missing_amount.append(spec.field(needed).zh)
    elif "amount" not in resolved:
        missing_amount.append(spec.field("amount").zh)
    return {
        "ok": not missing_required and not missing_amount,
        "version": version,
        "missing_required": missing_required,
        "missing_amount": missing_amount,
        "resolved": resolved,
        "unknown": unknown,
    }


def switch_with_preflight(portal_id: str, version: str,
                          sample_headers: list[str] | None = None) -> dict:
    """契约切换：先预检，失败保留旧版并返回原因；成功才登记新版本。"""
    if version not in CONTRACTS:
        return {"ok": False, "portal": portal_id, "active": active_version(portal_id),
                "requested": version, "code": "Q_CONTRACT_UNKNOWN_VERSION",
                "detail": f"未登记的契约版本，支持 {SUPPORTED_VERSIONS}"}
    previous = active_version(portal_id)
    if sample_headers is not None:
        check = validate_headers(version, sample_headers)
        if not check["ok"]:
            # 关键：切换失败保留旧版，门户仍按旧契约采集
            return {"ok": False, "portal": portal_id, "active": previous,
                    "requested": version, "code": "Q_CONTRACT_PREFLIGHT_FAILED",
                    "detail": "表头预检未通过，保留旧版契约",
                    "missing_required": check["missing_required"],
                    "missing_amount": check["missing_amount"]}
    set_portal_version(portal_id, version)
    return {"ok": True, "portal": portal_id, "active": version, "previous": previous,
            "detail": f"门户契约 {previous} → {version}"}


def canonicalize(version: str, raw_fields: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """按指定契约版本把原始列名归一到规范字段；未识别列留备查。"""
    spec = contract_for(version)
    alias_index = _alias_index(spec)
    canon: dict[str, str] = {}
    unknown: dict[str, str] = {}
    for key, value in raw_fields.items():
        name = alias_index.get(m2_fields.norm_header(key))
        if name is None:
            unknown[key] = value
            continue
        canon.setdefault(name, value)
    return canon, unknown


def portal_snapshot(portal_id: str) -> dict:
    """采集瞬间冻结的门户契约快照（定义 + digest），随批次绑定。"""
    from .snapshot import snapshot_digest
    version = active_version(portal_id)
    spec = contract_for(version)
    body = spec.to_dict()
    return {
        "portal": portal_id,
        "contract_version": version,
        "amount_mode": spec.amount_mode,
        "digest": snapshot_digest(body),
        "contract": body,
    }


def catalog() -> dict:
    """契约目录（给 /api/contract 与页面）。"""
    return {
        "supported_versions": list(SUPPORTED_VERSIONS),
        "contracts": {v: contract_for(v).to_dict() for v in SUPPORTED_VERSIONS},
        "portal_registry": copy.deepcopy(PORTAL_CONTRACT_REGISTRY),
        "unit_choices": list(V2_UNIT_CHOICES),
        "unit_aliases": dict(V2_UNIT_ALIASES),
        "unit_to_wan": {k: str(v) for k, v in config.UNIT_TO_WAN.items()},
    }


def normalize_v2_unit(raw_unit: str | None) -> str | None:
    if raw_unit is None:
        return None
    text = m2_fields.norm_header(raw_unit)
    return V2_UNIT_ALIASES.get(text) or V2_UNIT_ALIASES.get(text.lower())


def v2_unit_factor(unit: str) -> Decimal | None:
    return config.UNIT_TO_WAN.get(unit)
