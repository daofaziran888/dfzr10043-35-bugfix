"""采集失败归属修复的端到端测试。

覆盖需求场景：
- A、B 同时触发同名定位失败，A 自有规则（立即单字段补采）与共享规则
  （人工复核后恢复）同时匹配，仲裁只能产出一个有权采用的结论；
- 恢复清单明确区分「可立即单字段补采」与「待复核」；
- 队列按归属核验领取实际采集范围，回执注明规则与恢复执行号；
- 只能兑现被采用规则允许的一次处置，B 的专用重试保持原有资格；
- 采用共享规则不扣减另一执行的剩余次数，原匹配/执行历史可回查。
"""
from __future__ import annotations

import pytest

from app.pipeline import detect_element_drift
from app.services import service_for
from app.services.recovery import (
    RecoveryService,
    STATUS_DISPATCHED,
    STATUS_PENDING_REVIEW,
    STATUS_READY,
    build_failure_key,
)

SIG = "元素定位漂移:ORDER_LIST:drift_x"


@pytest.fixture()
def recovery():
    store_reset()
    return RecoveryService()


def store_reset():
    from app.store import store
    store.reset()


def _rule_codes(rules):
    return {r["规则编号"] for r in rules}


class TestOwnershipArbitration:
    """归属核验与规则仲裁：一次失败只能有一个有权采用的结论。"""

    def test_template_a_owned_rule_wins_over_shared(self, recovery):
        result = recovery.resolve_failure(SIG, "TPL-A", flow_run_id=4)
        assert result["status"] == "resolved"
        resolution = recovery.get_resolution(result["resolution_id"])
        # A 自有规则胜出，共享规则只留痕不产生恢复权限
        assert resolution["采用规则编号"] == "EXCE-A-OWN"
        assert resolution["授权范围"] == "自有"
        assert resolution["恢复条件"] == "立即单字段补采"
        assert resolution["清单状态"] == STATUS_READY

    def test_template_b_uses_its_own_rule_and_keeps_quota(self, recovery):
        result = recovery.resolve_failure(SIG, "TPL-B", flow_run_id=5)
        resolution = recovery.get_resolution(result["resolution_id"])
        assert resolution["采用规则编号"] == "EXCE-B-OWN"
        assert resolution["授权范围"] == "自有"
        # B 同名失败是另一条失败，不与 A 串位
        assert resolution["failure_key"] != build_failure_key("TPL-A", SIG)

    def test_same_named_failure_under_two_templates_are_distinct(self, recovery):
        a = recovery.resolve_failure(SIG, "TPL-A")
        b = recovery.resolve_failure(SIG, "TPL-B")
        assert a["failure_key"] != b["failure_key"]
        assert recovery.find_resolution(a["failure_key"])["采用规则编号"] == "EXCE-A-OWN"
        assert recovery.find_resolution(b["failure_key"])["采用规则编号"] == "EXCE-B-OWN"

    def test_owned_rule_from_other_template_is_not_authorized(self):
        store_reset()
        rule_svc = service_for("exception_rule")
        # TPL-B 的自有规则对 TPL-A 失败无权采用
        winners = rule_svc.find_matching_rules(SIG, "TPL-A", "定位")
        codes = _rule_codes(winners)
        assert "EXCE-B-OWN" not in codes
        assert "EXCE-A-OWN" in codes

    def test_all_matching_rules_leave_history_but_only_one_adopted(self, recovery):
        result = recovery.resolve_failure(SIG, "TPL-A")
        history = recovery.list_match_history(result["failure_key"])
        # 自有 A 规则 + 共享规则同时匹配，两条都留痕
        codes = {h["规则编号"] for h in history}
        assert {"EXCE-A-OWN", "EXCE-SHARED-LOC"} <= codes
        adopted = [h for h in history if h["是否采用"] == "是"]
        assert len(adopted) == 1
        assert adopted[0]["规则编号"] == "EXCE-A-OWN"

    def test_resolve_is_idempotent_no_double_arbitration(self, recovery):
        first = recovery.resolve_failure(SIG, "TPL-A")
        history_before = recovery.list_match_history(first["failure_key"])
        second = recovery.resolve_failure(SIG, "TPL-A")
        assert second["status"] == "existing"
        assert first["resolution_id"] == second["resolution_id"]
        history_after = recovery.list_match_history(first["failure_key"])
        # 重复上报不产生二次仲裁、不重复留痕，且始终只有一条被采用
        assert len(history_after) == len(history_before)
        assert sum(1 for h in history_after if h["是否采用"] == "是") == 1


