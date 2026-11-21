"""重跑补偿领域服务：管理管线重跑任务、回刷范围编排和并行度控制。

上游：depend_scheduler 产出调度状态 → rerun_compensation 执行重跑
消费：backfill_tracker 消费回刷进度用于追踪和数据写入量统计
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class RerunCompensationService(BaseModuleService):
    MODULE = "rerun_compensation"
    ENTITY = "重跑任务"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["任务名称", "目标管线", "重跑范围", "开始分区", "结束分区", "并行度", "补偿原因"]
    REQUIRED_FIELDS = ["任务名称", "目标管线", "重跑范围"]
    STATUS_ORDER = ["待执行", "执行中", "已完成", "补偿失败", "已取消"]
    ACTION_RULES = {"开始回刷": "待执行", "完成补偿": "执行中", "标记失败": "执行中", "取消任务": "待执行"}
    NEGATIVE_ACTIONS = []

    def query_by_pipeline(self, pipeline_name: str) -> list[dict[str, Any]]:
        """按目标管线查询重跑任务。

        从 store 中筛选指定管线的重跑任务记录，
        返回任务列表供回刷进度面板消费。
        """
        result: list[dict[str, Any]] = []
        for row in store.rows(self.MODULE):
            if pipeline_name.casefold() in str(row.get("目标管线", "")).casefold():
                result.append(dict(row))
        return result

    def estimate_rerun_cost(self, task_name: str) -> dict[str, Any]:
        """估算重跑任务的计算资源消耗。

        根据重跑范围的分区跨度、并行度和数据量级估算计算时长和费用，
        产出预估数据供成本分析模块消费。
        """
        task = None
        for row in store.rows(self.MODULE):
            if row.get("任务名称") == task_name:
                task = row
                break
        if task is None:
            return {"error": f"重跑任务 {task_name} 不存在"}

        start_part = str(task.get("开始分区", "0"))
        end_part = str(task.get("结束分区", "0"))
        parallelism = int(task.get("并行度", "2"))
        try:
            start_h = int(start_part)
            end_h = int(end_part)
        except ValueError:
            start_h = 0
            end_h = 0

        partition_count = max(end_h - start_h, 1)
        avg_rows_per_part = 500000
        total_rows = partition_count * avg_rows_per_part
        estimated_seconds = total_rows / (parallelism * 10000)

        return {
            "task": task_name,
            "pipeline": task.get("目标管线"),
            "partition_count": partition_count,
            "estimated_rows": total_rows,
            "parallelism": parallelism,
            "estimated_seconds": round(estimated_seconds, 1),
            "estimated_minutes": round(estimated_seconds / 60, 1),
            "estimated_at": datetime.now().isoformat(),
        }

    def split_batch_range(self, task_name: str, batch_size: int = 3) -> list[dict[str, Any]]:
        """将重跑范围拆分为多个批次。

        根据并行度和开始/结束分区，将重跑任务拆分为可控的批次管道，
        支持分批提交和部分回滚。
        """
        task = None
        for row in store.rows(self.MODULE):
            if row.get("任务名称") == task_name:
                task = row
                break
        if task is None:
            return []

        start_part_str = str(task.get("开始分区", "0"))
        end_part_str = str(task.get("结束分区", "0"))
        try:
            start_h = int(start_part_str)
            end_h = int(end_part_str)
        except ValueError:
            start_h = 0
            end_h = 0

        total_parts = max(end_h - start_h, 1)
        batches: list[dict[str, Any]] = []
        for i in range(0, total_parts, batch_size):
            batch_start = start_h + i
            batch_end = min(start_h + i + batch_size, end_h)
            batches.append({
                "batch_no": len(batches) + 1,
                "start_part": str(batch_start),
                "end_part": str(batch_end),
                "partitions": batch_end - batch_start,
                "task": task_name,
                "status": "待执行",
            })

        return batches

    def compute_compensation_rate(self, pipeline_name: str) -> dict[str, Any]:
        """计算管线的补偿率。

        统计指定管线所有重跑任务的完成和失败情况，
        产出补偿率指标供运维质量评估。
        """
        tasks = self.query_by_pipeline(pipeline_name)
        total = len(tasks)
        completed = sum(1 for t in tasks if t.get("status") == "已完成")
        failed = sum(1 for t in tasks if t.get("status") == "补偿失败")
        in_progress = sum(1 for t in tasks if t.get("status") == "执行中")

        return {
            "pipeline": pipeline_name,
            "total_rerun_tasks": total,
            "completed": completed,
            "failed": failed,
            "in_progress": in_progress,
            "completion_rate_pct": round(completed / max(total, 1) * 100, 1),
            "failure_rate_pct": round(failed / max(total, 1) * 100, 1),
            "snapshot_at": datetime.now().isoformat(),
        }

    def register_evidence_compensation(
        self,
        *,
        pipeline_id: str,
        execution_id: str,
        partitions: list[str],
        reason: str,
    ) -> dict[str, Any]:
        """登记证据驱动的回刷补偿任务，分区列表来自恢复入口的可核对范围。"""
        rows = store.rows(self.MODULE)
        new_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
        task_name = f"证据补偿-{execution_id}-{partitions[0]}"
        entry = {
            "id": new_id,
            "status": "待执行",
            "pending": True,
            "abnormal": False,
            "任务名称": task_name,
            "目标管线": pipeline_id,
            "重跑范围": "证据分区复跑",
            "开始分区": partitions[0],
            "结束分区": partitions[-1],
            "并行度": "4",
            "补偿原因": reason or f"异常核对文件 {execution_id} 驱动的坏行补偿",
            "证据批次": execution_id,
            "补偿分区": ",".join(partitions),
        }
        rows.append(entry)
        return dict(entry)

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry, msg = super().run_action(entry_id, action)
        if entry:
            entry["last_operated_at"] = datetime.now().isoformat()
        return entry, msg