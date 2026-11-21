"""规则证据台账：质量门禁执行证据的冻结、导出、导入核验与补偿范围识别。

领域约束（与异常核对文件需求一一对应）：
- 每次质量门禁执行冻结一份执行记录：规则快照（阈值/类型/级别）、逐规则结果、
  带链路定位的坏行样本、来源分区与来源日期。
- 异常核对文件只能引用该次执行的规则与样本；跨批次引用原始行一律拒绝。
- 样本定位分 ACTIVE（链路可达，可重算实际坏行）与 STALE（定位失效，等待证据补齐），
  失效时必须标明缺在哪一条规则，不能用其他批次的数据填补。
- 证据文件带凭证版本（evidence_version）与内容哈希（content_hash），重新核验
  生成新版本执行记录并回链原凭证，任何一次核验都能追到凭证版本。
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.engine.validation_executor import (
    CheckLevel,
    CheckType,
    QualityRule,
    ValidationEngine,
    ValidationResult,
)
from app.store import store

LOCATOR_ACTIVE = "ACTIVE"
LOCATOR_STALE = "STALE"

EVIDENCE_VERIFIABLE = "可核对"        # 规则失败且样本定位有效，可重算实际坏行
EVIDENCE_PENDING = "待补证"           # 规则失败但样本定位失效，等待证据补齐
EVIDENCE_PASSED = "无失败"            # 规则当轮通过，无需证据

PACKAGE_DOC_TYPE = "quality_exception_evidence"
PACKAGE_SCHEMA_VERSION = "1.0"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def stable_hash(payload: dict[str, Any]) -> str:
    """对证据内容做规范化哈希，导入时重新计算即可识别篡改与凭证版本。"""
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class RuleEvidence:
    """单条规则在一次执行中的证据视图。"""
    rule: QualityRule
    result: ValidationResult
    evidence_status: str
    locator_status: str
    missing_reason: str = ""
    partitions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule.rule_id,
            "rule_name": self.rule.rule_name,
            "target_table": self.rule.target_table,
            "check_field": self.rule.check_field,
            "check_type": self.rule.check_type.value,
            "threshold": self.rule.threshold,
            "level": self.rule.level.value,
            "rule_extra": dict(self.rule.extra),
            "total_rows": self.result.total_rows,
            "passed_rows": self.result.passed_rows,
            "failed_rows": self.result.failed_rows,
            "failed_ratio": round(self.result.failed_ratio, 6),
            "threshold_exceeded": self.result.threshold_exceeded,
            "blocked": self.result.blocked,
            "evidence_status": self.evidence_status,
            "locator_status": self.locator_status,
            "missing_reason": self.missing_reason,
            "partitions": list(self.partitions),
            "samples": copy.deepcopy(self.result.error_samples),
        }


class EvidenceLedger:
    """内存证据台账：执行记录、证据包版本、导入登记和补偿范围。"""

    def __init__(self, validation_engine: ValidationEngine | None = None) -> None:
        self.validation = validation_engine or ValidationEngine()
        self._executions: dict[str, dict[str, Any]] = {}
        self._packages: dict[str, dict[str, Any]] = {}
        # package_id -> 导入登记信息
        self._imports: dict[str, dict[str, Any]] = {}
        self._exec_seq = 0
        self._package_seq = 0
        self._import_seq = 0

    # ---- 血缘可达性 ----

    def _lineage_active(self, source_table: str, target_table: str) -> bool:
        """从血缘图谱登记判断 source→target 链路是否仍活跃。"""
        edges = store.rows("lineage_graph")
        for edge in edges:
            if edge.get("源节点") == source_table and edge.get("目标节点") == target_table:
                return str(edge.get("status")) == "活跃"
        # 未登记血缘边时视为链路不可追溯，定位按失效处理
        return False

    # ---- 质量门禁执行（冻结证据） ----

    def create_execution(
        self,
        *,
        pipeline_id: str,
        target_table: str,
        partition: str,
        rules: list[QualityRule],
        sample_data: list[dict[str, Any]],
        source_table: str = "",
        stale_rule_ids: list[str] | None = None,
        source_date: str = "",
        note: str = "",
        parent_execution_id: str = "",
        parent_package_id: str = "",
    ) -> dict[str, Any]:
        """执行一轮质量门禁并冻结执行证据。

        stale_rule_ids 显式声明本轮定位失效的规则；未声明的规则按血缘图谱
        的活跃状态自动判定，保证“失效样本定位”有明确来源。
        """
        self._exec_seq += 1
        execution_id = f"QE-{datetime.now().strftime('%Y%m%d')}-{self._exec_seq:03d}"
        stale_set = set(stale_rule_ids or [])
        source = source_table or target_table
        lineage_active = self._lineage_active(source, target_table)
        lineage_path = [source, target_table]

        evidences: list[RuleEvidence] = []
        rule_results: list[dict[str, Any]] = []
        for rule in rules:
            result = self.validation.run_with_locators(
                rule, sample_data, partition,
                source_table=source, lineage_path=lineage_path,
            )
            # 定位状态：显式失效优先，其次看血缘链路是否活跃
            if rule.rule_id in stale_set:
                locator_status = LOCATOR_STALE
                missing_reason = (
                    f"规则 {rule.rule_id}（{rule.rule_name}）的坏行样本定位已失效："
                    "链路定位被标记为不可达，无法回溯原始行"
                )
            elif not lineage_active:
                locator_status = LOCATOR_STALE
                missing_reason = (
                    f"规则 {rule.rule_id}（{rule.rule_name}）的坏行样本定位已失效："
                    f"血缘链路 {source} → {target_table} 非活跃状态"
                )
            else:
                locator_status = LOCATOR_ACTIVE
                missing_reason = ""
            result.locator_status = locator_status

            failed = result.failed_rows > 0
            if not failed:
                evidence_status = EVIDENCE_PASSED
            elif locator_status == LOCATOR_STALE:
                evidence_status = EVIDENCE_PENDING
            else:
                evidence_status = EVIDENCE_VERIFIABLE

            ev = RuleEvidence(
                rule=rule, result=result,
                evidence_status=evidence_status,
                locator_status=locator_status,
                missing_reason=missing_reason,
                partitions=[partition] if failed else [],
            )
            evidences.append(ev)
            rule_results.append(ev.to_dict())

        any_blocked = any(e.result.blocked for e in evidences)
        execution = {
            "execution_id": execution_id,
            "pipeline_id": pipeline_id,
            "target_table": target_table,
            "source_table": source,
            "partition": partition,
            "source_date": source_date or partition,
            "created_at": _now(),
            "overall_pass": not any_blocked,
            "decision": "RELEASE" if not any_blocked else "BLOCKED",
            "stale_rule_ids": sorted(stale_set),
            "lineage_active": lineage_active,
            "note": note,
            "parent_execution_id": parent_execution_id,
            "parent_package_id": parent_package_id,
            "revision_of": parent_execution_id,
            # 规则快照随执行冻结，后续复核只能用这份快照解释样本
            "rule_snapshot": [
                {
                    "rule_id": r.rule_id,
                    "rule_name": r.rule_name,
                    "target_table": r.target_table,
                    "check_field": r.check_field,
                    "check_type": r.check_type.value,
                    "threshold": r.threshold,
                    "level": r.level.value,
                    "extra": dict(r.extra),
                }
                for r in rules
            ],
            "rule_results": rule_results,
        }
        self._executions[execution_id] = execution
        return self.serialize_execution(execution_id)

    def list_executions(self) -> list[dict[str, Any]]:
        return [self.serialize_execution(eid) for eid in self._executions]

    def get_execution(self, execution_id: str) -> dict[str, Any] | None:
        if execution_id not in self._executions:
            return None
        return self.serialize_execution(execution_id)

    def mark_locator_stale(self, execution_id: str, rule_id: str, reason: str = "") -> dict[str, Any]:
        """将已冻结执行中的某条规则定位标记为失效（模拟链路失效场景）。"""
        execution = self._require_execution(execution_id)
        target = None
        for item in execution["rule_results"]:
            if item["rule_id"] == rule_id:
                target = item
                break
        if target is None:
            raise KeyError(f"执行 {execution_id} 中不存在规则 {rule_id}")
        target["locator_status"] = LOCATOR_STALE
        if target["failed_rows"] > 0:
            target["evidence_status"] = EVIDENCE_PENDING
        # 无论调用方是否给出原因，都必须标明缺在哪一条规则
        target["missing_reason"] = (
            f"规则 {rule_id}（{target['rule_name']}）的坏行样本定位已失效："
            + (reason or "原始行链路不可达，无法重算实际坏行数据")
        )
        for sample in target["samples"]:
            if "locator" in sample:
                sample["locator"]["locator_status"] = LOCATOR_STALE
        if rule_id not in execution["stale_rule_ids"]:
            execution["stale_rule_ids"].append(rule_id)
        return self.serialize_execution(execution_id)

    # ---- 异常核对文件（证据包） ----

    def export_package(self, execution_id: str, remark: str = "") -> dict[str, Any]:
        """为一次执行导出异常核对文件，同次执行重复导出产生新的凭证版本。"""
        execution = self._require_execution(execution_id)
        self._package_seq += 1
        package_id = f"EVF-{execution_id}-v{self._package_seq:02d}"
        version = self._next_version_for_execution(execution_id)

        body = {
            "doc_type": PACKAGE_DOC_TYPE,
            "schema_version": PACKAGE_SCHEMA_VERSION,
            "package_id": package_id,
            "evidence_version": version,
            "execution_id": execution_id,
            "pipeline_id": execution["pipeline_id"],
            "target_table": execution["target_table"],
            "source_table": execution["source_table"],
            "partition": execution["partition"],
            "source_date": execution["source_date"],
            "exported_at": _now(),
            "remark": remark,
            "overall_pass": execution["overall_pass"],
            "decision": execution["decision"],
            "rule_snapshot": copy.deepcopy(execution["rule_snapshot"]),
            "rule_evidence": copy.deepcopy(execution["rule_results"]),
        }
        package = dict(body)
        package["content_hash"] = stable_hash(body)
        package["summary"] = self._package_summary(package)
        self._packages[package_id] = package
        return copy.deepcopy(package)

    def list_packages(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(p) for p in self._packages.values()]

    def get_package(self, package_id: str) -> dict[str, Any] | None:
        package = self._packages.get(package_id)
        return copy.deepcopy(package) if package else None

    def import_package(self, package: dict[str, Any]) -> dict[str, Any]:
        """异常捕获接收异常核对文件：核验后按规则×分区登记异常数据。

        核验项：文档类型、凭证哈希、执行批次是否存在、证据规则是否属于该批次、
        是否重复导入。任一失败都拒绝整包，不做部分登记。
        """
        if not isinstance(package, dict):
            raise ValueError("核对文件格式不是合法 JSON 对象")
        if package.get("doc_type") != PACKAGE_DOC_TYPE:
            raise ValueError("文档类型不是异常核对文件（quality_exception_evidence）")
        execution_id = str(package.get("execution_id", ""))
        execution = self._executions.get(execution_id)
        if execution is None:
            raise ValueError(f"执行批次 {execution_id} 不存在，不能跨批次引用规则与样本")

        claimed_hash = package.get("content_hash")
        # 与导出端保持一致：content_hash 与 summary 不参与哈希计算
        body = {k: v for k, v in package.items() if k not in ("content_hash", "summary")}
        if not claimed_hash or stable_hash(body) != claimed_hash:
            raise ValueError("凭证哈希校验失败：核对文件内容与导出时不一致")

        package_id = str(package.get("package_id", ""))
        if package_id in self._imports:
            raise ValueError(f"核对文件 {package_id} 已导入，不能重复登记")

        snapshot_ids = {r["rule_id"] for r in execution["rule_snapshot"]}
        for item in package.get("rule_evidence", []):
            rid = item.get("rule_id")
            if rid not in snapshot_ids:
                raise ValueError(
                    f"规则 {rid} 不属于执行批次 {execution_id}，"
                    "单份报告只能引用该次执行的规则与样本"
                )
            # 样本的定位必须来自本批次：locator_id 前缀即规则+分区，无法伪造跨批次行
            for sample in item.get("samples", []):
                locator = sample.get("locator") or {}
                if not str(locator.get("locator_id", "")).startswith(f"{rid}-"):
                    raise ValueError(f"规则 {rid} 存在非本批次样本定位，拒绝跨批次取原始行")

        self._import_seq += 1
        import_id = f"EVI-{self._import_seq:04d}"
        registered = self._register_exceptions(package, import_id)
        record = {
            "import_id": import_id,
            "package_id": package_id,
            "evidence_version": package.get("evidence_version"),
            "content_hash": claimed_hash,
            "execution_id": execution_id,
            "pipeline_id": package.get("pipeline_id"),
            "target_table": package.get("target_table"),
            "partition": package.get("partition"),
            "source_date": package.get("source_date"),
            "imported_at": _now(),
            "registered_exception_ids": registered,
            "verifiable_rules": [
                item["rule_id"] for item in package["rule_evidence"]
                if item.get("evidence_status") == EVIDENCE_VERIFIABLE
            ],
            "pending_rules": [
                {
                    "rule_id": item["rule_id"],
                    "rule_name": item["rule_name"],
                    "missing_reason": item.get("missing_reason", ""),
                }
                for item in package["rule_evidence"]
                if item.get("evidence_status") == EVIDENCE_PENDING
            ],
        }
        self._imports[package_id] = record
        return copy.deepcopy(record)

    def list_imports(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(r) for r in self._imports.values()]

    # ---- 恢复入口：可提交的补偿范围 ----

    def recovery_scope(self, execution_id: str) -> dict[str, Any]:
        """识别恢复入口可提交的补偿分区。

        只有证据可核对（定位有效）的规则所覆盖分区可以复跑；
        待补证规则的分区单独列出并标明缺在哪条规则，不进入可补偿范围。
        """
        execution = self._require_execution(execution_id)
        rerunnable: dict[str, list[str]] = {}
        blocked: list[dict[str, Any]] = []
        for item in execution["rule_results"]:
            if item["evidence_status"] == EVIDENCE_VERIFIABLE:
                for part in item["partitions"]:
                    rerunnable.setdefault(part, []).append(item["rule_id"])
            elif item["evidence_status"] == EVIDENCE_PENDING:
                blocked.append({
                    "partition": execution["partition"],
                    "rule_id": item["rule_id"],
                    "rule_name": item["rule_name"],
                    "missing_reason": item["missing_reason"],
                })
        return {
            "execution_id": execution_id,
            "package_ids": [
                pid for pid, imp in self._imports.items()
                if imp["execution_id"] == execution_id
            ],
            "rerunnable_partitions": [
                {"partition": part, "evidence_rules": sorted(rids)}
                for part, rids in sorted(rerunnable.items())
            ],
            "blocked_partitions": blocked,
            "can_submit_compensation": bool(rerunnable),
        }

    def submit_compensation(
        self, execution_id: str, partitions: list[str], reason: str = ""
    ) -> dict[str, Any]:
        """通过恢复入口提交补偿：仅接受证据可核对的分区，并回写回刷补偿登记。"""
        scope = self.recovery_scope(execution_id)
        allowed = {item["partition"] for item in scope["rerunnable_partitions"]}
        requested = list(dict.fromkeys(partitions))
        illegal = [p for p in requested if p not in allowed]
        if illegal:
            raise ValueError(
                f"分区 {illegal} 缺少可核对证据（定位失效或未登记），"
                "等待证据补齐后才能提交补偿"
            )
        if not requested:
            raise ValueError("未指定补偿分区")

        execution = self._require_execution(execution_id)
        from app.services.rerun_compensation import RerunCompensationService

        entry = RerunCompensationService().register_evidence_compensation(
            pipeline_id=execution["pipeline_id"],
            execution_id=execution_id,
            partitions=requested,
            reason=reason,
        )
        return {
            "compensation_id": entry["id"],
            "task_name": entry["任务名称"],
            "execution_id": execution_id,
            "partitions": requested,
            "status": "待执行",
        }

    # ---- 恢复后重新核验（凭证版本可追溯） ----

    def reverify(
        self,
        execution_id: str,
        *,
        sample_data: list[dict[str, Any]],
        stale_rule_ids: list[str] | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """恢复后重新核验：沿用原批次冻结的规则快照，生成新版本执行记录。

        新复核与旧包并存时，规则与样本仍以各自执行批次为准——这里显式从
        原执行的 rule_snapshot 重建规则，不读取当前 quality_rule 表，
        因此不会拿当前规则解释过去失败的样本。
        """
        original = self._require_execution(execution_id)
        package_id = ""
        for pid, imp in self._imports.items():
            if imp["execution_id"] == execution_id:
                package_id = pid
                break
        rules = [
            QualityRule(
                rule_id=snap["rule_id"],
                rule_name=snap["rule_name"],
                target_table=snap["target_table"],
                check_field=snap["check_field"],
                check_type=CheckType(snap["check_type"]),
                threshold=float(snap["threshold"]),
                level=CheckLevel(snap["level"]),
                extra=dict(snap.get("extra") or {}),
            )
            for snap in original["rule_snapshot"]
        ]
        return self.create_execution(
            pipeline_id=original["pipeline_id"],
            target_table=original["target_table"],
            partition=original["partition"],
            rules=rules,
            sample_data=sample_data,
            source_table=original["source_table"],
            stale_rule_ids=stale_rule_ids if stale_rule_ids is not None else list(original["stale_rule_ids"]),
            source_date=original["source_date"],
            note=note or f"基于凭证批次 {execution_id} 的恢复后复核",
            parent_execution_id=execution_id,
            parent_package_id=package_id,
        )

    # ---- 内部工具 ----

    def _register_exceptions(self, package: dict[str, Any], import_id: str) -> list[int]:
        # 延迟导入：engine 层不反向依赖 services 模块的加载顺序
        from app.services.exception_capture import ExceptionCaptureService

        exception_service = ExceptionCaptureService()
        registered: list[int] = []
        for item in package.get("rule_evidence", []):
            if item.get("evidence_status") == EVIDENCE_PASSED:
                continue
            entry = exception_service.register_evidence_exception(
                pipeline_id=str(package.get("pipeline_id", "")),
                partition=str(package.get("partition", "")),
                package_id=str(package.get("package_id", "")),
                evidence_version=str(package.get("evidence_version", "")),
                execution_id=str(package.get("execution_id", "")),
                import_id=import_id,
                rule_item=item,
            )
            registered.append(int(entry["id"]))
        return registered

    @staticmethod
    def _package_summary(package: dict[str, Any]) -> dict[str, Any]:
        items = package.get("rule_evidence", [])
        return {
            "failed_rule_count": sum(1 for i in items if i["evidence_status"] != EVIDENCE_PASSED),
            "verifiable_rule_count": sum(1 for i in items if i["evidence_status"] == EVIDENCE_VERIFIABLE),
            "pending_rule_count": sum(1 for i in items if i["evidence_status"] == EVIDENCE_PENDING),
            "sample_count": sum(len(i.get("samples", [])) for i in items),
        }

    def _next_version_for_execution(self, execution_id: str) -> str:
        versions = [
            p["evidence_version"] for p in self._packages.values()
            if p["execution_id"] == execution_id
        ]
        return f"v{len(versions) + 1:02d}"

    def _require_execution(self, execution_id: str) -> dict[str, Any]:
        execution = self._executions.get(execution_id)
        if execution is None:
            raise KeyError(f"执行批次 {execution_id} 不存在")
        return execution

    def serialize_execution(self, execution_id: str) -> dict[str, Any]:
        """输出执行记录视图（含逐规则证据状态与分区）。"""
        execution = self._executions[execution_id]
        verifiable = [r["rule_id"] for r in execution["rule_results"]
                      if r["evidence_status"] == EVIDENCE_VERIFIABLE]
        pending = [
            {"rule_id": r["rule_id"], "rule_name": r["rule_name"],
             "missing_reason": r["missing_reason"]}
            for r in execution["rule_results"] if r["evidence_status"] == EVIDENCE_PENDING
        ]
        return {
            "execution_id": execution["execution_id"],
            "pipeline_id": execution["pipeline_id"],
            "target_table": execution["target_table"],
            "source_table": execution["source_table"],
            "partition": execution["partition"],
            "source_date": execution["source_date"],
            "created_at": execution["created_at"],
            "overall_pass": execution["overall_pass"],
            "decision": execution["decision"],
            "lineage_active": execution["lineage_active"],
            "stale_rule_ids": list(execution["stale_rule_ids"]),
            "parent_execution_id": execution["parent_execution_id"],
            "parent_package_id": execution["parent_package_id"],
            "revision_of": execution["revision_of"],
            "note": execution["note"],
            "verifiable_rules": verifiable,
            "pending_rules": pending,
            "rule_snapshot": copy.deepcopy(execution["rule_snapshot"]),
            "rule_results": copy.deepcopy(execution["rule_results"]),
        }


# 全局单例，路由层共享同一份证据台账
evidence_ledger = EvidenceLedger()
