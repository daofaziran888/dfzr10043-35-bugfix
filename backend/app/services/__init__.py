"""领域服务注册表：通用资源路由按模块 key 分派到独立业务服务。"""
from __future__ import annotations

from app.services.base import BaseModuleService
from app.services.flow_template import FlowTemplateService
from app.services.element_locator import ElementLocatorService
from app.services.robot_node import RobotNodeService
from app.services.flow_run import FlowRunService
from app.services.step_trace import StepTraceService
from app.services.task_queue import TaskQueueService
from app.services.credential_vault import CredentialVaultService
from app.services.form_field import FormFieldService
from app.services.file_drop import FileDropService
from app.services.ocr_region import OcrRegionService
from app.services.schedule_plan import SchedulePlanService
from app.services.exception_rule import ExceptionRuleService
from app.services.retry_policy import RetryPolicyService
from app.services.capture_batch import CaptureBatchService
from app.services.field_mapping import FieldMappingService
from app.services.data_landing import DataLandingService
from app.services.audit_trail import AuditTrailService
from app.services.resource_pool import ResourcePoolService
from app.services.version_release import VersionReleaseService
from app.services.metric_board import MetricBoardService
from app.services.sample_check import SampleCheckService
from app.services.handoff_pack import HandoffPackService
from app.services.recovery import RecoveryService

SERVICES: dict[str, BaseModuleService | RecoveryService] = {
    "flow_template": FlowTemplateService(),
    "element_locator": ElementLocatorService(),
    "robot_node": RobotNodeService(),
    "flow_run": FlowRunService(),
    "step_trace": StepTraceService(),
    "task_queue": TaskQueueService(),
    "credential_vault": CredentialVaultService(),
    "form_field": FormFieldService(),
    "file_drop": FileDropService(),
    "ocr_region": OcrRegionService(),
    "schedule_plan": SchedulePlanService(),
    "exception_rule": ExceptionRuleService(),
    "retry_policy": RetryPolicyService(),
    "capture_batch": CaptureBatchService(),
    "field_mapping": FieldMappingService(),
    "data_landing": DataLandingService(),
    "audit_trail": AuditTrailService(),
    "resource_pool": ResourcePoolService(),
    "version_release": VersionReleaseService(),
    "metric_board": MetricBoardService(),
    "sample_check": SampleCheckService(),
    "handoff_pack": HandoffPackService(),
    "failure_resolution": RecoveryService(),
}


def service_for(module: str) -> BaseModuleService | None:
    return SERVICES.get(module)
