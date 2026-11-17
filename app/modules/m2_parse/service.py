"""解析编排：原始列行 → 规范公告；每个失败都落原因码而不是默认值。

一个「行」最多产生一个公告或一组隔离行；隔离行数按记录计，
对账时 rows_seen = 解析成功记录 + 隔离记录。

契约版本：
- v1（contract/1.4）：额度为带单位单列文本，保留改版前的文本折算路径，
  历史失败批次按原契约恢复，不把旧额度换算成新金额；
- v2（contract/2.0）：额度拆成数值/单位两列，单位缺失或单位冲突整行进隔离。
每行公告都记下 `contract_version`、额度原文与「采用依据」，导出可逐行复现。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ... import config, contracts, reasons
from . import coerce, contract, fields


@dataclass
class ParseResult:
    announcements: list[dict] = field(default_factory=list)
    quarantine: list[dict] = field(default_factory=list)
    rows_seen: int = 0
    rejected_records: int = 0
    unknown_headers: dict[str, int] = field(default_factory=dict)

    def counters(self) -> dict:
        return {
            "rows_seen": self.rows_seen,
            "parsed": len(self.announcements),
            "rejected_rows": self.rejected_records,
            "reason_entries": len(self.quarantine),
        }


def _q(code: str, *, portal: str, source_url: str, row_ref: str,
       field_name: str | None = None, raw_value: str | None = None,
       detail: str = "") -> dict:
    reason = reasons.get(code)
    return {
        "code": code, "stage": reason.stage, "level": reason.level,
        "portal": portal, "source_url": source_url, "row_ref": row_ref,
        "field": field_name, "raw_value": raw_value, "detail": detail or reason.title,
    }


def _amount_basis_v1(raw_text: str | None, amount: Decimal | None,
                     unit: str) -> dict:
    if amount is None:
        return {"mode": "text", "raw_amount": coerce.clean(raw_text),
                "raw_unit_text": "", "basis": "v1 文本契约：空值/哨兵/转引按未登记，未折算"}
    return {
        "mode": "text",
        "raw_amount": coerce.clean(raw_text),
        "raw_unit_text": unit,
        "basis": (f"v1 文本契约：原文「{coerce.clean(raw_text)}」按采集时单位表"
                  f"（{unit}）折算成万元，保留原文不重算"),
    }


def _parse_amount_v2(canon: dict, *, portal: str, source_url: str,
                     row_ref: str) -> tuple[Decimal | None, str | None, list[dict], dict]:
    """v2 双列额度：返回 (万元值, 单位, 错误隔离行, 依据元数据)。"""
    value_text = canon.get("amount_value")
    unit_text = canon.get("amount_unit")
    amount, unit, error, meta = coerce.norm_amount_split(value_text, unit_text)
    basis = {
        "mode": "split",
        "raw_amount": meta["raw_value"],
        "raw_unit_text": meta["raw_unit"],
        "embedded_unit": meta["embedded_unit"],
        "factor": meta["factor"],
        "basis": meta["basis"] or (
            "v2 双列契约：数值/单位均未登记，按空值处理" if amount is None
            else "v2 双列契约：仅给单位未给数值，额度按未登记"),
    }
    if error:
        q = _q(error, portal=portal, source_url=source_url, row_ref=row_ref,
               field_name="amount_unit",
               raw_value=f"数值={value_text or ''} | 单位={unit_text or ''}",
               detail=meta["basis"] or reasons.get(error).title)
        return None, unit, [q], basis
    return amount, unit, [], basis


def parse_one(raw_fields: dict[str, str], *, portal: str = "preview",
              source_url: str = "preview://manual",
              row_ref: str = "preview#1",
              contract_version: str = contracts.CONTRACT_V1
              ) -> tuple[dict | None, list[dict]]:
    """单条解析（M2 独立入口 /api/parse/preview 与批量共用）。"""
    spec = contracts.contract_for(contract_version)
    if spec.amount_mode == "split":
        canon, unknown = contracts.canonicalize(contract_version, raw_fields)
    else:
        canon, unknown = fields.canonicalize(raw_fields)
    errors: list[dict] = []
    missing = [name for name in spec.required_names
               if not (canon.get(name) or "").strip()]
    if missing:
        for name in missing:
            f = spec.field(name)
            errors.append(_q("Q_MISSING_REQUIRED", portal=portal, source_url=source_url,
                             row_ref=row_ref, field_name=name, raw_value="",
                             detail=f"缺少必需字段 {f.zh if f else name}"))
        return None, errors

    doc_no, error = coerce.norm_doc_no(canon.get("doc_no"))
    if error:
        errors.append(_q(error, portal=portal, source_url=source_url, row_ref=row_ref,
                         field_name="doc_no", raw_value=canon.get("doc_no")))
    seq, error = coerce.norm_seq(canon.get("seq"))
    if error:
        errors.append(_q(error, portal=portal, source_url=source_url, row_ref=row_ref,
                         field_name="seq", raw_value=canon.get("seq")))
    kind, error = coerce.norm_kind(canon.get("kind"))
    if error:
        errors.append(_q(error, portal=portal, source_url=source_url, row_ref=row_ref,
                         field_name="kind", raw_value=canon.get("kind")))
    published_at, error = coerce.norm_instant(canon.get("published_at"))
    if error:
        errors.append(_q(error, portal=portal, source_url=source_url, row_ref=row_ref,
                         field_name="published_at", raw_value=canon.get("published_at")))
    if errors:
        return None, errors

    title = coerce.clean(canon.get("title"))
    amount_unit: str | None = config.MONEY_UNIT
    amount_basis: dict
    if spec.amount_mode == "split":
        amount, amount_unit, amount_errors, amount_basis = _parse_amount_v2(
            canon, portal=portal, source_url=source_url, row_ref=row_ref)
        errors.extend(amount_errors)
        raw_amount_text = amount_basis["raw_amount"]
        if amount is None and not amount_unit:
            amount_unit = None
    else:
        raw_amount_text = canon.get("amount")
        amount, _u, amount_error = coerce.norm_amount(raw_amount_text)
        if amount_error:
            errors.append(_q(amount_error, portal=portal, source_url=source_url,
                             row_ref=row_ref, field_name="amount",
                             raw_value=raw_amount_text))
        amount_basis = _amount_basis_v1(raw_amount_text, amount, _u)
    deadline_spec, deadline_error, deadline_note = coerce.norm_deadline(canon.get("deadline"))
    if deadline_error:
        errors.append(_q(deadline_error, portal=portal, source_url=source_url,
                         row_ref=row_ref, field_name="deadline",
                         raw_value=canon.get("deadline"), detail=deadline_note))

    target_field_raw = coerce.clean(canon.get("target_field"))
    target_value_raw = coerce.clean(canon.get("target_value"))
    target_key = fields.target_field_name(target_field_raw) if target_field_raw else None
    if kind == "CORRECT" and target_key is None \
            and not coerce.is_sentinel(target_field_raw) \
            and not coerce.is_deferred(target_field_raw):
        errors.append(_q("C_ORPHAN_CORRECTION", portal=portal, source_url=source_url,
                         row_ref=row_ref, field_name="target_field",
                         raw_value=target_field_raw,
                         detail=f"更正事项「{target_field_raw}」不在 截止说明/资助额度/标题 内"))
    if target_key == "amount":
        _value, _u, target_error = coerce.norm_amount(target_value_raw)
        if target_error:
            errors.append(_q(target_error, portal=portal, source_url=source_url,
                             row_ref=row_ref, field_name="target_value",
                             raw_value=target_value_raw))
    elif target_key == "deadline":
        _spec, target_error, target_note = coerce.norm_deadline(target_value_raw)
        if target_error:
            errors.append(_q(target_error, portal=portal, source_url=source_url,
                             row_ref=row_ref, field_name="target_value",
                             raw_value=target_value_raw, detail=target_note))
    if errors:
        return None, errors

    announcement = {
        "doc_no": doc_no,
        "seq": seq,
        "kind": kind,
        "title": title,
        "published_at": published_at.astimezone(config.UTC).isoformat(),
        "deadline_spec": deadline_spec,
        "amount_wan": amount,
        "amount_unit": amount_unit if amount_unit is not None else "",
        "target_field": target_key,
        "target_value": target_value_raw,
        "portal": portal,
        "source_url": source_url,
        "row_ref": row_ref,
        "raw": dict(raw_fields),
        "unknown_headers": unknown,
        "flags": [],
        "deadline_note": deadline_note,
        "contract_version": contract_version,
        "amount_mode": spec.amount_mode,
        "amount_basis": amount_basis,
        "raw_amount_text": amount_basis.get("raw_amount") or "",
    }
    return announcement, []


def resolve_target(announcement: dict) -> tuple[str, object] | None:
    """更正后取值的类型化解释；M4 链合并复用，历史数据重新读取后可复算。"""
    key = announcement.get("target_field")
    raw = announcement.get("target_value") or ""
    if key == "amount":
        value, _unit, error = coerce.norm_amount(raw)
        return None if error else ("amount", value)
    if key == "deadline":
        spec, error, _note = coerce.norm_deadline(raw)
        return None if error else ("deadline", spec)
    if key == "title":
        return ("title", coerce.clean(raw))
    return None


def parse_records(records: list[dict], snapshots: list[dict] | None = None) -> ParseResult:
    """批量解析。`snapshots` 给出本次采集各门户绑定的契约版本；
    缺省时按登记表当前版本（预览/单测直连路径）。"""
    version_by_portal = {s["portal"]: s["contract_version"] for s in (snapshots or [])}
    result = ParseResult()
    for record in records:
        result.rows_seen += 1
        header_count = record.get("header_count") or 0
        cell_count = record.get("cell_count") or 0
        column_code = contract.column_error(header_count, cell_count)
        if column_code:
            result.rejected_records += 1
            result.quarantine.append(_q(
                column_code, portal=record["portal"], source_url=record["source_url"],
                row_ref=record["row_ref"], field_name="row",
                raw_value=" | ".join(record.get("cells", []))[:160],
                detail=f"表头 {header_count} 列，本行 {cell_count} 列"))
            continue
        portal = record["portal"]
        version = version_by_portal.get(portal) or contracts.active_version(portal)
        announcement, errors = parse_one(
            record["fields"], portal=portal,
            source_url=record["source_url"], row_ref=record["row_ref"],
            contract_version=version)
        if announcement is None:
            result.rejected_records += 1
            result.quarantine.extend(errors)
            continue
        for key in announcement.get("unknown_headers", {}):
            result.unknown_headers[key] = result.unknown_headers.get(key, 0) + 1
        result.announcements.append(announcement)
    return result


def effective_deadline_spec(row: dict) -> dict | None:
    """某版本实际生效的截止说明（M4 展示推算依据时按版本回溯）。"""
    kind = row.get("kind")
    if kind in ("ORIGINAL", "EXTEND"):
        spec = row.get("deadline_spec")
        if isinstance(spec, str):
            import json
            spec = json.loads(spec)
        return spec
    if kind == "CORRECT" and row.get("target_field") == "deadline":
        spec, error, _note = coerce.norm_deadline(row.get("target_value"))
        return None if error else spec
    return None
