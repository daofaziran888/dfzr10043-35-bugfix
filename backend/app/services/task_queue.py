"""任务队列服务：排队顺序管理、资源额度占用与派发回收。

维护待执行任务的排队顺序、优先级和资源额度占用情况，
派发失败时回收入队。支持优先级排序和等待秒数计算。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.base import BaseModuleService


class TaskQueueService(BaseModuleService):
    MODULE = "task_queue"
    ENTITY = "排队任务"
    OPERATION_TERM = "关联"
    LIST_FIELDS = [
        "队列编号", "关联模板", "优先级", "期望开始", "重试次数",
        "占用节点", "等待秒数", "入队时间",
        "failure_key", "采用规则编号", "恢复执行号", "采集范围", "派发回执",
    ]
    REQUIRED_FIELDS = ["队列编号", "关联模板", "优先级"]
    STATUS_ORDER = ["待派发", "已派发", "已消费", "已丢弃"]
    ACTION_RULES = {"派发任务": "已派发", "回收任务": "已消费", "丢弃任务": "已丢弃"}
    NEGATIVE_ACTIONS = []

    _PRIORITY_WEIGHTS = {"high": 3, "medium": 2, "low": 1}

    def get_pending_by_priority(self) -> list[dict[str, Any]]:
        """按优先级排序返回待派发任务。"""
        items, _ = self.list_entries(size=10000)
        pending = [it for it in items if it.get("status") == "待派发"]
        pending.sort(
            key=lambda t: self._PRIORITY_WEIGHTS.get(
                str(t.get("优先级", "low")).lower(), 1
            ),
            reverse=True,
        )
        return pending

    def recycle_failed_dispatches(self) -> dict[str, Any]:
        """回收派发失败的任务（已派发但被标记为待派发）。"""
        items, _ = self.list_entries(size=10000)
        recycled = 0
        for it in items:
            if it.get("status") == "已派发" and not it.get("占用节点"):
                self.run_action(it["id"], "回收任务")
                recycled += 1
        return {"ok": True, "recycled_count": recycled}

    def get_pending_by_template(self, template_code: str) -> list[dict[str, Any]]:
        """按归属模板返回待派发任务，供恢复清单核验后领取。"""
        pending = self.get_pending_by_priority()
        return [t for t in pending if t.get("关联模板") == template_code]

    def dispatch_to_node(self, task_id: int, node_code: str) -> dict[str, Any]:
        """将任务派发到指定节点。"""
        entry = self.get_entry(task_id)
        if entry is None:
            return {"ok": False, "reason": "任务不存在"}
        if entry.get("status") != "待派发":
            return {"ok": False, "reason": "任务状态不是待派发"}
        entry["占用节点"] = node_code
        self.run_action(task_id, "派发任务")
        return {"ok": True, "task_id": task_id, "node": node_code}