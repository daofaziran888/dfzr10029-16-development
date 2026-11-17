"""数据快照 时间线服务：生成作业时间序列和示例数据。"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from app.services.snapshot_manifest import compute_checksum
from app.services.snapshot_recovery import (
    NOT_CLEANED,
    RECOVERABLE,
    VERIFICATION_NONE,
    recovery_service,
)
from app.store import store

MODULE = "snapshot"
LIST_FIELDS = ["快照编号", "数据集名称", "快照时间", "行数快照", "校验哈希", "快照策略", "存储位置", "快照状态"]
REQUIRED_FIELDS = ["快照编号", "数据集名称", "快照时间"]
STATUS_ORDER = ["待执行", "执行中", "已完成", "执行失败"]
ACTION_RULES = {
    "执行快照": "执行中",
    "暂停快照": "待执行",
    "标记失败": "执行失败",
    "完成快照": "已完成",
    "快照完成": "已完成",
    "完成执行": "已完成",
    "标记完成": "已完成",
}
COMPLETION_ACTIONS = {"完成快照", "快照完成", "完成执行", "标记完成"}
NEGATIVE_ACTIONS = {"标记失败"}


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _day(value: Any, fallback: date) -> str:
    text = _text(value)
    if len(text) >= 10:
        try:
            datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return fallback.isoformat()
        return text[:10]
    return fallback.isoformat()


class SnapshotService:
    def get_timeline(self) -> list[dict[str, Any]]:
        rows = store.rows(MODULE)
        today = date.today()
        items: list[dict] = []
        for i, row in enumerate(rows):
            start = _day(row.get("快照时间"), today - timedelta(days=7 - i * 2))
            end_day = (datetime.strptime(start, "%Y-%m-%d").date() + timedelta(days=1)).isoformat()
            verification = _text(row.get("校验结果")) or recovery_service.verification_of(row)["校验结果"]
            recoverable_state = _text(row.get("可恢复状态")) or "未校验"
            items.append({
                "id": int(row.get("id", i + 1)),
                "name": _text(row.get("快照编号")) or f"快照-{i + 1}",
                "dataset": _text(row.get("数据集名称")),
                "label": _text(row.get("快照编号")),
                "start": start,
                "end": end_day,
                "status": str(row.get("status", STATUS_ORDER[i % len(STATUS_ORDER)])),
                "verification": verification,
                "recoverable": recoverable_state,
                "checksum": _text(row.get("校验哈希")),
                "basis_version": row.get("依据包版本"),
                "logs": (
                    f"[{start}] 快照作业登记\n"
                    f"[{start}] 数据集：{_text(row.get('数据集名称')) or '—'}\n"
                    f"[{end_day}] 状态：{_text(row.get('status')) or '待执行'}，"
                    f"校验结果：{verification}，可恢复：{recoverable_state}"
                ),
            })
        return items

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [f for f in REQUIRED_FIELDS if not str(values.get(f) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(r.get("id", 0)) for r in rows), default=0) + 1}
        entry.update({f: values.get(f) for f in LIST_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["校验结果"] = VERIFICATION_NONE
        entry["可恢复状态"] = "不可恢复"
        entry["清理状态"] = NOT_CLEANED
        entry["依据包版本"] = None
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"快照记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据快照可执行范围"
        target = ACTION_RULES[action]
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        entry["快照状态"] = target
        if action in COMPLETION_ACTIONS:
            entry["abnormal"] = False
            if not _text(entry.get("校验哈希")):
                entry["校验哈希"] = compute_checksum(entry)
            package, message = recovery_service.verify_snapshot(
                entry["id"], source="执行完成", remark="快照执行完成后解析校验哈希"
            )
            if package is not None:
                return entry, (
                    f"快照记录已{action}，校验结果：{package.get('校验结果')}，"
                    f"可恢复状态：{package.get('可恢复状态')}"
                )
            return entry, f"快照记录已{action}，但{message}"
        entry["可恢复状态"] = "不可恢复"
        if not _text(entry.get("校验哈希")):
            entry["校验结果"] = VERIFICATION_NONE
        return entry, f"快照记录已{action}"
