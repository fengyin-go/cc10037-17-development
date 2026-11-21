"""异常捕获领域服务：管理管线执行异常记录、分类统计和恢复建议。

上游：所有管线执行模块产出异常 → exception_capture 统一采集
消费：alert_policy 消费异常数据触发告警策略
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class ExceptionCaptureService(BaseModuleService):
    MODULE = "exception_capture"
    ENTITY = "异常记录"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["发生时间", "所属管线", "异常类型", "异常消息", "影响分区", "栈跟踪", "处理状态"]
    REQUIRED_FIELDS = ["发生时间", "所属管线", "异常类型"]
    STATUS_ORDER = ["未处理", "处理中", "已解决", "已忽略"]
    ACTION_RULES = {"开始处理": "未处理", "标记解决": "处理中", "忽略异常": "未处理"}
    NEGATIVE_ACTIONS = ["忽略异常"]

    def query_by_pipeline(self, pipeline_name: str) -> list[dict[str, Any]]:
        """按所属管线查询异常记录。

        从 store 中筛选指定管线的异常数据，
        返回异常列表供排障和回溯分析消费。
        """
        result: list[dict[str, Any]] = []
        for row in store.rows(self.MODULE):
            if pipeline_name.casefold() in str(row.get("所属管线", "")).casefold():
                result.append(dict(row))
        return result

    def classify_exceptions(self) -> dict[str, Any]:
        """分类统计异常记录。

        按异常类型和处理状态统计异常分布，
        产出异常概览数据供运维看板消费。
        """
        rows = store.rows(self.MODULE)
        by_type: dict[str, int] = {}
        by_status: dict[str, int] = {}
        by_pipeline: dict[str, int] = {}
        unresolved = 0

        for row in rows:
            ex_type = str(row.get("异常类型", "未知"))
            status = str(row.get("处理状态", "未处理"))
            pipeline = str(row.get("所属管线", "未知"))

            by_type[ex_type] = by_type.get(ex_type, 0) + 1
            by_status[status] = by_status.get(status, 0) + 1
            by_pipeline[pipeline] = by_pipeline.get(pipeline, 0) + 1
            if status in ("未处理", "处理中"):
                unresolved += 1

        return {
            "total_exceptions": len(rows),
            "unresolved": unresolved,
            "resolved": len(rows) - unresolved,
            "by_type": by_type,
            "by_status": by_status,
            "by_pipeline": by_pipeline,
            "snapshot_at": datetime.now().isoformat(),
        }

    def detect_recurring(self, window_hours: int = 24) -> list[dict[str, Any]]:
        """检测重复发生的异常模式。

        在指定时间窗口内查找同一管线的重复异常，
        识别根因问题并提供恢复建议。
        """
        rows = store.rows(self.MODULE)
        now = datetime.now()
        pipeline_counts: dict[str, int] = {}
        pipeline_latest: dict[str, str] = {}

        for row in rows:
            pipeline = str(row.get("所属管线", ""))
            try:
                ex_time = datetime.fromisoformat(
                    str(row.get("发生时间", "")).replace("Z", "+00:00")
                )
            except (ValueError, TypeError):
                continue
            if (now - ex_time).total_seconds() / 3600 <= window_hours:
                pipeline_counts[pipeline] = pipeline_counts.get(pipeline, 0) + 1
                if pipeline not in pipeline_latest:
                    pipeline_latest[pipeline] = str(row.get("异常消息", ""))

        recurring = []
        for pipeline, count in pipeline_counts.items():
            if count >= 3:
                recurring.append({
                    "pipeline": pipeline,
                    "occurrences": count,
                    "latest_message": pipeline_latest.get(pipeline, ""),
                    "severity": "高" if count >= 5 else "中",
                    "recommendation": "建议检查数据源稳定性和处理逻辑",
                })

        return recurring

    def register_evidence_exception(
        self,
        *,
        pipeline_id: str,
        partition: str,
        package_id: str,
        evidence_version: str,
        execution_id: str,
        import_id: str,
        rule_item: dict[str, Any],
    ) -> dict[str, Any]:
        """按可验证的规则与分区登记一条质量门禁异常。

        证据状态为「待补证」的规则同样登记，但消息中明确样本定位失效、
        暂不可复跑；登记记录回链凭证编号与版本，恢复后复核仍可追溯。
        """
        rows = store.rows(self.MODULE)
        new_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        pending = rule_item.get("evidence_status") == "待补证"
        base = (
            f"规则 {rule_item['rule_id']}（{rule_item['rule_name']}）失败："
            f"坏行 {rule_item['failed_rows']} 行，失败率 {rule_item['failed_ratio']}，"
            f"阈值 {rule_item['threshold']}"
        )
        message = (
            base + "；样本定位失效，等待证据补齐，暂不可复跑"
            if pending
            else base + "；样本定位有效，可按分区重算实际坏行"
        )
        entry = {
            "id": new_id,
            "status": "未处理",
            "pending": True,
            "abnormal": True,
            "发生时间": datetime.now().isoformat(timespec="seconds"),
            "所属管线": pipeline_id,
            "异常类型": "QualityGateViolation",
            "异常消息": message,
            "影响分区": partition,
            "栈跟踪": f"凭证:{package_id}/规则:{rule_item['rule_id']}",
            "处理状态": "未处理",
            "证据批次": execution_id,
            "证据凭证": package_id,
            "凭证版本": evidence_version,
            "规则ID": rule_item["rule_id"],
            "证据状态": rule_item.get("evidence_status"),
            "定位状态": rule_item.get("locator_status"),
            "导入登记号": import_id,
        }
        rows.append(entry)
        return dict(entry)

    def suggest_recovery(self, exception_id: int) -> dict[str, Any]:
        """为异常提供恢复建议。

        根据异常类型和影响分区，从 store 中的管线定义数据推断
        可用的恢复路径和回滚方案。
        """
        entry = store.find(self.MODULE, exception_id)
        if entry is None:
            return {"error": f"异常记录 {exception_id} 不存在"}

        ex_type = str(entry.get("异常类型", ""))
        pipeline = str(entry.get("所属管线", ""))
        partition = str(entry.get("影响分区", ""))

        suggestions: list[dict[str, Any]] = []
        pipeline_rows = store.rows("pipeline_def") if "pipeline_def" in store._tables else []
        task_rows = store.rows("task_node") if "task_node" in store._tables else []

        if "数据" in ex_type:
            suggestions.append({
                "action": "数据回刷",
                "detail": f"对分区 {partition} 执行回填重刷",
                "estimated_cost": "中",
            })
        if "超时" in ex_type or "timeout" in ex_type.lower():
            suggestions.append({
                "action": "增加资源分配",
                "detail": f"管线 {pipeline} 建议增加计算资源或调整超时阈",
                "estimated_cost": "低",
            })
        if "字段" in ex_type or "schema" in ex_type.lower():
            suggestions.append({
                "action": "Schema 修复",
                "detail": "检查字段定义变更，回滚或适配 Schema",
                "estimated_cost": "高",
            })

        return {
            "exception_id": exception_id,
            "type": ex_type,
            "pipeline": pipeline,
            "suggestions": suggestions,
            "generated_at": datetime.now().isoformat(),
        }

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry, msg = super().run_action(entry_id, action)
        if entry:
            entry["last_operated_at"] = datetime.now().isoformat()
        return entry, msg