class TestManualReviewGating:
    """共享规则要求人工复核：值班员必须看到待复核，复核前不能派发。"""

    def test_shared_rule_failure_enters_pending_review(self, recovery):
        # 没有自有规则的模板，共享规则胜出并进入待复核
        result = recovery.resolve_failure(SIG, "TPL-C")
        resolution = recovery.get_resolution(result["resolution_id"])
        assert resolution["采用规则编号"] == "EXCE-SHARED-LOC"
        assert resolution["清单状态"] == STATUS_PENDING_REVIEW
        assert resolution["恢复条件"] == "人工复核后恢复"

    def test_dispatch_blocked_before_review(self, recovery):
        result = recovery.resolve_failure(SIG, "TPL-C")
        rid = result["resolution_id"]
        # TPL-C 没有队列任务，先借一条待派发任务并改归属以聚焦复核闸门
        queue_svc = service_for("task_queue")
        task = queue_svc.get_pending_by_priority()[0]
        task["关联模板"] = "TPL-C"
        blocked = recovery.dispatch_recovery(rid, task["id"], "ROBO-0002")
        assert blocked["ok"] is False
        assert blocked.get("need_review") is True
        # 未复核不扣任何次数
        policy = service_for("retry_policy").get_policy_by_rule("EXCE-SHARED-LOC")
        assert policy["累计重试"] == "0"

    def test_review_then_dispatch_allowed(self, recovery):
        result = recovery.resolve_failure(SIG, "TPL-C")
        rid = result["resolution_id"]
        review = recovery.review_resolution(rid, reviewer="值班员甲", approved=True,
                                            remark="漂移可恢复")
        assert review["ok"] is True
        assert recovery.get_resolution(rid)["清单状态"] == STATUS_READY


class TestDispatchReceiptAndScope:
    """队列按归属核验领取采集范围，回执说明规则与执行号。"""

    def test_dispatch_uses_resolution_scope_and_writes_receipt(self, recovery):
        result = recovery.resolve_failure(SIG, "TPL-A")
        rid = result["resolution_id"]
        queue_svc = service_for("task_queue")
        task = next(t for t in queue_svc.get_pending_by_template("TPL-A"))
        dispatched = recovery.dispatch_recovery(rid, task["id"], "ROBO-0002")
        assert dispatched["ok"] is True
        assert dispatched["rule_code"] == "EXCE-A-OWN"
        assert dispatched["capture_scope"] == "单字段:order_list"
        assert dispatched["execution_no"].startswith("EXEC-")
        # 任务上留存归属与回执
        updated = queue_svc.get_entry(task["id"])
        assert updated["status"] == "已派发"
        assert updated["采用规则编号"] == "EXCE-A-OWN"
        assert updated["采集范围"] == "单字段:order_list"
        assert "EXCE-A-OWN" in updated["派发回执"]
        assert updated["恢复执行号"] == dispatched["execution_no"]

    def test_cross_template_task_rejected_by_ownership_check(self, recovery):
        result_a = recovery.resolve_failure(SIG, "TPL-A")
        queue_svc = service_for("task_queue")
        task_b = next(t for t in queue_svc.get_pending_by_template("TPL-B"))
        blocked = recovery.dispatch_recovery(result_a["resolution_id"],
                                             task_b["id"], "ROBO-0002")
        assert blocked["ok"] is False
        assert "归属核验失败" in blocked["reason"]
        # B 的任务保持待派发，未被串位消费
        assert queue_svc.get_entry(task_b["id"])["status"] == "待派发"


