"""采集失败恢复路由：恢复清单、归属仲裁、人工复核与派发回执。

与通用资源路由分开，因为恢复流程跨 exception_rule / retry_policy /
task_queue / audit_trail 四个模块，状态流转只能在领域服务内完成。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services import service_for
from app.services.recovery import RecoveryService, build_failure_key

router = APIRouter(prefix="/api/recovery", tags=["采集失败恢复"])


def _recovery() -> RecoveryService:
    svc = service_for("failure_resolution")
    if not isinstance(svc, RecoveryService):
        raise RuntimeError("恢复服务未注册")
    return svc


class FailureTrigger(BaseModel):
    signature: str = Field(..., description="异常特征，如 元素定位漂移:ELEM-A001:flow_run_4")
    template_code: str = Field(..., description="失败所属模板编号，用于归属核验")
    flow_run_id: int | None = None
    scope: str | None = "定位"
    source: str | None = "元素定位漂移"


class ReviewPayload(BaseModel):
    reviewer: str = "值班员"
    approved: bool = True
    remark: str = ""


class DispatchPayload(BaseModel):
    task_id: int
    node_code: str
    reviewer: str = "值班员"


@router.get("/queue")
def recovery_queue(status: str | None = None) -> dict[str, Any]:
    """值班员视角的恢复清单：可立即补采 / 待复核 / 已派发 一目了然。"""
    svc = _recovery()
    items = svc.list_recovery_queue(status)
    return {"items": items, "total": len(items)}


@router.get("/resolutions/{resolution_id}")
def get_resolution(resolution_id: int) -> dict[str, Any]:
    """单条恢复清单详情，含采用规则、恢复条件与采集范围。"""
    svc = _recovery()
    resolution = svc.get_resolution(resolution_id)
    if resolution is None:
        return {"ok": False, "reason": "恢复清单条目不存在"}
    return {"ok": True, "resolution": resolution}


@router.post("/failures/resolve")
def resolve_failure(payload: FailureTrigger) -> dict[str, Any]:
    """采集失败入口：归属核验 + 规则仲裁，唯一结论写入恢复清单（幂等）。"""
    svc = _recovery()
    return svc.resolve_failure(
        payload.signature,
        payload.template_code,
        flow_run_id=payload.flow_run_id,
        scope=payload.scope,
        source=payload.source or "元素定位漂移",
    )


@router.post("/resolutions/{resolution_id}/review")
def review_resolution(resolution_id: int, payload: ReviewPayload) -> dict[str, Any]:
    """值班员人工复核：共享规则命中的失败须复核通过后才能启动恢复。"""
    return _recovery().review_resolution(
        resolution_id, payload.reviewer, payload.approved, payload.remark
    )


@router.post("/resolutions/{resolution_id}/dispatch")
def dispatch_recovery(resolution_id: int, payload: DispatchPayload) -> dict[str, Any]:
    """按清单派发实际采集范围，回执注明采用的规则与恢复执行号。"""
    return _recovery().dispatch_recovery(
        resolution_id, payload.task_id, payload.node_code, reviewer=payload.reviewer
    )


@router.get("/failures/match-history")
def match_history(template_code: str | None = None,
                  signature: str | None = None) -> dict[str, Any]:
    """原匹配历史回查：一次失败命中过哪些规则、哪条被采用。"""
    svc = _recovery()
    failure_key = build_failure_key(template_code, signature) if template_code and signature else None
    return {"items": svc.list_match_history(failure_key)}


@router.get("/failures/dispatch-history")
def dispatch_history(template_code: str | None = None,
                     signature: str | None = None) -> dict[str, Any]:
    """执行历史回查：每次派发使用的规则、策略与恢复执行号。"""
    svc = _recovery()
    failure_key = build_failure_key(template_code, signature) if template_code and signature else None
    return {"items": svc.list_dispatch_history(failure_key)}
