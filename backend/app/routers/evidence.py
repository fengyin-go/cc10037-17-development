"""规则证据路由：质量门禁证据冻结、异常核对文件导出/下载/导入与恢复补偿。

所有写操作只在领域服务/证据台账内完成，路由层不做业务判断。
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.engine.evidence_ledger import evidence_ledger
from app.services.evidence import evidence_service

router = APIRouter(prefix="/api/evidence", tags=["规则证据"])


@router.post("/executions")
def run_evidence_gate(body: dict[str, Any]) -> dict[str, Any]:
    """执行一轮质量门禁并冻结规则证据（规则快照+坏行样本定位+来源日期）。"""
    try:
        return evidence_service.run_gate(body)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"缺少必填参数: {exc}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/executions")
def list_executions() -> dict[str, Any]:
    items = evidence_ledger.list_executions()
    return {"items": items, "total": len(items)}


@router.get("/executions/{execution_id}")
def get_execution(execution_id: str) -> dict[str, Any]:
    execution = evidence_ledger.get_execution(execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail=f"执行批次 {execution_id} 不存在")
    return execution


@router.post("/executions/{execution_id}/mark-stale")
def mark_locator_stale(execution_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """将某条规则的样本定位标记为失效（值班人员确认链路已断时使用）。"""
    try:
        return evidence_service.mark_stale(execution_id, body)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'"))


@router.post("/executions/{execution_id}/export")
def export_evidence_package(execution_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """导出异常核对文件（JSON 视图），同批次重复导出递增凭证版本。"""
    try:
        return evidence_service.export_package(execution_id, body)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'"))


@router.get("/packages/{package_id}/download")
def download_evidence_package(package_id: str) -> StreamingResponse:
    """下载异常核对文件，供值班组离线交接。"""
    package = evidence_ledger.get_package(package_id)
    if package is None:
        raise HTTPException(status_code=404, detail=f"核对文件 {package_id} 不存在")
    content = json.dumps(package, ensure_ascii=False, indent=2)
    filename = f"{package_id}.json"
    return StreamingResponse(
        iter([content.encode("utf-8")]),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/packages")
def list_packages() -> dict[str, Any]:
    items = evidence_ledger.list_packages()
    return {"items": items, "total": len(items)}


@router.post("/imports")
def import_evidence_file(payload: dict[str, Any]) -> dict[str, Any]:
    """异常捕获接收异常核对文件：接收导出文件的 JSON 正文。

    值班人员下载的 .json 文件可原样回传本接口；核验通过后按可验证的
    规则与分区登记异常数据，供恢复入口识别补偿范围。
    """
    package = payload.get("evidence_file", payload)
    if not isinstance(package, dict):
        raise HTTPException(status_code=400, detail="核对文件格式不是合法 JSON 对象")
    try:
        return evidence_service.import_package(package)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/imports")
def list_imports() -> dict[str, Any]:
    items = evidence_ledger.list_imports()
    return {"items": items, "total": len(items)}


@router.get("/executions/{execution_id}/recovery-scope")
def get_recovery_scope(execution_id: str) -> dict[str, Any]:
    """恢复入口：区分可复跑分区与等待证据补齐的分区。"""
    try:
        return evidence_service.recovery_scope(execution_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'"))


@router.post("/executions/{execution_id}/compensations")
def submit_compensation(execution_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """通过恢复入口提交补偿，只接受证据可核对的分区。"""
    try:
        return evidence_service.submit_compensation(execution_id, body)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/executions/{execution_id}/reverify")
def reverify_execution(execution_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """恢复后重新核验：沿用原批次规则快照，新版本执行回链原凭证。"""
    try:
        return evidence_service.reverify(execution_id, body)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc).strip("'"))