class TestSingleDisposalAndQuotaIsolation:
    """只能兑现被采用规则允许的处置；共享规则不扣另一执行的剩余次数。"""

    def test_owned_single_field_capture_only_once(self, recovery):
        result = recovery.resolve_failure(SIG, "TPL-A")
        rid = result["resolution_id"]
        queue_svc = service_for("task_queue")
        task = next(t for t in queue_svc.get_pending_by_template("TPL-A"))
        first = recovery.dispatch_recovery(rid, task["id"], "ROBO-0002")
        assert first["ok"] is True
        # 一次性处置兑现后清单关闭，重复派发被拒
        again = recovery.dispatch_recovery(rid, task["id"], "ROBO-0002")
        assert again["ok"] is False
        assert "不能重复派发" in again["reason"]
        assert recovery.get_resolution(rid)["清单状态"] == STATUS_DISPATCHED
        # A 自有策略最大 1 次，已耗尽
        policy_a = service_for("retry_policy").get_policy_by_rule("EXCE-A-OWN")
        assert policy_a["累计重试"] == "1"

    def test_using_shared_rule_does_not_touch_other_execution_quota(self, recovery):
        retry_svc = service_for("retry_policy")
        # B 的专用策略在 A 走共享规则之前的剩余资格
        policy_b_before = retry_svc.get_policy_by_rule("EXCE-B-OWN")
        remaining_b_before = retry_svc.remaining_attempts(policy_b_before["id"])
        assert remaining_b_before == 2  # 最大3，种子已重试1

        # 构造只能由共享规则接管的失败，复核后派发一次
        shared = recovery.resolve_failure(SIG, "TPL-C")
        rid = shared["resolution_id"]
        recovery.review_resolution(rid)
        queue_svc = service_for("task_queue")
        task = queue_svc.get_pending_by_priority()[0]
        task["关联模板"] = "TPL-C"
        dispatched = recovery.dispatch_recovery(rid, task["id"], "ROBO-0002")
        assert dispatched["ok"] is True
        assert dispatched["rule_code"] == "EXCE-SHARED-LOC"

        # 共享策略只扣自己的次数，B 执行的剩余资格原封不动
        policy_b_after = retry_svc.get_policy_by_rule("EXCE-B-OWN")
        assert retry_svc.remaining_attempts(policy_b_after["id"]) == remaining_b_before
        assert policy_b_after["累计重试"] == "1"
        shared_policy = retry_svc.get_policy_by_rule("EXCE-SHARED-LOC")
        assert shared_policy["累计重试"] == "1"

    def test_template_b_retry_retains_original_eligibility(self, recovery):
        # B 走自己的规则，种子已重试 1 次、最大 3 次，仍可兑现 2 次
        result = recovery.resolve_failure(SIG, "TPL-B")
        rid = result["resolution_id"]
        retry_svc = service_for("retry_policy")
        policy_b = retry_svc.get_policy_by_rule("EXCE-B-OWN")
        assert retry_svc.remaining_attempts(policy_b["id"]) == 2
        queue_svc = service_for("task_queue")
        task = next(t for t in queue_svc.get_pending_by_template("TPL-B"))
        dispatched = recovery.dispatch_recovery(rid, task["id"], "ROBO-0002")
        assert dispatched["ok"] is True
        assert dispatched["rule_code"] == "EXCE-B-OWN"
        assert dispatched["remaining"] == 1


