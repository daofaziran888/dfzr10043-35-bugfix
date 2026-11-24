"""重试策略服务：退避参数、放弃阈值与重试执行管理。

维护异常处置对应的重试策略，含退避基数、倍数和抖动区间，
达到放弃阈值后抑制并为下游写入审计轨迹。

归属修正：每条策略只服务其「关联规则」。采用共享规则不会扣减另一执行
（另一规则对应策略）的剩余次数；剩余次数 = 最大次数 - 本策略累计重试，
与调用方身份无关。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService

# 恢复条件取值
CONDITION_IMMEDIATE = "立即单字段补采"
CONDITION_MANUAL_REVIEW = "人工复核后恢复"
CONDITION_AUTO_RETRY = "自动重试"
# 需要人工复核通过后才允许执行恢复的条件
MANUAL_GATED_CONDITIONS = {CONDITION_MANUAL_REVIEW}


class RetryPolicyService(BaseModuleService):
    MODULE = "retry_policy"
    ENTITY = "重试策略"
    OPERATION_TERM = "关联"
    LIST_FIELDS = [
        "策略编号", "关联规则", "最大次数", "退避基数", "退避倍数",
        "抖动区间", "放弃阈值", "累计重试",
        "恢复条件", "采集范围",
    ]
    REQUIRED_FIELDS = ["策略编号", "关联规则", "最大次数"]
    STATUS_ORDER = ["草稿", "执行中", "已放弃", "已停用"]
    ACTION_RULES = {"启用策略": "执行中", "标记放弃": "已放弃", "停用策略": "已停用"}
    NEGATIVE_ACTIONS = ["停用策略"]

    def get_policies_for_rule(self, rule_code: str) -> list[dict[str, Any]]:
        """按规则编号找关联策略（只看本规则自己的策略，不跨规则借用）。"""
        items, _ = self.list_entries(size=10000)
        return [p for p in items if p.get("关联规则") == rule_code]

    def get_policy_by_rule(self, rule_code: str) -> dict[str, Any] | None:
        """取规则关联的第一条策略。"""
        policies = self.get_policies_for_rule(rule_code)
        return policies[0] if policies else None

    def remaining_attempts(self, policy_id: int) -> int:
        """剩余可处置次数 = 最大次数 - 累计重试，不为负。"""
        entry = self.get_entry(policy_id)
        if entry is None:
            return 0
        max_attempts = int(entry.get("最大次数", "3") or "3")
        current = int(entry.get("累计重试", "0") or "0")
        return max(0, max_attempts - current)

    def requires_manual_review(self, policy_id: int) -> bool:
        """该策略的恢复条件是否要求人工复核后再启动。"""
        entry = self.get_entry(policy_id)
        if entry is None:
            return False
        return str(entry.get("恢复条件", "")) in MANUAL_GATED_CONDITIONS

    def should_abandon(self, policy_id: int) -> bool:
        """判断重试是否应放弃：累计重试>=最大次数或达到放弃阈值。"""
        entry = self.get_entry(policy_id)
        if entry is None:
            return True
        max_attempts = int(entry.get("最大次数", "3") or "3")
        current = int(entry.get("累计重试", "0") or "0")
        threshold = int(entry.get("放弃阈值", str(max_attempts)) or str(max_attempts))
        return current >= threshold or current >= max_attempts

    def calculate_backoff(self, policy_id: int, attempt: int) -> int:
        """计算退避等待毫秒数，含指数退避与抖动。"""
        entry = self.get_entry(policy_id)
        if entry is None:
            return 1000
        base = int(entry.get("退避基数", "500") or "500")
        multiplier = int(entry.get("退避倍数", "2") or "2")
        jitter_range = entry.get("抖动区间", "200") or "200"
        jitter = int(jitter_range)
        import random
        return base * (multiplier ** (attempt - 1)) + random.randint(0, jitter)

    def increment_retry(self, policy_id: int) -> dict[str, Any]:
        """递增累计重试次数（仅作用于本策略，调用方无法借此扣减别的策略）。"""
        entry = self.get_entry(policy_id)
        if entry is None:
            return {"ok": False, "reason": "策略不存在"}
        current = int(entry.get("累计重试", "0") or "0")
        entry["累计重试"] = str(current + 1)
        return {"ok": True, "policy_id": policy_id, "total_retries": current + 1}

    def attempt_recovery(self, policy_id: int, *, reviewed: bool = False) -> dict[str, Any]:
        """按策略真正兑现一次恢复处置。

        - 人工复核条件未复核：拒绝执行，不扣次数；
        - 剩余次数为 0：标记放弃，不扣次数；
        - 其余情况：本策略累计重试 +1（只扣自己的剩余次数），返回剩余次数。
        """
        entry = self.get_entry(policy_id)
        if entry is None:
            return {"ok": False, "reason": "策略不存在"}
        if self.requires_manual_review(policy_id) and not reviewed:
            return {
                "ok": False, "reason": "恢复条件要求人工复核后再启动",
                "policy_id": policy_id, "need_review": True,
                "remaining": self.remaining_attempts(policy_id),
            }
        if self.remaining_attempts(policy_id) <= 0:
            self.run_action(policy_id, "标记放弃")
            return {
                "ok": False, "reason": "剩余处置次数为0，策略已放弃",
                "policy_id": policy_id, "abandoned": True, "remaining": 0,
            }
        before = self.remaining_attempts(policy_id)
        self.increment_retry(policy_id)
        self.run_action(policy_id, "启用策略")
        return {
            "ok": True, "policy_id": policy_id,
            "condition": entry.get("恢复条件", CONDITION_AUTO_RETRY),
            "remaining": before - 1,
            "total_retries": int(entry.get("累计重试", "0") or "0"),
        }
