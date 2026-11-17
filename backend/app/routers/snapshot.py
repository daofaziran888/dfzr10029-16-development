"""数据快照接口：时间线、快照恢复依据包、可恢复清单与检查点。

固定路径（/recoverable、/basis、/checkpoints、/export）必须声明在 /{entry_id} 之前，
否则会被解析成整数主键而返回 422。
"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.schemas import (
    ActionPayload,
    ActionResult,
    CheckpointEventPayload,
    CheckpointPayload,
    EntryPayload,
    RecoveryPayload,
    VerifyPayload,
)
from app.services.snapshot import SnapshotService
from app.services.snapshot_recovery import recovery_service
from app.services.snapshot_manifest import compute_checksum
from app.store import store

router = APIRouter(prefix="/api/snapshot", tags=["数据快照"])
service = SnapshotService()
LIST_FIELDS = ["快照编号", "数据集名称", "快照时间", "行数快照", "校验哈希", "快照策略", "存储位置", "快照状态"]
STATUSES = ["待执行", "执行中", "已完成", "执行失败"]
RECOVERABLE_FIELDS = [
    "数据集名称",
    "快照编号",
    "快照时间",
    "行数快照",
    "校验结果",
    "可恢复状态",
    "依据包版本",
    "确认校验哈希",
    "存储位置",
]


def csv_cell(value: Any) -> str:
    text = str(value) if value is not None else ""
    if text and text[0] in "=+-@":
        text = "'" + text
    return text


def csv_quoted(value: Any) -> str:
    text = str(value) if value is not None else ""
    if text and text[0] in "=+-@":
        text = "'" + text
    return '"' + text.replace('"', '""') + '"'


def selection_text(selection: dict[str, Any]) -> str:
    """把筛选条件压成一段可读文本，导出时逐行保留选择依据。"""
    parts = []
    if selection.get("数据集名称"):
        parts.append(f"数据集={selection['数据集名称']}")
    if selection.get("校验结果"):
        parts.append(f"校验结果={selection['校验结果']}")
    if selection.get("开始日期") or selection.get("结束日期"):
        parts.append(f"快照时间={selection.get('开始日期') or '不限'}~{selection.get('结束日期') or '不限'}")
    if selection.get("仅看可恢复"):
        parts.append("仅看可恢复=是")
    return "；".join(parts) or "全部已执行快照"


# ------------------------------------------------------------------ 时间线
@router.get("")
def list_entries() -> dict[str, Any]:
    items = service.get_timeline()
    return {"items": items, "recoverable": recovery_service.list_manifest()["summary"]}


@router.get("/export")
def export_entries() -> StreamingResponse:
    items = service.get_timeline()
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    all_keys = ["id"]
    if items:
        all_keys = list(items[0].keys())
    writer.writerow(all_keys)
    for item in items:
        writer.writerow([csv_cell(item.get(k, "")) for k in all_keys])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=snapshot.csv"})


# -------------------------------------------------------------- 可恢复清单
@router.get("/recoverable")
def list_recoverable(
    dataset: str | None = Query(default=None, description="按数据集名称过滤"),
    verification: str | None = Query(default=None, description="校验通过、校验失败、未校验"),
    date_from: str | None = Query(default=None, alias="date_from"),
    date_to: str | None = Query(default=None, alias="date_to"),
    only_recoverable: bool = Query(default=False),
    include_stale: bool = Query(default=False, description="是否包含与当前快照记录不一致的旧清单行"),
) -> dict[str, Any]:
    """按数据集查看可恢复快照清单，校验结果与可恢复状态来自同一条快照记录。"""
    return recovery_service.list_manifest(
        dataset=dataset,
        verification=verification,
        date_from=date_from,
        date_to=date_to,
        only_recoverable=only_recoverable,
        include_stale=include_stale,
    )


@router.get("/recoverable/datasets")
def list_recoverable_datasets() -> dict[str, Any]:
    items = recovery_service.datasets()
    return {"items": items, "total": len(items)}


@router.get("/recoverable/export")
def export_recoverable(
    dataset: str | None = Query(default=None),
    verification: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    only_recoverable: bool = Query(default=False),
) -> StreamingResponse:
    """导出可恢复快照清单；每行带上筛选依据，便于之后复核这份文件是怎么选出来的。"""
    payload = recovery_service.list_manifest(
        dataset=dataset,
        verification=verification,
        date_from=date_from,
        date_to=date_to,
        only_recoverable=only_recoverable,
    )
    basis_text = selection_text(payload["selection"])
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow([*RECOVERABLE_FIELDS, "筛选依据"])
    for item in payload["items"]:
        writer.writerow([
            *[item.get(field, "") for field in RECOVERABLE_FIELDS],
            basis_text,
        ])
    output = "﻿" + buf.getvalue()
    return StreamingResponse(iter([output]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=snapshot_recoverable.csv"})


@router.get("/recoverable/{dataset_name}")
def get_dataset_recoverable(dataset_name: str) -> dict[str, Any]:
    return recovery_service.dataset_manifest(dataset_name)


@router.post("/recoverable/refresh")
def refresh_recoverable() -> dict[str, Any]:
    """重新解析已执行快照的校验哈希并刷新可恢复清单。"""
    return recovery_service.rebuild_manifest()


# ------------------------------------------------------------ 依据包回查
@router.get("/basis")
def list_basis(
    snapshot_id: int | None = Query(default=None),
    source: str | None = Query(default=None),
) -> dict[str, Any]:
    return recovery_service.list_basis(snapshot_id=snapshot_id, source=source)


@router.get("/checkpoints")
def list_checkpoints(status: str | None = Query(default=None)) -> dict[str, Any]:
    return recovery_service.list_checkpoints(status=status)


@router.post("/checkpoints")
def create_checkpoint(payload: CheckpointPayload) -> ActionResult:
    checkpoint, message = recovery_service.create_checkpoint({
        "检查点编号": payload.checkpoint_no,
        "快照记录id": payload.snapshot_id,
        "快照编号": payload.snapshot_no,
        "快照目录": payload.snapshot_dir,
    })
    if checkpoint is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=checkpoint)


@router.get("/checkpoints/{checkpoint_id}")
def get_checkpoint(checkpoint_id: str) -> dict[str, Any]:
    checkpoint = recovery_service.get_checkpoint(checkpoint_id)
    if checkpoint is None:
        raise HTTPException(status_code=404, detail=f"检查点 {checkpoint_id} 不存在")
    return checkpoint


@router.post("/checkpoints/{checkpoint_id}/events")
def submit_checkpoint_event(checkpoint_id: str, payload: CheckpointEventPayload) -> dict[str, Any]:
    """清理完成通知与恢复请求并发落到同一检查点时，只产生一个确认版本。

    用同步路由跑在线程池里，两个请求真的会并发进入服务；服务里同一把锁保证
    读确认版本和写事件是一个整体，因此最终只会有一个「已确认」版本。
    """
    return recovery_service.submit_checkpoint_event(
        checkpoint_id,
        payload.event,
        operator=payload.operator,
        remark=payload.remark or "",
    )


# --------------------------------------------------------- 单条快照动作
@router.get("/{entry_id}")
def get_entry(entry_id: int) -> dict:
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"快照记录 {entry_id} 不存在")
    result = dict(entry)
    verification = recovery_service.verification_of(entry)
    result.setdefault("校验结果", verification["校验结果"])
    result["计算校验哈希"] = verification["计算校验哈希"]
    result["校验说明"] = verification["校验说明"]
    return result


@router.post("")
def create_entry(payload: EntryPayload) -> ActionResult:
    entry_or_none, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="快照记录已登记", entry=entry_or_none)


@router.post("/{entry_id}/actions")
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    entry_or_none, message = service.run_action(entry_id, payload.action)
    if entry_or_none is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry_or_none)


@router.post("/{entry_id}/verify")
def verify_entry(entry_id: int, payload: VerifyPayload) -> ActionResult:
    """解析快照校验哈希并把恢复依据包按版本持久化。"""
    entry = recovery_service.find_snapshot(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"快照记录 {entry_id} 不存在")
    if payload.checksum and str(payload.checksum).strip():
        entry["校验哈希"] = str(payload.checksum).strip()
    package, message = recovery_service.verify_snapshot(
        entry_id, source=payload.source or "校验接口", remark=payload.remark or ""
    )
    if package is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=package)


@router.get("/{entry_id}/basis")
def get_basis(
    entry_id: int,
    version: int | None = Query(default=None),
    source: str | None = Query(default=None),
) -> dict[str, Any]:
    """按版本回查依据包：重新进入时读同一版本，不重新计算当前快照。"""
    view = recovery_service.basis_view(entry_id, version=version, source=source)
    if view is None:
        raise HTTPException(status_code=404, detail=f"快照记录 {entry_id} 还没有可回查的恢复依据包")
    return view


@router.get("/{entry_id}/basis/versions")
def list_basis_versions(entry_id: int) -> dict[str, Any]:
    entry = recovery_service.find_snapshot(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"快照记录 {entry_id} 不存在")
    payload = recovery_service.list_basis(snapshot_id=int(entry.get("id", 0)))
    return {
        "items": [
            {
                "版本": item.get("版本"),
                "来源": item.get("来源"),
                "校验结果": item.get("校验结果"),
                "可恢复状态": item.get("可恢复状态"),
                "状态": item.get("状态"),
                "创建时间": item.get("创建时间"),
                "检查点编号": item.get("检查点编号"),
            }
            for item in payload["items"]
        ],
        "total": payload["total"],
        "sources": payload["sources"],
    }


@router.get("/{entry_id}/recoverable")
def get_entry_recoverable(entry_id: int) -> dict[str, Any]:
    entry = recovery_service.find_snapshot(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"快照记录 {entry_id} 不存在")
    items = [
        item
        for item in recovery_service.list_manifest()["items"]
        if int(item.get("快照记录id", 0)) == int(entry.get("id", 0))
    ]
    return {"items": items, "total": len(items)}


@router.post("/{entry_id}/recover")
def recover_entry(entry_id: int, payload: RecoveryPayload) -> ActionResult:
    """恢复入口使用已确认的校验哈希；未确认或已过期的依据包会被拒绝。"""
    record, message = recovery_service.recover(
        entry_id, operator=payload.operator, remark=payload.remark or ""
    )
    if record is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=record)


@router.get("/{entry_id}/recovery-requests")
def list_recovery_requests(entry_id: int) -> dict[str, Any]:
    entry = recovery_service.find_snapshot(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"快照记录 {entry_id} 不存在")
    rows = [
        row
        for row in store.rows("snapshot_recovery_request")
        if int(row.get("快照记录id", 0)) == int(entry.get("id", 0))
    ]
    return {"items": rows, "total": len(rows)}


@router.get("/{entry_id}/checksum")
def preview_checksum(entry_id: int) -> dict[str, Any]:
    """按清单口径重算校验哈希，供页面和校验接口对齐。"""
    entry = recovery_service.find_snapshot(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"快照记录 {entry_id} 不存在")
    return {
        "快照编号": entry.get("快照编号"),
        "存储校验哈希": entry.get("校验哈希", ""),
        "计算校验哈希": compute_checksum(entry),
        "校验结果": recovery_service.verification_of(entry)["校验结果"],
    }
