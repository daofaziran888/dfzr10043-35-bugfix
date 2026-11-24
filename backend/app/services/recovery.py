"""采集失败恢复归属服务：一次失败只允许一个有权采用的规则结论进入恢复清单。

职责（对应修复点）：
1. 归属核验：采集失败带所属模板，只在「自有（同模板）/共享」且命中特征的
   规则里仲裁，自有优先、同级按生效优先级，修正漂移事件串位到其他模板的问题；
2. 唯一结论：胜出规则写入 failure_resolution，其余命中写入 failure_match 留痕，
   原匹配与执行历史均可回查；重复上报同一失败幂等返回既有结论，不会二次仲裁；
3. 权限隔离：采用共享规则只扣共享策略自己的次数，另一执行的剩余次数不受影响；
4. 复核闸门：恢复条件为「人工复核后恢复」时，清单保持待复核，值班员复核通过
   后才允许派发；「立即单字段补采」可直接派发且只能兑现一次；
5. 派发回执：任务队列按归属核验后领取实际采集范围，回执注明采用的规则与执行号。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.services.retry_policy import CONDITION_MANUAL_REVIEW

# 恢复清单状态
STATUS_PENDING_REVIEW = "待复核"
STATUS_READY = "可恢复"
STATUS_DISPATCHED = "已派发"
STATUS_EXHAUSTED = "已耗尽"


def _now_iso() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def build_failure_key(template_code: str, signature: str) -> str:
    """失败唯一键：同名特征在不同模板下是不同失败，杜绝跨模板串位。"""
    return f"{template_code or 'UNKNOWN'}::{signature}"


class RecoveryService(BaseModuleService):
    """恢复清单服务，底层复用 store 的三张台账表。"""

    MODULE = "failure_resolution"
    ENTITY = "恢复清单"

    def __init__(self) -> None:
        # 不依赖 BaseModuleService 的字段/动作配置，仅复用 store 访问
        self._table_resolution = "failure_resolution"
        self._table_match = "failure_match"
        self._table_dispatch = "recovery_dispatch"

    # ── 台账访问 ──────────────────────────────────────────────

    def _rows(self, table: str) -> list[dict[str, Any]]:
        from app.store import store
        return store.rows(table)

    def _find(self, table: str, entry_id: int) -> dict[str, Any] | None:
        for row in self._rows(table):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def _create(self, table: str, values: dict[str, Any]) -> dict[str, Any]:
        rows = self._rows(table)
        entry = {"id": max((int(r.get("id", 0)) for r in rows), default=0) + 1}
        entry.update(values)
        rows.append(entry)
        return entry

    def get_resolution(self, resolution_id: int) -> dict[str, Any] | None:
        return self._find(self._table_resolution, resolution_id)

    def find_resolution(self, failure_key: str) -> dict[str, Any] | None:
        for row in self._rows(self._table_resolution):
            if row.get("failure_key") == failure_key:
                return row
        return None

    def list_recovery_queue(self, status: str | None = None) -> list[dict[str, Any]]:
        """值班员视角的恢复清单。"""
        rows = list(self._rows(self._table_resolution))
        if status:
            rows = [r for r in rows if r.get("清单状态") == status]
        rows.sort(key=lambda r: int(r.get("id", 0)), reverse=True)
        return rows

    def list_match_history(self, failure_key: str | None = None) -> list[dict[str, Any]]:
        """原匹配历史回查：一次失败命中过哪些规则、哪条被采用。"""
        rows = list(self._rows(self._table_match))
        if failure_key:
            rows = [r for r in rows if r.get("failure_key") == failure_key]
        return rows

    def list_dispatch_history(self, failure_key: str | None = None) -> list[dict[str, Any]]:
        """执行历史回查：每次派发用了哪份规则、哪次执行。"""
        rows = list(self._rows(self._table_dispatch))
        if failure_key:
            rows = [r for r in rows if r.get("failure_key") == failure_key]
        return rows

    # ── 失败归属仲裁 ──────────────────────────────────────────

    def resolve_failure(
        self,
        signature: str,
        template_code: str,
        *,
        flow_run_id: int | None = None,
        scope: str | None = "定位",
        source: str = "元素定位漂移",
    ) -> dict[str, Any]:
        """采集失败入口：仲裁规则并把唯一结论写入恢复清单（幂等）。"""
        from app.services import service_for

        rule_svc = service_for("exception_rule")
        retry_svc = service_for("retry_policy")
        failure_key = build_failure_key(template_code, signature)

        # 幂等：同一失败重复上报，直接回吐既有结论，不再二次仲裁/二次扣次数
        existing = self.find_resolution(failure_key)
        if existing is not None:
            return {
                "status": "existing",
                "failure_key": failure_key,
                "resolution_id": existing["id"],
                "resolution": existing,
            }

        winner, candidates = rule_svc.match_for_failure(signature, template_code, scope)
        if winner is None:
            return {
                "status": "no_rule",
                "failure_key": failure_key,
                "reason": "没有对该模板有权采用且特征命中的已生效规则",
            }

        policy = retry_svc.get_policy_by_rule(winner.get("规则编号", ""))
        need_review = (
            str(winner.get("恢复条件", "")) == CONDITION_MANUAL_REVIEW
            or (policy is not None and retry_svc.requires_manual_review(policy["id"]))
        )
        queue_status = STATUS_PENDING_REVIEW if need_review else STATUS_READY

        resolution = self._create(self._table_resolution, {
            "failure_key": failure_key,
            "清单编号": f"REC-{failure_key}",
            "所属模板": template_code,
            "异常特征": signature,
            "来源": source,
            "关联执行id": flow_run_id or "",
            "采用规则id": winner["id"],
            "采用规则编号": winner.get("规则编号", ""),
            "授权范围": winner.get("授权范围", ""),
            "恢复条件": winner.get("恢复条件", ""),
            "采集范围": winner.get("采集范围", ""),
            "关联策略id": policy["id"] if policy else "",
            "策略编号": policy.get("策略编号", "") if policy else "",
            "清单状态": queue_status,
            "已处置次数": "0",
            "入单时间": _now_iso(),
            "更新时间": _now_iso(),
        })

        # 全部有权采用的命中都留痕（含未采用的），原匹配历史可回查
        for rule in candidates:
            self._create(self._table_match, {
                "failure_key": failure_key,
                "所属模板": template_code,
                "规则id": rule["id"],
                "规则编号": rule.get("规则编号", ""),
                "授权范围": rule.get("授权范围", ""),
                "归属模板": rule.get("归属模板", ""),
                "生效优先级": rule.get("生效优先级", "0"),
                "是否采用": "是" if rule["id"] == winner["id"] else "否",
                "匹配时间": _now_iso(),
            })
            rule_svc.record_hit(rule["id"])

        return {
            "status": "resolved",
            "failure_key": failure_key,
            "resolution_id": resolution["id"],
            "resolution": resolution,
            "candidate_rule_ids": [r["id"] for r in candidates],
            "winner_rule_id": winner["id"],
        }

    # ── 人工复核 ──────────────────────────────────────────────

    def review_resolution(self, resolution_id: int, reviewer: str = "值班员",
                          approved: bool = True, remark: str = "") -> dict[str, Any]:
        """值班员复核：通过后清单转为可恢复；驳回则保持待复核并记录原因。"""
        resolution = self.get_resolution(resolution_id)
        if resolution is None:
            return {"ok": False, "reason": "恢复清单条目不存在"}
        if resolution.get("清单状态") != STATUS_PENDING_REVIEW:
            return {"ok": False, "reason": f"当前状态「{resolution.get('清单状态')}」无需复核"}
        if not approved:
            resolution["复核备注"] = f"驳回：{remark or '复核未通过'}"
            resolution["更新时间"] = _now_iso()
            self._write_audit(resolution, "恢复复核驳回", reviewer, remark)
            return {"ok": True, "resolution_id": resolution_id, "approved": False}
        resolution["清单状态"] = STATUS_READY
        resolution["复核人"] = reviewer
        resolution["复核时间"] = _now_iso()
        resolution["复核备注"] = remark
        resolution["更新时间"] = _now_iso()
        self._write_audit(resolution, "恢复复核通过", reviewer, remark)
        return {"ok": True, "resolution_id": resolution_id, "approved": True}

    # ── 派发：归属核验 + 回执 ─────────────────────────────────

    def dispatch_recovery(self, resolution_id: int, task_id: int,
                          node_code: str, *, reviewer: str = "值班员") -> dict[str, Any]:
        """按恢复清单把实际采集范围派发给节点，回执注明规则与执行号。"""
        resolution = self.get_resolution(resolution_id)
        if resolution is None:
            return {"ok": False, "reason": "恢复清单条目不存在"}

        from app.services import service_for

        queue_svc = service_for("task_queue")
        task = queue_svc.get_entry(task_id)
        if task is None:
            return {"ok": False, "reason": "排队任务不存在"}
        # 归属核验：队列任务必须属于失败所属模板，防止串位派发
        if task.get("关联模板") != resolution.get("所属模板"):
            return {
                "ok": False,
                "reason": (
                    f"归属核验失败：任务模板「{task.get('关联模板')}」与失败归属"
                    f"「{resolution.get('所属模板')}」不一致，拒绝派发"
                ),
            }

        # 清单状态闸门优先于任务状态，保证一次性处置兑现后给出明确的重复提示
        status = resolution.get("清单状态")
        if status == STATUS_PENDING_REVIEW:
            return {"ok": False, "reason": "该失败须人工复核通过后才能启动恢复", "need_review": True}
        if status == STATUS_DISPATCHED:
            return {"ok": False, "reason": "采用规则允许的处置已兑现，不能重复派发"}
        if status not in (STATUS_READY, STATUS_EXHAUSTED):
            return {"ok": False, "reason": f"清单状态「{status}」不可派发"}

        if task.get("status") != "待派发":
            return {"ok": False, "reason": f"任务当前状态「{task.get('status')}」不可派发"}

        policy_id = resolution.get("关联策略id")
        retry_svc = service_for("retry_policy")
        if policy_id == "" or policy_id is None:
            return {"ok": False, "reason": "采用规则未关联重试策略，无法兑现恢复"}

        # 真正兑现一次处置：只有采用规则自己的策略会被扣减
        attempt = retry_svc.attempt_recovery(
            int(policy_id),
            reviewed=(resolution.get("恢复条件") == CONDITION_MANUAL_REVIEW),
        )
        if not attempt.get("ok"):
            if attempt.get("abandoned"):
                resolution["清单状态"] = STATUS_EXHAUSTED
                resolution["更新时间"] = _now_iso()
            return {"ok": False, "reason": attempt.get("reason", "恢复执行失败"), **attempt}

        # 执行号：同一失败的第 N 次恢复执行
        used_count = int(resolution.get("已处置次数", "0") or "0") + 1
        execution_no = f"EXEC-{resolution['failure_key']}-{used_count}"
        scope_value = resolution.get("采集范围", "")

        # 任务领取实际采集范围并派发
        task["failure_key"] = resolution["failure_key"]
        task["采用规则编号"] = resolution.get("采用规则编号", "")
        task["恢复执行号"] = execution_no
        task["采集范围"] = scope_value
        dispatch_result = queue_svc.dispatch_to_node(task_id, node_code)
        if not dispatch_result.get("ok"):
            # 派发未成功：回退本次次数扣减，保证计数与真实执行一致
            policy_row = retry_svc.get_entry(int(policy_id))
            current = int(policy_row.get("累计重试", "0") or "0")
            policy_row["累计重试"] = str(max(0, current - 1))
            task["failure_key"] = ""
            task["采用规则编号"] = ""
            task["恢复执行号"] = ""
            task["采集范围"] = ""
            return {"ok": False, "reason": dispatch_result.get("reason", "任务派发失败")}

        receipt = (
            f"回执：执行{execution_no} 采用规则{resolution.get('采用规则编号')}"
            f"（{resolution.get('授权范围')}，条件：{resolution.get('恢复条件')}），"
            f"采集范围「{scope_value}」派发至节点{node_code}"
        )
        task["派发回执"] = receipt
        resolution["已处置次数"] = str(used_count)
        # 只能兑现被采用规则允许的处置：
        # 立即单字段补采为一次性处置，兑现即关闭；其余条件在次数用尽后关闭。
        one_shot = resolution.get("恢复条件") == "立即单字段补采"
        if one_shot or attempt.get("remaining", 0) <= 0:
            resolution["清单状态"] = STATUS_DISPATCHED
        resolution["更新时间"] = _now_iso()

        record = self._create(self._table_dispatch, {
            "failure_key": resolution["failure_key"],
            "所属模板": resolution.get("所属模板", ""),
            "队列id": task_id,
            "队列编号": task.get("队列编号", ""),
            "节点编号": node_code,
            "采用规则id": resolution.get("采用规则id", ""),
            "采用规则编号": resolution.get("采用规则编号", ""),
            "策略编号": resolution.get("策略编号", ""),
            "恢复执行号": execution_no,
            "采集范围": scope_value,
            "派发时间": _now_iso(),
            "派发回执": receipt,
        })
        self._write_audit(resolution, "恢复派发", reviewer, receipt)
        return {
            "ok": True,
            "resolution_id": resolution_id,
            "dispatch_id": record["id"],
            "execution_no": execution_no,
            "rule_code": resolution.get("采用规则编号"),
            "policy_code": resolution.get("策略编号"),
            "capture_scope": scope_value,
            "node": node_code,
            "remaining": attempt.get("remaining"),
            "receipt": receipt,
        }

    # ── 审计 ──────────────────────────────────────────────────

    def _write_audit(self, resolution: dict[str, Any], operation: str,
                     operator: str, summary: str) -> None:
        from app.services import service_for

        audit_svc = service_for("audit_trail")
        audit_svc.create_entry({
            "条目编号": f"AUDI-REC-{resolution['id']}-{operation}-{int(datetime.now().timestamp() * 1000) % 1000000}",
            "操作对象": f"恢复清单.{resolution.get('清单编号', '')}",
            "操作类型": operation,
            "操作者标识": operator,
            "操作时间": _now_iso(),
            "变更摘要": summary or f"{operation}：失败{resolution.get('failure_key')}",
            "关联执行": str(resolution.get("关联执行id", "")),
            "追溯标记": resolution["failure_key"],
        })