class TestHistoryTraceability:
    """原匹配与执行历史均可回查。"""

    def test_dispatch_history_records_rule_and_execution(self, recovery):
        result = recovery.resolve_failure(SIG, "TPL-A")
        rid = result["resolution_id"]
        queue_svc = service_for("task_queue")
        task = next(t for t in queue_svc.get_pending_by_template("TPL-A"))
        dispatched = recovery.dispatch_recovery(rid, task["id"], "ROBO-0002")
        history = recovery.list_dispatch_history(result["failure_key"])
        assert len(history) == 1
        record = history[0]
        assert record["采用规则编号"] == "EXCE-A-OWN"
        assert record["恢复执行号"] == dispatched["execution_no"]
        assert record["采集范围"] == "单字段:order_list"
        assert record["节点编号"] == "ROBO-0002"

    def test_pipeline_drift_writes_resolution_with_ownership(self):
        store_reset()
        # 定位 4 属于 TPL-A，执行 4 关联 TPL-A
        result = detect_element_drift(4, 4)
        assert result["drifted"] is True
        assert result["template_code"] == "TPL-A"
        assert result["winner_rule_code"] == "EXCE-A-OWN"
        recovery = RecoveryService()
        resolution = recovery.find_resolution(result["failure_key"])
        assert resolution is not None
        assert resolution["采用规则编号"] == "EXCE-A-OWN"
        assert resolution["清单状态"] == STATUS_READY

    def test_pipeline_drift_for_template_b_picks_b_rule(self):
        store_reset()
        result = detect_element_drift(5, 5)
        assert result["winner_rule_code"] == "EXCE-B-OWN"
        assert result["template_code"] == "TPL-B"

    def test_pipeline_drift_enqueues_without_consuming_quota(self):
        store_reset()
        retry_svc = service_for("retry_policy")
        policy_a = retry_svc.get_policy_by_rule("EXCE-A-OWN")
        assert retry_svc.remaining_attempts(policy_a["id"]) == 1
        result = detect_element_drift(4, 4)
        assert result["status"] == "queued"
        # 漂移检测只入清单，处置尚未兑现，次数不被扣减
        assert retry_svc.remaining_attempts(policy_a["id"]) == 1
        # 值班员按清单派发时才兑现唯一一次处置
        recovery = RecoveryService()
        dispatched = recovery.dispatch_recovery(result["resolution_id"], 4, "ROBO-0002")
        assert dispatched["ok"] is True
        assert dispatched["remaining"] == 0
        assert retry_svc.remaining_attempts(policy_a["id"]) == 0


class TestRetryPolicyBinding:
    """重试策略页面对规则与恢复条件的归属修正。"""

    def test_policy_lookup_strictly_by_rule_code(self):
        store_reset()
        retry_svc = service_for("retry_policy")
        assert retry_svc.get_policy_by_rule("EXCE-A-OWN")["策略编号"] == "RETR-A-OWN"
        assert retry_svc.get_policy_by_rule("EXCE-SHARED-LOC")["策略编号"] == "RETR-SHARED-LOC"
        assert retry_svc.get_policy_by_rule("NOT-EXIST") is None

    def test_manual_review_flag_driven_by_policy_condition(self):
        store_reset()
        retry_svc = service_for("retry_policy")
        shared = retry_svc.get_policy_by_rule("EXCE-SHARED-LOC")
        owned = retry_svc.get_policy_by_rule("EXCE-A-OWN")
        assert retry_svc.requires_manual_review(shared["id"]) is True
        assert retry_svc.requires_manual_review(owned["id"]) is False

    def test_attempt_without_review_does_not_consume_quota(self):
        store_reset()
        retry_svc = service_for("retry_policy")
        shared = retry_svc.get_policy_by_rule("EXCE-SHARED-LOC")
        before = retry_svc.remaining_attempts(shared["id"])
        blocked = retry_svc.attempt_recovery(shared["id"], reviewed=False)
        assert blocked["ok"] is False
        assert blocked["need_review"] is True
        assert retry_svc.remaining_attempts(shared["id"]) == before
