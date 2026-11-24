"""异常规则服务：识别特征匹配、判定条件评估与处置动作执行。

把流程运行中的失败分类转成可执行的动作（重试/抑制/废弃），
按生效优先级决定匹配顺序，高优先级规则优先命中。

归属修正：规则带「归属模板 + 授权范围（自有/共享）」，匹配采集失败时
必须先核验失败所属模板。自有规则只服务本模板，共享规则可服务任意模板，
同优先级下自有规则优先；一次失败最终只允许一条有权采用的规则胜出，
其余命中仅作为历史匹配留痕，不产生恢复权限。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.base import BaseModuleService

# 授权范围取值
SCOPE_OWNED = "自有"
SCOPE_SHARED = "共享"


class ExceptionRuleService(BaseModuleService):
    MODULE = "exception_rule"
    ENTITY = "处置规则"
    OPERATION_TERM = "校验"
    LIST_FIELDS = [
        "规则编号", "匹配特征", "判定条件", "处置动作", "适用范围",
        "生效优先级", "命中次数", "最近命中",
        "归属模板", "授权范围", "恢复条件", "采集范围",
    ]
    REQUIRED_FIELDS = ["规则编号", "匹配特征", "判定条件"]
    STATUS_ORDER = ["草稿", "已生效", "已抑制", "已废弃"]
    ACTION_RULES = {"启效规则": "已生效", "抑制规则": "已抑制", "废弃规则": "已废弃"}
    NEGATIVE_ACTIONS = []

    def _active_rules(self) -> list[dict[str, Any]]:
        """全部已生效规则。"""
        items, _ = self.list_entries(size=10000)
        return [r for r in items if r.get("status") == "已生效"]

    @staticmethod
    def _is_authorized(rule: dict[str, Any], template_code: str | None) -> bool:
        """核验规则对失败所属模板是否有权采用：自有规则必须同模板，共享规则不限。"""
        scope = str(rule.get("授权范围", "") or SCOPE_SHARED)
        owner = str(rule.get("归属模板", "") or "")
        if scope == SCOPE_OWNED:
            return bool(template_code) and owner == template_code
        # 共享规则：无归属模板或归属模板与失败模板一致都可采用
        return not owner or not template_code or owner == template_code

    @staticmethod
    def _match_rank(rule: dict[str, Any]) -> tuple[int, int]:
        """排序键：自有优先于共享，同级按生效优先级降序。"""
        scope_rank = 1 if str(rule.get("授权范围", "")) == SCOPE_OWNED else 0
        priority = int(rule.get("生效优先级", "0") or "0")
        return scope_rank, priority

    def find_matching_rules(
        self, signature: str, template_code: str | None = None,
        scope: str | None = None,
    ) -> list[dict[str, Any]]:
        """找出全部命中特征且对该模板有权采用的规则，按仲裁顺序排列。

        仲裁顺序：自有规则优先于共享规则，同授权范围内生效优先级高者优先。
        只做子串特征匹配与归属核验，不在这里决定最终采用哪条——最终结论
        由恢复清单（failure_resolution）唯一落定，保证一次失败只有一个规则结论。
        """
        candidates: list[dict[str, Any]] = []
        for rule in self._active_rules():
            feature = rule.get("匹配特征", "")
            if not feature or signature.find(feature) < 0:
                continue
            if scope and scope not in str(rule.get("适用范围", "")):
                continue
            if not self._is_authorized(rule, template_code):
                continue
            candidates.append(rule)
        candidates.sort(key=self._match_rank, reverse=True)
        return candidates

    def match_exception(
        self, signature: str, template_code: str | None = None,
    ) -> dict[str, Any] | None:
        """用异常特征匹配最优先且对失败模板有权采用的已生效规则。"""
        matched = self.find_matching_rules(signature, template_code)
        if not matched:
            return None
        return {"rule": matched[0], "matched": True, "candidates": matched}

    def match_for_failure(
        self, signature: str, template_code: str | None = None,
        scope: str | None = None,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """采集失败入口：返回（仲裁胜出规则, 全部有权采用的候选规则）。

        与旧 match_exception 的区别是把全部候选一并返回，供恢复清单留痕
        （原匹配历史可回查），但只有胜出规则可以产出恢复权限。
        """
        candidates = self.find_matching_rules(signature, template_code, scope)
        if not candidates:
            return None, []
        return candidates[0], candidates

    def record_hit(self, rule_id: int) -> dict[str, Any]:
        """记录规则命中并更新命中次数与最近命中时间。"""
        entry = self.get_entry(rule_id)
        if entry is None:
            return {"ok": False, "reason": "规则不存在"}
        hits = int(entry.get("命中次数", "0") or "0")
        entry["命中次数"] = str(hits + 1)
        entry["最近命中"] = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
        return {"ok": True, "rule_id": rule_id, "total_hits": hits + 1}

    def get_rules_by_scope(self, scope: str) -> list[dict[str, Any]]:
        """按适用范围筛选已生效规则（如定位、字段、超时等）。"""
        return [r for r in self._active_rules() if scope in str(r.get("适用范围", ""))]
