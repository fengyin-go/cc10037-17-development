"""规则证据领域服务：质量门禁证据导出、异常核对文件导入与补偿范围编排。

上游：quality_rule 定义规则 → evidence_service 冻结执行证据
消费：exception_capture 登记可核验异常 → rerun_compensation 提交分区复跑
"""
from __future__ import annotations

from typing import Any

from app.engine.evidence_ledger import evidence_ledger
from app.engine.validation_executor import (
    CheckLevel,
    CheckType,
    QualityRule,
)


class EvidenceService:
    """路由层与证据台账之间的领域服务，负责入参组装与错误语义转换。"""

    def _build_rules(self, rule_payloads: list[dict[str, Any]]) -> list[QualityRule]:
        rules: list[QualityRule] = []
        for payload in rule_payloads:
            rules.append(QualityRule(
                rule_id=str(payload["rule_id"]),
                rule_name=str(payload.get("rule_name", payload["rule_id"])),
                target_table=str(payload.get("target_table", "")),
                check_field=str(payload["check_field"]),
                check_type=CheckType(payload["check_type"]),
                threshold=float(payload.get("threshold", 0.01)),
                level=CheckLevel(payload.get("level", "WARN")),
                extra=dict(payload.get("extra") or {}),
            ))
        return rules

    def run_gate(self, body: dict[str, Any]) -> dict[str, Any]:
        rules = self._build_rules(body.get("rules", []))
        return evidence_ledger.create_execution(
            pipeline_id=str(body["pipeline_id"]),
            target_table=str(body["target_table"]),
            partition=str(body["partition"]),
            rules=rules,
            sample_data=list(body.get("sample_data", [])),
            source_table=str(body.get("source_table", "")),
            stale_rule_ids=list(body.get("stale_rule_ids", [])),
            source_date=str(body.get("source_date", "")),
            note=str(body.get("note", "")),
        )

    def mark_stale(self, execution_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return evidence_ledger.mark_locator_stale(
            execution_id,
            rule_id=str(body["rule_id"]),
            reason=str(body.get("reason", "")),
        )

    def export_package(self, execution_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        body = body or {}
        return evidence_ledger.export_package(
            execution_id, remark=str(body.get("remark", "")),
        )

    def import_package(self, package: dict[str, Any]) -> dict[str, Any]:
        return evidence_ledger.import_package(package)

    def recovery_scope(self, execution_id: str) -> dict[str, Any]:
        return evidence_ledger.recovery_scope(execution_id)

    def submit_compensation(self, execution_id: str, body: dict[str, Any]) -> dict[str, Any]:
        partitions = body.get("partitions") or []
        if isinstance(partitions, str):
            partitions = [p.strip() for p in partitions.split(",") if p.strip()]
        return evidence_ledger.submit_compensation(
            execution_id, partitions=[str(p) for p in partitions],
            reason=str(body.get("reason", "")),
        )

    def reverify(self, execution_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return evidence_ledger.reverify(
            execution_id,
            sample_data=list(body.get("sample_data", [])),
            stale_rule_ids=body.get("stale_rule_ids"),
            note=str(body.get("note", "")),
        )


evidence_service = EvidenceService()
