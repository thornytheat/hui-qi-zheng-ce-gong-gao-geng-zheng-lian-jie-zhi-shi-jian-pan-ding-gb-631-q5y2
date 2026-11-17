"""M2 入口：列契约文档 + 单条解析试跑 + 门户契约版本切换。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ... import contracts, errors
from ...web import Ctx, get_ctx
from . import contract, service

router = APIRouter(tags=["M2 契约与解析"])


@router.get("/api/contract")
def get_contract() -> dict:
    return contract.document()


@router.get("/api/contract/portals")
def get_portal_contracts() -> dict:
    """各门户当前生效的契约版本与契约目录。"""
    return contracts.catalog()


@router.post("/api/contract/switch")
def switch_contract(payload: dict) -> dict:
    """门户契约切换：带表头预检；预检失败保留旧版，不切到无法解析的契约。"""
    portal_id = str(payload.get("portal") or "").strip()
    version = str(payload.get("contract_version") or "").strip()
    if not portal_id:
        raise errors.BadRequest("portal 必填", code="Q_MISSING_REQUIRED")
    headers = payload.get("headers")
    if headers is not None and not isinstance(headers, list):
        raise errors.BadRequest("headers 必须是列名数组", code="Q_MISSING_REQUIRED")
    result = contracts.switch_with_preflight(
        portal_id, version,
        sample_headers=[str(h) for h in headers] if headers is not None else None)
    if not result["ok"]:
        raise errors.Conflict(result["detail"], code=result["code"])
    return result


@router.post("/api/parse/preview")
def preview(payload: dict, ctx: Ctx = Depends(get_ctx)) -> dict:
    raw = payload.get("fields")
    if not isinstance(raw, dict) or not raw:
        raise errors.BadRequest("fields 必须是「列名 → 原文」的非空对象",
                                code="Q_MISSING_REQUIRED")
    clean = {str(k): str(v) for k, v in raw.items()}
    version = str(payload.get("contract_version") or contracts.CONTRACT_V1).strip()
    if version not in contracts.SUPPORTED_VERSIONS:
        raise errors.BadRequest(
            f"未知契约版本 {version!r}；支持 {contracts.SUPPORTED_VERSIONS}",
            code="Q_CONTRACT_UNKNOWN_VERSION")
    announcement, quarantine = service.parse_one(clean, contract_version=version)
    return {
        "ok": announcement is not None,
        "announcement": None if announcement is None else {
            "doc_no": announcement["doc_no"],
            "seq": announcement["seq"],
            "kind": announcement["kind"],
            "title": announcement["title"],
            "published_at": announcement["published_at"],
            "deadline_spec": announcement["deadline_spec"],
            "amount_wan": None if announcement["amount_wan"] is None
            else str(announcement["amount_wan"]),
            "amount_unit": announcement["amount_unit"],
            "contract_version": announcement["contract_version"],
            "amount_mode": announcement["amount_mode"],
            "raw_amount_text": announcement.get("raw_amount_text", ""),
            "amount_basis": announcement.get("amount_basis", {}),
            "target_field": announcement["target_field"],
            "target_value": announcement["target_value"],
        },
        "quarantine": quarantine,
        "contract_version": version,
    }
